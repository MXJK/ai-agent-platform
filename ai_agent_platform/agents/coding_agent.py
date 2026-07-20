from __future__ import annotations

from time import perf_counter
from typing import Any, Optional
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from ai_agent_platform.agents.coding.formatting import (
    format_answer,
    format_error_answer,
)
from ai_agent_platform.agents.coding.change_loop import (
    ChangeLoopExecutor,
    partition_tool_calls,
)
from ai_agent_platform.agents.coding.models import (
    CODING_AGENT_OBJECTIVE,
    CODING_AGENT_ROLE,
    AgentChangeSummary,
    AgentPlanner,
    AgentRunInvalidStateError,
    AgentRunMetrics,
    AgentRunNotFoundError,
    AgentRunRecord,
    AgentRunResult,
    AgentRunStatus,
    AgentRunStore,
    CodingAgentState,
)
from ai_agent_platform.agents.coding.planner import (
    LLMStructuredAgentPlanner,
    RuleBasedAgentPlanner,
    approval_required_tools as collect_approval_required_tools,
    bounded_confidence,
)
from ai_agent_platform.agents.coding.store import InMemoryAgentRunStore
from ai_agent_platform.agents.coding.runtime_support import (
    append_errors as _append_errors,
    append_trace as _append_trace,
    build_repository_query as _build_repository_query,
    build_change_summary as _build_change_summary,
    build_run_metrics as _build_run_metrics,
    build_tool_plan_approval_request as _build_tool_plan_approval_request,
    checkpoint_id as _checkpoint_id,
    classify_answer_error as _classify_answer_error,
    classify_rag_error as _classify_rag_error,
    error_from_exception as _error_from_exception,
    latest_trace_node as _latest_trace_node,
    next_node_for_intent as _next_node_for_intent,
    next_nodes as _next_nodes,
    pending_approval as _pending_approval,
    route_after_answer_composition as _route_after_answer_composition,
    route_after_classification as _route_after_classification,
    route_after_change_execution as _route_after_change_execution,
    route_after_inspection as _route_after_inspection,
    route_after_repair_review as _route_after_repair_review,
    route_after_retrieval as _route_after_retrieval,
    route_after_tool_plan_review as _route_after_tool_plan_review,
    route_after_tool_planning as _route_after_tool_planning,
    route_after_validation as _route_after_validation,
    run_with_retries as _run_with_retries,
    snapshot_errors as _snapshot_errors,
    snapshot_trace as _snapshot_trace,
    unresolved_errors as _unresolved_errors,
    waiting_node as _waiting_node,
)
from ai_agent_platform.agents.coding.text import unique
from ai_agent_platform.agents.coding.tools import create_coding_tool_registry
from ai_agent_platform.domain import Message
from ai_agent_platform.integrations import RAGService
from ai_agent_platform.integrations.tools import ToolRegistry


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
        self._change_loop = ChangeLoopExecutor(
            tools=self._tools,
            planner=self._planner,
        )
        self._graph = self._build_graph()
        self.graph_engine = "langgraph"

    def run(
        self,
        *,
        conversation_id: str,
        user_input: str,
        history: list[Message | dict[str, str]],
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
                        {
                            "role": (
                                message["role"]
                                if isinstance(message, dict)
                                else message.role
                            ),
                            "content": (
                                message["content"]
                                if isinstance(message, dict)
                                else message.content
                            ),
                        }
                        for message in history
                    ],
                    "trace": [],
                    "errors": [],
                    "artifacts": [],
                    "change_iteration": 0,
                    "changed_files": [],
                    "validation_history": [],
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
                    latest_node=_waiting_node(snapshot),
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

    def mark_queued_run_failed(self, *, run_id: str, error: str) -> AgentRunRecord:
        return self.mark_run_failed(
            run_id=run_id,
            error=error,
            node="task_queue",
            attempt=1,
            max_attempts=1,
        )

    def mark_run_failed(
        self,
        *,
        run_id: str,
        error: str,
        node: str = "task_execution",
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> AgentRunRecord:
        record = self.get_run(run_id)
        if record.status in {"completed", "failed"}:
            return record
        failed_record = AgentRunRecord(
            run_id=record.run_id,
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            repository_id=record.repository_id,
            status="failed",
            checkpoint_id=record.checkpoint_id,
            latest_node=record.latest_node,
            next_nodes=[],
            trace=record.trace,
            error=error,
            errors=record.errors
            + [
                _error_from_exception(
                    node,
                    RuntimeError(error),
                    attempt=attempt,
                    max_attempts=max_attempts,
                )
            ],
        )
        self._run_store.save(failed_record)
        return failed_record

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
                    latest_node=_waiting_node(snapshot),
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
            metrics=_build_run_metrics(state),
            change_summary=_build_change_summary(state),
            artifacts=state.get("artifacts", []),
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
        workflow.add_node("execute_changes", self._change_loop.execute_changes)
        workflow.add_node("validate_changes", self._change_loop.validate_changes)
        workflow.add_node(
            "review_repair_plan",
            self._change_loop.review_repair_plan,
        )
        workflow.add_node("collect_artifacts", self._change_loop.collect_artifacts)
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
        workflow.add_conditional_edges(
            "inspect_repository",
            _route_after_inspection,
            {
                "execute_changes": "execute_changes",
                "validate_changes": "validate_changes",
                "collect_artifacts": "collect_artifacts",
                "compose_answer": "compose_answer",
            },
        )
        workflow.add_conditional_edges(
            "execute_changes",
            _route_after_change_execution,
            {
                "validate_changes": "validate_changes",
                "collect_artifacts": "collect_artifacts",
            },
        )
        workflow.add_conditional_edges(
            "validate_changes",
            _route_after_validation,
            {
                "review_repair_plan": "review_repair_plan",
                "collect_artifacts": "collect_artifacts",
            },
        )
        workflow.add_conditional_edges(
            "review_repair_plan",
            _route_after_repair_review,
            {
                "execute_changes": "execute_changes",
                "collect_artifacts": "collect_artifacts",
            },
        )
        workflow.add_edge("collect_artifacts", "compose_answer")
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
        confidence = bounded_confidence(decision.get("confidence"))
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
        analysis_calls, change_calls, validation_calls = partition_tool_calls(
            tool_calls
        )
        approval_required_tools = collect_approval_required_tools(
            tool_calls, tool_specs
        )
        return {
            "tool_calls": tool_calls,
            "analysis_tool_calls": analysis_calls,
            "change_tool_calls": change_calls,
            "validation_tool_calls": validation_calls,
            "repair_tool_calls": [],
            "approval_required_tools": approval_required_tools,
            "trace": _append_trace(
                state,
                node="plan_tools",
                summary="根据意图和检索结果规划研发助手工具调用。",
                output={
                    "available_tool_count": len(tool_specs),
                    "planned_tools": [tool_call.name for tool_call in tool_calls],
                    "change_tools": [call.name for call in change_calls],
                    "validation_tools": [call.name for call in validation_calls],
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
        tool_results = self._change_loop.execute_tool_calls(
            state,
            state.get("analysis_tool_calls", state.get("tool_calls", [])),
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
            operation=lambda: format_answer(state),
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
                    "nodes": unique([error["node"] for error in unresolved_errors]),
                },
            ),
        }

    def _compose_error_answer(self, state: CodingAgentState) -> CodingAgentState:
        answer = format_error_answer(state)
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
