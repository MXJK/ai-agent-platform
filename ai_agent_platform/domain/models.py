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
    compressed_summary: Optional[str] = None
    summarized_message_count: int = 0
    summary_version: int = 0
    summary_updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class ConversationSummary:
    session_id: str
    content: str
    summarized_message_count: int
    through_message_id: str
    version: int
    source_chars: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TokenUsageRecord:
    id: str
    session_id: Optional[str]
    workspace_id: Optional[str]
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int
    total_tokens: int
    created_at: datetime
    operation: str = "chat"
    resource_id: Optional[str] = None
    requested_provider: Optional[str] = None
    requested_model: Optional[str] = None
    input_count_method: str = "provider_usage"
    budget_decision: str = "allowed"


@dataclass(frozen=True)
class TokenUsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
    total_tokens: int = 0
    record_count: int = 0


@dataclass(frozen=True)
class ConversationContextUsage:
    estimated_tokens: int
    message_count: int
    max_context_messages: int
    includes_summary: bool
    estimation_method: str


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
    revision: int = 1


@dataclass(frozen=True)
class KnowledgeBaseRecord:
    id: str
    name: str
    description: str
    tags: list[str]
    document_count: int
    created_at: datetime
    updated_at: datetime
