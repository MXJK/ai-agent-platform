from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


USER_MEMORY_KINDS = frozenset(
    {
        "profile_fact",
        "communication_preference",
        "tooling_preference",
        "workflow_preference",
        "standing_goal",
        "personal_constraint",
    }
)
USER_MEMORY_STATUSES = frozenset(
    {"candidate", "active", "superseded", "rejected"}
)
USER_MEMORY_MODES = frozenset({"off", "review", "auto"})


@dataclass(frozen=True)
class ConversationMemoryHit:
    message_id: str
    session_id: str
    workspace_id: str | None
    role: str
    excerpt: str
    created_at: datetime
    score: float


@dataclass(frozen=True)
class UserMemoryEvidence:
    id: str
    memory_id: str
    source_kind: str
    source_id: str
    excerpt: str | None
    created_at: datetime


@dataclass(frozen=True)
class UserMemory:
    id: str
    user_id: str
    kind: str
    title: str
    content: str
    canonical_key: str
    status: str
    confidence: float
    importance: int
    version: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    supersedes_id: str | None = None
    last_confirmed_at: datetime | None = None
    evidence: list[UserMemoryEvidence] = field(default_factory=list)


@dataclass(frozen=True)
class UserMemorySettings:
    user_id: str
    mode: str
    updated_at: datetime


@dataclass(frozen=True)
class UserMemoryScene:
    id: str
    user_id: str
    workspace_id: str
    title: str
    content: str
    source_memory_ids: list[str]
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class UserProfileSnapshot:
    user_id: str
    version: int
    content: str
    source_memory_ids: list[str]
    updated_at: datetime


__all__ = [
    "USER_MEMORY_KINDS",
    "USER_MEMORY_MODES",
    "USER_MEMORY_STATUSES",
    "ConversationMemoryHit",
    "UserMemory",
    "UserMemoryEvidence",
    "UserMemorySettings",
    "UserMemoryScene",
    "UserProfileSnapshot",
]
