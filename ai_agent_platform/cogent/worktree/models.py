from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Worktree:
    name: str
    repo_root: str
    path: str
    branch: str
    based_on: str
    head_commit: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "repo_root": self.repo_root,
            "path": self.path,
            "branch": self.branch,
            "based_on": self.based_on,
            "head_commit": self.head_commit,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class WorktreeChanges:
    uncommitted: int = 0
    new_commits: int = 0

    @property
    def changed(self) -> bool:
        return self.uncommitted > 0 or self.new_commits > 0


@dataclass(frozen=True)
class CleanupResult:
    kept: bool
    path: str = ""
    branch: str = ""
    changes: WorktreeChanges = field(default_factory=WorktreeChanges)
