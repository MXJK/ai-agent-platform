"""Named Git worktrees used by isolated Subagents and Agent Teams."""

from .manager import WorktreeError, WorktreeManager
from .models import CleanupResult, Worktree, WorktreeChanges
from .slug import flatten_slug, validate_slug

__all__ = [
    "CleanupResult",
    "Worktree",
    "WorktreeChanges",
    "WorktreeError",
    "WorktreeManager",
    "flatten_slug",
    "validate_slug",
]
