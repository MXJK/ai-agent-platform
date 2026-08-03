from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ai_agent_platform.domain import (
    ConversationContextUsage,
    Session,
    TokenUsageRecord,
    TokenUsageTotals,
)
from ai_agent_platform.usage_ledger import (
    TokenBudgetScopeStatus,
    TokenBudgetStatus,
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
    session_id: str | None
    workspace_id: str | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int
    total_tokens: int
    created_at: datetime
    operation: str
    resource_id: str | None
    requested_provider: str | None
    requested_model: str | None
    input_count_method: str
    budget_decision: str

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


class TokenUsageOperationResponse(TokenUsageTotalsResponse):
    operation: str


class TokenBudgetScopeResponse(BaseModel):
    limit: int
    used: int
    remaining: int | None
    exceeded: bool

    @classmethod
    def from_domain(
        cls,
        status: TokenBudgetScopeStatus,
    ) -> "TokenBudgetScopeResponse":
        return cls(**status.__dict__)


class TokenBudgetStatusResponse(BaseModel):
    action: str
    session: TokenBudgetScopeResponse
    workspace: TokenBudgetScopeResponse

    @classmethod
    def from_domain(
        cls,
        status: TokenBudgetStatus,
    ) -> "TokenBudgetStatusResponse":
        return cls(
            action=status.action,
            session=TokenBudgetScopeResponse.from_domain(status.session),
            workspace=TokenBudgetScopeResponse.from_domain(status.workspace),
        )


class TokenUsagesResponse(BaseModel):
    session_id: str
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int
    total_tokens: int
    record_count: int
    context: ContextTokenUsageResponse
    workspaces: list[WorkspaceTokenBreakdownResponse]
    operations: list[TokenUsageOperationResponse]
    budget: TokenBudgetStatusResponse | None
    records: list[TokenUsageResponse]
