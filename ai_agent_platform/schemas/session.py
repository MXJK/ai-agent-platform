from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_agent_platform.domain import (
    ConversationContextUsage,
    Session,
    TokenUsageRecord,
    TokenUsageTotals,
    UserPreferences,
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
    title: str
    title_source: Literal["default", "auto", "manual"]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    workspace_id: str | None
    provider: str | None
    model: str | None
    thinking_level: str | None
    composer_mode: Literal["chat", "agent"]
    message_count: int
    last_message_preview: str | None

    @classmethod
    def from_domain(cls, session: Session) -> "SessionResponse":
        return cls(
            id=session.id,
            user_id=session.user_id,
            title=session.title,
            title_source=session.title_source,  # type: ignore[arg-type]
            created_at=session.created_at,
            updated_at=session.updated_at or session.created_at,
            archived_at=session.archived_at,
            workspace_id=session.workspace_id,
            provider=session.provider,
            model=session.model,
            thinking_level=session.thinking_level,
            composer_mode=session.composer_mode,  # type: ignore[arg-type]
            message_count=session.message_count,
            last_message_preview=session.last_message_preview,
        )


class SessionsResponse(BaseModel):
    sessions: list[SessionResponse]
    next_cursor: str | None = None


class SessionConfigurationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["fake", "openai", "deepseek", "anthropic", "google"] | None = None
    model: str | None = Field(default=None, min_length=1, max_length=128)
    thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    workspace_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    composer_mode: Literal["chat", "agent"] | None = None


class SessionPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None
    configuration: SessionConfigurationPatch | None = None
    save_configuration_as_default: bool = False

    @model_validator(mode="after")
    def require_change(self) -> "SessionPatchRequest":
        if not ({"title", "archived", "configuration"} & self.model_fields_set):
            raise ValueError("at least one session change is required")
        return self


class UserPreferencesResponse(BaseModel):
    user_id: str
    default_provider: str | None
    default_model: str | None
    default_thinking_level: str | None
    default_workspace_id: str | None
    default_composer_mode: Literal["chat", "agent"]
    last_active_session_id: str | None
    updated_at: datetime

    @classmethod
    def from_domain(
        cls, preferences: UserPreferences
    ) -> "UserPreferencesResponse":
        return cls(
            user_id=preferences.user_id,
            default_provider=preferences.default_provider,
            default_model=preferences.default_model,
            default_thinking_level=preferences.default_thinking_level,
            default_workspace_id=preferences.default_workspace_id,
            default_composer_mode=preferences.default_composer_mode,  # type: ignore[arg-type]
            last_active_session_id=preferences.last_active_session_id,
            updated_at=preferences.updated_at or datetime.now().astimezone(),
        )


class UserPreferencesPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_provider: Literal[
        "fake", "openai", "deepseek", "anthropic", "google"
    ] | None = None
    default_model: str | None = Field(default=None, min_length=1, max_length=128)
    default_thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    default_workspace_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    default_composer_mode: Literal["chat", "agent"] | None = None
    last_active_session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )

    @model_validator(mode="after")
    def require_change(self) -> "UserPreferencesPatchRequest":
        if not self.model_fields_set:
            raise ValueError("at least one preference change is required")
        return self


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
    budget_tokens: int = 0
    dropped_messages: int = 0
    truncated_messages: int = 0
    synchronous_compactions: int = 0
    summary_realigned: bool = False

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
