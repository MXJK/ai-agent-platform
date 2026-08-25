from __future__ import annotations

from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, replace
import hashlib
import json
import re
import time
from typing import Any, Callable, Iterable, Iterator, Literal, Mapping, Protocol, cast
from uuid import uuid4

import httpx

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.model_router import (
    ModelCapabilities,
    ModelConfig,
    ModelRouteTrace,
    ModelRouter,
    ProviderHealthManager,
    RoutingPolicy,
    RoutingRequirements,
    load_model_catalog,
)
from ai_agent_platform.integrations.tools import ToolCall, ToolSpec
from ai_agent_platform.model_registry.selection import current_model_selection
from ai_agent_platform.token_counting import estimate_text_tokens
from ai_agent_platform.usage_ledger import (
    TokenBudgetExceededError,
    current_model_usage_context,
)


LLMEventType = Literal["route", "delta", "usage", "done"]


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.thoughts_tokens


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    usage: LLMUsage | None = None
    provider: str | None = None
    route_trace: dict[str, Any] | None = None


@dataclass(frozen=True)
class ContextBudget:
    """Input-context allowance derived from the model that will serve the turn."""

    window_tokens: int
    reserved_output_tokens: int
    input_tokens: int
    provider: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class LLMRequestPlan:
    requested_provider: str
    requested_model: str
    provider: str
    model: str
    input_tokens: int
    max_output_tokens: int
    input_count_method: str
    budget_decision: str = "allowed"
    budget_reason: str | None = None
    usage_context: Any = None
    candidate: ModelConfig | None = None
    fallback_candidates: tuple[ModelConfig, ...] = ()
    route_trace: ModelRouteTrace | None = None
    requirements: RoutingRequirements | None = None


@dataclass(frozen=True)
class LLMToolDecision:
    text: str
    tool_calls: list[ToolCall]
    model: str
    provider: str
    stop_reason: str
    usage: LLMUsage | None = None
    provider_items: list[dict[str, Any]] | None = None
    route_trace: dict[str, Any] | None = None


@dataclass
class LLMUsageAccumulator:
    input_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0

    def add(self, usage: LLMUsage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.thoughts_tokens += usage.thoughts_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.thoughts_tokens


_LLM_USAGE_ACCUMULATOR: ContextVar[LLMUsageAccumulator | None] = ContextVar(
    "llm_usage_accumulator",
    default=None,
)
_PROVIDER_ERROR_DETAIL_MAX_CHARS = 400
_PROVIDER_ERROR_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|authorization|token|password|secret)\b"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_PROVIDER_ERROR_BEARER = re.compile(
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"
)
_PROVIDER_ERROR_KEY = re.compile(
    r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{12,}\b"
)


@contextmanager
def collect_llm_usage() -> Iterator[LLMUsageAccumulator]:
    accumulator = LLMUsageAccumulator()
    token = _LLM_USAGE_ACCUMULATOR.set(accumulator)
    try:
        yield accumulator
    finally:
        _LLM_USAGE_ACCUMULATOR.reset(token)


@dataclass(frozen=True)
class LLMStreamEvent:
    type: LLMEventType
    text: str = ""
    usage: LLMUsage | None = None
    provider: str | None = None
    model: str | None = None
    route_trace: dict[str, Any] | None = None


class LLMProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        code: str = "llm_provider_error",
        finish_reason: str | None = None,
        route_trace: dict[str, Any] | None = None,
        usage: LLMUsage | None = None,
        tool_argument_chars: int | None = None,
        json_error_position: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.code = code
        self.finish_reason = finish_reason
        self.route_trace = route_trace
        self.usage = usage
        self.tool_argument_chars = tool_argument_chars
        self.json_error_position = json_error_position


class LLMProviderAdapter(Protocol):
    """Optional provider boundary used by deterministic tests and extensions."""

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        thinking_level: str | None,
    ) -> Iterable[LLMStreamEvent]: ...

    def decide_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        model: str,
    ) -> LLMToolDecision: ...


class LLMClient:
    """Streams text from Google, OpenAI, Anthropic, or a local fake provider."""

    def __init__(
        self,
        settings: Settings,
        usage_ledger=None,
        *,
        model_router: ModelRouter | None = None,
        provider_adapters: Mapping[str, LLMProviderAdapter] | None = None,
        credential_resolver: Callable[[str], str | None] | None = None,
        model_access_resolver: Callable[[str, str], bool] | None = None,
        model_observer: Any = None,
    ) -> None:
        self._settings = settings
        self._usage_ledger = usage_ledger
        self._provider_adapters = dict(provider_adapters or {})
        self._credential_resolver = credential_resolver
        self._model_access_resolver = model_access_resolver
        self._model_observer = model_observer
        catalog = list(
            load_model_catalog(
                settings.llm_model_catalog_json,
                default_provider=settings.llm_provider,
                default_model=settings.llm_model,
                default_context_window_tokens=(
                    settings.llm_model_context_window_tokens
                ),
            )
        )
        if (
            settings.token_budget_fallback_provider
            and settings.token_budget_fallback_model
            and not any(
                item.provider == settings.token_budget_fallback_provider
                and item.model == settings.token_budget_fallback_model
                for item in catalog
            )
        ):
            real_provider = settings.token_budget_fallback_provider != "fake"
            catalog.append(
                ModelConfig(
                    provider=settings.token_budget_fallback_provider,
                    model=settings.token_budget_fallback_model,
                    context_window_tokens=(
                        settings.llm_model_context_window_tokens
                    ),
                    capabilities=ModelCapabilities(
                        tool_calling=real_provider,
                        structured_output=real_provider,
                    ),
                    quality_score=0.0,
                    latency_ms=2000,
                )
            )
        self._model_router = model_router or ModelRouter(
            catalog,
            default_policy=cast(RoutingPolicy, settings.llm_routing_policy),
            health=ProviderHealthManager(
                failure_threshold=settings.llm_circuit_failure_threshold,
                recovery_timeout_seconds=(
                    settings.llm_circuit_recovery_timeout_seconds
                ),
                error_window_size=settings.llm_circuit_error_window_size,
                error_rate_min_requests=(
                    settings.llm_circuit_error_rate_min_requests
                ),
                error_rate_threshold=(
                    settings.llm_circuit_error_rate_threshold
                ),
            ),
        )

    @property
    def model_router(self) -> ModelRouter:
        return self._model_router

    def set_usage_ledger(self, usage_ledger) -> None:
        self._usage_ledger = usage_ledger

    def set_model_registry(self, registry: Any) -> None:
        self._credential_resolver = registry.credential_for_provider
        self._model_access_resolver = registry.is_model_available
        self._model_observer = registry

    def replace_model_catalog(self, models: Iterable[ModelConfig]) -> None:
        self._model_router.replace_models(tuple(models))

    def test_connection(self, provider: str, model: str) -> dict[str, Any]:
        started_at = time.perf_counter()
        events = list(
            self.stream_chat(
                [{"role": "user", "content": "Reply with OK only."}],
                provider=provider,
                model=model,
            )
        )
        selected = next((event for event in events if event.type == "route"), None)
        return {
            "provider": selected.provider if selected else provider,
            "model": selected.model if selected else model,
            "status": "available",
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
        }

    @property
    def native_tool_calling_enabled(self) -> bool:
        return any(
            model.enabled
            and model.capabilities.tool_calling
            and model.provider != "fake"
            for model in self._model_router.models
        )

    def decide_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        provider: str | None = None,
        model: str | None = None,
        routing_policy: RoutingPolicy | None = None,
        structured_output: bool = False,
        min_context_tokens: int = 0,
        alias_tools: list[ToolSpec] | None = None,
        max_output_tokens: int | None = None,
        disable_tool_calls: bool = False,
    ) -> LLMToolDecision:
        (
            provider,
            model,
            routing_policy,
            preferred_provider,
            preferred_model,
            fallback_enabled,
        ) = self._effective_routing(provider, model, routing_policy)
        if provider is not None or model is not None:
            self._require_model_available(
                provider or self._settings.llm_provider,
                model or self._settings.llm_model,
            )
        requested_output_tokens = (
            max_output_tokens
            if max_output_tokens is not None
            else self._settings.llm_max_output_tokens
        )
        if requested_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        estimated_input_tokens = _estimate_tokens(_join_any_message_text(messages))
        complexity, complexity_reasons = _assess_task_complexity(
            _join_any_message_text(messages),
            estimated_input_tokens=estimated_input_tokens,
            tool_calling=True,
            structured_output=structured_output,
        )
        requirements = RoutingRequirements(
            tool_calling=True,
            structured_output=structured_output,
            min_context_tokens=max(
                min_context_tokens,
                estimated_input_tokens + 1,
            ),
            estimated_input_tokens=estimated_input_tokens,
            expected_output_tokens=requested_output_tokens,
            task_complexity=complexity,
            complexity_reasons=complexity_reasons,
        )
        candidates, trace = self._route_allowed(
            requirements,
            policy=routing_policy,
            provider=provider,
            model=model,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            fallback_enabled=fallback_enabled,
        )
        if not candidates:
            raise LLMProviderError(
                "no healthy model satisfies the routing requirements",
                code="no_eligible_model",
                route_trace=trace.to_dict(),
            )
        aliases = _tool_aliases(alias_tools if alias_tools is not None else tools)
        last_error: LLMProviderError | None = None
        for routed_candidate in candidates:
            try:
                request_plan = self._prepare_tool_candidate(
                    messages,
                    tools,
                    aliases,
                    candidate=routed_candidate,
                    requirements=requirements,
                    trace=trace,
                    requested_max_output_tokens=requested_output_tokens,
                )
            except LLMProviderError as exc:
                if exc.code == "token_budget_exceeded":
                    exc.route_trace = trace.to_dict()
                    raise
                last_error = exc
                self._model_router.record_failure(
                    trace,
                    routed_candidate,
                    code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                    after_stream_start=False,
                )
                continue
            trace = request_plan.route_trace or trace
            candidate = request_plan.candidate or routed_candidate
            candidate_error: LLMProviderError | None = None
            attempt_messages = list(messages)
            for attempt in range(self._settings.llm_max_retries + 1):
                attempt_started = time.perf_counter()
                try:
                    if attempt > 0:
                        request_plan = self._prepare_tool_candidate(
                            attempt_messages,
                            tools,
                            aliases,
                            candidate=candidate,
                            requirements=requirements,
                            trace=trace,
                            requested_max_output_tokens=requested_output_tokens,
                        )
                        trace = request_plan.route_trace or trace
                        candidate = request_plan.candidate or candidate
                    decision = self._decide_tools_once(
                        candidate,
                        attempt_messages,
                        tools,
                        aliases,
                        max_output_tokens=request_plan.max_output_tokens,
                        disable_tool_calls=disable_tool_calls,
                    )
                    self._observe_success(
                        candidate,
                        started_at=attempt_started,
                        ttft_ms=None,
                    )
                    usage = decision.usage or LLMUsage(
                        input_tokens=request_plan.input_tokens,
                        output_tokens=0,
                    )
                    if usage.input_tokens <= 0 or candidate.provider == "fake":
                        usage = replace(
                            usage,
                            input_tokens=request_plan.input_tokens,
                        )
                        decision = replace(decision, usage=usage)
                    self._record_request_usage(request_plan, usage)
                    self._model_router.record_success(trace, candidate)
                    accumulator = _LLM_USAGE_ACCUMULATOR.get()
                    if accumulator is not None:
                        accumulator.add(usage)
                    return replace(
                        decision,
                        provider=candidate.provider,
                        model=candidate.model,
                        route_trace=trace.to_dict(),
                    )
                except LLMProviderError as exc:
                    self._record_failed_tool_usage(request_plan, exc, candidate)
                    self._observe_failure(
                        candidate,
                        started_at=attempt_started,
                        error=str(exc),
                    )
                    candidate_error = exc
                    if exc.code == "context_overflow":
                        break
                    if (
                        not exc.retryable
                        or attempt >= self._settings.llm_max_retries
                    ):
                        break
                    attempt_messages = _tool_retry_messages(
                        messages,
                        exc,
                        attempt=attempt + 2,
                    )
                    time.sleep(min(0.2 * (2**attempt), 2.0))
            assert candidate_error is not None
            last_error = candidate_error
            self._model_router.record_failure(
                trace,
                candidate,
                code=candidate_error.code,
                message=str(candidate_error),
                retryable=candidate_error.retryable,
                after_stream_start=False,
            )
            if candidate_error.code == "context_overflow":
                candidate_error.route_trace = trace.to_dict()
                raise candidate_error
            if request_plan.budget_decision == "downgraded":
                break
        assert last_error is not None
        last_error.route_trace = trace.to_dict()
        raise last_error

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        routing_policy: RoutingPolicy | None = None,
        structured_output: bool = False,
        min_context_tokens: int = 0,
        request_plan: LLMRequestPlan | None = None,
    ) -> Iterable[LLMStreamEvent]:
        plan = request_plan or self.prepare_chat_request(
            messages,
            provider=provider,
            model=model,
            routing_policy=routing_policy,
            structured_output=structured_output,
            min_context_tokens=min_context_tokens,
        )
        trace = plan.route_trace
        candidate = plan.candidate
        if trace is None or candidate is None:
            requirements = plan.requirements or RoutingRequirements(
                min_context_tokens=plan.input_tokens + plan.max_output_tokens,
                estimated_input_tokens=plan.input_tokens,
                expected_output_tokens=plan.max_output_tokens,
            )
            candidates, trace = self._route_allowed(
                requirements,
                provider=plan.provider,
                model=plan.model,
            )
            if not candidates:
                raise LLMProviderError(
                    "prepared model is no longer eligible",
                    code="no_eligible_model",
                    route_trace=trace.to_dict(),
                )
            candidate = candidates[0]
            plan = replace(
                plan,
                candidate=candidate,
                route_trace=trace,
                requirements=requirements,
            )

        last_error: LLMProviderError | None = None
        fallback_candidates = list(plan.fallback_candidates)
        while True:
            candidate_error: LLMProviderError | None = None
            for attempt in range(self._settings.llm_max_retries + 1):
                pending_events: list[LLMStreamEvent] = []
                stream_started = False
                attempt_started = time.perf_counter()
                first_delta_ms: int | None = None
                observation_recorded = False
                latest_usage: LLMUsage | None = None
                usage_recorded = False
                try:
                    stream_factory = self._stream_factory(
                        candidate.provider,
                        candidate.model,
                        thinking_level,
                        plan.max_output_tokens,
                    )
                    for raw_event in stream_factory(messages):
                        if raw_event.type == "route":
                            continue
                        event_usage = raw_event.usage
                        if raw_event.type == "usage" and event_usage is not None:
                            latest_usage = LLMUsage(
                                input_tokens=(
                                    event_usage.input_tokens
                                    if event_usage.input_tokens > 0
                                    else plan.input_tokens
                                ),
                                output_tokens=event_usage.output_tokens,
                                thoughts_tokens=event_usage.thoughts_tokens,
                            )
                        event = LLMStreamEvent(
                            type=raw_event.type,
                            text=raw_event.text,
                            usage=latest_usage if raw_event.type == "usage" else event_usage,
                            provider=candidate.provider,
                            model=candidate.model,
                        )
                        is_done = event.type == "done"
                        if is_done:
                            latest_usage = latest_usage or LLMUsage(
                                input_tokens=plan.input_tokens,
                                output_tokens=0,
                            )
                            self._record_request_usage(plan, latest_usage)
                            accumulator = _LLM_USAGE_ACCUMULATOR.get()
                            if accumulator is not None:
                                accumulator.add(latest_usage)
                            usage_recorded = True
                            self._model_router.record_success(
                                trace,
                                candidate,
                            )
                            self._observe_success(
                                candidate,
                                started_at=attempt_started,
                                ttft_ms=first_delta_ms,
                            )
                            observation_recorded = True
                        if event.type == "delta" and event.text and not stream_started:
                            stream_started = True
                            first_delta_ms = int(
                                (time.perf_counter() - attempt_started) * 1000
                            )
                            self._model_router.mark_selected(trace, candidate)
                            yield self._route_event(trace, candidate)
                            yield from pending_events
                            pending_events.clear()
                        if stream_started:
                            yield event
                        else:
                            pending_events.append(event)
                        if is_done:
                            if not stream_started:
                                yield self._route_event(trace, candidate)
                                yield from pending_events
                            return

                    if not usage_recorded:
                        latest_usage = latest_usage or LLMUsage(
                            input_tokens=plan.input_tokens,
                            output_tokens=0,
                        )
                        self._record_request_usage(plan, latest_usage)
                        accumulator = _LLM_USAGE_ACCUMULATOR.get()
                        if accumulator is not None:
                            accumulator.add(latest_usage)
                    self._model_router.record_success(trace, candidate)
                    if not observation_recorded:
                        self._observe_success(
                            candidate,
                            started_at=attempt_started,
                            ttft_ms=first_delta_ms,
                        )
                    if not stream_started:
                        yield self._route_event(trace, candidate)
                        yield from pending_events
                    return
                except LLMProviderError as exc:
                    self._observe_failure(
                        candidate,
                        started_at=attempt_started,
                        error=str(exc),
                    )
                    candidate_error = exc
                    if stream_started:
                        if not usage_recorded:
                            partial_usage = latest_usage or LLMUsage(
                                input_tokens=plan.input_tokens,
                                output_tokens=0,
                            )
                            self._record_request_usage(plan, partial_usage)
                            accumulator = _LLM_USAGE_ACCUMULATOR.get()
                            if accumulator is not None:
                                accumulator.add(partial_usage)
                        self._model_router.record_failure(
                            trace,
                            candidate,
                            code=exc.code,
                            message=str(exc),
                            retryable=exc.retryable,
                            after_stream_start=True,
                        )
                        exc.route_trace = trace.to_dict()
                        raise
                    if latest_usage is not None and not usage_recorded:
                        self._record_request_usage(plan, latest_usage)
                        accumulator = _LLM_USAGE_ACCUMULATOR.get()
                        if accumulator is not None:
                            accumulator.add(latest_usage)
                    if (
                        not exc.retryable
                        or attempt >= self._settings.llm_max_retries
                    ):
                        break
                    time.sleep(min(0.2 * (2**attempt), 2.0))
                    try:
                        plan = self._prepare_chat_candidate(
                            messages,
                            candidate=candidate,
                            requirements=plan.requirements or trace.requirements,
                            trace=trace,
                            fallback_candidates=tuple(fallback_candidates),
                            usage_context=plan.usage_context,
                        )
                        trace = plan.route_trace or trace
                        candidate = plan.candidate or candidate
                    except LLMProviderError as prepare_error:
                        candidate_error = prepare_error
                        break

            assert candidate_error is not None
            last_error = candidate_error
            self._model_router.record_failure(
                trace,
                candidate,
                code=candidate_error.code,
                message=str(candidate_error),
                retryable=candidate_error.retryable,
                after_stream_start=False,
            )
            if (
                plan.budget_decision == "downgraded"
                or candidate_error.code == "token_budget_exceeded"
            ):
                break
            next_plan: LLMRequestPlan | None = None
            while fallback_candidates:
                routed_candidate = fallback_candidates.pop(0)
                try:
                    next_plan = self._prepare_chat_candidate(
                        messages,
                        candidate=routed_candidate,
                        requirements=plan.requirements or trace.requirements,
                        trace=trace,
                        fallback_candidates=tuple(fallback_candidates),
                        usage_context=plan.usage_context,
                    )
                    break
                except LLMProviderError as exc:
                    last_error = exc
                    self._model_router.record_failure(
                        trace,
                        routed_candidate,
                        code=exc.code,
                        message=str(exc),
                        retryable=exc.retryable,
                        after_stream_start=False,
                    )
                    if exc.code == "token_budget_exceeded":
                        fallback_candidates.clear()
                        break
            if next_plan is None:
                break
            plan = next_plan
            trace = plan.route_trace or trace
            candidate = plan.candidate or routed_candidate

        if last_error is not None:
            last_error.route_trace = trace.to_dict()
            raise last_error

    def finalize_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        reason: str,
        tools: list[ToolSpec] | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMToolDecision:
        """Produce one final, text-only turn over the complete tool transcript.

        The transcript still carries provider tool blocks, and providers reject
        those when the request declares no tools, so the definitions stay in the
        request and the provider's own tool choice forbids calling them.
        """
        final_tools = list(tools or [])
        transcript = (
            list(messages)
            if final_tools
            else _text_only_tool_transcript(messages)
        )
        final_messages = transcript + [
            {
                "role": "system",
                "content": (
                    "Tool execution is now closed. Return the best grounded final "
                    "answer from the observed results. State incomplete work and "
                    f"the stopping reason explicitly. Stopping reason: {reason}."
                ),
            }
        ]
        decision = self.decide_tools(
            final_messages,
            final_tools,
            alias_tools=final_tools,
            max_output_tokens=max_output_tokens,
            disable_tool_calls=True,
        )
        if decision.tool_calls:
            raise LLMProviderError(
                "provider returned a tool call during text-only finalization",
                code="invalid_finalization_tool_call",
            )
        return decision

    def resolve_context_budget(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        routing_policy: RoutingPolicy | None = None,
        structured_output: bool = False,
        input_token_ratio: float | None = None,
    ) -> ContextBudget:
        """Report how much input context the model about to serve this turn allows.

        Context is assembled before the request is planned, so this resolves the
        candidate the router would pick without needing the assembled messages.
        Routing problems degrade to the configured default window: a budget is a
        cost guardrail, and it must never be the reason a turn cannot start.
        """
        ratio = (
            input_token_ratio
            if input_token_ratio is not None
            else self._settings.llm_context_input_token_ratio
        )
        reserved_output = self._settings.llm_max_output_tokens
        window = self._settings.llm_model_context_window_tokens
        resolved_provider: str | None = None
        resolved_model: str | None = None
        try:
            (
                provider,
                model,
                routing_policy,
                preferred_provider,
                preferred_model,
                fallback_enabled,
            ) = self._effective_routing(provider, model, routing_policy)
            candidates, _ = self._route_allowed(
                RoutingRequirements(
                    structured_output=structured_output,
                    min_context_tokens=reserved_output,
                    expected_output_tokens=reserved_output,
                ),
                policy=routing_policy,
                provider=provider,
                model=model,
                preferred_provider=preferred_provider,
                preferred_model=preferred_model,
                fallback_enabled=fallback_enabled,
            )
            if candidates:
                window = candidates[0].context_window_tokens
                resolved_provider = candidates[0].provider
                resolved_model = candidates[0].model
        except Exception:  # noqa: BLE001 - budget resolution is best effort
            pass
        return ContextBudget(
            window_tokens=window,
            reserved_output_tokens=reserved_output,
            input_tokens=max(0, int(window * ratio) - reserved_output),
            provider=resolved_provider,
            model=resolved_model,
        )

    def prepare_chat_request(
        self,
        messages: list[dict[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
        routing_policy: RoutingPolicy | None = None,
        structured_output: bool = False,
        min_context_tokens: int = 0,
    ) -> LLMRequestPlan:
        (
            provider,
            model,
            routing_policy,
            preferred_provider,
            preferred_model,
            fallback_enabled,
        ) = self._effective_routing(provider, model, routing_policy)
        if provider is not None or model is not None:
            self._require_model_available(
                provider or self._settings.llm_provider,
                model or self._settings.llm_model,
            )
        estimated_input_tokens = _estimate_tokens(_join_message_text(messages))
        complexity, complexity_reasons = _assess_task_complexity(
            _join_message_text(messages),
            estimated_input_tokens=estimated_input_tokens,
            tool_calling=False,
            structured_output=structured_output,
        )
        requirements = RoutingRequirements(
            structured_output=structured_output,
            min_context_tokens=max(
                min_context_tokens,
                estimated_input_tokens + self._settings.llm_max_output_tokens,
            ),
            estimated_input_tokens=estimated_input_tokens,
            expected_output_tokens=self._settings.llm_max_output_tokens,
            task_complexity=complexity,
            complexity_reasons=complexity_reasons,
        )
        candidates, trace = self._route_allowed(
            requirements,
            policy=routing_policy,
            provider=provider,
            model=model,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            fallback_enabled=fallback_enabled,
        )
        if not candidates:
            raise LLMProviderError(
                "no healthy model satisfies the routing requirements",
                code="no_eligible_model",
                route_trace=trace.to_dict(),
            )
        last_error: LLMProviderError | None = None
        for index, candidate in enumerate(candidates):
            try:
                return self._prepare_chat_candidate(
                    messages,
                    candidate=candidate,
                    requirements=requirements,
                    trace=trace,
                    fallback_candidates=candidates[index + 1 :],
                )
            except LLMProviderError as exc:
                if exc.code == "token_budget_exceeded":
                    exc.route_trace = trace.to_dict()
                    raise
                last_error = exc
                self._model_router.record_failure(
                    trace,
                    candidate,
                    code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                    after_stream_start=False,
                )
        assert last_error is not None
        last_error.route_trace = trace.to_dict()
        raise last_error

    def complete(self, prompt: str) -> LLMResponse:
        return self.complete_stream(prompt)

    def complete_stream(
        self,
        prompt: str,
        *,
        on_delta: Callable[[str], None] | None = None,
        delta_batch_chars: int = 128,
        delta_batch_seconds: float = 0.1,
    ) -> LLMResponse:
        if delta_batch_chars <= 0:
            raise ValueError("delta_batch_chars must be positive")
        if delta_batch_seconds < 0:
            raise ValueError("delta_batch_seconds must not be negative")
        text_parts: list[str] = []
        pending_delta: list[str] = []
        pending_chars = 0
        last_delta_at = time.monotonic()
        latest_usage: LLMUsage | None = None
        selected_provider = self._settings.llm_provider
        selected_model = self._settings.llm_model
        route_trace: dict[str, Any] | None = None
        messages = [{"role": "user", "content": prompt}]
        plan = self.prepare_chat_request(messages)
        selection = current_model_selection()

        def flush_delta() -> None:
            nonlocal pending_chars, last_delta_at
            if not pending_delta:
                return
            text = "".join(pending_delta)
            pending_delta.clear()
            pending_chars = 0
            last_delta_at = time.monotonic()
            if on_delta is not None:
                on_delta(text)

        for event in self.stream_chat(
            messages,
            thinking_level=(selection.thinking_level if selection else None),
            request_plan=plan,
        ):
            if event.type == "route":
                selected_provider = event.provider or selected_provider
                selected_model = event.model or selected_model
                route_trace = event.route_trace
            elif event.type == "delta":
                text_parts.append(event.text)
                pending_delta.append(event.text)
                pending_chars += len(event.text)
                if (
                    pending_chars >= delta_batch_chars
                    or time.monotonic() - last_delta_at >= delta_batch_seconds
                ):
                    flush_delta()
            elif event.type == "usage" and event.usage is not None:
                latest_usage = event.usage
        flush_delta()
        return LLMResponse(
            text="".join(text_parts),
            model=selected_model,
            usage=latest_usage,
            provider=selected_provider,
            route_trace=route_trace,
        )

    def _decide_tools_once(
        self,
        candidate: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        aliases: dict[str, str],
        *,
        max_output_tokens: int,
        disable_tool_calls: bool = False,
    ) -> LLMToolDecision:
        adapter = self._provider_adapters.get(candidate.provider)
        if adapter is not None:
            # Adapters have no tool-choice channel, so a text-only turn keeps
            # offering them nothing to call.
            return adapter.decide_tools(
                messages,
                [] if disable_tool_calls else tools,
                model=candidate.model,
            )
        if candidate.provider == "openai":
            return self._decide_openai_tools(
                messages,
                tools,
                aliases,
                candidate.model,
                max_output_tokens=max_output_tokens,
                disable_tool_calls=disable_tool_calls,
            )
        if candidate.provider == "deepseek":
            return self._decide_deepseek_tools(
                messages,
                tools,
                aliases,
                candidate.model,
                max_output_tokens=max_output_tokens,
                disable_tool_calls=disable_tool_calls,
            )
        if candidate.provider == "anthropic":
            return self._decide_anthropic_tools(
                messages,
                tools,
                aliases,
                candidate.model,
                max_output_tokens=max_output_tokens,
                disable_tool_calls=disable_tool_calls,
            )
        if candidate.provider == "google":
            return self._decide_google_tools(
                messages,
                tools,
                aliases,
                candidate.model,
                max_output_tokens=max_output_tokens,
                disable_tool_calls=disable_tool_calls,
            )
        if candidate.provider == "fake":
            return self._decide_fake_tools(messages, candidate.model)
        raise LLMProviderError(
            f"unsupported llm provider: {candidate.provider}",
            code="unsupported_llm_provider",
        )

    @staticmethod
    def _route_event(
        trace: ModelRouteTrace,
        candidate: ModelConfig,
    ) -> LLMStreamEvent:
        return LLMStreamEvent(
            type="route",
            provider=candidate.provider,
            model=candidate.model,
            route_trace=trace.to_dict(),
        )

    def _decide_fake_tools(
        self,
        messages: list[dict[str, Any]],
        model: str,
    ) -> LLMToolDecision:
        text = "fake model completed the native tool turn"
        usage = LLMUsage(
            input_tokens=_count_fake_text_tokens(
                _join_any_message_text(messages)
            ),
            output_tokens=_count_fake_text_tokens(text),
        )
        return LLMToolDecision(
            text=text,
            tool_calls=[],
            model=model,
            provider="fake",
            stop_reason="end_turn",
            usage=usage,
            provider_items=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
        )

    def _decide_openai_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        aliases: dict[str, str],
        model: str,
        *,
        max_output_tokens: int,
        disable_tool_calls: bool = False,
    ) -> LLMToolDecision:
        api_key = self._api_key("openai")
        if not api_key:
            raise LLMProviderError("OpenAI credential is not configured in model management")
        reverse_aliases = {registry_name: alias for alias, registry_name in aliases.items()}
        payload: dict[str, Any] = {
            "model": model,
            "input": _openai_tool_input(messages, reverse_aliases),
            "max_output_tokens": max_output_tokens,
        }
        if tools:
            payload.update({
                "tools": [
                {
                    "type": "function",
                    "name": reverse_aliases[spec.name],
                    "description": spec.description,
                    "parameters": spec.input_schema,
                }
                for spec in tools
                ],
                "tool_choice": "none" if disable_tool_calls else "auto",
                "parallel_tool_calls": not disable_tool_calls,
            })
        body = self._post_json(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
        )
        output = body.get("output", [])
        output = output if isinstance(output, list) else []
        usage = _usage_from_mapping(body.get("usage"))
        status = str(body.get("status") or "completed")
        incomplete_details = body.get("incomplete_details")
        incomplete_details = (
            incomplete_details if isinstance(incomplete_details, dict) else {}
        )
        finish_reason = (
            str(incomplete_details.get("reason") or "incomplete")
            if status == "incomplete"
            else status
        )
        calls: list[ToolCall] = []
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                calls.append(
                    ToolCall(
                        call_id=str(item.get("call_id") or f"tool_{uuid4().hex[:12]}"),
                        name=aliases.get(str(item.get("name") or ""), str(item.get("name") or "")),
                        arguments=_json_arguments(
                            item.get("arguments"),
                            finish_reason=finish_reason,
                            usage=usage,
                        ),
                        source="openai_native",
                    )
                )
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("type") in {
                        "output_text",
                        "refusal",
                    }:
                        text_parts.append(str(block.get("text") or block.get("refusal") or ""))
        if not calls:
            _raise_truncated_tool_turn(finish_reason, usage)
        return LLMToolDecision(
            text="".join(text_parts).strip(),
            tool_calls=calls,
            model=str(body.get("model") or model),
            provider="openai",
            stop_reason="tool_use" if calls else finish_reason,
            usage=usage,
            provider_items=[dict(item) for item in output if isinstance(item, dict)],
        )

    def _decide_anthropic_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        aliases: dict[str, str],
        model: str,
        *,
        max_output_tokens: int,
        disable_tool_calls: bool = False,
    ) -> LLMToolDecision:
        api_key = self._api_key("anthropic")
        if not api_key:
            raise LLMProviderError("Anthropic credential is not configured in model management")
        reverse_aliases = {registry_name: alias for alias, registry_name in aliases.items()}
        system = [
            str(message.get("content") or "")
            for message in messages
            if message.get("role") == "system"
        ]
        payload: dict[str, Any] = {
            "model": model,
            "messages": _anthropic_tool_messages(messages, reverse_aliases),
            "max_tokens": max_output_tokens,
        }
        if tools:
            payload.update({
                "tools": [
                {
                    "name": reverse_aliases[spec.name],
                    "description": spec.description,
                    "input_schema": spec.input_schema,
                }
                for spec in tools
                ],
                "tool_choice": (
                    {"type": "none"}
                    if disable_tool_calls
                    else {"type": "auto", "disable_parallel_tool_use": False}
                ),
            })
        if system:
            payload["system"] = "\n\n".join(system)
        body = self._post_json(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload=payload,
        )
        content = body.get("content", [])
        content = content if isinstance(content, list) else []
        calls: list[ToolCall] = []
        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        call_id=str(block.get("id") or f"tool_{uuid4().hex[:12]}"),
                        name=aliases.get(str(block.get("name") or ""), str(block.get("name") or "")),
                        arguments=dict(block.get("input") or {}),
                        source="anthropic_native",
                    )
                )
            elif block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
        usage = _usage_from_mapping(body.get("usage"))
        finish_reason = str(
            body.get("stop_reason") or ("tool_use" if calls else "end_turn")
        )
        if not calls:
            _raise_truncated_tool_turn(finish_reason, usage)
        return LLMToolDecision(
            text="".join(text_parts).strip(),
            tool_calls=calls,
            model=str(body.get("model") or model),
            provider="anthropic",
            stop_reason=finish_reason,
            usage=usage,
            provider_items=[dict(block) for block in content if isinstance(block, dict)],
        )

    def _decide_deepseek_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        aliases: dict[str, str],
        model: str,
        *,
        max_output_tokens: int,
        disable_tool_calls: bool = False,
    ) -> LLMToolDecision:
        api_key = self._api_key("deepseek")
        if not api_key:
            raise LLMProviderError("DeepSeek credential is not configured in model management")
        reverse_aliases = {
            registry_name: alias for alias, registry_name in aliases.items()
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": _deepseek_tool_messages(messages, reverse_aliases),
            "max_tokens": max_output_tokens,
            "stream": False,
        }
        if tools:
            payload.update({
                "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": reverse_aliases[spec.name],
                        "description": spec.description,
                        "parameters": spec.input_schema,
                    },
                }
                for spec in tools
                ],
                "tool_choice": "none" if disable_tool_calls else "auto",
            })
        body = self._post_json(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
        )
        choices = body.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else {}
        message = first.get("message") if isinstance(first, dict) else {}
        message = message if isinstance(message, dict) else {}
        finish_reason = str(
            first.get("finish_reason") or "stop"
        )
        usage = _chat_usage_from_mapping(body.get("usage"))
        calls: list[ToolCall] = []
        for item in message.get("tool_calls") or []:
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            function = function if isinstance(function, dict) else {}
            alias = str(function.get("name") or "")
            calls.append(
                ToolCall(
                    call_id=str(item.get("id") or f"tool_{uuid4().hex[:12]}"),
                    name=aliases.get(alias, alias),
                    arguments=_json_arguments(
                        function.get("arguments"),
                        finish_reason=finish_reason,
                        usage=usage,
                    ),
                    source="deepseek_native",
                )
            )
        if not calls:
            _raise_truncated_tool_turn(finish_reason, usage)
        return LLMToolDecision(
            text=str(message.get("content") or "").strip(),
            tool_calls=calls,
            model=str(body.get("model") or model),
            provider="deepseek",
            stop_reason=finish_reason,
            usage=usage,
            provider_items=[message] if message else [],
        )

    def _decide_google_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        aliases: dict[str, str],
        model: str,
        *,
        max_output_tokens: int,
        disable_tool_calls: bool = False,
    ) -> LLMToolDecision:
        api_key = self._api_key("google")
        if not api_key:
            raise LLMProviderError("Google credential is not configured in model management")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise LLMProviderError(
                "google-genai is not installed; run pip install google-genai"
            ) from exc

        reverse_aliases = {registry_name: alias for alias, registry_name in aliases.items()}
        declarations = [
            types.FunctionDeclaration(
                name=reverse_aliases[spec.name],
                description=spec.description,
                parameters_json_schema=spec.input_schema,
                response_json_schema=spec.output_schema,
            )
            for spec in tools
        ]
        config_kwargs: dict[str, Any] = {"max_output_tokens": max_output_tokens}
        if declarations:
            config_kwargs["tools"] = [
                types.Tool(function_declarations=declarations)
            ]
            if disable_tool_calls:
                config_kwargs["tool_config"] = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="NONE",
                    )
                )
        system_instruction = _google_system_instruction_any(messages)
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=max(1, int(self._settings.llm_timeout_seconds * 1000))
            ),
        )
        try:
            response = client.models.generate_content(
                model=model,
                contents=_google_tool_contents(messages, types, reverse_aliases),
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:
            if _is_timeout_exception(exc):
                raise LLMProviderError(
                    "llm provider request timed out",
                    retryable=True,
                    code="llm_timeout",
                ) from exc
            raise LLMProviderError(str(exc), retryable=True) from exc
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()

        candidates = getattr(response, "candidates", None) or []
        candidate = candidates[0] if candidates else None
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        calls: list[ToolCall] = []
        text_parts: list[str] = []
        for part in parts:
            function_call = getattr(part, "function_call", None)
            if function_call is not None and getattr(function_call, "name", None):
                alias = str(function_call.name)
                calls.append(
                    ToolCall(
                        call_id=str(
                            getattr(function_call, "id", None)
                            or f"tool_{uuid4().hex[:12]}"
                        ),
                        name=aliases.get(alias, alias),
                        arguments=dict(getattr(function_call, "args", None) or {}),
                        source="google_native",
                    )
                )
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                text_parts.append(text)
        provider_items: list[dict[str, Any]] = []
        if content is not None:
            dump = getattr(content, "model_dump", None)
            if callable(dump):
                provider_items.append(dump(mode="json", by_alias=False))
        finish_reason = _google_candidate_finish_reason(candidate)
        usage = _google_usage(getattr(response, "usage_metadata", None))
        if not calls:
            _raise_truncated_tool_turn(finish_reason, usage)
        return LLMToolDecision(
            text="".join(text_parts).strip(),
            tool_calls=calls,
            model=model,
            provider="google",
            stop_reason="tool_use" if calls else (finish_reason or "STOP"),
            usage=usage,
            provider_items=provider_items,
        )

    def _route_allowed(
        self,
        requirements: RoutingRequirements,
        *,
        policy: RoutingPolicy | None = None,
        provider: str | None = None,
        model: str | None = None,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
        fallback_enabled: bool = True,
    ) -> tuple[tuple[ModelConfig, ...], ModelRouteTrace]:
        route_plan = self._model_router.route(
            requirements,
            policy=policy,
            provider=provider,
            model=model,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            fallback_enabled=fallback_enabled,
        )
        original_first = route_plan.candidates[0] if route_plan.candidates else None
        original_reason = route_plan.trace.selection_reason
        allowed: list[ModelConfig] = []
        for candidate_trace in route_plan.trace.candidates:
            config = candidate_trace.config
            if (
                candidate_trace.eligible
                and not self._is_model_available(
                    config.provider,
                    config.model,
                )
            ):
                candidate_trace.eligible = False
                candidate_trace.rejection_reasons.append("model_unavailable")
                candidate_trace.rank = None
            if (
                candidate_trace.eligible
                and config.provider
                in {"openai", "deepseek", "anthropic", "google"}
                and config.provider not in self._provider_adapters
                and not self._api_key(config.provider)
            ):
                candidate_trace.eligible = False
                candidate_trace.rejection_reasons.append(
                    "provider_credentials_unavailable"
                )
                candidate_trace.rank = None
        for config in route_plan.candidates:
            if (
                self._is_model_available(config.provider, config.model)
                and (
                    config.provider
                    not in {"openai", "deepseek", "anthropic", "google"}
                    or config.provider in self._provider_adapters
                    or bool(self._api_key(config.provider))
                )
            ):
                allowed.append(config)
        for index, config in enumerate(allowed, start=1):
            item = next(
                trace_item
                for trace_item in route_plan.trace.candidates
                if trace_item.config.key == config.key
            )
            item.rank = index
        if allowed:
            if original_first is None or original_first.key != allowed[0].key:
                suffix = (
                    f"runtime availability filters selected {allowed[0].key} "
                    f"from {len(allowed)} candidate(s)"
                )
                route_plan.trace.selection_reason = (
                    f"{original_reason}; {suffix}" if original_reason else suffix
                )
        else:
            route_plan.trace.selection_reason = None
        return tuple(allowed), route_plan.trace

    def _effective_routing(
        self,
        provider: str | None,
        model: str | None,
        routing_policy: RoutingPolicy | None,
    ) -> tuple[
        str | None,
        str | None,
        RoutingPolicy | None,
        str | None,
        str | None,
        bool,
    ]:
        if provider is not None or model is not None:
            return provider, model, routing_policy, None, None, False
        selection = current_model_selection()
        if selection is None:
            return provider, model, routing_policy, None, None, True
        selected_policy = cast(RoutingPolicy, routing_policy or selection.routing_policy)
        if (
            selection.mode == "manual"
            and selection.preferred_provider
            and selection.preferred_model
        ):
            if not selection.fallback_enabled:
                return (
                    selection.preferred_provider,
                    selection.preferred_model,
                    selected_policy,
                    None,
                    None,
                    False,
                )
            return (
                None,
                None,
                selected_policy,
                selection.preferred_provider,
                selection.preferred_model,
                True,
            )
        return None, None, selected_policy, None, None, True

    def _prepare_chat_candidate(
        self,
        messages: list[dict[str, str]],
        *,
        candidate: ModelConfig,
        requirements: RoutingRequirements,
        trace: ModelRouteTrace,
        fallback_candidates: tuple[ModelConfig, ...],
        usage_context: Any = None,
    ) -> LLMRequestPlan:
        return self._authorize_candidate(
            candidate=candidate,
            requirements=requirements,
            trace=trace,
            fallback_candidates=fallback_candidates,
            usage_context=usage_context,
            count_tokens=lambda provider, model: self._count_input_tokens(
                messages,
                provider=provider,
                model=model,
            ),
        )

    def _prepare_tool_candidate(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        aliases: dict[str, str],
        *,
        candidate: ModelConfig,
        requirements: RoutingRequirements,
        trace: ModelRouteTrace,
        requested_max_output_tokens: int,
    ) -> LLMRequestPlan:
        return self._authorize_candidate(
            candidate=candidate,
            requirements=requirements,
            trace=trace,
            fallback_candidates=(),
            requested_max_output_tokens=requested_max_output_tokens,
            count_tokens=lambda provider, model: self._count_tool_input_tokens(
                messages,
                tools,
                aliases,
                provider=provider,
                model=model,
            ),
        )

    def _authorize_candidate(
        self,
        *,
        candidate: ModelConfig,
        requirements: RoutingRequirements,
        trace: ModelRouteTrace,
        fallback_candidates: tuple[ModelConfig, ...],
        count_tokens: Callable[[str, str], tuple[int, str]],
        usage_context: Any = None,
        requested_max_output_tokens: int | None = None,
    ) -> LLMRequestPlan:
        usage_context = usage_context or current_model_usage_context()
        self._require_model_available(candidate.provider, candidate.model)
        input_tokens, count_method = count_tokens(
            candidate.provider,
            candidate.model,
        )
        actual_candidate = candidate
        requested_output_tokens = (
            requested_max_output_tokens
            if requested_max_output_tokens is not None
            else self._settings.llm_max_output_tokens
        )
        max_output_tokens = _effective_model_output_limit(
            candidate,
            input_tokens=input_tokens,
            requested_output_tokens=requested_output_tokens,
        )
        budget_decision = "allowed"
        budget_reason: str | None = None
        if self._usage_ledger is not None:
            try:
                authorization = self._usage_ledger.authorize(
                    requested_provider=candidate.provider,
                    requested_model=candidate.model,
                    input_tokens=input_tokens,
                    max_output_tokens=max_output_tokens,
                    input_count_method=count_method,
                    context=usage_context,
                )
            except TokenBudgetExceededError as exc:
                raise LLMProviderError(
                    str(exc),
                    code="token_budget_exceeded",
                ) from exc
            max_output_tokens = authorization.max_output_tokens
            budget_decision = authorization.budget_decision
            budget_reason = authorization.budget_reason
            if (
                authorization.provider != candidate.provider
                or authorization.model != candidate.model
            ):
                self._require_model_available(
                    authorization.provider,
                    authorization.model,
                )
                input_tokens, count_method = count_tokens(
                    authorization.provider,
                    authorization.model,
                )
                target_candidate = next(
                    (
                        item
                        for item in self._model_router.models
                        if item.provider == authorization.provider
                        and item.model == authorization.model
                    ),
                    None,
                )
                if target_candidate is None:
                    raise LLMProviderError(
                        "budget downgrade target is not a catalog model",
                        code="budget_fallback_ineligible",
                    )
                target_max_output_tokens = _effective_model_output_limit(
                    target_candidate,
                    input_tokens=input_tokens,
                    requested_output_tokens=requested_output_tokens,
                )
                try:
                    authorization = self._usage_ledger.authorize(
                        requested_provider=candidate.provider,
                        requested_model=candidate.model,
                        input_tokens=input_tokens,
                        max_output_tokens=target_max_output_tokens,
                        input_count_method=count_method,
                        context=usage_context,
                    )
                except TokenBudgetExceededError as exc:
                    raise LLMProviderError(
                        str(exc),
                        code="token_budget_exceeded",
                    ) from exc
                max_output_tokens = authorization.max_output_tokens
                budget_decision = authorization.budget_decision
                budget_reason = authorization.budget_reason
                target_requirements = replace(
                    requirements,
                    estimated_input_tokens=input_tokens,
                    expected_output_tokens=max_output_tokens,
                    min_context_tokens=max(
                        requirements.min_context_tokens,
                        input_tokens + max_output_tokens,
                    ),
                )
                target_candidates, target_trace = self._route_allowed(
                    target_requirements,
                    provider=authorization.provider,
                    model=authorization.model,
                )
                if not target_candidates:
                    raise LLMProviderError(
                        "budget downgrade target is not a healthy capable catalog model",
                        code="budget_fallback_ineligible",
                        route_trace=target_trace.to_dict(),
                    )
                actual_candidate = target_candidates[0]
                trace = target_trace
                trace.requested_provider = candidate.provider
                trace.requested_model = candidate.model
                trace.selection_reason = (
                    "token budget downgraded the routed candidate from "
                    f"{candidate.key} to {actual_candidate.key} after capability, "
                    "registration, context, and health validation"
                )
                fallback_candidates = ()

        required_context = max(
            requirements.min_context_tokens,
            input_tokens + max_output_tokens,
        )
        if actual_candidate.context_window_tokens < required_context:
            raise LLMProviderError(
                f"model context window is smaller than {required_context} tokens",
                code="context_window_too_small",
            )
        trace.budget_decision = budget_decision
        trace.budget_reason = budget_reason
        trace.budget_requested_provider = candidate.provider
        trace.budget_requested_model = candidate.model
        trace.budget_actual_provider = actual_candidate.provider
        trace.budget_actual_model = actual_candidate.model
        return LLMRequestPlan(
            requested_provider=candidate.provider,
            requested_model=candidate.model,
            provider=actual_candidate.provider,
            model=actual_candidate.model,
            input_tokens=input_tokens,
            max_output_tokens=max_output_tokens,
            input_count_method=count_method,
            budget_decision=budget_decision,
            budget_reason=budget_reason,
            usage_context=usage_context,
            candidate=actual_candidate,
            fallback_candidates=fallback_candidates,
            route_trace=trace,
            requirements=requirements,
        )

    def _count_tool_input_tokens(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        aliases: dict[str, str],
        *,
        provider: str,
        model: str,
    ) -> tuple[int, str]:
        reverse_aliases = {
            registry_name: alias for alias, registry_name in aliases.items()
        }
        if provider in self._provider_adapters:
            serialized = _join_any_message_text(messages) + json.dumps(
                [spec.input_schema for spec in tools],
                ensure_ascii=False,
                sort_keys=True,
            )
            return _count_fake_text_tokens(serialized), "adapter_lexical_tokenizer"
        if provider == "fake":
            serialized = _join_any_message_text(messages) + json.dumps(
                [spec.input_schema for spec in tools],
                ensure_ascii=False,
                sort_keys=True,
            )
            return _count_fake_text_tokens(serialized), "fake_lexical_tokenizer"
        if provider == "deepseek":
            if not self._api_key("deepseek"):
                raise LLMProviderError("DeepSeek credential is not configured in model management")
            serialized = _join_any_message_text(messages) + json.dumps(
                [spec.input_schema for spec in tools],
                ensure_ascii=False,
                sort_keys=True,
            )
            return _estimate_tokens(serialized), "deepseek_preflight_estimate"
        if provider == "openai":
            api_key = self._api_key("openai")
            if not api_key:
                raise LLMProviderError("OpenAI credential is not configured in model management")
            payload = {
                "model": model,
                "input": _openai_tool_input(messages, reverse_aliases),
                "tools": [
                    {
                        "type": "function",
                        "name": reverse_aliases[spec.name],
                        "description": spec.description,
                        "parameters": spec.input_schema,
                    }
                    for spec in tools
                ],
            }
            body = self._post_json(
                "https://api.openai.com/v1/responses/input_tokens",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                payload=payload,
            )
            count = body.get("input_tokens")
            if not isinstance(count, int):
                raise LLMProviderError(
                    "OpenAI token count response is missing input_tokens",
                    code="token_count_failed",
                )
            return max(0, count), "openai_responses_input_tokens"
        if provider == "anthropic":
            api_key = self._api_key("anthropic")
            if not api_key:
                raise LLMProviderError("Anthropic credential is not configured in model management")
            system = [
                str(message.get("content") or "")
                for message in messages
                if message.get("role") == "system"
            ]
            payload: dict[str, Any] = {
                "model": model,
                "messages": _anthropic_tool_messages(
                    messages,
                    reverse_aliases,
                ),
                "tools": [
                    {
                        "name": reverse_aliases[spec.name],
                        "description": spec.description,
                        "input_schema": spec.input_schema,
                    }
                    for spec in tools
                ],
            }
            if system:
                payload["system"] = "\n\n".join(system)
            body = self._post_json(
                "https://api.anthropic.com/v1/messages/count_tokens",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                payload=payload,
            )
            count = body.get("input_tokens")
            if not isinstance(count, int):
                raise LLMProviderError(
                    "Anthropic token count response is missing input_tokens",
                    code="token_count_failed",
                )
            return max(0, count), "anthropic_messages_count_tokens"
        if provider == "google":
            api_key = self._api_key("google")
            if not api_key:
                raise LLMProviderError("Google credential is not configured in model management")
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:
                raise LLMProviderError(
                    "google-genai is not installed; run pip install google-genai"
                ) from exc
            declarations = [
                types.FunctionDeclaration(
                    name=reverse_aliases[spec.name],
                    description=spec.description,
                    parameters_json_schema=spec.input_schema,
                    response_json_schema=spec.output_schema,
                )
                for spec in tools
            ]
            system_instruction = _google_system_instruction_any(messages)
            count_contents = _google_tool_contents(
                messages,
                types,
                reverse_aliases,
            )
            count_context: list[str] = []
            if system_instruction:
                count_context.append(system_instruction)
            if tools:
                count_context.append(
                    json.dumps(
                        [
                            {
                                "name": reverse_aliases[spec.name],
                                "description": spec.description,
                                "parameters": spec.input_schema,
                                "response": spec.output_schema,
                            }
                            for spec in tools
                        ],
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            if count_context:
                # Gemini Developer API rejects both system_instruction and tools
                # in CountTokensConfig even though GenerateContentConfig accepts
                # them. Count the same text/schema as a leading content part so
                # preflight remains conservative without Enterprise-only fields.
                count_contents.insert(
                    0,
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text="\n\n".join(count_context))
                        ],
                    ),
                )
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=max(
                        1,
                        int(self._settings.llm_timeout_seconds * 1000),
                    )
                ),
            )
            try:
                response = client.models.count_tokens(
                    model=model,
                    contents=count_contents,
                    config=types.CountTokensConfig(),
                )
            except Exception as exc:
                raise LLMProviderError(
                    f"Gemini token count failed: {exc}",
                    retryable=True,
                    code="token_count_failed",
                ) from exc
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    with suppress(Exception):
                        close()
            count = getattr(response, "total_tokens", None)
            if not isinstance(count, int):
                raise LLMProviderError(
                    "Gemini token count response is missing total_tokens",
                    code="token_count_failed",
                )
            return max(0, count), "gemini_models_count_tokens"
        raise LLMProviderError(
            f"unsupported llm provider: {provider}",
            code="llm_provider_not_allowed",
        )

    def _require_model_available(self, provider: str, model: str) -> None:
        if not self._is_model_available(provider, model):
            registered_providers = {
                item.provider for item in self._model_router.models
            }
            code = (
                "llm_provider_not_allowed"
                if provider not in registered_providers
                else "llm_model_not_allowed"
            )
            raise LLMProviderError(
                f"model selection is not registered and enabled: {provider}:{model}",
                code=code,
            )

    def _is_model_available(self, provider: str, model: str) -> bool:
        if self._model_access_resolver is not None:
            return bool(self._model_access_resolver(provider, model))
        return any(
            item.provider == provider
            and item.model == model
            and item.enabled
            for item in self._model_router.models
        )

    def _api_key(self, provider: str) -> str | None:
        if self._credential_resolver is None:
            return None
        return self._credential_resolver(provider) or None

    def _count_input_tokens(
        self,
        messages: list[dict[str, str]],
        *,
        provider: str,
        model: str,
    ) -> tuple[int, str]:
        if provider in self._provider_adapters:
            return (
                _count_fake_message_tokens(messages),
                "adapter_lexical_tokenizer",
            )
        if provider == "fake":
            return _count_fake_message_tokens(messages), "fake_lexical_tokenizer"
        if provider == "openai":
            api_key = self._api_key("openai")
            if not api_key:
                raise LLMProviderError("OpenAI credential is not configured in model management")
            body = self._post_json(
                "https://api.openai.com/v1/responses/input_tokens",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                payload={"model": model, "input": messages},
            )
            count = body.get("input_tokens")
            if not isinstance(count, int):
                raise LLMProviderError(
                    "OpenAI token count response is missing input_tokens",
                    code="token_count_failed",
                )
            return max(0, count), "openai_responses_input_tokens"
        if provider == "deepseek":
            if not self._api_key("deepseek"):
                raise LLMProviderError("DeepSeek credential is not configured in model management")
            return (
                _estimate_tokens(_join_message_text(messages)),
                "deepseek_preflight_estimate",
            )
        if provider == "anthropic":
            api_key = self._api_key("anthropic")
            if not api_key:
                raise LLMProviderError("Anthropic credential is not configured in model management")
            system_messages = [
                message["content"]
                for message in messages
                if message["role"] == "system"
            ]
            payload: dict[str, Any] = {
                "model": model,
                "messages": [
                    message
                    for message in messages
                    if message["role"] in {"user", "assistant"}
                ],
            }
            if system_messages:
                payload["system"] = "\n\n".join(system_messages)
            body = self._post_json(
                "https://api.anthropic.com/v1/messages/count_tokens",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                payload=payload,
            )
            count = body.get("input_tokens")
            if not isinstance(count, int):
                raise LLMProviderError(
                    "Anthropic token count response is missing input_tokens",
                    code="token_count_failed",
                )
            return max(0, count), "anthropic_messages_count_tokens"
        if provider == "google":
            api_key = self._api_key("google")
            if not api_key:
                raise LLMProviderError("Google credential is not configured in model management")
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:
                raise LLMProviderError(
                    "google-genai is not installed; run pip install google-genai"
                ) from exc
            system_instruction = _google_system_instruction(messages)
            config = (
                types.CountTokensConfig(system_instruction=system_instruction)
                if system_instruction
                else None
            )
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=max(
                        1,
                        int(self._settings.llm_timeout_seconds * 1000),
                    )
                ),
            )
            try:
                response = client.models.count_tokens(
                    model=model,
                    contents=_google_contents(messages, types),
                    config=config,
                )
            except Exception as exc:
                raise LLMProviderError(
                    f"Gemini token count failed: {exc}",
                    retryable=True,
                    code="token_count_failed",
                ) from exc
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    with suppress(Exception):
                        close()
            count = getattr(response, "total_tokens", None)
            if not isinstance(count, int):
                raise LLMProviderError(
                    "Gemini token count response is missing total_tokens",
                    code="token_count_failed",
                )
            return max(0, count), "gemini_models_count_tokens"
        raise LLMProviderError(
            f"unsupported llm provider: {provider}",
            code="llm_provider_not_allowed",
        )

    def _record_request_usage(
        self,
        plan: LLMRequestPlan,
        usage: LLMUsage,
    ) -> None:
        if self._usage_ledger is None:
            return
        self._usage_ledger.record(
            provider=plan.provider,
            model=plan.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            thoughts_tokens=usage.thoughts_tokens,
            requested_provider=plan.requested_provider,
            requested_model=plan.requested_model,
            input_count_method=plan.input_count_method,
            budget_decision=plan.budget_decision,
            context=plan.usage_context,
        )

    def _record_failed_tool_usage(
        self,
        plan: LLMRequestPlan,
        error: LLMProviderError,
        candidate: ModelConfig,
    ) -> None:
        usage = error.usage
        if usage is None:
            return
        if usage.input_tokens <= 0 or candidate.provider == "fake":
            usage = replace(usage, input_tokens=plan.input_tokens)
            error.usage = usage
        self._record_request_usage(plan, usage)
        accumulator = _LLM_USAGE_ACCUMULATOR.get()
        if accumulator is not None:
            accumulator.add(usage)

    def _observe_success(
        self,
        candidate: ModelConfig,
        *,
        started_at: float,
        ttft_ms: int | None,
    ) -> None:
        if self._model_observer is None:
            return
        with suppress(Exception):
            self._model_observer.record_success(
                candidate.provider,
                candidate.model,
                total_latency_ms=int((time.perf_counter() - started_at) * 1000),
                ttft_ms=ttft_ms,
            )

    def _observe_failure(
        self,
        candidate: ModelConfig,
        *,
        started_at: float,
        error: str,
    ) -> None:
        if self._model_observer is None:
            return
        with suppress(Exception):
            self._model_observer.record_failure(
                candidate.provider,
                candidate.model,
                total_latency_ms=int((time.perf_counter() - started_at) * 1000),
                error=error,
            )

    def _post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._settings.llm_timeout_seconds)
            ) as client:
                response = client.post(url, headers=headers, json=payload)
                if response.status_code >= 400:
                    retryable = response.status_code in {408, 409, 429} or (
                        response.status_code >= 500
                    )
                    detail = _safe_provider_error_detail(response)
                    raise LLMProviderError(
                        f"llm provider returned HTTP {response.status_code}"
                        + (f": {detail}" if detail else ""),
                        retryable=retryable,
                        code=_http_error_code(
                            response.status_code,
                            detail=detail,
                        ),
                    )
                body = response.json()
        except httpx.TimeoutException as exc:
            raise LLMProviderError(
                "llm provider request timed out",
                retryable=True,
                code="llm_timeout",
            ) from exc
        except httpx.TransportError as exc:
            raise LLMProviderError(
                "llm provider network request failed",
                retryable=True,
                code="llm_transport_error",
            ) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMProviderError(
                "llm provider returned invalid JSON",
                code="llm_invalid_json",
            ) from exc
        if not isinstance(body, dict):
            raise LLMProviderError(
                "llm provider returned an invalid response object",
                code="llm_invalid_response",
            )
        return body

    def _stream_factory(
        self,
        provider: str,
        model: str,
        thinking_level: str | None,
        max_output_tokens: int,
    ):
        adapter = self._provider_adapters.get(provider)
        if adapter is not None:
            return lambda messages: adapter.stream_chat(
                messages,
                model=model,
                thinking_level=thinking_level,
            )
        if provider == "fake":
            return lambda messages: self._stream_fake(messages, model)
        if provider == "openai":
            return lambda messages: self._stream_openai(
                messages,
                model,
                max_output_tokens=max_output_tokens,
            )
        if provider == "deepseek":
            return lambda messages: self._stream_deepseek(
                messages,
                model,
                max_output_tokens=max_output_tokens,
            )
        if provider == "anthropic":
            return lambda messages: self._stream_anthropic(
                messages,
                model,
                max_output_tokens=max_output_tokens,
            )
        if provider == "google":
            return lambda messages: self._stream_google(
                messages,
                model,
                thinking_level=thinking_level,
                max_output_tokens=max_output_tokens,
            )
        raise LLMProviderError(f"unsupported llm provider: {provider}")

    def _stream_fake(
        self, messages: list[dict[str, str]], model: str
    ) -> Iterable[LLMStreamEvent]:
        user_text = messages[-1]["content"] if messages else ""
        answer = f"fake model reply to: {user_text}"
        for token in answer.split(" "):
            yield LLMStreamEvent(type="delta", text=f"{token} ")
        yield LLMStreamEvent(
            type="usage",
            usage=LLMUsage(
                input_tokens=_count_fake_message_tokens(messages),
                output_tokens=_count_fake_text_tokens(answer),
            ),
        )
        yield LLMStreamEvent(type="done")

    def _stream_openai(
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        max_output_tokens: int,
    ) -> Iterable[LLMStreamEvent]:
        api_key = self._api_key("openai")
        if not api_key:
            raise LLMProviderError("OpenAI credential is not configured in model management")

        payload = {
            "model": model,
            "input": messages,
            "max_output_tokens": max_output_tokens,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        yield from self._stream_http_sse(
            "https://api.openai.com/v1/responses",
            headers=headers,
            payload=payload,
            parser=_parse_openai_event,
        )

    def _stream_anthropic(
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        max_output_tokens: int,
    ) -> Iterable[LLMStreamEvent]:
        api_key = self._api_key("anthropic")
        if not api_key:
            raise LLMProviderError("Anthropic credential is not configured in model management")

        system_messages = [
            message["content"] for message in messages if message["role"] == "system"
        ]
        chat_messages = [
            message
            for message in messages
            if message["role"] in {"user", "assistant"}
        ]
        payload: dict[str, object] = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": max_output_tokens,
            "stream": True,
        }
        if system_messages:
            payload["system"] = "\n\n".join(system_messages)

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        yield from self._stream_http_sse(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            payload=payload,
            parser=_parse_anthropic_event,
        )

    def _stream_deepseek(
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        max_output_tokens: int,
    ) -> Iterable[LLMStreamEvent]:
        api_key = self._api_key("deepseek")
        if not api_key:
            raise LLMProviderError("DeepSeek credential is not configured in model management")
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        yield from self._stream_http_sse(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            parser=_parse_deepseek_event,
        )

    def _stream_google(
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        thinking_level: str | None = None,
        max_output_tokens: int,
    ) -> Iterable[LLMStreamEvent]:
        api_key = self._api_key("google")
        if not api_key:
            raise LLMProviderError("Google credential is not configured in model management")

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise LLMProviderError(
                "google-genai is not installed; run pip install google-genai"
            ) from exc

        system_instruction = _google_system_instruction(messages)
        config_kwargs: dict[str, object] = {
            "max_output_tokens": max_output_tokens
        }
        selected_thinking_level = thinking_level
        if selected_thinking_level is None and model.startswith("gemini-3"):
            selected_thinking_level = self._settings.llm_thinking_level
        if selected_thinking_level is not None:
            if not model.startswith("gemini-3"):
                raise LLMProviderError(
                    "thinking_level is only supported for Gemini 3 models",
                    code="unsupported_thinking_level",
                )
            try:
                thinking_level_value = getattr(
                    types.ThinkingLevel,
                    selected_thinking_level.upper(),
                )
            except (AttributeError, TypeError) as exc:
                raise LLMProviderError(
                    "google-genai >=2.14.0 is required for Gemini thinking_level",
                    code="google_sdk_upgrade_required",
                ) from exc
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=thinking_level_value
            )
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=max(1, int(self._settings.llm_timeout_seconds * 1000))
            ),
        )
        usage: LLMUsage | None = None
        finish_reason: str | None = None
        try:
            stream = client.models.generate_content_stream(
                model=model,
                contents=_google_contents(messages, types),
                config=types.GenerateContentConfig(**config_kwargs),
            )
            for chunk in stream:
                text = getattr(chunk, "text", "")
                if isinstance(text, str) and text:
                    yield LLMStreamEvent(type="delta", text=text)
                chunk_usage = _google_usage(getattr(chunk, "usage_metadata", None))
                if chunk_usage is not None:
                    usage = chunk_usage
                chunk_finish_reason = _google_finish_reason(chunk)
                if chunk_finish_reason is not None:
                    finish_reason = chunk_finish_reason
        except Exception as exc:
            if _is_timeout_exception(exc):
                raise LLMProviderError(
                    "llm provider request timed out",
                    retryable=True,
                    code="llm_timeout",
                ) from exc
            raise LLMProviderError(str(exc), retryable=True) from exc
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()

        if usage is not None:
            yield LLMStreamEvent(type="usage", usage=usage)
        else:
            yield LLMStreamEvent(
                type="usage",
                usage=LLMUsage(
                    input_tokens=_estimate_tokens(_join_message_text(messages)),
                    output_tokens=0,
                ),
            )
        if finish_reason not in {None, "STOP"}:
            if finish_reason == "MAX_TOKENS":
                raise LLMProviderError(
                    "Gemini reached the configured output token limit",
                    code="max_output_tokens",
                    finish_reason=finish_reason,
                )
            raise LLMProviderError(
                f"Gemini stopped with finish reason {finish_reason}",
                code="llm_finish_reason",
                finish_reason=finish_reason,
            )
        yield LLMStreamEvent(type="done")

    def _stream_http_sse(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        parser,
    ) -> Iterable[LLMStreamEvent]:
        timeout = httpx.Timeout(self._settings.llm_timeout_seconds)
        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream(
                    "POST", url, headers=headers, json=payload
                ) as response:
                    if response.status_code >= 400:
                        retryable = response.status_code in {408, 409, 429} or (
                            response.status_code >= 500
                        )
                        read = getattr(response, "read", None)
                        if callable(read):
                            read()
                        detail = _safe_provider_error_detail(response)
                        raise LLMProviderError(
                            f"llm provider returned HTTP {response.status_code}"
                            + (f": {detail}" if detail else ""),
                            retryable=retryable,
                            code=_http_error_code(
                                response.status_code,
                                detail=detail,
                            ),
                        )

                    event_name = ""
                    data_lines: list[str] = []
                    for raw_line in response.iter_lines():
                        line = raw_line.strip()
                        if not line:
                            yield from _parse_sse_event(
                                event_name=event_name,
                                data_lines=data_lines,
                                parser=parser,
                            )
                            event_name = ""
                            data_lines = []
                            continue
                        if line.startswith("event:"):
                            event_name = line.removeprefix("event:").strip()
                        elif line.startswith("data:"):
                            data_lines.append(line.removeprefix("data:").strip())
        except httpx.TimeoutException as exc:
            raise LLMProviderError(
                "llm provider request timed out",
                retryable=True,
                code="llm_timeout",
            ) from exc
        except httpx.TransportError as exc:
            raise LLMProviderError(
                "llm provider network request failed",
                retryable=True,
                code="llm_transport_error",
            ) from exc


def _http_error_code(status_code: int, *, detail: str = "") -> str:
    if status_code in {400, 413, 422} and _is_context_overflow_detail(detail):
        return "context_overflow"
    if status_code in {401, 403}:
        return "llm_auth_error"
    if status_code == 402:
        return "llm_quota_exhausted"
    if status_code == 429:
        return "rate_limit"
    return "llm_http_error"


def _is_context_overflow_detail(detail: str) -> bool:
    normalized = " ".join(detail.casefold().split())
    if not normalized:
        return False
    return any(
        re.search(pattern, normalized)
        for pattern in (
            r"context[_ -]?length[_ -]?exceeded",
            r"maximum context length",
            r"context window.{0,40}(?:exceed|limit|maximum)",
            r"prompt is too long",
            r"too many (?:input )?tokens",
            r"input token count.{0,40}(?:exceed|limit|maximum)",
            r"(?:input|prompt|request).{0,40}exceeds.{0,40}token limit",
        )
    )


def _safe_provider_error_detail(response: Any) -> str:
    """Return one bounded, credential-redacted provider error message."""

    try:
        payload = response.json()
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        detail = error.get("message")
        error_code = error.get("code") or error.get("type")
    elif isinstance(error, str):
        detail = error
        error_code = None
    else:
        detail = payload.get("message")
        error_code = payload.get("code")
    if not isinstance(detail, str):
        detail = ""
    if isinstance(error_code, str) and error_code not in detail:
        detail = f"{error_code}: {detail}".strip()
    if not detail:
        return ""
    safe = " ".join(detail.split())
    safe = _PROVIDER_ERROR_SECRET.sub(r"\1=[REDACTED]", safe)
    safe = _PROVIDER_ERROR_BEARER.sub("Bearer [REDACTED]", safe)
    safe = _PROVIDER_ERROR_KEY.sub("[REDACTED]", safe)
    return safe[:_PROVIDER_ERROR_DETAIL_MAX_CHARS].strip()


def _parse_sse_event(event_name: str, data_lines: list[str], parser):
    if not data_lines:
        return
    data = "\n".join(data_lines)
    if data == "[DONE]":
        yield LLMStreamEvent(type="done")
        return
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise LLMProviderError("llm provider returned malformed SSE JSON") from exc
    yield from parser(event_name, payload)


def _parse_openai_event(
    event_name: str, payload: dict[str, object]
) -> Iterable[LLMStreamEvent]:
    event_type = str(payload.get("type") or event_name)
    if event_type in {"response.output_text.delta", "response.refusal.delta"}:
        delta = payload.get("delta")
        if isinstance(delta, str) and delta:
            yield LLMStreamEvent(type="delta", text=delta)
    elif event_type == "response.completed":
        response = payload.get("response")
        if isinstance(response, dict):
            usage = _usage_from_mapping(response.get("usage"))
            if usage is not None:
                yield LLMStreamEvent(type="usage", usage=usage)
        yield LLMStreamEvent(type="done")
    elif event_type == "error":
        raise LLMProviderError(_error_message(payload), retryable=False)


def _parse_anthropic_event(
    event_name: str, payload: dict[str, object]
) -> Iterable[LLMStreamEvent]:
    event_type = str(payload.get("type") or event_name)
    if event_type == "message_start":
        message = payload.get("message")
        if isinstance(message, dict):
            usage = message.get("usage")
            if isinstance(usage, dict):
                yield _usage_event(
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                )
    elif event_type == "content_block_delta":
        delta = payload.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            text = delta.get("text")
            if isinstance(text, str) and text:
                yield LLMStreamEvent(type="delta", text=text)
    elif event_type == "message_delta":
        usage = payload.get("usage")
        if isinstance(usage, dict):
            yield _usage_event(0, usage.get("output_tokens"))
    elif event_type == "message_stop":
        yield LLMStreamEvent(type="done")
    elif event_type == "error":
        raise LLMProviderError(_error_message(payload), retryable=True)


def _parse_deepseek_event(
    event_name: str, payload: dict[str, object]
) -> Iterable[LLMStreamEvent]:
    error = payload.get("error")
    if error:
        raise LLMProviderError(_error_message(payload), retryable=False)
    usage = _chat_usage_from_mapping(payload.get("usage"))
    if usage is not None:
        yield LLMStreamEvent(type="usage", usage=usage)
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if isinstance(delta, dict):
            text = delta.get("content")
            if isinstance(text, str) and text:
                yield LLMStreamEvent(type="delta", text=text)


def _google_contents(messages: list[dict[str, str]], types: Any) -> list[Any]:
    contents = []
    for message in messages:
        role = message["role"]
        if role == "system":
            continue
        contents.append(
            types.Content(
                role=_google_role(role),
                parts=[types.Part.from_text(text=message["content"])],
            )
        )
    return contents


def _google_system_instruction(messages: list[dict[str, str]]) -> str | None:
    system_messages = [
        message["content"] for message in messages if message["role"] == "system"
    ]
    if not system_messages:
        return None
    return "\n\n".join(system_messages)


def _google_role(role: str) -> str:
    return "model" if role == "assistant" else "user"


def _google_usage(usage_metadata: object) -> LLMUsage | None:
    if usage_metadata is None:
        return None
    return LLMUsage(
        input_tokens=_int_attr(usage_metadata, "prompt_token_count"),
        output_tokens=_int_attr(usage_metadata, "candidates_token_count"),
        thoughts_tokens=_int_attr(usage_metadata, "thoughts_token_count"),
    )


def _google_finish_reason(chunk: object) -> str | None:
    for candidate in getattr(chunk, "candidates", None) or []:
        reason = getattr(candidate, "finish_reason", None)
        if reason is None:
            continue
        value = getattr(reason, "value", reason)
        normalized = str(value).strip().upper()
        if normalized and normalized not in {"NONE", "FINISH_REASON_UNSPECIFIED"}:
            return normalized.removeprefix("FINISHREASON.")
    return None


def _is_timeout_exception(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, httpx.TimeoutException)):
            return True
        if "timed out" in str(current).lower() or "timeout" in str(current).lower():
            return True
        current = current.__cause__ or current.__context__
    return False


def _usage_event(input_tokens: object, output_tokens: object) -> LLMStreamEvent:
    return LLMStreamEvent(
        type="usage",
        usage=LLMUsage(
            input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
            output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
        ),
    )


def _int_attr(value: object, name: str) -> int:
    attr = getattr(value, name, 0)
    return attr if isinstance(attr, int) else 0


def _error_message(payload: dict[str, object]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    return "llm provider returned an error event"


def _estimate_tokens(text: str) -> int:
    return max(1, estimate_text_tokens(text))


def _count_fake_message_tokens(messages: list[dict[str, str]]) -> int:
    total = 2
    for message in messages:
        total += 4
        for value in (message.get("role", ""), message.get("content", "")):
            total += _count_fake_text_tokens(value)
    return total


def _count_fake_text_tokens(value: str) -> int:
    total = 0
    ascii_buffer: list[str] = []
    for character in value:
        if ord(character) < 128 and character.isalnum():
            ascii_buffer.append(character)
            continue
        if ascii_buffer:
            total += 1
            ascii_buffer = []
        if not character.isspace():
            total += 1
    if ascii_buffer:
        total += 1
    return total


def _join_message_text(messages: list[dict[str, str]]) -> str:
    return "\n".join(message["content"] for message in messages)


def _join_any_message_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif content is not None:
            parts.append(json.dumps(content, ensure_ascii=False, default=str))
    return "\n".join(parts)


def _tool_aliases(tools: list[ToolSpec]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for spec in tools:
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", spec.name).strip("_")
        if not base:
            base = "tool"
        if not base[0].isalpha() and base[0] != "_":
            base = f"tool_{base}"
        digest = hashlib.sha256(spec.name.encode("utf-8")).hexdigest()[:8]
        alias = base[:64]
        if alias in aliases and aliases[alias] != spec.name:
            alias = f"{base[:55]}_{digest}"
        if len(alias) > 64:
            alias = f"{alias[:55]}_{digest}"
        aliases[alias] = spec.name
    return aliases


def _json_arguments(
    value: Any,
    *,
    finish_reason: str | None = None,
    usage: LLMUsage | None = None,
) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    argument_chars = len(value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        truncated = _is_output_limit_reason(finish_reason)
        details = [f"chars={argument_chars}", f"json_error_position={exc.pos}"]
        if finish_reason:
            details.insert(0, f"finish_reason={finish_reason}")
        raise LLMProviderError(
            "model returned "
            + ("truncated" if truncated else "malformed")
            + " tool arguments ("
            + ", ".join(details)
            + ")",
            retryable=True,
            code=(
                "tool_arguments_truncated"
                if truncated
                else "invalid_tool_arguments"
            ),
            finish_reason=finish_reason,
            usage=usage,
            tool_argument_chars=argument_chars,
            json_error_position=exc.pos,
        ) from exc
    if not isinstance(parsed, dict):
        raise LLMProviderError(
            f"model returned non-object tool arguments (chars={argument_chars})",
            retryable=True,
            code="invalid_tool_arguments",
            finish_reason=finish_reason,
            usage=usage,
            tool_argument_chars=argument_chars,
        )
    return parsed


def _is_output_limit_reason(finish_reason: str | None) -> bool:
    return str(finish_reason or "").strip().lower() in {
        "length",
        "max_tokens",
        "max_output_tokens",
        "incomplete",
    }


def _raise_truncated_tool_turn(
    finish_reason: str | None,
    usage: LLMUsage | None,
) -> None:
    if not _is_output_limit_reason(finish_reason):
        return
    raise LLMProviderError(
        f"model tool turn reached its output limit (finish_reason={finish_reason})",
        retryable=True,
        code="tool_output_truncated",
        finish_reason=finish_reason,
        usage=usage,
    )


def _tool_retry_messages(
    messages: list[dict[str, Any]],
    error: LLMProviderError,
    *,
    attempt: int,
) -> list[dict[str, Any]]:
    diagnostic = (
        f" Previous finish reason: {error.finish_reason}."
        if error.finish_reason
        else ""
    )
    return list(messages) + [
        {
            "role": "system",
            "content": (
                "The previous tool call could not be parsed or was truncated. "
                "Retry with exactly one tool call. Keep its arguments substantially "
                "smaller: change one file at a time, prefer a focused patch, and do "
                "not embed multiple complete files in one argument."
                f" Corrective attempt: {attempt}.{diagnostic}"
            ),
        }
    ]


def _effective_model_output_limit(
    candidate: ModelConfig,
    *,
    input_tokens: int,
    requested_output_tokens: int,
) -> int:
    context_remaining = candidate.context_window_tokens - input_tokens
    if context_remaining <= 0:
        raise LLMProviderError(
            "model context window has no room for output tokens",
            code="context_window_too_small",
        )
    model_limit = candidate.max_output_tokens or requested_output_tokens
    return max(1, min(requested_output_tokens, model_limit, context_remaining))


def _usage_from_mapping(value: Any) -> LLMUsage | None:
    if not isinstance(value, dict):
        return None
    output_details = value.get("output_tokens_details")
    output_details = output_details if isinstance(output_details, dict) else {}
    output_tokens = int(value.get("output_tokens") or 0)
    thoughts_tokens = int(output_details.get("reasoning_tokens") or 0)
    return LLMUsage(
        input_tokens=int(value.get("input_tokens") or 0),
        output_tokens=max(0, output_tokens - thoughts_tokens),
        thoughts_tokens=thoughts_tokens,
    )


def _chat_usage_from_mapping(value: Any) -> LLMUsage | None:
    if not isinstance(value, dict):
        return None
    details = value.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    completion_tokens = int(value.get("completion_tokens") or 0)
    reasoning_tokens = int(details.get("reasoning_tokens") or 0)
    return LLMUsage(
        input_tokens=int(value.get("prompt_tokens") or 0),
        output_tokens=max(0, completion_tokens - reasoning_tokens),
        thoughts_tokens=max(0, reasoning_tokens),
    )


def _text_only_tool_transcript(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten tool blocks into text for a request that declares no tools.

    Providers reject tool_use/tool_result content when the request carries no
    tool definitions, so a finalization without definitions keeps the observed
    evidence as plain text instead.
    """

    flattened: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role == "tool":
            flattened.append(
                {
                    "role": "user",
                    "content": (
                        f"Tool result {message.get('name') or ''}: "
                        + json.dumps(
                            message.get("content"),
                            ensure_ascii=False,
                            default=str,
                        )
                    ),
                }
            )
            continue
        calls = [
            item
            for item in (message.get("tool_calls") or [])
            if isinstance(item, dict)
        ]
        text = str(message.get("content") or "")
        if role == "assistant" and calls:
            names = ", ".join(str(item.get("name") or "") for item in calls)
            flattened.append(
                {
                    "role": "assistant",
                    "content": (f"{text}\n" if text else "")
                    + f"[requested tools: {names}]",
                }
            )
            continue
        flattened.append({"role": role, "content": text})
    return flattened


def _openai_tool_input(
    messages: list[dict[str, Any]],
    reverse_aliases: dict[str, str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role == "assistant" and message.get("provider") == "openai":
            provider_items = message.get("provider_items")
            if isinstance(provider_items, list):
                items.extend(
                    dict(item) for item in provider_items if isinstance(item, dict)
                )
                continue
        if role in {"system", "user", "assistant"}:
            content = message.get("content")
            if isinstance(content, str) and content:
                items.append({"role": role, "content": content})
            for call in message.get("tool_calls", []):
                if not isinstance(call, dict):
                    continue
                registry_name = str(call.get("name") or "")
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("call_id") or ""),
                        "name": reverse_aliases.get(registry_name, registry_name),
                        "arguments": json.dumps(
                            call.get("arguments") or {},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                )
            continue
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("call_id") or ""),
                    "output": json.dumps(
                        message.get("content"),
                        ensure_ascii=False,
                        default=str,
                    ),
                }
            )
    return items


def _anthropic_tool_messages(
    messages: list[dict[str, Any]],
    reverse_aliases: dict[str, str],
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            converted.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for message in messages:
        role = str(message.get("role") or "")
        if role == "system":
            continue
        if role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": str(message.get("call_id") or ""),
                    "content": json.dumps(
                        message.get("content"),
                        ensure_ascii=False,
                        default=str,
                    ),
                    "is_error": bool(message.get("is_error")),
                }
            )
            continue
        flush_tool_results()
        if role == "assistant" and message.get("provider") == "anthropic":
            provider_items = message.get("provider_items")
            if isinstance(provider_items, list):
                converted.append(
                    {
                        "role": "assistant",
                        "content": [
                            dict(item)
                            for item in provider_items
                            if isinstance(item, dict)
                        ],
                    }
                )
                continue
        if role not in {"user", "assistant"}:
            continue
        blocks: list[dict[str, Any]] = []
        content = message.get("content")
        if isinstance(content, str) and content:
            blocks.append({"type": "text", "text": content})
        for call in message.get("tool_calls", []):
            if not isinstance(call, dict):
                continue
            registry_name = str(call.get("name") or "")
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(call.get("call_id") or ""),
                    "name": reverse_aliases.get(registry_name, registry_name),
                    "input": dict(call.get("arguments") or {}),
                }
            )
        if blocks:
            converted.append({"role": role, "content": blocks})
    flush_tool_results()
    return converted


def _deepseek_tool_messages(
    messages: list[dict[str, Any]],
    reverse_aliases: dict[str, str],
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role == "tool":
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": str(message.get("call_id") or ""),
                    "content": json.dumps(
                        message.get("content"),
                        ensure_ascii=False,
                        default=str,
                    ),
                }
            )
            continue
        if role not in {"system", "user", "assistant"}:
            continue
        if role == "assistant" and message.get("provider") == "deepseek":
            provider_items = message.get("provider_items")
            if isinstance(provider_items, list) and provider_items:
                converted.extend(
                    dict(item) for item in provider_items if isinstance(item, dict)
                )
                continue
        item: dict[str, Any] = {
            "role": role,
            "content": str(message.get("content") or ""),
        }
        tool_calls = []
        for call in message.get("tool_calls", []):
            if not isinstance(call, dict):
                continue
            registry_name = str(call.get("name") or "")
            tool_calls.append(
                {
                    "id": str(call.get("call_id") or ""),
                    "type": "function",
                    "function": {
                        "name": reverse_aliases.get(registry_name, registry_name),
                        "arguments": json.dumps(
                            call.get("arguments") or {},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
            )
        if tool_calls:
            item["tool_calls"] = tool_calls
            if role == "assistant":
                # DeepSeek thinking models validate every assistant tool-call
                # turn in the replayed history. Runtime-synthesized turns have
                # no private reasoning to preserve, but must still carry the
                # field; an empty value is the provider-supported sentinel.
                item["reasoning_content"] = str(
                    message.get("reasoning_content") or ""
                )
        converted.append(item)
    return converted


def _assess_task_complexity(
    text: str,
    *,
    estimated_input_tokens: int,
    tool_calling: bool,
    structured_output: bool,
) -> tuple[Literal["low", "medium", "high"], tuple[str, ...]]:
    """Return a deterministic, explainable profile without another LLM call."""
    normalized = text.lower()
    score = 0
    reasons: list[str] = []
    if tool_calling:
        score += 2
        reasons.append("requires_tool_calling")
    if structured_output:
        score += 1
        reasons.append("requires_structured_output")
    if estimated_input_tokens >= 12_000:
        score += 3
        reasons.append("very_long_context")
    elif estimated_input_tokens >= 4_000:
        score += 1
        reasons.append("long_context")
    complex_markers = (
        "architecture",
        "debug",
        "migration",
        "refactor",
        "security",
        "multi-file",
        "performance",
        "架构",
        "调试",
        "迁移",
        "重构",
        "安全",
        "多文件",
        "性能",
    )
    marker_count = sum(marker in normalized for marker in complex_markers)
    if marker_count:
        score += min(3, marker_count)
        reasons.append("complex_task_markers")
    if len(text) >= 2_000:
        score += 1
        reasons.append("long_instruction")
    if score >= 5:
        return "high", tuple(reasons or ["high_signal_score"])
    if score <= 1:
        return "low", tuple(reasons or ["short_general_request"])
    return "medium", tuple(reasons or ["moderate_request"])


def _google_tool_contents(
    messages: list[dict[str, Any]],
    types: Any,
    reverse_aliases: dict[str, str],
) -> list[Any]:
    contents: list[Any] = []
    native_google_call_ids: set[str] = set()
    for message in messages:
        role = str(message.get("role") or "")
        if role == "system":
            continue
        if role == "assistant" and message.get("provider") == "google":
            provider_items = message.get("provider_items")
            if isinstance(provider_items, list) and provider_items:
                native_google_call_ids.update(
                    str(call.get("call_id") or "")
                    for call in message.get("tool_calls", [])
                    if isinstance(call, dict) and call.get("call_id")
                )
                contents.extend(
                    types.Content(**item)
                    for item in provider_items
                    if isinstance(item, dict)
                )
                continue
        if role == "tool":
            content = message.get("content")
            response = content if isinstance(content, dict) else {"result": content}
            registry_name = str(message.get("name") or "")
            call_id = str(message.get("call_id") or "")
            if call_id not in native_google_call_ids:
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(
                                text=(
                                    f"Tool result from a previous provider for "
                                    f"{registry_name} ({call_id}): "
                                    + json.dumps(
                                        response,
                                        ensure_ascii=False,
                                        default=str,
                                        sort_keys=True,
                                    )
                                )
                            )
                        ],
                    )
                )
                continue
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=call_id,
                                name=reverse_aliases.get(registry_name, registry_name),
                                response=response,
                            )
                        )
                    ],
                )
            )
            continue
        if role not in {"user", "assistant"}:
            continue
        parts: list[Any] = []
        content = message.get("content")
        if isinstance(content, str) and content:
            parts.append(types.Part.from_text(text=content))
        foreign_calls = [
            call
            for call in message.get("tool_calls", [])
            if isinstance(call, dict)
        ]
        if foreign_calls:
            parts.append(
                types.Part.from_text(
                    text=(
                        "Tool calls proposed by a previous provider: "
                        + json.dumps(
                            foreign_calls,
                            ensure_ascii=False,
                            default=str,
                            sort_keys=True,
                        )
                    )
                )
            )
        if parts:
            contents.append(
                types.Content(
                    role="model" if role == "assistant" else "user",
                    parts=parts,
                )
            )
    return contents


def _google_system_instruction_any(
    messages: list[dict[str, Any]],
) -> str | None:
    system = [
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "system"
    ]
    return "\n\n".join(system) if system else None


def _google_candidate_finish_reason(candidate: Any) -> str | None:
    if candidate is None:
        return None
    reason = getattr(candidate, "finish_reason", None)
    if reason is None:
        return None
    value = getattr(reason, "value", reason)
    normalized = str(value).strip().upper()
    return normalized.removeprefix("FINISHREASON.") if normalized else None
