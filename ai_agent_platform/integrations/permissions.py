"""Deterministic, fail-closed authorization for tool and workspace actions."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from typing import Any, Literal, Mapping


PermissionEffect = Literal["allow", "ask", "deny"]
PermissionPhase = Literal["display", "plan", "execute"]
_ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}


def canonical_arguments_hash(arguments: Mapping[str, Any]) -> str:
    """Return the stable digest used by approvals and durable tool execution."""

    encoded = json.dumps(
        dict(arguments),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PermissionRequest:
    name: str
    permission_level: str = "read_only"
    requires_approval: bool = False
    provider: str = "local"
    risk_summary: str = "Read-only operation with no expected side effects."
    permission_source: str = "local_policy"

    @classmethod
    def from_spec(cls, spec: Any) -> "PermissionRequest":
        return cls(
            name=str(spec.name),
            permission_level=str(spec.permission_level),
            requires_approval=bool(spec.requires_approval),
            provider=str(spec.provider),
            risk_summary=str(spec.risk_summary),
            permission_source=str(
                getattr(spec, "permission_source", "local_policy")
            ),
        )


@dataclass(frozen=True)
class ToolApproval:
    run_id: str
    call_id: str
    tool_name: str
    arguments_hash: str
    approved_by: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ToolApproval":
        return cls(
            run_id=str(value.get("run_id") or ""),
            call_id=str(value.get("call_id") or ""),
            tool_name=str(value.get("tool_name") or value.get("name") or ""),
            arguments_hash=str(value.get("arguments_hash") or ""),
            approved_by=str(value.get("approved_by") or ""),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments_hash": self.arguments_hash,
            "approved_by": self.approved_by,
        }

    def matches(self, context: "ToolUseContext") -> bool:
        return bool(
            self.run_id
            and self.run_id == context.run_id
            and self.call_id == context.call_id
            and self.tool_name == context.tool_name
            and self.arguments_hash == context.arguments_hash
        )


@dataclass(frozen=True)
class ToolUseContext:
    conversation_id: str
    workspace_id: str
    workspace_root: str
    run_id: str | None = None
    actor_user_id: str = ""
    workspace_role: str = "admin"
    authorized_workspace_root: str | None = None
    execution_root: str | None = None
    execution_workspace_mode: str | None = None
    approval_policy: str = "on_request"
    process_allowed_tools: tuple[str, ...] | None = None
    project_allowed_tools: tuple[str, ...] | None = None
    process_denied_tools: tuple[str, ...] = ()
    project_denied_tools: tuple[str, ...] = ()
    approvals: tuple[ToolApproval, ...] = ()
    call_id: str | None = None
    tool_name: str | None = None
    arguments_hash: str | None = None

    def bind(self, *, call_id: str, tool_name: str, arguments: Mapping[str, Any]) -> "ToolUseContext":
        return replace(
            self,
            call_id=call_id,
            tool_name=tool_name,
            arguments_hash=canonical_arguments_hash(arguments),
        )

    def for_display(self, tool_name: str) -> "ToolUseContext":
        return replace(
            self,
            call_id=None,
            tool_name=tool_name,
            arguments_hash=None,
        )

    def with_approvals(
        self,
        approvals: tuple[ToolApproval, ...],
    ) -> "ToolUseContext":
        return replace(self, approvals=approvals)


# Compatibility name retained for local tool implementations. New authorization
# code should use ToolUseContext directly.
ToolExecutionContext = ToolUseContext


@dataclass(frozen=True)
class PermissionDecision:
    effect: PermissionEffect
    matched_rule: str
    reason: str
    risk_summary: str

    def to_dict(self) -> dict[str, str]:
        return {
            "effect": self.effect,
            "matched_rule": self.matched_rule,
            "reason": self.reason,
            "risk_summary": self.risk_summary,
        }


class PermissionResolver:
    """Resolve hard boundaries, project restrictions, and exact approvals."""

    def resolve_mcp_annotations(
        self,
        *,
        name: str,
        annotations: Mapping[str, Any] | None,
    ) -> PermissionRequest:
        """Convert untrusted MCP hints into conservative local metadata.

        This conversion is deliberately owned by the central resolver.  The
        resulting ``permission_source`` still forces the normal display/plan/
        execute policy path to treat the metadata as advisory rather than as
        an authorization grant.
        """

        hints = dict(annotations or {})
        destructive = hints.get("destructiveHint") is True
        open_world = hints.get("openWorldHint") is True
        explicitly_read_only = hints.get("readOnlyHint") is True
        explicitly_writable = hints.get("readOnlyHint") is False
        if destructive or open_world:
            permission_level = "external_side_effect"
        elif explicitly_writable:
            permission_level = "write_safe"
        elif explicitly_read_only:
            permission_level = "read_only"
        else:
            # Missing hints are not evidence that a remote operation is safe.
            permission_level = "external_side_effect"
        requires_approval = permission_level != "read_only"
        return PermissionRequest(
            name=name,
            permission_level=permission_level,
            requires_approval=requires_approval,
            provider="mcp",
            risk_summary=(
                f"MCP tool {name} reports advisory {permission_level} metadata."
            ),
            permission_source="mcp_annotation",
        )

    def resolve(
        self,
        request: PermissionRequest,
        context: ToolUseContext,
        *,
        phase: PermissionPhase = "execute",
    ) -> PermissionDecision:
        risk = request.risk_summary
        name = request.name

        if context.approval_policy not in {
            "always",
            "on_request",
            "never",
            "auto_approve",
        }:
            return self._deny(
                "process.invalid_approval_policy",
                "The effective approval policy is invalid.",
                risk,
            )
        if context.tool_name is not None and context.tool_name != name:
            return self._deny(
                "context.tool_binding_mismatch",
                "The ToolUseContext is bound to a different tool.",
                risk,
            )

        # Process rules, registered Workspace boundaries, and identity RBAC are
        # hard denies. Project configuration and user approval never bypass them.
        if name in context.process_denied_tools:
            return self._deny(
                "process.explicit_deny",
                "A process-level deny rule blocks this operation.",
                risk,
            )
        if (
            context.process_allowed_tools is not None
            and name not in context.process_allowed_tools
        ):
            return self._deny(
                "process.capability_boundary",
                "The operation is outside the process capability allowlist.",
                risk,
            )
        if not context.workspace_id or not context.workspace_root:
            return self._deny(
                "workspace.missing_boundary",
                "A registered Workspace boundary is required.",
                risk,
            )
        if context.authorized_workspace_root is not None and not _same_root(
            context.workspace_root,
            context.authorized_workspace_root,
        ):
            return self._deny(
                "workspace.root_boundary",
                "The requested Workspace root differs from the authorized root.",
                risk,
            )
        required_role = (
            "viewer" if request.permission_level == "read_only" else "editor"
        )
        if _ROLE_RANK.get(context.workspace_role, 0) < _ROLE_RANK[required_role]:
            return self._deny(
                f"workspace.rbac.{required_role}",
                f"Workspace role {required_role} or higher is required.",
                risk,
            )

        # Project rules are deny-only restrictions over the process boundary.
        if name in context.project_denied_tools:
            return self._deny(
                "project.explicit_deny",
                "A project-level deny rule blocks this operation.",
                risk,
            )
        if (
            context.project_allowed_tools is not None
            and name not in context.project_allowed_tools
        ):
            return self._deny(
                "project.tool_selection",
                "The project tool selection excludes this operation.",
                risk,
            )

        advisory_external = request.permission_source in {
            "mcp_annotation",
            "skill_annotation",
            "provider_annotation",
        }
        needs_approval = bool(
            request.requires_approval
            or request.permission_level != "read_only"
            or context.approval_policy == "always"
            or advisory_external
        )
        if not needs_approval:
            return PermissionDecision(
                effect="allow",
                matched_rule="central_policy.low_risk_allow",
                reason="Central policy allows this read-only operation.",
                risk_summary=risk,
            )
        if context.approval_policy == "auto_approve":
            return PermissionDecision(
                effect="allow",
                matched_rule="approval_policy.auto_approve",
                reason=(
                    "The effective approval policy auto-approves operations "
                    "that would otherwise require human review."
                ),
                risk_summary=risk,
            )
        if context.approval_policy == "never":
            return self._deny(
                "approval_policy.never",
                "The effective approval policy denies operations that require approval.",
                risk,
            )
        if phase == "execute" and (
            not context.run_id
            or not context.call_id
            or not context.tool_name
            or not context.arguments_hash
        ):
            return self._deny(
                "approval.incomplete_binding",
                "Execution approval requires run, call, tool, and argument bindings.",
                risk,
            )
        if any(approval.matches(context) for approval in context.approvals):
            return PermissionDecision(
                effect="allow",
                matched_rule="approval.exact_binding",
                reason=(
                    "A user approval exactly matches the run, call, tool, and "
                    "argument hash."
                ),
                risk_summary=risk,
            )
        return PermissionDecision(
            effect="ask",
            matched_rule=(
                "provider.annotation_requires_central_approval"
                if advisory_external
                else "central_policy.approval_required"
            ),
            reason=(
                "Provider permission annotations are advisory; central approval is required."
                if advisory_external
                else "This operation requires an exact user approval before execution."
            ),
            risk_summary=risk,
        )

    def issue_approval(
        self,
        request: PermissionRequest,
        context: ToolUseContext,
        *,
        approved_by: str,
    ) -> ToolApproval:
        decision = self.resolve(request, context, phase="plan")
        if decision.effect == "deny":
            raise PermissionError(decision.reason)
        if not all(
            (
                context.run_id,
                context.call_id,
                context.tool_name,
                context.arguments_hash,
                approved_by,
            )
        ):
            raise PermissionError("approval binding is incomplete")
        return ToolApproval(
            run_id=str(context.run_id),
            call_id=str(context.call_id),
            tool_name=str(context.tool_name),
            arguments_hash=str(context.arguments_hash),
            approved_by=approved_by,
        )

    @staticmethod
    def _deny(rule: str, reason: str, risk: str) -> PermissionDecision:
        return PermissionDecision(
            effect="deny",
            matched_rule=rule,
            reason=reason,
            risk_summary=risk,
        )


def _same_root(first: str, second: str) -> bool:
    return os.path.realpath(os.path.abspath(first)) == os.path.realpath(
        os.path.abspath(second)
    )


def effective_approval_policy(
    configured: str,
    runtime_override: object,
) -> str:
    """Resolve the effective approval policy for a single Run.

    The runtime override originates from a per-run UI choice (the composer's
    auto-approve toggle). It may only relax the default ``on_request`` policy
    to ``auto_approve``; it never bypasses the strict ``always`` or ``never``
    policies, which remain hard process/project security boundaries.
    """

    if configured == "on_request" and runtime_override == "auto_approve":
        return "auto_approve"
    return configured


__all__ = [
    "PermissionDecision",
    "PermissionEffect",
    "PermissionPhase",
    "PermissionRequest",
    "PermissionResolver",
    "ToolApproval",
    "ToolExecutionContext",
    "ToolUseContext",
    "canonical_arguments_hash",
    "effective_approval_policy",
]
