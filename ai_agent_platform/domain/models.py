from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


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


@dataclass(frozen=True)
class WorkspaceRecord:
    id: str
    root_path: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class KnowledgeBaseRecord:
    id: str
    name: str
    description: str
    tags: list[str]
    document_count: int
    created_at: datetime
    updated_at: datetime
