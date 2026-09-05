from __future__ import annotations

from typing import Any

from ai_agent_platform.project_memory.models import ROLE_RANK


class WorkspaceAccessDeniedError(PermissionError):
    pass


class WorkspaceAccessValidationError(ValueError):
    pass


class WorkspaceAccessService:
    """Workspace membership and role checks, independent of memory storage."""

    def __init__(self, *, repository: Any, workspace_service: Any) -> None:
        self._repository = repository
        self._workspace_service = workspace_service

    def ensure_workspace_admin(
        self, *, workspace_id: str, actor_user_id: str
    ) -> None:
        self._repository.ensure_member(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            role="admin",
        )

    def authorize(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        required_role: str = "viewer",
    ) -> None:
        if required_role not in ROLE_RANK:
            raise WorkspaceAccessValidationError(
                f"unsupported workspace role: {required_role}"
            )
        member = self._repository.get_member(
            workspace_id=workspace_id,
            user_id=actor_user_id,
        )
        if member is None or ROLE_RANK.get(member.role, 0) < ROLE_RANK[required_role]:
            raise WorkspaceAccessDeniedError(
                f"workspace role {required_role} is required"
            )

    def role_for(self, *, workspace_id: str, actor_user_id: str) -> str | None:
        member = self._repository.get_member(
            workspace_id=workspace_id,
            user_id=actor_user_id,
        )
        return member.role if member is not None else None

    def delete_workspace_state(self, *, workspace_id: str) -> None:
        delete_access = getattr(self._repository, "delete_workspace_access", None)
        if callable(delete_access):
            delete_access(workspace_id=workspace_id)


__all__ = [
    "WorkspaceAccessDeniedError",
    "WorkspaceAccessService",
    "WorkspaceAccessValidationError",
]
