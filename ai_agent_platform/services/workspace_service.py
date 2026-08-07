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

    def get_by_root_path(self, root_path: str) -> WorkspaceRecord | None:
        ...

    def list(self) -> list[WorkspaceRecord]:
        ...


class WorkspaceNotFoundError(KeyError):
    pass


class WorkspaceValidationError(ValueError):
    pass


class WorkspaceRootConflictError(WorkspaceValidationError):
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
        existing = self._store.get_by_root_path(str(root))
        if existing is not None and existing.id != workspace_id:
            raise WorkspaceRootConflictError(
                "workspace root is already registered"
            )
        try:
            return self._store.upsert(
                workspace_id=workspace_id,
                root_path=str(root),
            )
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "23505":
                raise WorkspaceRootConflictError(
                    "workspace root is already registered"
                ) from exc
            raise

    def get(self, workspace_id: str) -> WorkspaceRecord:
        workspace = self._store.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(workspace_id)
        return workspace

    def list(self) -> list[WorkspaceRecord]:
        return self._store.list()

    def browse_directories(
        self,
        root_path: str | None = None,
    ) -> tuple[str | None, str | None, list[Path]]:
        if root_path is None:
            roots = [
                root
                for root in self._allowed_roots
                if root.exists() and root.is_dir()
            ]
            return None, None, roots

        current = self._resolve_allowed_root(root_path)
        directories: set[Path] = set()
        try:
            children = current.iterdir()
            for child in children:
                try:
                    resolved = child.resolve()
                    if resolved.is_dir() and self._is_allowed_root(resolved):
                        directories.add(resolved)
                except OSError:
                    continue
        except OSError as exc:
            raise WorkspaceValidationError(
                "workspace directory cannot be read"
            ) from exc

        parent = current.parent
        parent_path = str(parent) if self._is_allowed_root(parent) else None
        return (
            str(current),
            parent_path,
            sorted(directories, key=lambda item: (item.name.casefold(), str(item))),
        )

    def resolve_for_run(self, workspace_id: str) -> str:
        workspace = self.get(workspace_id)
        root = self._resolve_allowed_root(workspace.root_path)
        return str(root)

    def status(self, workspace_id: str) -> str:
        try:
            self.resolve_for_run(workspace_id)
        except WorkspaceValidationError:
            return "unavailable"
        return "ready"

    def _resolve_allowed_root(self, root_path: str) -> Path:
        root = Path(root_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise WorkspaceValidationError(
                "workspace root must be an existing directory"
            )
        if not self._is_allowed_root(root):
            raise WorkspaceValidationError(
                "workspace root is outside WORKSPACE_ALLOWED_ROOTS"
            )
        return root

    def _is_allowed_root(self, root: Path) -> bool:
        return any(
            root == allowed or allowed in root.parents
            for allowed in self._allowed_roots
        )
