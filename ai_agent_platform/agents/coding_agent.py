from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Optional
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from ai_agent_platform.agents.coding.change_loop import (
    ChangeLoopExecutor,
    partition_tool_calls,
)
from ai_agent_platform.agents.coding.context import load_project_instructions
from ai_agent_platform.agents.coding.formatting import format_error_answer
from ai_agent_platform.agents.coding.models import (
    CODING_AGENT_OBJECTIVE,
    CODING_AGENT_ROLE,
    AgentPlanner,
    AgentRunInvalidStateError,
    AgentRunNotFoundError,
    AgentRunRecord,
    AgentRunResult,
    AgentRunStatus,
    AgentRunStore,
    CodingAgentState,
    ContextSource,
)
from ai_agent_platform.agents.coding.planner import (
    LLMStructuredAgentPlanner,
    RuleBasedAgentPlanner,
    approval_required_tools as collect_approval_required_tools,
    bounded_confidence,
)
from ai_agent_platform.agents.coding.runtime_support import (
    append_errors as _append_errors,
    append_trace as _append_trace,
    build_change_summary as _build_change_summary,
    build_run_metrics as _build_run_metrics,
    build_tool_plan_approval_request as _build_tool_plan_approval_request,
    checkpoint_id as _checkpoint_id,
    error_from_exception as _error_from_exception,
    latest_trace_node as _latest_trace_node,
    next_nodes as _next_nodes,
    pending_approval as _pending_approval,
    route_after_change_execution as _route_after_change_execution,
    route_after_inspection as _route_after_inspection,
    route_after_repair_review as _route_after_repair_review,
    route_after_tool_plan_review as _route_after_tool_plan_review,
    route_after_tool_planning as _route_after_tool_planning,
    route_after_validation as _route_after_validation,
    snapshot_errors as _snapshot_errors,
    snapshot_trace as _snapshot_trace,
    waiting_node as _waiting_node,
)
from ai_agent_platform.agents.coding.store import InMemoryAgentRunStore
from ai_agent_platform.agents.coding.text import extract_paths, unique
from ai_agent_platform.agents.coding.tools import create_coding_tool_registry
from ai_agent_platform.domain import Message
from ai_agent_platform.integrations.tools import ToolCall, ToolExecutionContext, ToolRegistry


CHANGE_INTENTS = {"change_planning", "bug_investigation"}
READ_ONLY_REPOSITORY_TOOLS = {
    "repo.find_files",
    "repo.list_files",
    "repo.read_file",
    "repo.search_code",
}


class CodingAgentRuntime:
    """Task-driven coding agent that reads live workspace files on demand."""

    def __init__(
        self,
        *,
        tool_registry: Optional[ToolRegistry] = None,
        run_store: Optional[AgentRunStore] = None,
        checkpointer: Any = None,
        planner: AgentPlanner | None = None,
        max_exploration_rounds: int = 4,
        max_read_tools_per_round: int = 6,
        max_context_files: int = 12,
        max_context_chars: int = 32000,
        max_instruction_chars: int = 16000,
        max_history_messages: int = 12,
    ) -> None:
        self._tools = tool_registry or create_coding_tool_registry()
        self._checkpointer = checkpointer or InMemorySaver()
        self._run_store = run_store or InMemoryAgentRunStore()
        self._planner = planner or RuleBasedAgentPlanner()
        self._max_exploration_rounds = max_exploration_rounds
        self._max_read_tools_per_round = max_read_tools_per_round
        self._max_context_files = max_context_files
        self._max_context_chars = max_context_chars
        self._max_instruction_chars = max_instruction_chars
        self._max_history_messages = max_history_messages
        self._change_loop = ChangeLoopExecutor(tools=self._tools, planner=self._planner)
        self._graph = self._build_graph()
        self.graph_engine = "langgraph"

    def run(
        self,
        *,
        conversation_id: str,
        user_input: str,
        history: list[Message | dict[str, str]],
        workspace_id: str,
        workspace_root: str,
        focus_files: Optional[list[str]] = None,
        run_id: Optional[str] = None,
    ) -> AgentRunResult:
        run_id = run_id or f"run_{uuid4().hex[:12]}"
        thread_id = run_id
        config = {"configurable": {"thread_id": thread_id}}
        self._save_record(
            run_id=run_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            status="running",
            next_nodes=["setup_workspace"],
        )
        initial_state: CodingAgentState = {
            "conversation_id": conversation_id,
            "run_id": run_id,
            "user_input": user_input,
            "workspace_id": workspace_id,
            "workspace_root": workspace_root,
            "focus_files": focus_files or [],
            "history": [
                {
                    "role": message["role"] if isinstance(message, dict) else message.role,
                    "content": (
                        message["content"]
                        if isinstance(message, dict)
                        else message.content
                    ),
                }
                for message in history[-self._max_history_messages :]
            ],
            "trace": [],
            "errors": [],
            "artifacts": [],
            "tool_calls": [],
            "tool_results": [],
            "context_sources": [],
            "project_instructions": [],
            "context_chars": 0,
            "context_files": [],
            "seen_context_keys": [],
            "exploration_round": 0,
            "change_iteration": 0,
            "changed_files": [],
            "validation_history": [],
            "started_at": perf_counter(),
        }
        try:
            state = self._graph.invoke(initial_state, config)
        except Exception as exc:
            snapshot = self._snapshot_for(config)
            self._run_store.save(
                AgentRunRecord(
                    run_id=run_id,
                    thread_id=thread_id,
                    conversation_id=conversation_id,
                    workspace_id=workspace_id,
                    workspace_root=workspace_root,
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
        return self._finish_invocation(
            config=config,
            state=state,
            run_id=run_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
        )

    def create_queued_run(
        self,
        *,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
    ) -> AgentRunRecord:
        run_id = f"run_{uuid4().hex[:12]}"
        return self._save_record(
            run_id=run_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            status="queued",
            next_nodes=["setup_workspace"],
        )

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
            workspace_id=record.workspace_id,
            workspace_root=record.workspace_root,
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
        try:
            state = self._graph.invoke(
                Command(resume={"approved": approved, "feedback": feedback or ""}),
                config,
            )
        except Exception as exc:
            snapshot = self._snapshot_for(config)
            self._run_store.save(
                AgentRunRecord(
                    run_id=record.run_id,
                    thread_id=record.thread_id,
                    conversation_id=record.conversation_id,
                    workspace_id=record.workspace_id,
                    workspace_root=record.workspace_root,
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
        return self._finish_invocation(
            config=config,
            state=state,
            run_id=record.run_id,
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            workspace_id=record.workspace_id,
            workspace_root=record.workspace_root,
        )

    def get_run(self, run_id: str) -> AgentRunRecord:
        try:
            return self._run_store.get(run_id)
        except KeyError as exc:
            raise AgentRunNotFoundError(run_id) from exc

    def _finish_invocation(
        self,
        *,
        config: dict[str, Any],
        state: CodingAgentState,
        run_id: str,
        thread_id: str,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
    ) -> AgentRunResult:
        snapshot = self._snapshot_for(config)
        pending = _pending_approval(snapshot, state)
        status: AgentRunStatus = "waiting_approval" if pending is not None else "completed"
        result = self._build_result(
            run_id=run_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            status=status,
            checkpoint_id=_checkpoint_id(snapshot),
            state=state,
            pending_approval=pending,
        )
        self._run_store.save(
            AgentRunRecord(
                run_id=run_id,
                thread_id=thread_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                workspace_root=workspace_root,
                status=status,
                checkpoint_id=result.checkpoint_id,
                latest_node=(
                    _waiting_node(snapshot)
                    if status == "waiting_approval"
                    else _latest_trace_node(snapshot)
                ),
                next_nodes=_next_nodes(snapshot),
                trace=result.trace,
                result=result,
                pending_approval=pending,
            )
        )
        return result

    def _save_record(
        self,
        *,
        run_id: str,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
        status: AgentRunStatus,
        next_nodes: list[str],
    ) -> AgentRunRecord:
        record = AgentRunRecord(
            run_id=run_id,
            thread_id=run_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            status=status,
            checkpoint_id=None,
            latest_node=None,
            next_nodes=next_nodes,
            trace=[],
        )
        self._run_store.save(record)
        return record

    def _build_result(
        self,
        *,
        run_id: str,
        thread_id: str,
        conversation_id: str,
        workspace_id: str,
        status: AgentRunStatus,
        checkpoint_id: Optional[str],
        state: CodingAgentState,
        pending_approval: Optional[dict[str, Any]] = None,
    ) -> AgentRunResult:
        return AgentRunResult(
            run_id=run_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            status=status,
            checkpoint_id=checkpoint_id,
            role=CODING_AGENT_ROLE,
            objective=CODING_AGENT_OBJECTIVE,
            intent=state.get("intent", "repository_question"),
            answer=state.get("answer", "") if status == "completed" else "",
            graph_engine=self.graph_engine,
            context_sources=(
                list(state.get("project_instructions", []))
                + list(state.get("context_sources", []))
            ),
            tool_calls=state.get("tool_calls", []),
            tool_results=state.get("tool_results", []),
            trace=state.get("trace", []),
            errors=state.get("errors", []),
            metrics=_build_run_metrics(state),
            change_summary=_build_change_summary(state),
            artifacts=state.get("artifacts", []),
            pending_approval=pending_approval,
        )

    def _snapshot_for(self, config: dict[str, Any]):
        try:
            return self._graph.get_state(config)
        except Exception:
            return None

    def _build_graph(self):
        workflow = StateGraph(CodingAgentState)
        workflow.add_node("setup_workspace", self._setup_workspace)
        workflow.add_node("load_project_instructions", self._load_project_instructions)
        workflow.add_node("classify_request", self._classify_request)
        workflow.add_node("plan_exploration", self._plan_exploration)
        workflow.add_node("execute_exploration", self._execute_exploration)
        workflow.add_node("assess_context", self._assess_context)
        workflow.add_node("plan_tools", self._plan_tools)
        workflow.add_node("review_tool_plan", self._review_tool_plan)
        workflow.add_node("inspect_repository", self._inspect_repository)
        workflow.add_node("execute_changes", self._change_loop.execute_changes)
        workflow.add_node("validate_changes", self._change_loop.validate_changes)
        workflow.add_node("review_repair_plan", self._change_loop.review_repair_plan)
        workflow.add_node("collect_artifacts", self._change_loop.collect_artifacts)
        workflow.add_node("compose_answer", self._compose_answer)
        workflow.add_node("compose_error_answer", self._compose_error_answer)
        workflow.set_entry_point("setup_workspace")
        workflow.add_edge("setup_workspace", "load_project_instructions")
        workflow.add_edge("load_project_instructions", "classify_request")
        workflow.add_conditional_edges(
            "classify_request",
            lambda state: (
                "compose_answer"
                if state.get("intent") == "small_talk"
                else "plan_exploration"
            ),
            {
                "compose_answer": "compose_answer",
                "plan_exploration": "plan_exploration",
            },
        )
        workflow.add_edge("plan_exploration", "execute_exploration")
        workflow.add_edge("execute_exploration", "assess_context")
        workflow.add_conditional_edges(
            "assess_context",
            self._route_after_context,
            {
                "plan_exploration": "plan_exploration",
                "plan_tools": "plan_tools",
                "compose_answer": "compose_answer",
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
        workflow.add_edge("compose_answer", END)
        workflow.add_edge("compose_error_answer", END)
        return workflow.compile(checkpointer=self._checkpointer)

    def _setup_workspace(self, state: CodingAgentState) -> CodingAgentState:
        root = Path(state["workspace_root"]).resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(
                "workspace_unavailable: captured workspace root is inaccessible"
            )
        for path in state.get("focus_files", []):
            _validate_relative_workspace_path(path, root)
        return {
            "workspace_root": str(root),
            "trace": _append_trace(
                state,
                node="setup_workspace",
                summary="固定本次 run 的工作区根路径快照并校验边界。",
                output={
                    "workspace_id": state["workspace_id"],
                    "focus_files": state.get("focus_files", []),
                },
            ),
        }

    def _load_project_instructions(
        self, state: CodingAgentState
    ) -> CodingAgentState:
        instructions = load_project_instructions(
            workspace_root=state["workspace_root"],
            focus_files=unique(
                state.get("focus_files", []) + extract_paths(state["user_input"])
            ),
            max_chars=self._max_instruction_chars,
        )
        return {
            "project_instructions": instructions,
            "trace": _append_trace(
                state,
                node="load_project_instructions",
                summary="按作用域加载 AGENTS 指令链，近层规则覆盖上层规则。",
                output={
                    "files": [item.path for item in instructions],
                    "chars": sum(len(item.text) for item in instructions),
                    "limit": self._max_instruction_chars,
                },
            ),
        }

    def _classify_request(self, state: CodingAgentState) -> CodingAgentState:
        decision = self._planner.classify_intent(state["user_input"])
        intent = str(decision.get("intent") or "repository_question")
        return {
            "intent": intent,
            "intent_reason": str(decision.get("reason") or ""),
            "intent_confidence": bounded_confidence(decision.get("confidence")),
            "planner_source": str(decision.get("source") or "unknown"),
            "trace": _append_trace(
                state,
                node="classify_request",
                summary="分类问答、定位、解释、排错、修改或测试任务。",
                output={"intent": intent, "source": decision.get("source")},
            ),
        }

    def _plan_exploration(self, state: CodingAgentState) -> CodingAgentState:
        round_number = state.get("exploration_round", 0) + 1
        tool_specs = [
            spec
            for spec in self._tools.list_specs()
            if spec.name in READ_ONLY_REPOSITORY_TOOLS
        ]
        proposed = self._planner.plan_tool_calls(state, tool_specs)
        proposed = [
            call for call in proposed if call.name in READ_ONLY_REPOSITORY_TOOLS
        ]
        deterministic = self._fallback_exploration_calls(state)
        calls = _unique_calls(proposed + deterministic)
        seen = set(state.get("seen_context_keys", []))
        context_files = set(state.get("context_files", []))
        filtered: list[ToolCall] = []
        for call in calls:
            key = _tool_call_key(call)
            if key in seen:
                continue
            if call.name == "repo.read_file":
                path = str(call.arguments.get("path") or "")
                if (
                    path not in context_files
                    and len(context_files) >= self._max_context_files
                ):
                    continue
                call = ToolCall(
                    name=call.name,
                    arguments={
                        **call.arguments,
                        "max_chars": min(
                            int(call.arguments.get("max_chars", 8000)),
                            max(1, self._max_context_chars - state.get("context_chars", 0)),
                        ),
                    },
                    source=call.source,
                )
            filtered.append(call)
            seen.add(key)
            if len(filtered) >= self._max_read_tools_per_round:
                break
        return {
            "exploration_round": round_number,
            "analysis_tool_calls": filtered,
            "seen_context_keys": list(seen),
            "tool_calls": list(state.get("tool_calls", [])) + filtered,
            "trace": _append_trace(
                state,
                node="plan_exploration",
                summary="规划本轮只读搜索与原始文件读取。",
                output={
                    "round": round_number,
                    "planned_tools": [call.name for call in filtered],
                    "limit": self._max_read_tools_per_round,
                },
            ),
        }

    def _fallback_exploration_calls(
        self, state: CodingAgentState
    ) -> list[ToolCall]:
        read_files = set(state.get("context_files", []))
        candidates = unique(
            state.get("focus_files", [])
            + extract_paths(state["user_input"])
            + _candidate_paths(state.get("exploration_results", []))
        )
        calls = [
            ToolCall(
                name="repo.read_file",
                arguments={"path": path, "max_chars": 8000},
                source="rules",
            )
            for path in candidates
            if path not in read_files
        ]
        if calls:
            return calls
        return [
            ToolCall(
                name="repo.search_code",
                arguments={
                    "query": state["user_input"],
                    "max_results": 12,
                    "context_lines": 1,
                },
                source="rules",
            )
        ]

    def _execute_exploration(self, state: CodingAgentState) -> CodingAgentState:
        context = ToolExecutionContext(
            conversation_id=state["conversation_id"],
            workspace_id=state["workspace_id"],
            workspace_root=state["workspace_root"],
            run_id=state.get("run_id"),
        )
        calls = state.get("analysis_tool_calls", [])
        results = [
            self._tools.execute(call, context=context).to_response()
            for call in calls
        ]
        return {
            "exploration_results": results,
            "tool_results": list(state.get("tool_results", [])) + results,
            "trace": _append_trace(
                state,
                node="execute_exploration",
                summary="在工作区快照边界内执行实时搜索和原始文件读取。",
                output={
                    "round": state.get("exploration_round", 0),
                    "success_count": sum(1 for result in results if result["ok"]),
                    "called_tools": [result["name"] for result in results],
                },
            ),
        }

    def _assess_context(self, state: CodingAgentState) -> CodingAgentState:
        sources = list(state.get("context_sources", []))
        seen = {
            f"{source.path}:{source.start_line}:{source.end_line}:{source.content_hash}"
            for source in sources
        }
        content_hashes = {source.content_hash for source in sources}
        context_files = set(state.get("context_files", []))
        chars = state.get("context_chars", 0)
        for result in state.get("exploration_results", []):
            if not result.get("ok"):
                continue
            output = result.get("result")
            if not isinstance(output, dict):
                continue
            additions = _context_sources_from_result(
                result["name"],
                output,
                focus_files=set(state.get("focus_files", [])),
            )
            for source in additions:
                key = (
                    f"{source.path}:{source.start_line}:"
                    f"{source.end_line}:{source.content_hash}"
                )
                if key in seen or source.content_hash in content_hashes:
                    continue
                if source.kind == "file" and source.path not in context_files:
                    if len(context_files) >= self._max_context_files:
                        continue
                    context_files.add(source.path)
                remaining = self._max_context_chars - chars
                if remaining <= 0:
                    break
                if len(source.text) > remaining:
                    source = ContextSource(
                        **{
                            **source.__dict__,
                            "text": source.text[:remaining],
                            "truncated": True,
                            "content_hash": hashlib.sha256(
                                source.text[:remaining].encode("utf-8")
                            ).hexdigest(),
                        }
                    )
                sources.append(source)
                seen.add(key)
                content_hashes.add(source.content_hash)
                chars += len(source.text)
        round_number = state.get("exploration_round", 0)
        budget_exhausted = (
            round_number >= self._max_exploration_rounds
            or len(context_files) >= self._max_context_files
            or chars >= self._max_context_chars
        )
        unread = [
            path
            for path in _candidate_paths(state.get("exploration_results", []))
            if path not in context_files
        ]
        no_new_plan = not state.get("analysis_tool_calls", [])
        sufficient = budget_exhausted or no_new_plan or (
            bool(context_files) and not unread
        )
        sources.sort(
            key=lambda source: (
                0
                if source.reason == "user-selected file"
                else 1
                if source.kind == "search_match"
                else 2,
                source.path,
                source.start_line or 0,
            )
        )
        return {
            "context_sources": sources,
            "context_files": sorted(context_files),
            "context_chars": chars,
            "context_budget_exhausted": budget_exhausted,
            "context_sufficient": sufficient,
            "trace": _append_trace(
                state,
                node="assess_context",
                summary="去重并裁剪证据，判断是否继续探索。",
                output={
                    "round": round_number,
                    "source_count": len(sources),
                    "file_count": len(context_files),
                    "chars": chars,
                    "unread_candidates": len(unread),
                    "sufficient": sufficient,
                    "budget_exhausted": budget_exhausted,
                },
            ),
        }

    def _route_after_context(self, state: CodingAgentState) -> str:
        if not state.get("context_sufficient"):
            return "plan_exploration"
        return "plan_tools"

    def _plan_tools(self, state: CodingAgentState) -> CodingAgentState:
        tool_specs = self._tools.list_specs()
        tool_calls = [
            call
            for call in self._planner.plan_tool_calls(state, tool_specs)
            if call.name not in READ_ONLY_REPOSITORY_TOOLS
        ]
        analysis_calls, change_calls, validation_calls = partition_tool_calls(tool_calls)
        approval_tools = collect_approval_required_tools(tool_calls, tool_specs)
        return {
            "tool_calls": list(state.get("tool_calls", [])) + tool_calls,
            "analysis_tool_calls": analysis_calls,
            "change_tool_calls": change_calls,
            "validation_tool_calls": validation_calls,
            "repair_tool_calls": [],
            "approval_required_tools": approval_tools,
            "trace": _append_trace(
                state,
                node="plan_tools",
                summary="基于已读证据规划变更、验证与审批。",
                output={
                    "planned_tools": [call.name for call in tool_calls],
                    "approval_required_tools": [
                        item["name"] for item in approval_tools
                    ],
                },
            ),
        }

    def _review_tool_plan(self, state: CodingAgentState) -> CodingAgentState:
        decision = interrupt(_build_tool_plan_approval_request(state))
        approved = (
            bool(decision.get("approved"))
            if isinstance(decision, dict)
            else bool(decision)
        )
        feedback = (
            str(decision.get("feedback") or "")
            if isinstance(decision, dict)
            else ""
        )
        review = {"approved": approved, "feedback": feedback}
        return {
            "review_decision": review,
            "trace": _append_trace(
                state,
                node="review_tool_plan",
                summary="人工审批需要权限的变更计划。",
                output=review,
            ),
        }

    def _inspect_repository(self, state: CodingAgentState) -> CodingAgentState:
        results = self._change_loop.execute_tool_calls(
            state, state.get("analysis_tool_calls", [])
        )
        return {
            "tool_results": list(state.get("tool_results", [])) + results,
            "trace": _append_trace(
                state,
                node="inspect_repository",
                summary="执行无需写权限的变更分析工具。",
                output={"called_tools": [result["name"] for result in results]},
            ),
        }

    def _compose_answer(self, state: CodingAgentState) -> CodingAgentState:
        try:
            compose = getattr(self._planner, "compose_answer", None)
            answer = (
                compose(state)
                if callable(compose)
                else RuleBasedAgentPlanner().compose_answer(state)
            )
            errors: list[dict[str, Any]] = []
        except Exception as exc:
            answer = ""
            errors = [_error_from_exception("compose_answer", exc, attempt=1, max_attempts=1)]
        return {
            "answer": answer,
            "errors": _append_errors(state, errors),
            "trace": _append_trace(
                state,
                node="compose_answer",
                summary="根据会话、项目指令、源码证据、测试和 Diff 生成回答。",
                output={"answer_chars": len(answer), "source_count": len(state.get("context_sources", []))},
            ),
        }

    def _compose_error_answer(self, state: CodingAgentState) -> CodingAgentState:
        return {
            "answer": format_error_answer(state),
            "trace": _append_trace(
                state,
                node="compose_error_answer",
                summary="生成结构化错误回答。",
                output={},
            ),
        }


def _validate_relative_workspace_path(path: str, root: Path) -> None:
    candidate = Path(path)
    if candidate.is_absolute():
        raise ValueError("focus_files must contain workspace-relative paths")
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"focus file escapes workspace root: {path}")


def _tool_call_key(call: ToolCall) -> str:
    return f"tool:{call.name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)}"


def _unique_calls(calls: list[ToolCall]) -> list[ToolCall]:
    seen: set[str] = set()
    result: list[ToolCall] = []
    for call in calls:
        key = _tool_call_key(call)
        if key in seen:
            continue
        seen.add(key)
        result.append(call)
    return result


def _candidate_paths(results: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for result in results:
        output = result.get("result")
        if not result.get("ok") or not isinstance(output, dict):
            continue
        if result.get("name") == "repo.find_files":
            paths.extend(str(item) for item in output.get("matches", []))
        if result.get("name") == "repo.search_code":
            paths.extend(
                str(item.get("path"))
                for item in output.get("matches", [])
                if isinstance(item, dict) and item.get("path")
            )
    return unique(paths)


def _context_sources_from_result(
    name: str,
    output: dict[str, Any],
    *,
    focus_files: set[str],
) -> list[ContextSource]:
    if name == "repo.read_file":
        text = str(output.get("content") or "")
        if not text:
            return []
        path = str(output.get("path") or "")
        return [
            ContextSource(
                kind="file",
                path=path,
                start_line=int(output.get("start_line") or 1),
                end_line=int(output.get("end_line") or 1),
                text=text,
                reason=(
                    "user-selected file"
                    if path in focus_files
                    else "read after search match"
                ),
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                truncated=bool(output.get("truncated")),
            )
        ]
    if name != "repo.search_code":
        return []
    sources: list[ContextSource] = []
    for match in output.get("matches", []):
        if not isinstance(match, dict):
            continue
        text = str(match.get("text") or "")
        if not text:
            continue
        line = int(match.get("line") or 1)
        sources.append(
            ContextSource(
                kind="search_match",
                path=str(match.get("path") or ""),
                start_line=line,
                end_line=line,
                text=text,
                reason="exact symbol or keyword match",
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                truncated=False,
            )
        )
    return sources


__all__ = [
    "CodingAgentRuntime",
    "LLMStructuredAgentPlanner",
    "RuleBasedAgentPlanner",
    "create_coding_tool_registry",
]
