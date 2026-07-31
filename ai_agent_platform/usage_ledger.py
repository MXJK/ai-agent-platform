from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator
from uuid import uuid4

from ai_agent_platform.core import Settings
from ai_agent_platform.domain import TokenUsageRecord


@dataclass(frozen=True)
class UsageContext:
    session_id: str | None = None
    workspace_id: str | None = None
    operation: str = "llm"
    resource_id: str | None = None


@dataclass(frozen=True)
class TokenBudgetScopeStatus:
    limit: int
    used: int
    remaining: int | None
    exceeded: bool


@dataclass(frozen=True)
class TokenBudgetStatus:
    action: str
    session: TokenBudgetScopeStatus
    workspace: TokenBudgetScopeStatus


@dataclass(frozen=True)
class UsageAuthorization:
    requested_provider: str
    requested_model: str
    provider: str
    model: str
    input_tokens: int
    max_output_tokens: int
    input_count_method: str
    budget_decision: str
    budget_reason: str | None
    budget: TokenBudgetStatus


class TokenBudgetExceededError(RuntimeError):
    def __init__(self, status: TokenBudgetStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


_USAGE_CONTEXT: ContextVar[UsageContext] = ContextVar(
    "model_usage_context",
    default=UsageContext(),
)
_INHERIT = object()


@contextmanager
def model_usage_scope(
    *,
    session_id: str | None | object = _INHERIT,
    workspace_id: str | None | object = _INHERIT,
    operation: str | object = _INHERIT,
    resource_id: str | None | object = _INHERIT,
) -> Iterator[UsageContext]:
    current = _USAGE_CONTEXT.get()
    scoped = UsageContext(
        session_id=(
            current.session_id if session_id is _INHERIT else session_id
        ),
        workspace_id=(
            current.workspace_id if workspace_id is _INHERIT else workspace_id
        ),
        operation=(
            current.operation if operation is _INHERIT else str(operation)
        ),
        resource_id=(
            current.resource_id if resource_id is _INHERIT else resource_id
        ),
    )
    token = _USAGE_CONTEXT.set(scoped)
    try:
        yield scoped
    finally:
        _USAGE_CONTEXT.reset(token)


def current_model_usage_context() -> UsageContext:
    return _USAGE_CONTEXT.get()


class UsageLedgerService:
    def __init__(self, repository, settings: Settings) -> None:
        self._repository = repository
        self._settings = settings

    def authorize(
        self,
        *,
        requested_provider: str,
        requested_model: str,
        input_tokens: int,
        max_output_tokens: int,
        input_count_method: str,
    ) -> UsageAuthorization:
        context = current_model_usage_context()
        budget = self.get_budget_status(
            session_id=context.session_id,
            workspace_id=context.workspace_id,
        )
        exceeded: list[str] = []
        if (
            budget.session.limit > 0
            and budget.session.used + input_tokens >= budget.session.limit
        ):
            exceeded.append("session")
        if (
            budget.workspace.limit > 0
            and budget.workspace.used + input_tokens >= budget.workspace.limit
        ):
            exceeded.append("workspace")

        provider = requested_provider
        model = requested_model
        decision = "allowed"
        reason = None
        authorized_max_output_tokens = max_output_tokens
        if exceeded:
            reason = (
                f"{' and '.join(exceeded)} token budget would be exceeded "
                "before model output"
            )
            if self._settings.token_budget_action == "reject":
                raise TokenBudgetExceededError(budget, reason)
            provider = self._settings.token_budget_fallback_provider or ""
            model = self._settings.token_budget_fallback_model or ""
            decision = "downgraded"
        elif self._settings.token_budget_action == "reject":
            output_allowances = [
                scope.limit - scope.used - input_tokens
                for scope in (budget.session, budget.workspace)
                if scope.limit > 0
            ]
            if output_allowances:
                authorized_max_output_tokens = min(
                    max_output_tokens,
                    min(output_allowances),
                )

        return UsageAuthorization(
            requested_provider=requested_provider,
            requested_model=requested_model,
            provider=provider,
            model=model,
            input_tokens=max(0, input_tokens),
            max_output_tokens=authorized_max_output_tokens,
            input_count_method=input_count_method,
            budget_decision=decision,
            budget_reason=reason,
            budget=budget,
        )

    def record(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        thoughts_tokens: int = 0,
        requested_provider: str | None = None,
        requested_model: str | None = None,
        input_count_method: str = "provider_usage",
        budget_decision: str = "allowed",
        record_id: str | None = None,
        context: UsageContext | None = None,
    ) -> TokenUsageRecord:
        scope = context or current_model_usage_context()
        return self._repository.add_token_usage(
            session_id=scope.session_id,
            workspace_id=scope.workspace_id,
            provider=provider,
            model=model,
            input_tokens=max(0, input_tokens),
            output_tokens=max(0, output_tokens),
            thoughts_tokens=max(0, thoughts_tokens),
            record_id=record_id or f"usage_{uuid4().hex[:16]}",
            operation=scope.operation,
            resource_id=scope.resource_id,
            requested_provider=requested_provider,
            requested_model=requested_model,
            input_count_method=input_count_method,
            budget_decision=budget_decision,
        )

    def list_session(self, session_id: str) -> list[TokenUsageRecord]:
        return self._repository.list_token_usage(session_id)

    def list_workspace(self, workspace_id: str) -> list[TokenUsageRecord]:
        return self._repository.list_workspace_token_usage(workspace_id)

    def list_all(self) -> list[TokenUsageRecord]:
        list_records = getattr(self._repository, "list_all_token_usage", None)
        if callable(list_records):
            return list_records()
        return [
            record
            for session in self._repository.list_sessions()
            for record in self._repository.list_token_usage(session.id)
        ]

    def get_budget_status(
        self,
        *,
        session_id: str | None,
        workspace_id: str | None,
    ) -> TokenBudgetStatus:
        session_used = (
            sum(record.total_tokens for record in self.list_session(session_id))
            if session_id is not None
            else 0
        )
        workspace_used = (
            sum(
                record.total_tokens
                for record in self.list_workspace(workspace_id)
            )
            if workspace_id is not None
            else 0
        )
        return TokenBudgetStatus(
            action=self._settings.token_budget_action,
            session=_scope_status(
                limit=(
                    self._settings.session_token_budget
                    if session_id is not None
                    else 0
                ),
                used=session_used,
            ),
            workspace=_scope_status(
                limit=(
                    self._settings.workspace_token_budget
                    if workspace_id is not None
                    else 0
                ),
                used=workspace_used,
            ),
        )


def _scope_status(*, limit: int, used: int) -> TokenBudgetScopeStatus:
    return TokenBudgetScopeStatus(
        limit=limit,
        used=used,
        remaining=max(0, limit - used) if limit > 0 else None,
        exceeded=limit > 0 and used >= limit,
    )


__all__ = [
    "TokenBudgetExceededError",
    "TokenBudgetScopeStatus",
    "TokenBudgetStatus",
    "UsageAuthorization",
    "UsageContext",
    "UsageLedgerService",
    "current_model_usage_context",
    "model_usage_scope",
]
