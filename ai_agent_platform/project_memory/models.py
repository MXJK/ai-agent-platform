"""Domain contracts for workspace-scoped project memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


MEMORY_KINDS = frozenset(
    {
        "architecture_fact",
        "constraint",
        "decision",
        "convention",
        "task_outcome",
        "incident_lesson",
    }
)
MEMORY_STATUSES = frozenset(
    {"candidate", "active", "superseded", "rejected", "stale"}
)
MEMORY_MODES = frozenset({"off", "shadow", "review", "auto"})
MEMBER_ROLES = frozenset({"viewer", "editor", "admin"})
ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}


@dataclass(frozen=True)
class MemorySettings:
    workspace_id: str
    mode: str
    updated_by: str
    updated_at: datetime


@dataclass(frozen=True)
class WorkspaceMember:
    workspace_id: str
    user_id: str
    role: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MemoryEvidence:
    id: str
    memory_id: str
    source_kind: str
    source_id: str
    path: str | None
    start_line: int | None
    end_line: int | None
    content_hash: str | None
    excerpt: str | None
    created_at: datetime


@dataclass(frozen=True)
class ProjectMemory:
    id: str
    workspace_id: str
    workspace_revision: int
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
    expires_at: datetime | None = None
    last_confirmed_at: datetime | None = None
    last_accessed_at: datetime | None = None
    access_count: int = 0
    evidence: list[MemoryEvidence] = field(default_factory=list)
    conflict: bool = False


@dataclass(frozen=True)
class RetrievedMemory:
    memory: ProjectMemory
    score: float
    relevance_score: float = 0.0
    recency_score: float = 0.0
    importance_score: float = 0.0
    dense_rank: int | None = None
    lexical_rank: int | None = None
    fusion_score: float | None = None


@dataclass(frozen=True)
class MemoryExtractionJob:
    id: str
    workspace_id: str
    workspace_revision: int
    source_type: str
    source_id: str
    status: str
    attempts: int
    candidate_count: int
    active_count: int
    error: str | None
    input_tokens: int
    output_tokens: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


@dataclass(frozen=True)
class MemoryIndexEvent:
    id: str
    memory_id: str
    operation: str
    memory_version: int
    status: str
    attempts: int
    error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MemoryAuditEvent:
    id: str
    workspace_id: str
    memory_id: str
    action: str
    actor_user_id: str
    metadata: dict[str, object]
    created_at: datetime


class ProjectMemoryRepository(Protocol):
    def ensure_member(
        self, *, workspace_id: str, user_id: str, role: str
    ) -> WorkspaceMember:
        ...

    def get_member(self, *, workspace_id: str, user_id: str) -> WorkspaceMember | None:
        ...

    def record_audit_event(self, event: MemoryAuditEvent) -> None:
        ...

    def get_settings(
        self, *, workspace_id: str, default_mode: str
    ) -> MemorySettings:
        ...

    def update_settings(
        self, *, workspace_id: str, mode: str, updated_by: str
    ) -> MemorySettings:
        ...

    def create_memory(
        self,
        memory: ProjectMemory,
        *,
        evidence: list[MemoryEvidence],
        audit: MemoryAuditEvent,
    ) -> ProjectMemory:
        ...

    def get_memory(self, memory_id: str) -> ProjectMemory | None:
        ...

    def find_current_by_key(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        canonical_key: str,
    ) -> ProjectMemory | None:
        ...

    def find_active_by_key(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        canonical_key: str,
        exclude_memory_id: str | None = None,
    ) -> ProjectMemory | None:
        ...

    def list_memories(
        self,
        *,
        workspace_id: str,
        workspace_revision: int | None,
        status: str | None,
        kind: str | None,
        limit: int,
        offset: int,
    ) -> list[ProjectMemory]:
        ...

    def update_memory(
        self,
        memory: ProjectMemory,
        *,
        expected_version: int,
        evidence: list[MemoryEvidence],
        audit: MemoryAuditEvent,
    ) -> ProjectMemory | None:
        ...

    def delete_memory(
        self,
        *,
        memory_id: str,
        expected_workspace_id: str,
        audit: MemoryAuditEvent,
    ) -> bool:
        ...

    def search_lexical(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        query: str,
        limit: int,
    ) -> list[tuple[str, float]]:
        ...

    def record_access(self, memory_ids: list[str]) -> None:
        ...

    def create_extraction_job(
        self, job: MemoryExtractionJob
    ) -> MemoryExtractionJob | None:
        ...

    def get_extraction_job(
        self,
        *,
        workspace_id: str,
        source_type: str,
        source_id: str,
    ) -> MemoryExtractionJob | None:
        ...

    def update_extraction_job(
        self, job: MemoryExtractionJob
    ) -> MemoryExtractionJob:
        ...

    def list_extraction_jobs(
        self, *, workspace_id: str, limit: int
    ) -> list[MemoryExtractionJob]:
        ...

    def list_index_events(self, *, limit: int) -> list[MemoryIndexEvent]:
        ...

    def mark_index_event(
        self, *, event_id: str, status: str, error: str | None
    ) -> None:
        ...

    def enqueue_index_event(
        self,
        *,
        memory_id: str,
        operation: str,
        memory_version: int,
    ) -> None:
        ...

    def count_pending_index_events(self) -> int:
        ...

    def enqueue_reindex(
        self, *, workspace_id: str, workspace_revision: int
    ) -> int:
        ...
