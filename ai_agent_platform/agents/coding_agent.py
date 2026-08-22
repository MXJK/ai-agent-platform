from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any, Optional
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

from ai_agent_platform.agents.coding.change_loop import ChangeLoopExecutor
from ai_agent_platform.agents.coding.checkpoint_coordinator import (
    CheckpointResumeCoordinator,
)
from ai_agent_platform.agents.coding.context_nodes import ContextRetrievalNodes
from ai_agent_platform.agents.coding.graph_builder import build_coding_agent_graph
from ai_agent_platform.agents.coding.models import (
    AgentPlanner,
    AgentRunInvalidStateError,
    AgentRunEvent,
    AgentRunNotFoundError,
    AgentRunRecord,
    AgentRunResult,
    AgentRunStore,
    CodingAgentState,
    ContextSource,
    KnowledgeContextProvider,
    ProjectMemoryContextProvider,
)
from ai_agent_platform.agents.coding.planner import (
    LLMStructuredAgentPlanner,
    RuleBasedAgentPlanner,
)
from ai_agent_platform.agents.coding.policies import AgentLoopPolicies
from ai_agent_platform.agents.coding.run_recorder import RunRecorder
from ai_agent_platform.agents.coding.runtime_support import (
    checkpoint_id as _checkpoint_id,
    error_from_exception as _error_from_exception,
    latest_trace_node as _latest_trace_node,
    next_nodes as _next_nodes,
    snapshot_errors as _snapshot_errors,
    snapshot_trace as _snapshot_trace,
)
from ai_agent_platform.agents.coding.store import InMemoryAgentRunStore
from ai_agent_platform.agents.coding.tool_access import ToolAccessCoordinator
from ai_agent_platform.agents.coding.tool_loop_nodes import ToolLoopNodes
from ai_agent_platform.core.metrics import MetricsRegistry
from ai_agent_platform.agents.coding.tools import create_coding_tool_registry
from ai_agent_platform.domain import (
    Message,
    QueryCommand,
    QueryLifecycle,
    QueryStateError,
    RunContextSnapshot,
)
from ai_agent_platform.integrations.llm import LLMUsageAccumulator
from ai_agent_platform.integrations.tools import ToolRegistry
from ai_agent_platform.integrations.tool_pool import ToolPoolBuilder


def _merge_llm_usage(
    state: CodingAgentState,
    usage: LLMUsageAccumulator,
    *,
    previous_metrics: Any = None,
) -> CodingAgentState:
    merged = dict(state)
    merged["llm_input_tokens"] = (
        int(getattr(previous_metrics, "input_tokens", 0)) + usage.input_tokens
    )
    merged["llm_output_tokens"] = (
        int(getattr(previous_metrics, "output_tokens", 0)) + usage.output_tokens
    )
    merged["llm_thoughts_tokens"] = (
        int(getattr(previous_metrics, "thoughts_tokens", 0))
        + usage.thoughts_tokens
    )
    return merged  # type: ignore[return-value]


class _RuntimeGraphNodes:
    """Internal adapter from the compatibility facade to graph node callables."""

    def __init__(self, runtime: "CodingAgentRuntime") -> None:
        context = runtime._context_nodes
        self.setup_workspace = context._setup_workspace
        self.load_project_instructions = context._load_project_instructions
        self.classify_request = context._classify_request
        self.decide_context_source = context._decide_context_source
        self.retrieve_project_memory = context._retrieve_project_memory
        self.retrieve_knowledge = context._retrieve_knowledge
        self.plan_exploration = context._plan_exploration
        self.execute_exploration = context._execute_exploration
        self.assess_context = context._assess_context
        self.route_after_context = context._route_after_context
        self.merge_evidence = context._merge_evidence
        tool_loop = runtime._tool_loop_nodes
        self.plan_tools = tool_loop._plan_tools
        self.review_tool_plan = tool_loop._review_tool_plan
        self.inspect_repository = tool_loop._inspect_repository
        self.execute_changes = runtime._change_loop.execute_changes
        self.validate_changes = runtime._change_loop.validate_changes
        self.review_repair_plan = runtime._change_loop.review_repair_plan
        self.collect_artifacts = tool_loop._collect_artifacts
        self.compose_answer = tool_loop._compose_answer
        self.compose_error_answer = tool_loop._compose_error_answer


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
        soft_tool_rounds: int = 12,
        max_tool_rounds: int = 24,
        soft_tool_calls: int = 36,
        max_tool_calls: int = 72,
        max_elapsed_seconds: int = 900,
        no_progress_rounds: int = 3,
        max_consecutive_failures: int = 3,
        native_context_max_chars: int = 48000,
        native_context_keep_messages: int = 10,
        native_context_token_ratio: float = 0.5,
        plan_max_output_tokens: int = 4096,
        mutation_max_output_tokens: int = 16384,
        final_max_output_tokens: int = 4096,
        tool_result_max_tokens: int = 2000,
        graph_recursion_limit: int = 128,
        approval_policy: str = "on_request",
        max_history_messages: int = 12,
        knowledge_context_provider: KnowledgeContextProvider | None = None,
        project_memory_provider: ProjectMemoryContextProvider | None = None,
        max_rag_context_chars: int = 6000,
        change_set_service: Any = None,
        tool_pool_builder: ToolPoolBuilder | None = None,
        execution_workspace_runtime: Any = None,
        llm_client: Any = None,
        context_compressor: Any = None,
        metrics: MetricsRegistry | None = None,
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
        self._max_tool_rounds = max_tool_rounds
        self._soft_tool_rounds = min(soft_tool_rounds, max_tool_rounds)
        self._max_tool_calls = max_tool_calls
        self._soft_tool_calls = min(soft_tool_calls, max_tool_calls)
        self._max_elapsed_seconds = max_elapsed_seconds
        self._no_progress_rounds = no_progress_rounds
        self._max_consecutive_failures = max_consecutive_failures
        self._native_context_max_chars = native_context_max_chars
        self._native_context_keep_messages = native_context_keep_messages
        self._native_context_token_ratio = native_context_token_ratio
        self._llm_client = llm_client
        self._context_compressor = context_compressor
        self._plan_max_output_tokens = plan_max_output_tokens
        self._mutation_max_output_tokens = mutation_max_output_tokens
        self._final_max_output_tokens = final_max_output_tokens
        if tool_result_max_tokens < 64:
            raise ValueError("tool_result_max_tokens must be at least 64")
        self._tool_result_max_tokens = tool_result_max_tokens
        self._metrics = metrics or MetricsRegistry()
        self._graph_recursion_limit = graph_recursion_limit
        if approval_policy not in {"always", "on_request", "never"}:
            raise ValueError("unsupported approval_policy")
        self._approval_policy = approval_policy
        self._max_history_messages = max_history_messages
        self._knowledge_context_provider = knowledge_context_provider
        self._project_memory_provider = project_memory_provider
        self._max_rag_context_chars = max_rag_context_chars
        self._change_set_service = change_set_service
        self._execution_workspace_runtime = (
            execution_workspace_runtime
            or getattr(self._tools, "execution_workspace_runtime", None)
        )
        self._tool_access = ToolAccessCoordinator(
            tools=self._tools,
            default_approval_policy=self._approval_policy,
            tool_pool_builder=tool_pool_builder,
        )
        self._tools_for_state = self._tool_access.tools_for_state
        self._tool_use_context = self._tool_access.tool_use_context
        self._visible_tool_specs = self._tool_access.visible_tool_specs
        self._change_loop = ChangeLoopExecutor(
            tools=self._tools,
            planner=self._planner,
            run_store=self._run_store,
            pool_provider=self._tools_for_state,
            context_provider=self._tool_use_context,
        )
        self._context_nodes = ContextRetrievalNodes(self)
        self._policies = AgentLoopPolicies(self)
        self._tool_loop_nodes = ToolLoopNodes(self)
        self._graph = build_coding_agent_graph(
            nodes=_RuntimeGraphNodes(self),
            checkpointer=self._checkpointer,
        )
        self.graph_engine = "langgraph"
        self._checkpoint_coordinator = CheckpointResumeCoordinator(
            graph=self._graph,
            recursion_limit=self._graph_recursion_limit,
        )
        self._recorder = RunRecorder(self)

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
        actor_user_id: str = "demo_user",
        run_context: RunContextSnapshot | None = None,
    ) -> AgentRunResult:
        snapshot_instructions: list[ContextSource] = []
        additional_directories: list[dict[str, Any]] = []
        enabled_tools = list(self._tool_access.legacy_view().allowed_names)
        workspace_role = "admin"
        approval_policy = self._approval_policy
        cwd = workspace_root
        execution_root = workspace_root
        execution_workspace_mode = "patch_only"
        if run_context is not None:
            run_id = run_context.metadata.run_id
            conversation_id = run_context.session.conversation_id
            user_input = run_context.session.user_message
            workspace_id = run_context.project.workspace_id
            workspace_root = run_context.project.workspace_root
            cwd = run_context.project.cwd
            if run_context.execution_workspace is not None:
                execution_root = run_context.execution_workspace.execution_root
                execution_workspace_mode = run_context.execution_workspace.mode
                cwd = _execution_path(
                    workspace_root,
                    execution_root,
                    run_context.project.cwd,
                )
                if self._execution_workspace_runtime is not None:
                    self._execution_workspace_runtime.restore(
                        run_context.execution_workspace.to_dict(),
                        authorized_source_root=workspace_root,
                    )
            actor_user_id = run_context.identity.actor_user_id
            workspace_role = run_context.identity.workspace_role
            approval_policy = _snapshot_config_value(
                run_context.project.project_config,
                "runtime",
                "agent_approval_policy",
                default=self._approval_policy,
            )
            focus_files = list(run_context.instructions.focus_files)
            history = [
                {"role": item.role, "content": item.content}
                for item in run_context.session.controlled_history
            ]
            snapshot_instructions = [
                ContextSource(
                    kind=item.kind,
                    path=item.path,
                    start_line=item.start_line,
                    end_line=item.end_line,
                    text=item.text,
                    reason=item.reason,
                    content_hash=item.content_hash,
                    truncated=item.truncated,
                )
                for item in run_context.instructions.sources
            ]
            additional_directories = [
                {
                    "workspace_id": item.workspace_id,
                    "workspace_root": item.workspace_root,
                    "workspace_revision": item.workspace_revision,
                    "workspace_role": item.workspace_role,
                }
                for item in run_context.additional_directories
            ]
            if run_context.tools.enabled_tools is not None:
                enabled_tools = list(run_context.tools.enabled_tools)
            tool_access = self._tool_access.restore_snapshot(run_context)
            enabled_tools = list(tool_access.allowed_names)
        run_id = run_id or f"run_{uuid4().hex[:12]}"
        thread_id = run_id
        config = self._checkpoint_coordinator.config(thread_id)
        self._recorder._save_record(
            run_id=run_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            status="running",
            next_nodes=["setup_workspace"],
            context_snapshot=run_context,
        )
        initial_state: CodingAgentState = {
            "conversation_id": conversation_id,
            "run_id": run_id,
            "user_input": user_input,
            "workspace_id": workspace_id,
            "workspace_root": workspace_root,
            "execution_root": execution_root,
            "execution_workspace_mode": execution_workspace_mode,
            "actor_user_id": actor_user_id,
            "workspace_role": workspace_role,
            "authorized_workspace_root": workspace_root,
            "approval_policy": approval_policy,
            "tool_approvals": [],
            "cwd": cwd,
            "additional_directories": additional_directories,
            "enabled_tools": enabled_tools,
            "instructions_snapshotted": run_context is not None,
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
            "native_tool_messages": [],
            "native_tool_round": 0,
            "native_tool_call_count": 0,
            "native_pending_tool_calls": [],
            "native_tool_signatures": [],
            "native_tool_loop_active": False,
            "native_tool_answer": "",
            "native_tool_stop_reason": "",
            "native_soft_limit_warned": False,
            "native_no_progress_rounds": 0,
            "native_unfulfilled_change_rounds": 0,
            "native_consecutive_failures": 0,
            "native_context_compactions": 0,
            "native_context_chars": 0,
            "native_artifacts_collected": False,
            "terminal_status": "",
            "terminal_reason": "",
            "context_sources": [],
            "rag_context_sources": [],
            "memory_context_sources": [],
            "context_warnings": (
                list(run_context.instructions.diagnostics)
                if run_context is not None
                else []
            ),
            "knowledge_base_catalog": [],
            "selected_knowledge_base_ids": [],
            "context_route": "repo",
            "route_reason": "",
            "catalog_truncated": False,
            "project_instructions": snapshot_instructions,
            "context_chars": 0,
            "context_files": [],
            "seen_context_keys": [],
            "exploration_round": 0,
            "exploration_strategy": "not_started",
            "context_sufficient": False,
            "context_budget_exhausted": False,
            "context_stop_reason": "not_started",
            "change_iteration": 0,
            "changed_files": [],
            "change_set_id": "",
            "validation_history": [],
            "started_at": perf_counter(),
        }
        try:
            state, llm_usage = self._checkpoint_coordinator.invoke(
                initial_state, config
            )
            state = _merge_llm_usage(state, llm_usage)
        except Exception as exc:
            snapshot = self._checkpoint_coordinator.snapshot_for(config)
            self._recorder._capture_snapshot_change_set(
                snapshot,
                run_id=run_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                workspace_root=workspace_root,
            )
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
                    context_snapshot=run_context,
                )
            )
            self._recorder._cleanup_run_workspace(
                run_id=run_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                workspace_root=workspace_root,
            )
            raise
        return self._recorder._finish_invocation(
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
        run_id: str | None = None,
        context_snapshot: RunContextSnapshot | None = None,
    ) -> AgentRunRecord:
        run_id = run_id or f"run_{uuid4().hex[:12]}"
        if (
            context_snapshot is not None
            and context_snapshot.metadata.run_id != run_id
        ):
            raise ValueError("Run context ID does not match queued Run ID")
        return self._recorder._save_record(
            run_id=run_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            status="queued",
            next_nodes=["setup_workspace"],
            context_snapshot=context_snapshot,
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
        if record.status in QueryLifecycle.TERMINAL_STATUSES:
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
            context_snapshot=record.context_snapshot,
        )
        self._run_store.save(failed_record)
        self._recorder._cleanup_run_workspace(
            run_id=record.run_id,
            conversation_id=record.conversation_id,
            workspace_id=record.workspace_id,
            workspace_root=record.workspace_root,
        )
        return failed_record

    def resume(
        self,
        *,
        run_id: str,
        approved: bool,
        feedback: Optional[str] = None,
        approved_by: str | None = None,
    ) -> AgentRunResult:
        record = self.get_run(run_id)
        if record.context_snapshot is not None:
            self._tool_access.restore_snapshot(record.context_snapshot)
        resume_pending = (
            record.status == "running" and record.control_action == "resume"
        )
        resumed_pause = (
            record.status == "paused"
            or (
                resume_pending
                and (record.pending_approval or {}).get("type") == "run_pause"
            )
        )
        if record.status not in QueryLifecycle.SUSPENDED_STATUSES and not resume_pending:
            raise AgentRunInvalidStateError(run_id, record.status)
        if resume_pending:
            record = replace(record, control_action=None)
            self._run_store.save(record)
        if resumed_pause and (feedback or "").strip():
            record = replace(
                record,
                steering_messages=[
                    *record.steering_messages,
                    str(feedback).strip(),
                ],
            )
            self._run_store.save(record)
        config = self._checkpoint_coordinator.config(record.thread_id)
        try:
            state, llm_usage, _ = self._checkpoint_coordinator.resume(
                record,
                approved=approved,
                feedback=feedback,
                approved_by=approved_by,
            )
            state = _merge_llm_usage(
                state,
                llm_usage,
                previous_metrics=(
                    record.result.metrics if record.result is not None else None
                ),
            )
        except Exception as exc:
            snapshot = self._checkpoint_coordinator.snapshot_for(config)
            self._recorder._capture_snapshot_change_set(
                snapshot,
                run_id=record.run_id,
                conversation_id=record.conversation_id,
                workspace_id=record.workspace_id,
                workspace_root=record.workspace_root,
            )
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
                    context_snapshot=record.context_snapshot,
                )
            )
            self._recorder._cleanup_run_workspace(
                run_id=record.run_id,
                conversation_id=record.conversation_id,
                workspace_id=record.workspace_id,
                workspace_root=record.workspace_root,
            )
            raise
        return self._recorder._finish_invocation(
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
            record = self._run_store.get(run_id)
        except KeyError as exc:
            raise AgentRunNotFoundError(run_id) from exc
        if record.status != "running":
            return record
        snapshot = self._checkpoint_coordinator.snapshot_for(
            self._checkpoint_coordinator.config(record.thread_id)
        )
        trace = _snapshot_trace(snapshot)
        if not trace:
            return record
        updated = AgentRunRecord(
            run_id=record.run_id,
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            workspace_id=record.workspace_id,
            workspace_root=record.workspace_root,
            status=record.status,
            checkpoint_id=_checkpoint_id(snapshot),
            latest_node=_latest_trace_node(snapshot),
            next_nodes=_next_nodes(snapshot),
            trace=trace,
            result=record.result,
            error=record.error,
            pending_approval=record.pending_approval,
            errors=_snapshot_errors(snapshot) or record.errors,
            control_action=record.control_action,
            steering_messages=record.steering_messages,
            context_snapshot=record.context_snapshot,
        )
        return updated

    def get_latest_run(self, conversation_id: str) -> AgentRunRecord | None:
        get_latest = getattr(self._run_store, "get_latest_for_conversation", None)
        if not callable(get_latest):
            return None
        record = get_latest(conversation_id)
        return self.get_run(record.run_id) if record is not None else None

    def effective_tool_pool(self, run_id: str):
        """Return the restored v3 pool or explicit legacy v1/v2 view for one Run."""

        record = self.get_run(run_id)
        return self._tool_access.tools_for_run(
            run_id,
            snapshot=record.context_snapshot,
        )

    def list_events(self, run_id: str, *, after: int = 0):
        self.get_run(run_id)
        list_events = getattr(self._run_store, "list_events", None)
        if not callable(list_events):
            return []
        return list_events(run_id, after=after)

    def request_control(
        self,
        *,
        run_id: str,
        action: str,
        message: str = "",
    ) -> AgentRunRecord:
        if action not in {"pause", "cancel", "steer"}:
            raise ValueError(f"unsupported agent control action: {action}")
        record = self.get_run(run_id)
        try:
            QueryLifecycle.assert_command(QueryCommand(action), record.status)
        except QueryStateError as exc:
            raise AgentRunInvalidStateError(run_id, record.status) from exc
        steering_messages = list(record.steering_messages)
        if action == "steer":
            if not message.strip():
                raise ValueError("steering message must not be empty")
            steering_messages.append(message.strip())
            updated = replace(record, steering_messages=steering_messages)
        elif action == "cancel" and record.status in {
            "queued",
            "waiting_approval",
            "waiting_input",
            "paused",
        }:
            snapshot = self._checkpoint_coordinator.snapshot_for(
                self._checkpoint_coordinator.config(record.thread_id)
            )
            captured_state = self._recorder._capture_snapshot_change_set(
                snapshot,
                run_id=record.run_id,
                conversation_id=record.conversation_id,
                workspace_id=record.workspace_id,
                workspace_root=record.workspace_root,
            )
            result = (
                replace(
                    record.result,
                    status="cancelled",
                    artifacts=(
                        captured_state.get("artifacts", record.result.artifacts)
                        if captured_state is not None
                        else record.result.artifacts
                    ),
                    change_set_id=(
                        captured_state.get("change_set_id") or None
                        if captured_state is not None
                        else record.result.change_set_id
                    ),
                )
                if record.result is not None
                else None
            )
            updated = replace(
                record,
                status="cancelled",
                next_nodes=[],
                result=result,
                pending_approval=None,
                control_action=None,
            )
            self._recorder._cleanup_run_workspace(
                run_id=record.run_id,
                conversation_id=record.conversation_id,
                workspace_id=record.workspace_id,
                workspace_root=record.workspace_root,
            )
        else:
            updated = replace(record, control_action=action)
        self._recorder.append_event(
            run_id,
            AgentRunEvent(
                sequence=0,
                type=f"control_{action}_requested",
                status=updated.status,
                node=updated.latest_node,
                summary=f"Agent run {action} requested.",
                output={"message_chars": len(message.strip())},
            ),
        )
        self._run_store.save(updated)
        return updated

    def mark_resume_queued(self, run_id: str) -> AgentRunRecord:
        record = self.get_run(run_id)
        try:
            command = (
                QueryCommand.RESUME
                if record.status == "waiting_approval"
                else QueryCommand.CONTINUE
            )
            QueryLifecycle.assert_command(command, record.status)
        except QueryStateError as exc:
            raise AgentRunInvalidStateError(run_id, record.status) from exc
        updated = replace(
            record,
            status="running",
            control_action="resume",
        )
        self._recorder.append_event(
            run_id,
            AgentRunEvent(
                sequence=0,
                type="run_resume_requested",
                status="running",
                node=record.latest_node,
                summary="Agent run resume requested.",
                output={},
            ),
        )
        self._run_store.save(updated)
        return updated

    def restore_record(self, record: AgentRunRecord) -> None:
        self._run_store.save(record)

    def record_change_set_event(
        self,
        *,
        record: Any,
        action: str,
        actor_user_id: str | None,
    ) -> None:
        self._recorder.record_change_set_event(
            record=record,
            action=action,
            actor_user_id=actor_user_id,
        )


def _execution_path(source_root: str, execution_root: str, source_path: str) -> str:
    relative = Path(source_path).resolve().relative_to(Path(source_root).resolve())
    target = (Path(execution_root).resolve() / relative).resolve()
    if not target.exists() or not target.is_dir():
        raise ValueError("frozen cwd is unavailable in the execution workspace")
    return str(target)


def _snapshot_config_value(
    snapshot: dict[str, object],
    section: str,
    field_name: str,
    *,
    default: str,
) -> str:
    config = snapshot.get("config")
    if not isinstance(config, dict):
        return default
    section_value = config.get(section)
    if not isinstance(section_value, dict):
        return default
    field_value = section_value.get(field_name)
    if not isinstance(field_value, dict):
        return default
    value = str(field_value.get("value") or default)
    return value if value in {"always", "on_request", "never"} else default




__all__ = [
    "CodingAgentRuntime",
    "LLMStructuredAgentPlanner",
    "RuleBasedAgentPlanner",
    "create_coding_tool_registry",
]
