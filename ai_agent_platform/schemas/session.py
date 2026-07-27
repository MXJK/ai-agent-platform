from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ai_agent_platform.domain import Session, TokenUsageRecord


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
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    created_at: datetime

    @classmethod
    def from_domain(cls, usage: TokenUsageRecord) -> "TokenUsageResponse":
        return cls(**usage.__dict__)


class TokenUsagesResponse(BaseModel):
    session_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    records: list[TokenUsageResponse]
