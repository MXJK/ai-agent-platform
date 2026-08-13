"""Run recording, event emission, result projection, and terminal cleanup."""

from __future__ import annotations

from typing import Any, Optional

from ai_agent_platform.agents.coding.change_loop import is_validation_success
from ai_agent_platform.agents.coding.models import (
    CODING_AGENT_OBJECTIVE,
    CODING_AGENT_ROLE,
    AgentRunEvent,
    AgentRunRecord,
    AgentRunResult,
    AgentRunStatus,
    CodingAgentState,
)
from ai_agent_platform.agents.coding.runtime_support import (
    append_errors as _append_errors,
    build_change_summary as _build_change_summary,
    build_run_metrics as _build_run_metrics,
    checkpoint_id as _checkpoint_id,
    error_from_exception as _error_from_exception,
    latest_trace_node as _latest_trace_node,
    next_nodes as _next_nodes,
    pending_approval as _pending_approval,
    waiting_node as _waiting_node,
)
from ai_agent_platform.domain import RunContextSnapshot
from ai_agent_platform.integrations.tools import ToolExecutionContext


class RunRecorder:
    """Internal event sink and terminal run projector."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._run_store = runtime._run_store
        self._tools = runtime._tools
        self._change_set_service = runtime._change_set_service
        self.graph_engine = runtime.graph_engine

    def _snapshot_for(self, config: dict[str, Any]):
        return self._runtime._checkpoint_coordinator.snapshot_for(config)

    def append_event(self, run_id: str, event: AgentRunEvent) -> None:
        append_event = getattr(self._run_store, "append_event", None)
        if callable(append_event):
            append_event(run_id, event)

    def record_change_set_event(
        self,
        *,
        record: Any,
        action: str,
        actor_user_id: str | None,
    ) -> None:
        self.append_event(
            record.run_id,
            AgentRunEvent(
                sequence=0,
                type=f"change_set_{action}",
                status=record.status,
                node="change_set",
                summary=f"ChangeSet {action}.",
                output={
                    "change_set_id": record.id,
                    "patch_sha256": record.patch_sha256,
                    "changed_file_count": len(record.changed_files),
                    "apply_mode": record.apply_mode,
                    "actor_user_id": actor_user_id,
                    "error": record.error,
                },
            ),
        )

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
        if pending is not None:
            pending_type = str(pending.get("type") or "")
            if pending_type == "run_pause":
                status: AgentRunStatus = "paused"
            elif pending_type == "input_required":
                status = "waiting_input"
            else:
                status = "waiting_approval"
        else:
            requested_status = str(state.get("terminal_status") or "completed")
            status = (
                requested_status
                if requested_status
                in {"completed", "partial", "blocked", "cancelled", "failed"}
                else "completed"
            )  # type: ignore[assignment]
        if status in {"completed", "partial", "blocked", "cancelled", "failed"}:
            state = self._capture_change_set(
                state,
                run_id=run_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                workspace_root=workspace_root,
            )
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
                errors=result.errors,
                context_snapshot=self._context_snapshot_for_run(run_id),
            )
        )
        if status in {"completed", "partial", "blocked", "cancelled", "failed"}:
            self._cleanup_run_workspace(
                run_id=run_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                workspace_root=workspace_root,
            )
        return result

    def _capture_change_set(
        self,
        state: CodingAgentState,
        *,
        run_id: str,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
    ) -> CodingAgentState:
        if self._change_set_service is None or not state.get("changed_files"):
            return state
        context = ToolExecutionContext(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            run_id=run_id,
        )
        try:
            snapshot = self._tools.export_context("sandbox", context)
            validation_results = state.get("validation_results", [])
            record = self._change_set_service.capture(
                run_id=run_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                workspace_root=workspace_root,
                created_by=state.get("actor_user_id", "demo_user"),
                snapshot=snapshot,
                validation_status=state.get("change_status", "changes_ready"),
                validation_summary={
                    "passed": bool(validation_results)
                    and all(is_validation_success(item) for item in validation_results),
                    "command_count": len(validation_results),
                    "iteration_count": state.get("change_iteration", 0),
                },
            )
        except Exception as exc:
            updated = dict(state)
            updated["errors"] = _append_errors(
                state,
                [_error_from_exception("capture_change_set", exc, attempt=1, max_attempts=1)],
            )
            return updated  # type: ignore[return-value]
        if record is None:
            return state
        updated = dict(state)
        updated["change_set_id"] = record.id
        updated["artifacts"] = list(state.get("artifacts", [])) + [
            {
                "type": "change_set",
                "id": record.id,
                "status": record.status,
                "apply_mode": record.apply_mode,
                "patch_sha256": record.patch_sha256,
                "changed_files": record.changed_files,
                "error": record.error,
            }
        ]
        return updated  # type: ignore[return-value]

    def _capture_snapshot_change_set(
        self,
        snapshot: Any,
        *,
        run_id: str,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
    ) -> CodingAgentState | None:
        values = getattr(snapshot, "values", None)
        if not isinstance(values, dict):
            return None
        return self._capture_change_set(
            values,  # type: ignore[arg-type]
            run_id=run_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
        )

    def _cleanup_run_workspace(
        self,
        *,
        run_id: str,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
    ) -> None:
        self._tools.cleanup_context(
            ToolExecutionContext(
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                workspace_root=workspace_root,
                run_id=run_id,
            )
        )

    def _save_record(
        self,
        *,
        run_id: str,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
        status: AgentRunStatus,
        next_nodes: list[str],
        context_snapshot: RunContextSnapshot | None = None,
    ) -> AgentRunRecord:
        try:
            existing = self._run_store.get(run_id)
        except KeyError:
            existing = None
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
            control_action=(existing.control_action if existing is not None else None),
            steering_messages=(
                list(existing.steering_messages) if existing is not None else []
            ),
            context_snapshot=(
                context_snapshot
                if context_snapshot is not None
                else existing.context_snapshot
                if existing is not None
                else None
            ),
        )
        self._run_store.save(record)
        return record

    def _context_snapshot_for_run(
        self, run_id: str
    ) -> RunContextSnapshot | None:
        try:
            return self._run_store.get(run_id).context_snapshot
        except KeyError:
            return None

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
            context_route=state.get("context_route", "repo"),
            selected_knowledge_base_ids=list(
                state.get("selected_knowledge_base_ids", [])
            ),
            answer=(
                state.get("answer", "")
                if status in {"completed", "partial", "blocked", "cancelled"}
                else ""
            ),
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
            change_set_id=state.get("change_set_id") or None,
            pending_approval=pending_approval,
        )
