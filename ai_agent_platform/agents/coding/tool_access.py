"""Per-run tool selection and permission projection for graph-internal nodes."""

from __future__ import annotations

from typing import Any

from ai_agent_platform.agents.coding.models import CodingAgentState
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


class ToolAccessCoordinator:
    """Build the immutable per-run tool view and authorization context."""

    def __init__(
        self,
        *,
        tools: ToolRegistry,
        default_approval_policy: str,
    ) -> None:
        self._tools = tools
        self._default_approval_policy = default_approval_policy

    def tools_for_state(self, state: CodingAgentState):
        selected_values = state.get("enabled_tools")
        selected = (
            tuple(selected_values)
            if selected_values is not None
            else tuple(spec.name for spec in self._tools.list_specs())
        )
        return self._tools.select(selected)

    def tool_use_context(self, state: CodingAgentState) -> ToolUseContext:
        approvals = tuple(
            ToolApproval.from_mapping(item)
            for item in state.get("tool_approvals", [])
        )
        process_tools = tuple(spec.name for spec in self._tools.list_specs())
        selected_values = state.get("enabled_tools")
        return ToolUseContext(
            conversation_id=state["conversation_id"],
            workspace_id=state["workspace_id"],
            workspace_root=state["workspace_root"],
            authorized_workspace_root=state.get("authorized_workspace_root"),
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
        return self.tools_for_state(state).list_specs(
            context=self.tool_use_context(state)
        )


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
