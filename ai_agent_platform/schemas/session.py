from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ai_agent_platform.domain import (
    ConversationContextUsage,
    Session,
    TokenUsageRecord,
    TokenUsageTotals,
)


class CreateSessionRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)


class SessionResponse(BaseModel):
    id: str
    user_id: str
    created_at: datetime

    @classmethod
    def from_domain(cls, session: Session) -> "SessionResponse":
        return cls(
            id=session.id,
            user_id=session.user_id,
            created_at=session.created_at,
        )


class SessionsResponse(BaseModel):
    sessions: list[SessionResponse]


class TokenUsageResponse(BaseModel):
    id: str
    session_id: str
    workspace_id: str | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int
    total_tokens: int
    created_at: datetime

    @classmethod
    def from_domain(cls, usage: TokenUsageRecord) -> "TokenUsageResponse":
        return cls(**usage.__dict__)


class TokenUsageTotalsResponse(BaseModel):
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int
    total_tokens: int
    record_count: int

    @classmethod
    def from_domain(
        cls, totals: TokenUsageTotals
    ) -> "TokenUsageTotalsResponse":
        return cls(**totals.__dict__)


class ContextTokenUsageResponse(BaseModel):
    estimated_tokens: int
    message_count: int
    max_context_messages: int
    includes_summary: bool
    estimation_method: str

    @classmethod
    def from_domain(
        cls, usage: ConversationContextUsage
    ) -> "ContextTokenUsageResponse":
        return cls(**usage.__dict__)


class WorkspaceTokenBreakdownResponse(TokenUsageTotalsResponse):
    workspace_id: str | None


class TokenUsagesResponse(BaseModel):
    session_id: str
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int
    total_tokens: int
    record_count: int
    context: ContextTokenUsageResponse
    workspaces: list[WorkspaceTokenBreakdownResponse]
    records: list[TokenUsageResponse]
