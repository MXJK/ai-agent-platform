from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ai_agent_platform.domain import WorkspaceRecord


class WorkspaceStore(Protocol):
    def upsert(
        self,
        *,
        workspace_id: str,
        root_path: str,
    ) -> WorkspaceRecord:
        ...

    def get(self, workspace_id: str) -> WorkspaceRecord | None:
        ...

    def list(self) -> list[WorkspaceRecord]:
        ...


class WorkspaceNotFoundError(KeyError):
    pass


class WorkspaceValidationError(ValueError):
    pass


class WorkspaceService:
    def __init__(
        self,
        *,
        store: WorkspaceStore,
        allowed_roots: tuple[str, ...],
    ) -> None:
        self._store = store
        self._allowed_roots = tuple(
            Path(root).expanduser().resolve() for root in allowed_roots
        )
        if not self._allowed_roots:
            raise WorkspaceValidationError("at least one workspace root is required")

    def register(self, *, workspace_id: str, root_path: str) -> WorkspaceRecord:
        root = self._resolve_allowed_root(root_path)
        return self._store.upsert(
            workspace_id=workspace_id,
            root_path=str(root),
        )

    def get(self, workspace_id: str) -> WorkspaceRecord:
        workspace = self._store.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(workspace_id)
        return workspace

    def list(self) -> list[WorkspaceRecord]:
        return self._store.list()

    def resolve_for_run(self, workspace_id: str) -> str:
        workspace = self.get(workspace_id)
        root = self._resolve_allowed_root(workspace.root_path)
        return str(root)

    def _resolve_allowed_root(self, root_path: str) -> Path:
        root = Path(root_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise WorkspaceValidationError(
                "workspace root must be an existing directory"
            )
        if not any(root == allowed or allowed in root.parents for allowed in self._allowed_roots):
            raise WorkspaceValidationError(
                "workspace root is outside WORKSPACE_ALLOWED_ROOTS"
            )
        return root
