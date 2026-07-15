from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional


@dataclass(frozen=True)
class Session:
    id: str
    user_id: str
    created_at: datetime


@dataclass(frozen=True)
class Message:
    id: str
    session_id: str
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    message_count: int
    last_message: Optional[str]


@dataclass(frozen=True)
class TokenUsageRecord:
    id: str
    session_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    created_at: datetime


@dataclass(frozen=True)
class AgentDecision:
    kind: str
    confidence: float
    reason: str


RepositoryIndexJobStatus = Literal["pending", "running", "completed", "failed"]


@dataclass(frozen=True)
class RepositoryRecord:
    id: str
    root_path: str
    created_at: datetime
    updated_at: datetime
    last_indexed_at: Optional[datetime] = None


@dataclass(frozen=True)
class RepositoryIndexJobRecord:
    id: str
    repository_id: str
    root_path: str
    include_patterns: list[str]
    exclude_patterns: list[str]
    max_file_size: int
    status: RepositoryIndexJobStatus
    scanned_files: int
    indexed_files: int
    skipped_files: int
    failed_files: int
    error: Optional[str]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None


@dataclass(frozen=True)
class RepositoryFileRecord:
    id: str
    repository_id: str
    path: str
    content_hash: str
    size_bytes: int
    document_id: Optional[str]
    indexed_at: Optional[datetime]
    skipped_reason: Optional[str]
    created_at: datetime
    updated_at: datetime
