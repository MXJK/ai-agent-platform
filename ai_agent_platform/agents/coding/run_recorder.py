"""Run recording, event emission, result projection, and terminal cleanup."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

from ai_agent_platform.agents.coding.change_loop import is_validation_success
from ai_agent_platform.agents.coding.completion_contract import (
    completion_contract_summary,
)
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
from ai_agent_platform.agents.coding.run_artifacts import (
    RUN_ARTIFACT_READ_TOOL,
    artifact_read_trace,
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

    def append_event(
        self,
        run_id: str,
        event: AgentRunEvent,
        *,
        event_key: str | None = None,
    ) -> None:
        if event_key:
            append_once = getattr(self._run_store, "append_event_once", None)
            if callable(append_once):
                append_once(run_id, event_key, event)
                return
        append_event = getattr(self._run_store, "append_event", None)
        if callable(append_event):
            append_event(run_id, event)

    def emit_event(
        self,
        *,
        run_id: str,
        event_type: str,
        node: str | None,
        summary: str,
        output: dict[str, Any] | None = None,
        event_key: str | None = None,
        status: str = "running",
    ) -> None:
        self.append_event(
            run_id,
            AgentRunEvent(
                sequence=0,
                type=event_type,
                status=status,
                node=node,
                summary=summary,
                output=output or {},
            ),
            event_key=event_key,
        )

    def record_node_started(
        self,
        *,
        run_id: str,
        node: str,
        state: CodingAgentState,
    ) -> None:
        step = len(state.get("trace", [])) + 1
        self.emit_event(
            run_id=run_id,
            event_type="node_started",
            node=node,
            summary=f"Agent started graph node {node}.",
            output={"step": step},
            event_key=f"node-started:{step}:{node}",
        )

    def record_node_completed(
        self,
        *,
        run_id: str,
        node: str,
        state: CodingAgentState,
        update: CodingAgentState,
    ) -> None:
        trace = list(update.get("trace") or state.get("trace", []))
        if not trace or len(trace) <= len(state.get("trace", [])):
            return
        latest = trace[-1]
        step = int(latest.get("step", len(trace)))
        try:
            existing = self._run_store.get(run_id)
        except KeyError:
            return
        if existing.status not in {"queued", "running"}:
            return
        self._run_store.save(
            replace(
                existing,
                status="running",
                latest_node=str(latest.get("node") or node),
                trace=trace,
            )
        )
        previous_call_ids = {
            call.call_id for call in state.get("tool_calls", [])
        }
        for call in update.get("tool_calls", []):
            if call.call_id in previous_call_ids:
                continue
            self.emit_event(
                run_id=run_id,
                event_type="tool_selected",
                node=node,
                summary=f"Tool selected: {call.name}.",
                output={
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "source": call.source,
                },
                event_key=f"tool-call:{call.call_id}",
            )
        previous_result_ids = {
            str(result.get("call_id") or "")
            for result in state.get("tool_results", [])
        }
        for result in update.get("tool_results", []):
            call_id = str(result.get("call_id") or "")
            if not call_id or call_id in previous_result_ids:
                continue
            tool_name = str(result.get("name") or "unknown")
            self.emit_event(
                run_id=run_id,
                event_type="tool_started",
                node=node,
                summary=f"Tool started: {tool_name}.",
                output={"call_id": call_id, "name": tool_name},
                event_key=f"tool-started:{call_id}",
            )
            event_output = dict(result)
            if tool_name == RUN_ARTIFACT_READ_TOOL:
                event_output["result"] = artifact_read_trace(result)
            self.emit_event(
                run_id=run_id,
                event_type="tool_result" if result.get("ok") else "tool_error",
                node=node,
                summary=(
                    f"Tool completed: {tool_name}."
                    if result.get("ok")
                    else f"Tool failed: {tool_name}."
                ),
                output=event_output,
                event_key=f"tool-result:{call_id}",
            )
            if result.get("ok") and tool_name in {
                "sandbox.write_file",
                "sandbox.apply_patch",
            }:
                self.emit_event(
                    run_id=run_id,
                    event_type="mutation_applied",
                    node=node,
                    summary="A workspace mutation was applied.",
                    output={"call_id": call_id, "name": tool_name},
                    event_key=f"mutation-applied:{call_id}",
                )
            if tool_name == "sandbox.run_command":
                passed = is_validation_success(result)
                self.emit_event(
                    run_id=run_id,
                    event_type="validation_passed" if passed else "validation_failed",
                    node=node,
                    summary=(
                        "A post-change validation passed."
                        if passed
                        else "A post-change validation failed."
                    ),
                    output={"call_id": call_id, "passed": passed},
                    event_key=f"validation:{call_id}",
                )
        merged_evidence_satisfied = bool(
            update.get(
                "evidence_contract_satisfied",
                state.get("evidence_contract_satisfied"),
            )
        )
        if merged_evidence_satisfied:
            self.emit_event(
                run_id=run_id,
                event_type="evidence_satisfied",
                node=node,
                summary="The evidence contract is satisfied.",
                output={"evidence_contract_satisfied": True},
                event_key="evidence-contract-satisfied",
            )
        prior_contract = state.get("change_completion_contract", {})
        current_contract = update.get("change_completion_contract", prior_contract)
        if isinstance(current_contract, dict) and current_contract:
            prior_revision = int(prior_contract.get("revision", 0)) if isinstance(prior_contract, dict) else 0
            current_revision = int(current_contract.get("revision", 0))
            prior_unresolved = (
                list(prior_contract.get("unresolved_changes", []))
                + list(prior_contract.get("unresolved_validations", []))
                if isinstance(prior_contract, dict)
                else []
            )
            current_unresolved = list(current_contract.get("unresolved_changes", [])) + list(
                current_contract.get("unresolved_validations", [])
            )
            if current_revision > prior_revision or current_unresolved != prior_unresolved:
                self.emit_event(
                    run_id=run_id,
                    event_type=(
                        "completion_contract_frozen"
                        if current_revision > prior_revision and prior_revision == 0
                        else "completion_contract_advanced"
                    ),
                    node=node,
                    summary="The ChangeCompletionContract state changed.",
                    output=completion_contract_summary(current_contract),
                    event_key=(
                        f"completion-contract:{current_revision}:"
                        + ",".join(current_unresolved)
                    ),
                )
            if (
                current_contract.get("completion_contract_satisfied")
                and not (
                    isinstance(prior_contract, dict)
                    and prior_contract.get("completion_contract_satisfied")
                )
            ):
                self.emit_event(
                    run_id=run_id,
                    event_type="completion_contract_satisfied",
                    node=node,
                    summary="The ChangeCompletionContract is satisfied.",
                    output={
                        "revision": current_revision,
                        "completion_contract_satisfied": True,
                    },
                    event_key="completion-contract-satisfied",
                )
        self.emit_event(
            run_id=run_id,
            event_type="reasoning_summary",
            node=str(latest.get("node") or node),
            summary=str(latest.get("summary") or "Agent completed a reasoning step."),
            output={"step": step},
            event_key=f"reasoning:{step}",
        )

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
                    "completion_contract": completion_contract_summary(
                        state.get("change_completion_contract")
                    ),
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
        context_snapshot = self._context_snapshot_for_run(run_id)
        execution = (
            context_snapshot.execution_workspace
            if context_snapshot is not None
            else None
        )
        return AgentRunResult(
            run_id=run_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            status=status,
            terminal_reason=str(state.get("terminal_reason") or ""),
            completion_contract=completion_contract_summary(
                state.get("change_completion_contract")
            ),
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
            workspace_mode=(execution.mode if execution is not None else "patch_only"),
            execution_root=(
                execution.execution_root if execution is not None else None
            ),
            branch_name=(execution.branch_name if execution is not None else None),
            worktree_path=(execution.worktree_path if execution is not None else None),
        )
