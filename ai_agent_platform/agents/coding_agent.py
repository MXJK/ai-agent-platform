from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Literal, Optional, Protocol, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from ai_agent_platform.domain import Message
from ai_agent_platform.integrations import (
    RAGConfigurationError,
    RAGProviderError,
    RAGService,
    RAGValidationError,
)
from ai_agent_platform.integrations.rag import RetrievedDocument
from ai_agent_platform.integrations.tools import (
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolSpec,
    summarize_tool_arguments,
)
from ai_agent_platform.integrations.mcp import MCPToolProvider, register_mcp_tools
from ai_agent_platform.tools import register_repository_tools, register_sandbox_tools


CODING_AGENT_ROLE = "研发助手 / 代码仓库问答 Agent"
CODING_AGENT_OBJECTIVE = (
    "围绕代码仓库检索上下文、解释实现、定位文件/符号、规划安全改动，"
    "并输出可复盘的执行轨迹。"
)
VALID_AGENT_INTENTS = {
    "repository_question",
    "repo_navigation",
    "code_explanation",
    "change_planning",
    "bug_investigation",
    "test_strategy",
    "small_talk",
}


class CodingAgentState(TypedDict, total=False):
    run_id: str
    conversation_id: str
    user_input: str
    repository_id: str
    history: list[dict[str, str]]
    focus_files: list[str]
    intent: str
    intent_reason: str
    intent_confidence: float
    planner_source: str
    rag_context: list[RetrievedDocument]
    tool_calls: list[ToolCall]
    tool_results: list[dict[str, Any]]
    answer: str
    trace: list[dict[str, Any]]
    started_at: float
    review_decision: dict[str, Any]
    approval_required_tools: list[dict[str, Any]]
    errors: list[dict[str, Any]]


AgentRoute = Literal["retrieve_repository_context", "compose_answer"]
PlanRoute = Literal["review_tool_plan", "inspect_repository"]
ReviewRoute = Literal["inspect_repository", "compose_answer"]
RetrievalRoute = Literal["plan_tools", "handle_error"]
AnswerRoute = Literal["handle_error", "end"]
AgentRunStatus = Literal["queued", "running", "waiting_approval", "completed", "failed"]
MAX_NODE_RETRIES = 2


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    thread_id: str
    conversation_id: str
    repository_id: str
    status: AgentRunStatus
    checkpoint_id: Optional[str]
    role: str
    objective: str
    intent: str
    answer: str
    graph_engine: str
    rag_context: list[RetrievedDocument]
    tool_calls: list[ToolCall]
    tool_results: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    errors: list[dict[str, Any]] = field(default_factory=list)
    pending_approval: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class AgentRunRecord:
    run_id: str
    thread_id: str
    conversation_id: str
    repository_id: str
    status: AgentRunStatus
    checkpoint_id: Optional[str]
    latest_node: Optional[str]
    next_nodes: list[str]
    trace: list[dict[str, Any]]
    result: Optional[AgentRunResult] = None
    error: Optional[str] = None
    pending_approval: Optional[dict[str, Any]] = None
    errors: list[dict[str, Any]] = field(default_factory=list)


class AgentRunNotFoundError(Exception):
    pass


class AgentRunInvalidStateError(Exception):
    def __init__(self, run_id: str, status: str) -> None:
        super().__init__(f"agent run {run_id} cannot be resumed from status {status}")
        self.run_id = run_id
        self.status = status


class AgentRunStore(Protocol):
    def save(self, record: AgentRunRecord) -> None:
        ...

    def get(self, run_id: str) -> AgentRunRecord:
        ...


class LLMCompletionClient(Protocol):
    def complete(self, prompt: str) -> Any:
        ...


class AgentPlanner(Protocol):
    def classify_intent(self, user_input: str) -> dict[str, Any]:
        ...

    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        ...


class RuleBasedAgentPlanner:
    source = "rules"

    def classify_intent(self, user_input: str) -> dict[str, Any]:
        intent, reason = _classify_intent(user_input)
        return {
            "intent": intent,
            "reason": reason,
            "confidence": 0.72,
            "source": self.source,
        }

    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        return _plan_rule_based_tool_calls(state, tool_specs)


class LLMStructuredAgentPlanner:
    """Uses structured LLM JSON for agent decisions with rule-based fallback."""

    source = "llm_structured"

    def __init__(
        self,
        llm_client: LLMCompletionClient,
        *,
        fallback: AgentPlanner | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._fallback = fallback or RuleBasedAgentPlanner()

    def classify_intent(self, user_input: str) -> dict[str, Any]:
        try:
            body = _json_object_from_llm(
                self._llm_client.complete(_intent_classification_prompt(user_input)).text
            )
            intent = str(body.get("intent", ""))
            if intent not in VALID_AGENT_INTENTS:
                raise ValueError(f"unsupported intent: {intent}")
            return {
                "intent": intent,
                "reason": str(body.get("reason") or "LLM structured decision"),
                "confidence": _bounded_confidence(body.get("confidence")),
                "source": self.source,
            }
        except Exception:
            return self._fallback.classify_intent(user_input)

    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        try:
            body = _json_object_from_llm(
                self._llm_client.complete(_tool_planning_prompt(state, tool_specs)).text
            )
            calls = _tool_calls_from_structured_plan(body, state, tool_specs)
            if not calls:
                raise ValueError("LLM returned no executable tool calls")
            return calls
        except Exception:
            return self._fallback.plan_tool_calls(state, tool_specs)


class InMemoryAgentRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, AgentRunRecord] = {}
        self._lock = Lock()

    def save(self, record: AgentRunRecord) -> None:
        with self._lock:
            self._runs[record.run_id] = record

    def get(self, run_id: str) -> AgentRunRecord:
        with self._lock:
            return self._runs[run_id]


class CodingAgentRuntime:
    """LangGraph runtime for a repository-aware development assistant."""

    def __init__(
        self,
        *,
        rag_service: RAGService,
        tool_registry: Optional[ToolRegistry] = None,
        run_store: Optional[AgentRunStore] = None,
        checkpointer: Any = None,
        planner: AgentPlanner | None = None,
    ) -> None:
        self._rag_service = rag_service
        self._tools = tool_registry or create_coding_tool_registry()
        self._checkpointer = checkpointer or InMemorySaver()
        self._run_store = run_store or InMemoryAgentRunStore()
        self._planner = planner or RuleBasedAgentPlanner()
        self._graph = self._build_graph()
        self.graph_engine = "langgraph"

    def run(
        self,
        *,
        conversation_id: str,
        user_input: str,
        history: list[Message],
        repository_id: str = "repo_main",
        focus_files: Optional[list[str]] = None,
        run_id: Optional[str] = None,
    ) -> AgentRunResult:
        run_id = run_id or f"run_{uuid4().hex[:12]}"
        thread_id = run_id
        config = {"configurable": {"thread_id": thread_id}}
        self._run_store.save(
            AgentRunRecord(
                run_id=run_id,
                thread_id=thread_id,
                conversation_id=conversation_id,
                repository_id=repository_id,
                status="running",
                checkpoint_id=None,
                latest_node=None,
                next_nodes=["setup"],
                trace=[],
            )
        )

        try:
            state = self._graph.invoke(
                {
                    "conversation_id": conversation_id,
                    "run_id": run_id,
                    "user_input": user_input,
                    "repository_id": repository_id,
                    "focus_files": focus_files or [],
                    "history": [
                        {"role": message.role, "content": message.content}
                        for message in history
                    ],
                    "trace": [],
                    "errors": [],
                    "started_at": perf_counter(),
                },
                config,
            )
        except Exception as exc:
            snapshot = self._snapshot_for(config)
            self._run_store.save(
                AgentRunRecord(
                    run_id=run_id,
                    thread_id=thread_id,
                    conversation_id=conversation_id,
                    repository_id=repository_id,
                    status="failed",
                    checkpoint_id=_checkpoint_id(snapshot),
                    latest_node=_latest_trace_node(snapshot),
                    next_nodes=_next_nodes(snapshot),
                    trace=_snapshot_trace(snapshot),
                    error=str(exc),
                    errors=_snapshot_errors(snapshot)
                    + [_error_from_exception("runtime", exc, attempt=1, max_attempts=1)],
                )
            )
            raise

        snapshot = self._snapshot_for(config)
        pending_approval = _pending_approval(snapshot, state)
        if pending_approval is not None:
            result = self._build_result(
                run_id=run_id,
                thread_id=thread_id,
                conversation_id=conversation_id,
                repository_id=repository_id,
                status="waiting_approval",
                checkpoint_id=_checkpoint_id(snapshot),
                state=state,
                pending_approval=pending_approval,
            )
            self._run_store.save(
                AgentRunRecord(
                    run_id=run_id,
                    thread_id=thread_id,
                    conversation_id=conversation_id,
                    repository_id=repository_id,
                    status="waiting_approval",
                    checkpoint_id=result.checkpoint_id,
                    latest_node="review_tool_plan",
                    next_nodes=_next_nodes(snapshot),
                    trace=result.trace,
                    result=result,
                    pending_approval=pending_approval,
                )
            )
            return result

        result = self._build_result(
            run_id=run_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            repository_id=repository_id,
            status="completed",
            checkpoint_id=_checkpoint_id(snapshot),
            state=state,
        )
        self._run_store.save(
            AgentRunRecord(
                run_id=run_id,
                thread_id=thread_id,
                conversation_id=conversation_id,
                repository_id=repository_id,
                status="completed",
                checkpoint_id=result.checkpoint_id,
                latest_node=_latest_trace_node(snapshot),
                next_nodes=_next_nodes(snapshot),
                trace=result.trace,
                result=result,
            )
        )
        return result

    def create_queued_run(
        self,
        *,
        conversation_id: str,
        repository_id: str,
    ) -> AgentRunRecord:
        run_id = f"run_{uuid4().hex[:12]}"
        record = AgentRunRecord(
            run_id=run_id,
            thread_id=run_id,
            conversation_id=conversation_id,
            repository_id=repository_id,
            status="queued",
            checkpoint_id=None,
            latest_node=None,
            next_nodes=["setup"],
            trace=[],
        )
        self._run_store.save(record)
        return record

    def resume(
        self,
        *,
        run_id: str,
        approved: bool,
        feedback: Optional[str] = None,
    ) -> AgentRunResult:
        record = self.get_run(run_id)
        if record.status != "waiting_approval":
            raise AgentRunInvalidStateError(run_id, record.status)

        config = {"configurable": {"thread_id": record.thread_id}}
        resume_payload = {
            "approved": approved,
            "feedback": feedback or "",
        }
        try:
            state = self._graph.invoke(Command(resume=resume_payload), config)
        except Exception as exc:
            snapshot = self._snapshot_for(config)
            self._run_store.save(
                AgentRunRecord(
                    run_id=record.run_id,
                    thread_id=record.thread_id,
                    conversation_id=record.conversation_id,
                    repository_id=record.repository_id,
                    status="failed",
                    checkpoint_id=_checkpoint_id(snapshot),
                    latest_node=_latest_trace_node(snapshot),
                    next_nodes=_next_nodes(snapshot),
                    trace=_snapshot_trace(snapshot),
                    error=str(exc),
                    errors=_snapshot_errors(snapshot)
                    + [_error_from_exception("runtime", exc, attempt=1, max_attempts=1)],
                )
            )
            raise

        snapshot = self._snapshot_for(config)
        pending_approval = _pending_approval(snapshot, state)
        if pending_approval is not None:
            result = self._build_result(
                run_id=record.run_id,
                thread_id=record.thread_id,
                conversation_id=record.conversation_id,
                repository_id=record.repository_id,
                status="waiting_approval",
                checkpoint_id=_checkpoint_id(snapshot),
                state=state,
                pending_approval=pending_approval,
            )
            self._run_store.save(
                AgentRunRecord(
                    run_id=record.run_id,
                    thread_id=record.thread_id,
                    conversation_id=record.conversation_id,
                    repository_id=record.repository_id,
                    status="waiting_approval",
                    checkpoint_id=result.checkpoint_id,
                    latest_node="review_tool_plan",
                    next_nodes=_next_nodes(snapshot),
                    trace=result.trace,
                    result=result,
                    pending_approval=pending_approval,
                )
            )
            return result

        result = self._build_result(
            run_id=record.run_id,
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            repository_id=record.repository_id,
            status="completed",
            checkpoint_id=_checkpoint_id(snapshot),
            state=state,
        )
        self._run_store.save(
            AgentRunRecord(
                run_id=record.run_id,
                thread_id=record.thread_id,
                conversation_id=record.conversation_id,
                repository_id=record.repository_id,
                status="completed",
                checkpoint_id=result.checkpoint_id,
                latest_node=_latest_trace_node(snapshot),
                next_nodes=_next_nodes(snapshot),
                trace=result.trace,
                result=result,
            )
        )
        return result

    def _build_result(
        self,
        *,
        run_id: str,
        thread_id: str,
        conversation_id: str,
        repository_id: str,
        status: AgentRunStatus,
        checkpoint_id: Optional[str],
        state: CodingAgentState,
        pending_approval: Optional[dict[str, Any]] = None,
    ) -> AgentRunResult:
        answer = state.get("answer", "") if status == "completed" else ""
        return AgentRunResult(
            run_id=run_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            repository_id=repository_id,
            status=status,
            checkpoint_id=checkpoint_id,
            role=CODING_AGENT_ROLE,
            objective=CODING_AGENT_OBJECTIVE,
            intent=state.get("intent", "repository_question"),
            answer=answer,
            graph_engine=self.graph_engine,
            rag_context=state.get("rag_context", []),
            tool_calls=state.get("tool_calls", []),
            tool_results=state.get("tool_results", []),
            trace=state.get("trace", []),
            errors=state.get("errors", []),
            pending_approval=pending_approval,
        )

    def get_run(self, run_id: str) -> AgentRunRecord:
        try:
            return self._run_store.get(run_id)
        except KeyError as exc:
            raise AgentRunNotFoundError(run_id) from exc

    def _snapshot_for(self, config: dict[str, Any]):
        try:
            return self._graph.get_state(config)
        except Exception:
            return None

    def _build_graph(self):
        workflow = StateGraph(CodingAgentState)
        workflow.add_node("setup", self._setup)
        workflow.add_node("classify_request", self._classify_request)
        workflow.add_node("retrieve_repository_context", self._retrieve_repository_context)
        workflow.add_node("plan_tools", self._plan_tools)
        workflow.add_node("review_tool_plan", self._review_tool_plan)
        workflow.add_node("inspect_repository", self._inspect_repository)
        workflow.add_node("compose_answer", self._compose_answer)
        workflow.add_node("handle_error", self._handle_error)
        workflow.add_node("compose_error_answer", self._compose_error_answer)
        workflow.set_entry_point("setup")
        workflow.add_edge("setup", "classify_request")
        workflow.add_conditional_edges(
            "classify_request",
            _route_after_classification,
            {
                "retrieve_repository_context": "retrieve_repository_context",
                "compose_answer": "compose_answer",
            },
        )
        workflow.add_conditional_edges(
            "retrieve_repository_context",
            _route_after_retrieval,
            {
                "plan_tools": "plan_tools",
                "handle_error": "handle_error",
            },
        )
        workflow.add_conditional_edges(
            "plan_tools",
            _route_after_tool_planning,
            {
                "review_tool_plan": "review_tool_plan",
                "inspect_repository": "inspect_repository",
            },
        )
        workflow.add_conditional_edges(
            "review_tool_plan",
            _route_after_tool_plan_review,
            {
                "inspect_repository": "inspect_repository",
                "compose_answer": "compose_answer",
            },
        )
        workflow.add_edge("inspect_repository", "compose_answer")
        workflow.add_conditional_edges(
            "compose_answer",
            _route_after_answer_composition,
            {
                "handle_error": "handle_error",
                "end": END,
            },
        )
        workflow.add_edge("handle_error", "compose_error_answer")
        workflow.add_edge("compose_error_answer", END)
        return workflow.compile(checkpointer=self._checkpointer)

    def _setup(self, state: CodingAgentState) -> CodingAgentState:
        history = state.get("history", [])
        return {
            "trace": _append_trace(
                state,
                node="setup",
                summary="加载研发助手角色、仓库范围和多轮上下文。",
                output={
                    "role": CODING_AGENT_ROLE,
                    "objective": CODING_AGENT_OBJECTIVE,
                    "repository_id": state["repository_id"],
                    "focus_files": state.get("focus_files", []),
                    "history_messages": len(history),
                },
            )
        }

    def _classify_request(self, state: CodingAgentState) -> CodingAgentState:
        decision = self._planner.classify_intent(state["user_input"])
        intent = str(decision.get("intent") or "repository_question")
        reason = str(decision.get("reason") or "")
        confidence = _bounded_confidence(decision.get("confidence"))
        source = str(decision.get("source") or "unknown")
        return {
            "intent": intent,
            "intent_reason": reason,
            "intent_confidence": confidence,
            "planner_source": source,
            "trace": _append_trace(
                state,
                node="classify_request",
                summary="判断用户是在问实现、定位代码、排查问题、规划改动还是设计测试。",
                output={
                    "intent": intent,
                    "reason": reason,
                    "confidence": confidence,
                    "planner_source": source,
                    "next_node": _next_node_for_intent(intent),
                },
            ),
        }

    def _retrieve_repository_context(
        self, state: CodingAgentState
    ) -> CodingAgentState:
        citations, errors, attempts = _run_with_retries(
            node="retrieve_repository_context",
            operation=lambda: self._rag_service.search(
                knowledge_base_id=state["repository_id"],
                query=_build_repository_query(state),
                limit=4,
                recall_limit=12,
            ),
            classify_error=_classify_rag_error,
        )

        if citations is None:
            citations = []
            trace_output: dict[str, Any] = {
                "repository_id": state["repository_id"],
                "citation_count": 0,
                "attempts": attempts,
                "error_count": len(errors),
                "status": "failed",
            }
        else:
            trace_output = {
                "repository_id": state["repository_id"],
                "citation_count": len(citations),
                "filenames": [citation.filename for citation in citations],
                "attempts": attempts,
                "recovered_error_count": len(errors),
            }

        return {
            "rag_context": citations,
            "errors": _append_errors(state, errors),
            "trace": _append_trace(
                state,
                node="retrieve_repository_context",
                summary="从仓库索引中检索最可能相关的文件片段。",
                output=trace_output,
            ),
        }

    def _plan_tools(self, state: CodingAgentState) -> CodingAgentState:
        tool_specs = self._tools.list_specs()
        tool_calls = self._planner.plan_tool_calls(state, tool_specs)
        approval_required_tools = _approval_required_tools(tool_calls, tool_specs)
        return {
            "tool_calls": tool_calls,
            "approval_required_tools": approval_required_tools,
            "trace": _append_trace(
                state,
                node="plan_tools",
                summary="根据意图和检索结果规划研发助手工具调用。",
                output={
                    "available_tool_count": len(tool_specs),
                    "planned_tools": [tool_call.name for tool_call in tool_calls],
                    "planner_source": state.get("planner_source", "unknown"),
                    "approval_required_tools": [
                        item["name"] for item in approval_required_tools
                    ],
                },
            ),
        }

    def _review_tool_plan(self, state: CodingAgentState) -> CodingAgentState:
        approval_request = _build_tool_plan_approval_request(state)
        decision = interrupt(approval_request)
        if isinstance(decision, dict):
            approved = bool(decision.get("approved"))
            feedback = str(decision.get("feedback") or "")
        else:
            approved = bool(decision)
            feedback = ""

        review_decision = {
            "approved": approved,
            "feedback": feedback,
        }
        return {
            "review_decision": review_decision,
            "trace": _append_trace(
                state,
                node="review_tool_plan",
                summary="人工审批需要权限确认的工具计划，决定是否继续执行。",
                output=review_decision,
            ),
        }

    def _inspect_repository(self, state: CodingAgentState) -> CodingAgentState:
        tool_results: list[dict[str, Any]] = []
        context = ToolExecutionContext(
            conversation_id=state["conversation_id"],
            repository_id=state["repository_id"],
            run_id=state.get("run_id"),
        )
        for tool_call in state.get("tool_calls", []):
            tool_results.append(
                self._tools.execute(tool_call, context=context).to_response()
            )

        return {
            "tool_results": tool_results,
            "trace": _append_trace(
                state,
                node="inspect_repository",
                summary="执行仓库检索、符号定位、方案规划或测试建议工具。",
                output={
                    "called_tools": [item["name"] for item in tool_results],
                    "success_count": sum(1 for item in tool_results if item["ok"]),
                },
            ),
        }

    def _compose_answer(self, state: CodingAgentState) -> CodingAgentState:
        answer, errors, attempts = _run_with_retries(
            node="compose_answer",
            operation=lambda: _format_answer(state),
            classify_error=_classify_answer_error,
        )
        elapsed_ms = int((perf_counter() - state["started_at"]) * 1000)
        if answer is None:
            answer = ""
        return {
            "answer": answer,
            "errors": _append_errors(state, errors),
            "trace": _append_trace(
                state,
                node="compose_answer",
                summary="汇总代码上下文、工具结果和下一步研发建议。",
                output={
                    "elapsed_ms": elapsed_ms,
                    "answer_chars": len(answer),
                    "attempts": attempts,
                    "error_count": len(errors),
                },
            ),
        }

    def _handle_error(self, state: CodingAgentState) -> CodingAgentState:
        unresolved_errors = _unresolved_errors(state)
        return {
            "trace": _append_trace(
                state,
                node="handle_error",
                summary="汇总未恢复错误，并切换到错误回答分支。",
                output={
                    "error_count": len(unresolved_errors),
                    "codes": [error["code"] for error in unresolved_errors],
                    "nodes": _unique([error["node"] for error in unresolved_errors]),
                },
            ),
        }

    def _compose_error_answer(self, state: CodingAgentState) -> CodingAgentState:
        answer = _format_error_answer(state)
        elapsed_ms = int((perf_counter() - state["started_at"]) * 1000)
        return {
            "answer": answer,
            "trace": _append_trace(
                state,
                node="compose_error_answer",
                summary="生成可复盘的错误回答，说明失败节点、重试情况和下一步处理建议。",
                output={"elapsed_ms": elapsed_ms, "answer_chars": len(answer)},
            ),
        }


def _checkpoint_id(snapshot: Any) -> Optional[str]:
    if snapshot is None:
        return None
    configurable = snapshot.config.get("configurable", {})
    checkpoint_id = configurable.get("checkpoint_id")
    return str(checkpoint_id) if checkpoint_id else None


def _next_nodes(snapshot: Any) -> list[str]:
    if snapshot is None:
        return []
    return [str(node) for node in snapshot.next]


def _pending_approval(
    snapshot: Any, state: dict[str, Any]
) -> Optional[dict[str, Any]]:
    if "__interrupt__" in state:
        interrupts = state["__interrupt__"]
        if interrupts:
            return _approval_payload_from_interrupt(interrupts[0])
    if snapshot is None:
        return None
    for task in getattr(snapshot, "tasks", ()):
        for task_interrupt in getattr(task, "interrupts", ()):
            return _approval_payload_from_interrupt(task_interrupt)
    return None


def _approval_payload_from_interrupt(task_interrupt: Any) -> dict[str, Any]:
    value = getattr(task_interrupt, "value", {})
    payload = dict(value) if isinstance(value, dict) else {"message": str(value)}
    interrupt_id = getattr(task_interrupt, "id", None)
    if interrupt_id:
        payload["interrupt_id"] = str(interrupt_id)
    return payload


def _snapshot_trace(snapshot: Any) -> list[dict[str, Any]]:
    if snapshot is None or not isinstance(snapshot.values, dict):
        return []
    trace = snapshot.values.get("trace", [])
    return list(trace) if isinstance(trace, list) else []


def _snapshot_errors(snapshot: Any) -> list[dict[str, Any]]:
    if snapshot is None or not isinstance(snapshot.values, dict):
        return []
    errors = snapshot.values.get("errors", [])
    return list(errors) if isinstance(errors, list) else []


def _latest_trace_node(snapshot: Any) -> Optional[str]:
    trace = _snapshot_trace(snapshot)
    if not trace:
        return None
    latest = trace[-1]
    node = latest.get("node") if isinstance(latest, dict) else None
    return str(node) if node else None


def _run_with_retries(
    *,
    node: str,
    operation: Callable[[], Any],
    classify_error: Callable[[Exception], tuple[str, bool]],
    max_retries: int = MAX_NODE_RETRIES,
) -> tuple[Any, list[dict[str, Any]], int]:
    errors: list[dict[str, Any]] = []
    max_attempts = max_retries + 1
    for attempt in range(1, max_attempts + 1):
        try:
            result = operation()
        except Exception as exc:
            code, retryable = classify_error(exc)
            should_retry = retryable and attempt < max_attempts
            errors.append(
                _structured_error(
                    node=node,
                    code=code,
                    message=str(exc),
                    retryable=retryable,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    recovered=False,
                )
            )
            if not should_retry:
                return None, errors, attempt
            continue

        if errors:
            errors = [dict(error, recovered=True) for error in errors]
        return result, errors, attempt

    return None, errors, max_attempts


def _classify_rag_error(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, RAGValidationError):
        return "rag_validation_error", False
    if isinstance(exc, RAGConfigurationError):
        return "rag_configuration_error", False
    if isinstance(exc, RAGProviderError):
        return "rag_provider_error", True
    return "rag_unhandled_error", False


def _classify_answer_error(exc: Exception) -> tuple[str, bool]:
    return "answer_generation_error", True


def _structured_error(
    *,
    node: str,
    code: str,
    message: str,
    retryable: bool,
    attempt: int,
    max_attempts: int,
    recovered: bool,
) -> dict[str, Any]:
    return {
        "node": node,
        "code": code,
        "message": message,
        "retryable": retryable,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "recovered": recovered,
    }


def _error_from_exception(
    node: str, exc: Exception, *, attempt: int, max_attempts: int
) -> dict[str, Any]:
    return _structured_error(
        node=node,
        code="runtime_error",
        message=str(exc),
        retryable=False,
        attempt=attempt,
        max_attempts=max_attempts,
        recovered=False,
    )


def _append_errors(
    state: CodingAgentState, errors: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not errors:
        return list(state.get("errors", []))
    return list(state.get("errors", [])) + errors


def _unresolved_errors(state: CodingAgentState) -> list[dict[str, Any]]:
    return [
        error
        for error in state.get("errors", [])
        if not error.get("recovered", False)
    ]


def create_coding_tool_registry(
    root_path: Path | str | None = None,
    mcp_providers: Optional[list[MCPToolProvider]] = None,
    sandbox_mode: str = "local",
    sandbox_docker_image: str = "python:3.11-slim",
    sandbox_command_timeout_seconds: float = 30.0,
    sandbox_workspace_parent: Path | str | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    resolved_root_path = root_path or Path.cwd()
    register_repository_tools(registry, resolved_root_path)
    register_sandbox_tools(
        registry,
        root_path=resolved_root_path,
        mode=sandbox_mode,
        docker_image=sandbox_docker_image,
        command_timeout_seconds=sandbox_command_timeout_seconds,
        workspace_parent=sandbox_workspace_parent,
    )
    if mcp_providers:
        register_mcp_tools(registry, mcp_providers)
    registry.register(
        "repository_context_search",
        _repository_context_search_tool,
        description="Summarize repository RAG retrieval state for answer grounding.",
    )
    registry.register(
        "file_symbol_locator",
        _file_symbol_locator_tool,
        description="Suggest file and symbol location commands from retrieved context.",
    )
    registry.register(
        "code_explainer",
        _code_explainer_tool,
        description="Build a structured explanation plan from retrieved snippets.",
    )
    registry.register(
        "change_planner",
        _change_planner_tool,
        description="Plan a safe code change across candidate files.",
        permission_level="write_safe",
        requires_approval=True,
        risk_summary=(
            "Plans implementation work that may lead to code edits; human review "
            "is required before execution."
        ),
    )
    registry.register(
        "bug_investigator",
        _bug_investigator_tool,
        description="Plan a focused debugging path for a reported symptom.",
    )
    registry.register(
        "test_designer",
        _test_designer_tool,
        description="Suggest focused tests for a requested behavior or fix.",
    )
    return registry


def _classify_intent(text: str) -> tuple[str, str]:
    normalized = text.lower()
    if re.search(r"(帮我|需要|请|新增|修改|改成|支持|接入).{0,12}实现", normalized):
        return "change_planning", "implementation planning phrase matched"
    rules: list[tuple[str, tuple[str, ...], str]] = [
        (
            "bug_investigation",
            ("报错", "异常", "bug", "失败", "fail", "traceback", "exception", "修复"),
            "failure or debugging keyword matched",
        ),
        (
            "test_strategy",
            ("测试", "单测", "覆盖率", "pytest", "unittest", "test"),
            "test keyword matched",
        ),
        (
            "repo_navigation",
            ("在哪", "哪里", "哪个文件", "哪个函数", "入口", "调用链", "symbol", "class "),
            "repository navigation keyword matched",
        ),
        (
            "code_explanation",
            ("解释", "讲解", "怎么启动", "流程", "架构", "模块", "接口", "函数", "class"),
            "code explanation keyword matched",
        ),
        (
            "change_planning",
            ("新增", "修改", "重构", "接入", "支持", "改成", "帮我做", "加上"),
            "implementation planning keyword matched",
        ),
    ]
    for intent, keywords, reason in rules:
        if any(keyword in normalized for keyword in keywords):
            return intent, reason
    return "repository_question", "default repository QA route"


def _route_after_classification(state: CodingAgentState) -> AgentRoute:
    return _next_node_for_intent(state.get("intent", "repository_question"))


def _next_node_for_intent(intent: str) -> AgentRoute:
    if intent == "small_talk":
        return "compose_answer"
    return "retrieve_repository_context"


def _route_after_retrieval(state: CodingAgentState) -> RetrievalRoute:
    if _unresolved_errors(state):
        return "handle_error"
    return "plan_tools"


def _route_after_tool_planning(state: CodingAgentState) -> PlanRoute:
    if state.get("approval_required_tools"):
        return "review_tool_plan"
    return "inspect_repository"


def _route_after_tool_plan_review(state: CodingAgentState) -> ReviewRoute:
    decision = state.get("review_decision", {})
    if decision.get("approved"):
        return "inspect_repository"
    return "compose_answer"


def _route_after_answer_composition(state: CodingAgentState) -> AnswerRoute:
    if _unresolved_errors(state):
        return "handle_error"
    return "end"


def _build_repository_query(state: CodingAgentState) -> str:
    parts = [state["user_input"]]
    focus_files = state.get("focus_files", [])
    if focus_files:
        parts.append("重点文件: " + " ".join(focus_files))
    return "\n".join(parts)


def _build_tool_plan_approval_request(state: CodingAgentState) -> dict[str, Any]:
    approval_required_tools = state.get("approval_required_tools", [])
    return {
        "type": "tool_plan_review",
        "approval_required": True,
        "reason": "one or more planned tools require human approval before execution",
        "intent": state.get("intent", "change_planning"),
        "repository_id": state["repository_id"],
        "message": state["user_input"],
        "planned_tools": [tool_call.name for tool_call in state.get("tool_calls", [])],
        "approval_required_tools": approval_required_tools,
        "tool_calls": [
            {
                "name": tool_call.name,
                "arguments": tool_call.arguments,
            }
            for tool_call in state.get("tool_calls", [])
        ],
    }


def _intent_classification_prompt(user_input: str) -> str:
    intents = ", ".join(sorted(VALID_AGENT_INTENTS))
    return (
        "You are classifying a coding-agent user request. "
        "Return only one JSON object with keys intent, reason, confidence. "
        f"Allowed intent values: {intents}.\n"
        f"User request:\n{user_input}"
    )


def _tool_planning_prompt(
    state: CodingAgentState,
    tool_specs: list[ToolSpec],
) -> str:
    tool_payload = [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
            "provider": spec.provider,
            "permission_level": spec.permission_level,
            "requires_approval": spec.requires_approval,
        }
        for spec in tool_specs
    ]
    citations = [
        {
            "filename": citation.filename,
            "chunk_index": citation.chunk_index,
            "score": citation.score,
            "snippet": _snippet(citation.text, limit=180),
        }
        for citation in state.get("rag_context", [])
    ]
    payload = {
        "user_input": state["user_input"],
        "intent": state.get("intent", "repository_question"),
        "repository_id": state["repository_id"],
        "focus_files": state.get("focus_files", []),
        "retrieved_context": citations,
        "available_tools": tool_payload,
    }
    return (
        "You are planning tool calls for a coding-agent backend. "
        "Return only one JSON object: {\"tool_calls\": [{\"name\": string, "
        "\"arguments\": object}]}. Use only available tool names. Prefer "
        "read-only tools unless the intent needs change planning.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _json_object_from_llm(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("structured LLM response must be a JSON object")
    return parsed


def _bounded_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(confidence, 1.0))


def _tool_calls_from_structured_plan(
    body: dict[str, Any],
    state: CodingAgentState,
    tool_specs: list[ToolSpec],
) -> list[ToolCall]:
    raw_calls = body.get("tool_calls", [])
    if not isinstance(raw_calls, list):
        raise ValueError("tool_calls must be a list")

    specs_by_name = {spec.name: spec for spec in tool_specs}
    planned: list[ToolCall] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            continue
        name = str(raw_call.get("name") or "")
        spec = specs_by_name.get(name)
        if spec is None:
            continue
        raw_arguments = raw_call.get("arguments", {})
        arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        arguments = _complete_tool_arguments(
            spec,
            arguments,
            state=state,
        )
        planned.append(
            ToolCall(
                name=name,
                arguments=arguments,
                source="llm_structured",
            )
        )
    return _unique_tool_calls(planned)


def _complete_tool_arguments(
    spec: ToolSpec,
    arguments: dict[str, Any],
    *,
    state: CodingAgentState,
) -> dict[str, Any]:
    if spec.name == "repository_context_search":
        arguments.setdefault("query", state["user_input"])
        arguments.setdefault("repository_id", state["repository_id"])
        citations = state.get("rag_context", [])
        arguments.setdefault("citation_count", len(citations))
        arguments.setdefault(
            "candidate_files",
            _unique([citation.filename for citation in citations]),
        )
    required = spec.input_schema.get("required", [])
    if not isinstance(required, list):
        return arguments
    mentioned_paths = _extract_paths(state["user_input"])
    symbols = _extract_symbols(state["user_input"])
    for name in required:
        if name in arguments:
            continue
        arguments[name] = _argument_value_for_name(
            str(name),
            user_input=state["user_input"],
            mentioned_paths=mentioned_paths,
            symbols=symbols,
        )
    return arguments


def _unique_tool_calls(tool_calls: list[ToolCall]) -> list[ToolCall]:
    seen: set[str] = set()
    unique_calls: list[ToolCall] = []
    for tool_call in tool_calls:
        if tool_call.name in seen:
            continue
        seen.add(tool_call.name)
        unique_calls.append(tool_call)
    return unique_calls


def _plan_rule_based_tool_calls(
    state: CodingAgentState, tool_specs: list[ToolSpec] | None = None
) -> list[ToolCall]:
    intent = state.get("intent", "repository_question")
    user_input = state["user_input"]
    repository_id = state["repository_id"]
    focus_files = state.get("focus_files", [])
    citations = state.get("rag_context", [])
    cited_files = _unique([citation.filename for citation in citations])
    mentioned_paths = _extract_paths(user_input)
    symbols = _extract_symbols(user_input)

    calls = [
        ToolCall(
            name="repository_context_search",
            arguments={
                "query": user_input,
                "repository_id": repository_id,
                "citation_count": len(citations),
                "candidate_files": cited_files,
            },
        )
    ]
    if intent in {
        "repository_question",
        "repo_navigation",
        "code_explanation",
        "bug_investigation",
        "test_strategy",
        "change_planning",
    }:
        calls.append(
            ToolCall(
                name="repo.search_code",
                arguments={
                    "query": _build_repo_tool_search_query(
                        user_input, symbols, mentioned_paths, cited_files
                    ),
                    "max_results": 8,
                    "context_lines": 0,
                },
            )
        )
    files_to_read = _unique(focus_files + mentioned_paths + cited_files)[:3]
    for file_path in files_to_read:
        calls.append(
            ToolCall(
                name="repo.read_file",
                arguments={
                    "path": file_path,
                    "max_chars": 6000,
                },
            )
        )
    if intent in {"repo_navigation", "code_explanation", "bug_investigation"}:
        calls.append(
            ToolCall(
                name="file_symbol_locator",
                arguments={
                    "query": user_input,
                    "focus_files": _unique(focus_files + mentioned_paths + cited_files),
                    "symbols": symbols,
                },
            )
        )
    if intent in {"repository_question", "code_explanation", "repo_navigation"}:
        calls.append(
            ToolCall(
                name="code_explainer",
                arguments={
                    "query": user_input,
                    "files": _unique(focus_files + cited_files),
                    "context_snippets": [_snippet(citation.text) for citation in citations],
                },
            )
        )
    if intent == "change_planning":
        calls.append(
            ToolCall(
                name="change_planner",
                arguments={
                    "goal": user_input,
                    "candidate_files": _unique(focus_files + mentioned_paths + cited_files),
                },
            )
        )
    if intent == "bug_investigation":
        calls.append(
            ToolCall(
                name="bug_investigator",
                arguments={
                    "symptom": user_input,
                    "candidate_files": _unique(focus_files + mentioned_paths + cited_files),
                },
            )
        )
    if intent in {"test_strategy", "change_planning", "bug_investigation"}:
        calls.append(
            ToolCall(
                name="test_designer",
                arguments={
                    "goal": user_input,
                    "candidate_files": _unique(focus_files + cited_files),
                },
            )
        )
    calls.extend(
        _plan_dynamic_mcp_tool_calls(
            user_input=user_input,
            mentioned_paths=mentioned_paths,
            symbols=symbols,
            tool_specs=tool_specs or [],
            already_planned={call.name for call in calls},
        )
    )
    return calls


def _approval_required_tools(
    tool_calls: list[ToolCall], tool_specs: list[ToolSpec]
) -> list[dict[str, Any]]:
    specs_by_name = {spec.name: spec for spec in tool_specs}
    calls_by_name = {tool_call.name: tool_call for tool_call in tool_calls}
    approval_tools: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        spec = specs_by_name.get(tool_call.name)
        if spec is None:
            continue
        if spec.requires_approval or spec.permission_level != "read_only":
            approval_tools.append(
                {
                    "name": tool_call.name,
                    "provider": spec.provider,
                    "permission_level": spec.permission_level,
                    "requires_approval": spec.requires_approval,
                    "risk_summary": spec.risk_summary,
                    "arguments_summary": summarize_tool_arguments(
                        calls_by_name[tool_call.name].arguments
                    ),
                }
            )
    return approval_tools


def _plan_dynamic_mcp_tool_calls(
    *,
    user_input: str,
    mentioned_paths: list[str],
    symbols: list[str],
    tool_specs: list[ToolSpec],
    already_planned: set[str],
) -> list[ToolCall]:
    scored_specs: list[tuple[int, ToolSpec]] = []
    for spec in tool_specs:
        if not spec.provider.startswith("mcp:") or spec.name in already_planned:
            continue
        score = _score_tool_for_request(spec, user_input)
        if score > 0:
            scored_specs.append((score, spec))

    planned: list[ToolCall] = []
    for _, spec in sorted(scored_specs, key=lambda item: item[0], reverse=True)[:3]:
        arguments = _arguments_for_tool_spec(
            spec,
            user_input=user_input,
            mentioned_paths=mentioned_paths,
            symbols=symbols,
        )
        planned.append(
            ToolCall(
                name=spec.name,
                arguments=arguments,
                source="dynamic_tool_spec",
            )
        )
    return planned


def _score_tool_for_request(spec: ToolSpec, user_input: str) -> int:
    normalized_input = user_input.lower()
    tool_name = spec.name.split(".")[-1]
    searchable_parts = [
        tool_name,
        spec.description,
        " ".join(_schema_property_names(spec.input_schema)),
    ]
    searchable_text = " ".join(searchable_parts).lower().replace("_", " ")
    score = 0
    for token in _tool_match_tokens(searchable_text):
        if token in normalized_input:
            score += 3 if token in tool_name.lower().replace("_", " ") else 1
    if tool_name.lower() in normalized_input:
        score += 6
    return score


def _arguments_for_tool_spec(
    spec: ToolSpec,
    *,
    user_input: str,
    mentioned_paths: list[str],
    symbols: list[str],
) -> dict[str, Any]:
    properties = _schema_properties(spec.input_schema)
    required = spec.input_schema.get("required", [])
    if not isinstance(required, list):
        required = []

    arguments: dict[str, Any] = {}
    for name in properties:
        if name not in required:
            continue
        arguments[name] = _argument_value_for_name(
            name,
            user_input=user_input,
            mentioned_paths=mentioned_paths,
            symbols=symbols,
        )
    return arguments


def _argument_value_for_name(
    name: str,
    *,
    user_input: str,
    mentioned_paths: list[str],
    symbols: list[str],
) -> Any:
    normalized = name.lower()
    if normalized in {"query", "question", "prompt", "input", "message", "text"}:
        return user_input
    if normalized in {"title", "summary", "name"}:
        return _snippet(user_input, limit=80)
    if normalized in {"path", "file", "filename"}:
        return mentioned_paths[0] if mentioned_paths else ""
    if normalized in {"symbol", "function", "class_name"}:
        return symbols[0] if symbols else ""
    return user_input


def _schema_properties(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties", {})
    return properties if isinstance(properties, dict) else {}


def _schema_property_names(schema: dict[str, Any]) -> list[str]:
    return list(_schema_properties(schema).keys())


def _tool_match_tokens(text: str) -> list[str]:
    ignored = {
        "tool",
        "tools",
        "the",
        "and",
        "for",
        "with",
        "object",
        "string",
        "payload",
    }
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", text)
    return _unique([token.lower() for token in tokens if token.lower() not in ignored])


def _build_repo_tool_search_query(
    user_input: str,
    symbols: list[str],
    mentioned_paths: list[str],
    cited_files: list[str],
) -> str:
    focused_terms = _unique(symbols + mentioned_paths + cited_files)
    if focused_terms:
        return " ".join(focused_terms)
    return user_input


def _repository_context_search_tool(
    *, query: str, repository_id: str, citation_count: int, candidate_files: list[str]
) -> dict[str, Any]:
    return {
        "query": query,
        "repository_id": repository_id,
        "citation_count": citation_count,
        "candidate_files": candidate_files,
        "next_step": "ground the answer in retrieved file chunks and ask for indexing if citations are empty",
    }


def _file_symbol_locator_tool(
    *, query: str, focus_files: list[str], symbols: list[str]
) -> dict[str, Any]:
    return {
        "query": query,
        "focus_files": focus_files,
        "symbols": symbols,
        "suggested_commands": [
            "rg -n '<symbol-or-keyword>' .",
            "rg --files | rg '<path-fragment>'",
        ],
    }


def _code_explainer_tool(
    *, query: str, files: list[str], context_snippets: list[str]
) -> dict[str, Any]:
    return {
        "query": query,
        "files": files,
        "summary_style": "explain responsibility, call flow, inputs, outputs, and extension points",
        "context_snippet_count": len(context_snippets),
    }


def _change_planner_tool(*, goal: str, candidate_files: list[str]) -> dict[str, Any]:
    return {
        "goal": goal,
        "candidate_files": candidate_files,
        "plan": [
            "confirm current API/data contracts around the candidate files",
            "make the smallest behavior change behind the existing boundary",
            "add focused tests for the changed agent route",
            "run unit tests and a compile check before handing off",
        ],
    }


def _bug_investigator_tool(*, symptom: str, candidate_files: list[str]) -> dict[str, Any]:
    return {
        "symptom": symptom,
        "candidate_files": candidate_files,
        "debug_order": [
            "reproduce the failing request or test",
            "trace from route to service/runtime to integration boundary",
            "check whether history, retrieved context, or tool results are missing",
            "patch the narrowest failing branch and add a regression test",
        ],
    }


def _test_designer_tool(*, goal: str, candidate_files: list[str]) -> dict[str, Any]:
    return {
        "goal": goal,
        "candidate_files": candidate_files,
        "recommended_tests": [
            "API contract test for /api/v1/agent/runs",
            "runtime unit test for intent routing and planned tools",
            "RAG-scoped test that proves repository_id isolates code indexes",
        ],
    }


def _format_answer(state: CodingAgentState) -> str:
    lines = [
        f"我是{CODING_AGENT_ROLE}。",
        f"目标：{CODING_AGENT_OBJECTIVE}",
        (
            f"我把本轮请求归类为 `{state.get('intent', 'repository_question')}`，"
            f"原因：{state.get('intent_reason', '')}。"
        ),
        f"仓库索引：`{state['repository_id']}`。",
    ]

    history_count = len(state.get("history", []))
    lines.append(f"已读取 {history_count} 条历史消息作为上下文。")

    review_decision = state.get("review_decision", {})
    if review_decision and not review_decision.get("approved"):
        feedback = review_decision.get("feedback") or "未提供补充说明"
        lines.append("人工审批结果：未批准执行本轮需要权限确认的工具计划。")
        lines.append(f"审批反馈：{feedback}")
        lines.append("我已停止执行后续工具；可以根据反馈调整目标后重新发起 run。")
        return "\n".join(lines)

    citations = state.get("rag_context", [])
    if citations:
        lines.append("我检索到的代码上下文：")
        for index, citation in enumerate(citations, start=1):
            snippet = _snippet(citation.text, limit=140)
            line_range = _citation_line_range(citation)
            symbols = _citation_symbols(citation)
            lines.append(
                f"[{index}] {citation.filename} chunk={citation.chunk_index} "
                f"{line_range}{symbols}score={citation.score:.3f}: {snippet}"
            )
    else:
        lines.append(
            "我还没有检索到代码片段。可以先把 README、关键源码文件或目录索引写入这个 repository_id。"
        )

    tool_results = state.get("tool_results", [])
    if tool_results:
        lines.append("工具复盘：")
        for item in tool_results:
            if item.get("ok"):
                lines.append(f"- {item['name']}: {item['result']}")
            else:
                lines.append(f"- {item['name']}: failed, {item.get('error')}")

    if citations:
        lines.append(
            "回答建议：优先依据上面的文件片段定位实现；如果要改代码，下一步应沿 trace 中的候选文件做最小修改并补测试。"
        )
    else:
        lines.append(
            "下一步建议：先通过知识库 ingest 接口索引仓库文件，再用同一个 repository_id 继续提问。"
        )
    return "\n".join(lines)


def _format_error_answer(state: CodingAgentState) -> str:
    errors = _unresolved_errors(state)
    if not errors:
        errors = state.get("errors", [])

    lines = [
        f"我是{CODING_AGENT_ROLE}。",
        "本轮 Agent 运行进入错误分支，未继续执行后续正常节点。",
        f"仓库索引：`{state['repository_id']}`。",
    ]
    if errors:
        lines.append("结构化错误：")
        for index, error in enumerate(errors, start=1):
            retry_text = "可重试" if error.get("retryable") else "不可重试"
            recovered_text = "已恢复" if error.get("recovered") else "未恢复"
            lines.append(
                f"[{index}] node={error.get('node')} code={error.get('code')} "
                f"attempt={error.get('attempt')}/{error.get('max_attempts')} "
                f"{retry_text} {recovered_text}: {error.get('message')}"
            )
    else:
        lines.append("结构化错误为空，但 graph 已切换到错误回答分支。")

    lines.append(
        "下一步建议：先根据 `errors` 中的 node/code 定位失败边界；"
        "如果是 provider/network 类错误可以重试，如果是 configuration/validation 类错误应先修配置或输入。"
    )
    return "\n".join(lines)


def _append_trace(
    state: CodingAgentState,
    *,
    node: str,
    summary: str,
    output: dict[str, Any],
) -> list[dict[str, Any]]:
    trace = list(state.get("trace", []))
    trace.append(
        {
            "step": len(trace) + 1,
            "node": node,
            "summary": summary,
            "output": output,
        }
    )
    return trace


def _extract_paths(text: str) -> list[str]:
    path_pattern = r"[\w./-]+\.(?:py|ts|tsx|js|jsx|md|toml|yaml|yml|json|go|rs|java)"
    return _unique(re.findall(path_pattern, text))


def _extract_symbols(text: str) -> list[str]:
    candidates = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", text)
    ignored = {
        "the",
        "and",
        "for",
        "with",
        "class",
        "def",
        "api",
        "rag",
        "sse",
    }
    symbols = [
        item
        for item in candidates
        if item.lower() not in ignored and ("_" in item or item[:1].isupper())
    ]
    return _unique(symbols)


def _snippet(text: str, *, limit: int = 120) -> str:
    return text.strip().replace("\n", " ")[:limit]


def _citation_line_range(citation: RetrievedDocument) -> str:
    if citation.start_line is None or citation.end_line is None:
        return ""
    return f"lines={citation.start_line}-{citation.end_line} "


def _citation_symbols(citation: RetrievedDocument) -> str:
    if not citation.symbols:
        return ""
    return "symbols=" + ",".join(citation.symbols[:3]) + " "


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
