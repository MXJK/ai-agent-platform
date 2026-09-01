"""Per-run tool selection and permission projection for graph-internal nodes."""

from __future__ import annotations

from threading import RLock
from typing import Any

from ai_agent_platform.agents.coding.models import CodingAgentState
from ai_agent_platform.agents.coding.run_artifacts import RUN_ARTIFACT_TOOL_NAME
from ai_agent_platform.domain import RunContextSnapshot
from ai_agent_platform.integrations.permissions import (
    PermissionDecision,
    ToolApproval,
    ToolUseContext,
    canonical_arguments_hash,
)
from ai_agent_platform.integrations.tools import (
    ToolCall,
    ToolRegistry,
    summarize_tool_arguments,
)
from ai_agent_platform.integrations.tool_pool import ToolPoolBuilder, ToolPoolRestoreError


_WORKSPACE_MUTATION_TOOLS = frozenset(
    {"sandbox.apply_patch", "sandbox.write_file"}
)


class ToolAccessCoordinator:
    """Build the immutable per-run tool view and authorization context."""

    def __init__(
        self,
        *,
        tools: ToolRegistry,
        default_approval_policy: str,
        tool_pool_builder: ToolPoolBuilder | None = None,
    ) -> None:
        self._tools = tools
        self._default_approval_policy = default_approval_policy
        self._tool_pool_builder = tool_pool_builder or ToolPoolBuilder(tools)
        self._run_tools: dict[str, Any] = {}
        self._lock = RLock()

    def restore_snapshot(self, snapshot: RunContextSnapshot):
        """Restore and cache the exact v3 pool or the explicit legacy v1/v2 view."""

        if snapshot.metadata.schema_version >= 3:
            tools = self._tool_pool_builder.restore(snapshot.tools)
        else:
            selected = snapshot.tools.enabled_tools
            if selected is None:
                selected = tuple(spec.name for spec in self._tools.list_specs())
            selected = tuple(
                name for name in selected if name != RUN_ARTIFACT_TOOL_NAME
            )
            try:
                tools = self._tools.select(tuple(selected))
            except ValueError as exc:
                raise ToolPoolRestoreError(
                    "legacy Run tool selection is unavailable"
                ) from exc
        with self._lock:
            self._run_tools[snapshot.metadata.run_id] = tools
        return tools

    def legacy_view(self, allowed_names: tuple[str, ...] | None = None):
        """Explicit compatibility path for callers without a persisted snapshot."""

        selected = (
            tuple(spec.name for spec in self._tools.list_specs())
            if allowed_names is None
            else allowed_names
        )
        return self._tools.select(tuple(selected))

    def tools_for_run(
        self,
        run_id: str,
        *,
        snapshot: RunContextSnapshot | None = None,
    ):
        with self._lock:
            tools = self._run_tools.get(run_id)
        if tools is None and snapshot is not None:
            tools = self.restore_snapshot(snapshot)
        if tools is None:
            raise ToolPoolRestoreError("Run tool access has not been restored")
        tools.list_specs()
        return tools

    def tools_for_state(self, state: CodingAgentState):
        run_id = str(state.get("run_id") or "")
        if run_id:
            with self._lock:
                tools = self._run_tools.get(run_id)
            if tools is not None:
                tools.list_specs()
                return self._task_profile_view(tools, state)
        selected_values = state.get("enabled_tools")
        selected = tuple(selected_values) if selected_values is not None else None
        return self._task_profile_view(self.legacy_view(selected), state)

    @staticmethod
    def _task_profile_view(tools: Any, state: CodingAgentState):
        profile = tuple(state.get("task_tool_profile", []))
        mutation_authorized = state.get("mutation_authorized")
        if not profile and mutation_authorized is not False:
            return tools
        available = set(tools.allowed_names)
        selected = tuple(name for name in profile if name in available) if profile else tuple(
            name for name in tools.allowed_names if name in available
        )
        if mutation_authorized is False:
            selected = tuple(
                name for name in selected if name not in _WORKSPACE_MUTATION_TOOLS
            )
        return tools.select(selected)

    def tool_use_context(self, state: CodingAgentState) -> ToolUseContext:
        approvals = tuple(
            ToolApproval.from_mapping(item)
            for item in state.get("tool_approvals", [])
        )
        process_tools = tuple(spec.name for spec in self._tools.list_specs())
        selected_values = self.tools_for_state(state).allowed_names
        return ToolUseContext(
            conversation_id=state["conversation_id"],
            workspace_id=state["workspace_id"],
            workspace_root=state["workspace_root"],
            authorized_workspace_root=state.get("authorized_workspace_root"),
            execution_root=state.get("execution_root"),
            execution_workspace_mode=state.get("execution_workspace_mode"),
            run_id=state.get("run_id"),
            actor_user_id=state.get("actor_user_id", ""),
            workspace_role=state.get("workspace_role", "viewer"),
            approval_policy=state.get(
                "approval_policy", self._default_approval_policy
            ),
            process_allowed_tools=process_tools,
            project_allowed_tools=(
                tuple(selected_values) if selected_values is not None else None
            ),
            approvals=approvals,
        )

    def visible_tool_specs(self, state: CodingAgentState) -> list[Any]:
        specs = self.tools_for_state(state).list_specs(
            context=self.tool_use_context(state)
        )
        if not state.get("run_artifact_read_enabled", False):
            specs = [
                spec for spec in specs if spec.name != RUN_ARTIFACT_TOOL_NAME
            ]
        profile = state.get("task_tool_profile", [])
        if profile:
            by_name = {spec.name: spec for spec in specs}
            specs = [by_name[name] for name in profile if name in by_name]
        return specs


def permission_approval_item(
    tool_call: ToolCall,
    decision: PermissionDecision,
    tool_specs: list[Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    spec = next((item for item in tool_specs if item.name == tool_call.name), None)
    return {
        "name": tool_call.name,
        "run_id": run_id,
        "call_id": tool_call.call_id,
        "arguments_hash": canonical_arguments_hash(tool_call.arguments),
        "provider": spec.provider if spec is not None else "unknown",
        "permission_level": (
            spec.permission_level if spec is not None else "unknown"
        ),
        "requires_approval": True,
        "matched_rule": decision.matched_rule,
        "reason": decision.reason,
        "risk_summary": decision.risk_summary,
        "arguments_summary": summarize_tool_arguments(tool_call.arguments),
    }
