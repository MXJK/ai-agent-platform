from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
from threading import RLock

from .models import CleanupResult, Worktree, WorktreeChanges
from .slug import flatten_slug, validate_slug


_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}


class WorktreeError(RuntimeError):
    pass


class WorktreeManager:
    """Create and retire server-owned worktrees without touching user changes."""

    def __init__(
        self,
        *,
        parent: str | Path,
        branch_prefix: str = "codex/",
        command_timeout_seconds: float = 30.0,
    ) -> None:
        self.parent = Path(parent).expanduser().resolve()
        self.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.branch_prefix = branch_prefix
        self.command_timeout_seconds = command_timeout_seconds
        self._active: dict[tuple[str, str], Worktree] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        repo_root: str | Path,
        name: str,
        base_ref: str = "HEAD",
    ) -> Worktree:
        error = validate_slug(name)
        if error:
            raise WorktreeError(error)
        root = Path(repo_root).expanduser().resolve(strict=True)
        self._require_repository(root)
        self._require_parent_outside(root)
        key = (str(root), name)
        with self._lock:
            cached = self._active.get(key)
            if cached is not None and self.read_head(cached.path):
                return cached
            flat = flatten_slug(name)
            directory = self._directory(root, name)
            branch = f"{self.branch_prefix}agent-{flat}"
            recovered = self._recover(root, directory, name, branch, base_ref)
            if recovered is not None:
                self._active[key] = recovered
                return recovered
            directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if directory.exists():
                raise WorktreeError(
                    f"worktree path exists but is not a registered Git worktree: {directory}"
                )
            result = self._git(
                root,
                ["worktree", "add", "-b", branch, str(directory), base_ref],
            )
            if result.returncode != 0:
                raise WorktreeError(
                    f"git worktree add failed: {result.stderr.strip() or result.stdout.strip()}"
                )
            head = self.read_head(directory)
            if not head:
                self._remove_registration(root, directory, branch)
                raise WorktreeError("new worktree has no readable HEAD")
            worktree = Worktree(
                name=name,
                repo_root=str(root),
                path=str(directory),
                branch=branch,
                based_on=base_ref,
                head_commit=head,
            )
            self._active[key] = worktree
            return worktree

    def adopt(self, worktree: Worktree) -> Worktree:
        """Restore a persisted server-owned worktree without changing its base."""

        error = validate_slug(worktree.name)
        if error:
            raise WorktreeError(error)
        root = Path(worktree.repo_root).expanduser().resolve(strict=True)
        path = Path(worktree.path).expanduser().resolve(strict=True)
        self._require_repository(root)
        if path != self._directory(root, worktree.name):
            raise WorktreeError("persisted worktree path is not server-owned")
        expected_branch = f"{self.branch_prefix}agent-{flatten_slug(worktree.name)}"
        branch = self._git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
        if branch.returncode != 0 or branch.stdout.strip() != expected_branch:
            raise WorktreeError("persisted worktree branch does not match its name")
        with self._lock:
            self._active[(str(root), worktree.name)] = worktree
        return worktree

    def get(self, *, repo_root: str | Path, name: str) -> Worktree | None:
        root = Path(repo_root).expanduser().resolve()
        key = (str(root), name)
        with self._lock:
            value = self._active.get(key)
        if value is None or self.read_head(value.path) is None:
            return None
        return value

    def list_worktrees(self, *, repo_root: str | Path | None = None) -> list[Worktree]:
        root = str(Path(repo_root).expanduser().resolve()) if repo_root else None
        with self._lock:
            values = list(self._active.values())
        return [item for item in values if root is None or item.repo_root == root]

    def changes(self, worktree: Worktree) -> WorktreeChanges:
        path = Path(worktree.path)
        if self.read_head(path) is None:
            raise WorktreeError(f"worktree is unavailable: {path}")
        status = self._git(path, ["status", "--porcelain"])
        if status.returncode != 0:
            raise WorktreeError(f"git status failed: {status.stderr.strip()}")
        commits = self._git(
            path,
            ["rev-list", "--count", f"{worktree.head_commit}..HEAD"],
        )
        if commits.returncode != 0:
            raise WorktreeError(f"git rev-list failed: {commits.stderr.strip()}")
        try:
            new_commits = int(commits.stdout.strip() or "0")
        except ValueError as exc:
            raise WorktreeError("git returned an invalid commit count") from exc
        return WorktreeChanges(
            uncommitted=sum(1 for line in status.stdout.splitlines() if line.strip()),
            new_commits=new_commits,
        )

    def remove(
        self,
        *,
        repo_root: str | Path,
        name: str,
        discard_changes: bool = False,
    ) -> CleanupResult:
        root = Path(repo_root).expanduser().resolve(strict=True)
        worktree = self.get(repo_root=root, name=name)
        if worktree is None:
            raise WorktreeError(f"worktree not found: {name}")
        changes = self.changes(worktree)
        if changes.changed and not discard_changes:
            raise WorktreeError(
                "worktree has changes "
                f"({changes.uncommitted} uncommitted, {changes.new_commits} new commits)"
            )
        self._remove_registration(root, Path(worktree.path), worktree.branch)
        with self._lock:
            self._active.pop((str(root), name), None)
        return CleanupResult(kept=False, changes=changes)

    def auto_cleanup(self, worktree: Worktree) -> CleanupResult:
        changes = self.changes(worktree)
        if changes.changed:
            return CleanupResult(
                kept=True,
                path=worktree.path,
                branch=worktree.branch,
                changes=changes,
            )
        return self.remove(
            repo_root=worktree.repo_root,
            name=worktree.name,
            discard_changes=False,
        )

    @staticmethod
    def read_head(worktree_path: str | Path) -> str | None:
        path = Path(worktree_path)
        try:
            result = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
                env={**os.environ, **_GIT_ENV},
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    def _recover(
        self,
        root: Path,
        directory: Path,
        name: str,
        branch: str,
        base_ref: str,
    ) -> Worktree | None:
        head = self.read_head(directory)
        if not head:
            return None
        branch_result = self._git(directory, ["rev-parse", "--abbrev-ref", "HEAD"])
        if branch_result.returncode != 0 or branch_result.stdout.strip() != branch:
            raise WorktreeError(
                f"existing worktree branch does not match {branch}: {directory}"
            )
        base = self._git(root, ["rev-parse", base_ref])
        if base.returncode != 0 or not base.stdout.strip():
            raise WorktreeError(
                f"cannot resolve recovered worktree base {base_ref}: {base.stderr.strip()}"
            )
        return Worktree(
            name=name,
            repo_root=str(root),
            path=str(directory),
            branch=branch,
            based_on=base_ref,
            head_commit=base.stdout.strip(),
        )

    def _remove_registration(self, root: Path, directory: Path, branch: str) -> None:
        result = self._git(root, ["worktree", "remove", "--force", str(directory)])
        if result.returncode != 0:
            raise WorktreeError(f"git worktree remove failed: {result.stderr.strip()}")
        deleted = self._git(root, ["branch", "-D", branch])
        if deleted.returncode != 0 and "not found" not in deleted.stderr.casefold():
            raise WorktreeError(f"git branch cleanup failed: {deleted.stderr.strip()}")

    def _require_repository(self, root: Path) -> None:
        result = self._git(root, ["rev-parse", "--show-toplevel"])
        if result.returncode != 0 or Path(result.stdout.strip()).resolve() != root:
            raise WorktreeError("worktree isolation requires the registered Git repository root")

    def _require_parent_outside(self, root: Path) -> None:
        if self.parent == root or root in self.parent.parents or self.parent in root.parents:
            raise WorktreeError("worktree parent and repository must not contain each other")

    def _directory(self, root: Path, name: str) -> Path:
        repo_key = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
        return (
            self.parent / f"agent-worktree-{repo_key}-{flatten_slug(name)}"
        ).resolve()

    def _git(self, cwd: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *arguments],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
                stdin=subprocess.DEVNULL,
                env={**os.environ, **_GIT_ENV},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorktreeError(f"git command failed: {exc}") from exc
