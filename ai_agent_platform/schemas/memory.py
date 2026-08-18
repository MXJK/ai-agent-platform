from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ai_agent_platform.memory import (
    ConversationMemoryHit,
    UserMemory,
    UserMemoryEvidence,
    UserMemorySettings,
    UserProfileSnapshot,
)


UserMemoryKind = Literal[
    "profile_fact",
    "communication_preference",
    "tooling_preference",
    "workflow_preference",
    "standing_goal",
    "personal_constraint",
]
UserMemoryStatus = Literal["candidate", "active", "superseded", "rejected"]
UserMemoryMode = Literal["off", "review"]


class ConversationMemoryHitResponse(BaseModel):
    message_id: str
    session_id: str
    workspace_id: str | None
    role: str
    excerpt: str
    created_at: datetime
    score: float

    @classmethod
    def from_domain(cls, value: ConversationMemoryHit) -> "ConversationMemoryHitResponse":
        return cls(**value.__dict__)


class ConversationMemorySearchResponse(BaseModel):
    hits: list[ConversationMemoryHitResponse]


class UserMemoryEvidenceResponse(BaseModel):
    id: str
    source_kind: str
    source_id: str
    excerpt: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, value: UserMemoryEvidence) -> "UserMemoryEvidenceResponse":
        return cls(
            id=value.id,
            source_kind=value.source_kind,
            source_id=value.source_id,
            excerpt=value.excerpt,
            created_at=value.created_at,
        )


class UserMemoryResponse(BaseModel):
    id: str
    kind: UserMemoryKind
    title: str
    content: str
    canonical_key: str
    status: UserMemoryStatus
    confidence: float
    importance: int
    version: int
    created_by: str
    supersedes_id: str | None
    last_confirmed_at: datetime | None
    evidence: list[UserMemoryEvidenceResponse]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: UserMemory) -> "UserMemoryResponse":
        return cls(
            **{
                key: getattr(value, key)
                for key in (
                    "id",
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
                    "last_confirmed_at",
                    "created_at",
                    "updated_at",
                )
            },
            evidence=[UserMemoryEvidenceResponse.from_domain(item) for item in value.evidence],
        )


class UserMemoriesResponse(BaseModel):
    memories: list[UserMemoryResponse]


class UserMemoryCreateRequest(BaseModel):
    kind: UserMemoryKind
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=1000)
    importance: int = Field(default=3, ge=1, le=5)


class UserMemoryUpdateRequest(UserMemoryCreateRequest):
    version: int = Field(ge=1)


class UserMemoryVersionRequest(BaseModel):
    version: int = Field(ge=1)


class UserMemorySettingsResponse(BaseModel):
    mode: UserMemoryMode
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: UserMemorySettings) -> "UserMemorySettingsResponse":
        return cls(mode=value.mode, updated_at=value.updated_at)


class UserMemorySettingsUpdateRequest(BaseModel):
    mode: UserMemoryMode


class UserProfileSnapshotResponse(BaseModel):
    version: int
    content: str
    source_memory_ids: list[str]
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: UserProfileSnapshot) -> "UserProfileSnapshotResponse":
        return cls(
            version=value.version,
            content=value.content,
            source_memory_ids=value.source_memory_ids,
            updated_at=value.updated_at,
        )


__all__ = [
    "ConversationMemoryHitResponse",
    "ConversationMemorySearchResponse",
    "UserMemoriesResponse",
    "UserMemoryCreateRequest",
    "UserMemoryResponse",
    "UserMemorySettingsResponse",
    "UserMemorySettingsUpdateRequest",
    "UserMemoryUpdateRequest",
    "UserMemoryVersionRequest",
    "UserProfileSnapshotResponse",
]
