from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ai_agent_platform.project_memory.models import (
    MemoryEvidence,
    MemoryExtractionJob,
    MemorySettings,
    ProjectMemory,
)


MemoryKind = Literal[
    "architecture_fact",
    "constraint",
    "decision",
    "convention",
    "task_outcome",
    "incident_lesson",
]
MemoryStatus = Literal[
    "candidate", "active", "superseded", "rejected", "stale"
]
MemoryMode = Literal["off", "shadow", "review", "auto"]


class MemoryEvidenceResponse(BaseModel):
    id: str
    source_kind: str
    source_id: str
    path: str | None
    start_line: int | None
    end_line: int | None
    content_hash: str | None
    excerpt: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, value: MemoryEvidence) -> "MemoryEvidenceResponse":
        return cls(
            id=value.id,
            source_kind=value.source_kind,
            source_id=value.source_id,
            path=value.path,
            start_line=value.start_line,
            end_line=value.end_line,
            content_hash=value.content_hash,
            excerpt=value.excerpt,
            created_at=value.created_at,
        )


class ProjectMemoryResponse(BaseModel):
    id: str
    workspace_id: str
    workspace_revision: int
    kind: MemoryKind
    title: str
    content: str
    canonical_key: str
    status: MemoryStatus
    confidence: float
    importance: int
    version: int
    created_by: str
    supersedes_id: str | None
    expires_at: datetime | None
    last_confirmed_at: datetime | None
    last_accessed_at: datetime | None
    access_count: int
    conflict: bool
    evidence: list[MemoryEvidenceResponse]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: ProjectMemory) -> "ProjectMemoryResponse":
        return cls(
            **{
                key: getattr(value, key)
                for key in (
                    "id",
                    "workspace_id",
                    "workspace_revision",
                    "kind",
                    "title",
                    "content",
                    "canonical_key",
                    "status",
                    "confidence",
                    "importance",
                    "version",
                    "created_by",
                    "supersedes_id",
                    "expires_at",
                    "last_confirmed_at",
                    "last_accessed_at",
                    "access_count",
                    "conflict",
                    "created_at",
                    "updated_at",
                )
            },
            evidence=[
                MemoryEvidenceResponse.from_domain(item)
                for item in value.evidence
            ],
        )


class ProjectMemoriesResponse(BaseModel):
    memories: list[ProjectMemoryResponse]


class ProjectMemoryCreateRequest(BaseModel):
    kind: MemoryKind
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=2000)
    importance: int = Field(default=3, ge=1, le=5)
    expires_at: datetime | None = None


class ProjectMemoryUpdateRequest(ProjectMemoryCreateRequest):
    version: int = Field(ge=1)


class MemoryVersionRequest(BaseModel):
    version: int = Field(ge=1)


class MemorySettingsResponse(BaseModel):
    workspace_id: str
    mode: MemoryMode
    updated_by: str
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: MemorySettings) -> "MemorySettingsResponse":
        return cls(**value.__dict__)


class MemorySettingsUpdateRequest(BaseModel):
    mode: MemoryMode


class MemoryExtractionJobResponse(BaseModel):
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
    completed_at: datetime | None

    @classmethod
    def from_domain(
        cls, value: MemoryExtractionJob
    ) -> "MemoryExtractionJobResponse":
        return cls(**value.__dict__)


class MemoryExtractionJobsResponse(BaseModel):
    jobs: list[MemoryExtractionJobResponse]


class MemoryReindexResponse(BaseModel):
    queued_count: int
