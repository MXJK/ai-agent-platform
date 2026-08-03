"""Capability, policy, and health-aware routing across configured LLM models."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
from threading import RLock
from time import monotonic
from typing import Any, Callable, Literal, Mapping, Sequence


RoutingPolicy = Literal["quality", "cost", "latency"]
CircuitState = Literal["closed", "open", "half_open"]


@dataclass(frozen=True)
class ModelCapabilities:
    tool_calling: bool = False
    structured_output: bool = False


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    context_window_tokens: int
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    quality_score: float = 0.5
    latency_ms: int = 1000
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("model provider and model must not be empty")
        if self.context_window_tokens <= 0:
            raise ValueError("model context_window_tokens must be positive")
        if self.input_cost_per_million < 0 or self.output_cost_per_million < 0:
            raise ValueError("model prices must be greater than or equal to 0")
        if not 0.0 <= self.quality_score <= 1.0:
            raise ValueError("model quality_score must be between 0 and 1")
        if self.latency_ms <= 0:
            raise ValueError("model latency_ms must be positive")

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelConfig":
        raw_capabilities = value.get("capabilities", {})
        if not isinstance(raw_capabilities, Mapping):
            raise ValueError("model capabilities must be an object")
        return cls(
            provider=str(value.get("provider", "")),
            model=str(value.get("model", "")),
            context_window_tokens=int(value.get("context_window_tokens", 0)),
            capabilities=ModelCapabilities(
                tool_calling=bool(raw_capabilities.get("tool_calling", False)),
                structured_output=bool(
                    raw_capabilities.get("structured_output", False)
                ),
            ),
            input_cost_per_million=float(
                value.get("input_cost_per_million", 0.0)
            ),
            output_cost_per_million=float(
                value.get("output_cost_per_million", 0.0)
            ),
            quality_score=float(value.get("quality_score", 0.5)),
            latency_ms=int(value.get("latency_ms", 1000)),
            enabled=bool(value.get("enabled", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "capabilities": {
                "tool_calling": self.capabilities.tool_calling,
                "structured_output": self.capabilities.structured_output,
            },
            "context_window_tokens": self.context_window_tokens,
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
            "quality_score": self.quality_score,
            "latency_ms": self.latency_ms,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class RoutingRequirements:
    tool_calling: bool = False
    structured_output: bool = False
    min_context_tokens: int = 0
    estimated_input_tokens: int = 0
    expected_output_tokens: int = 0

    def __post_init__(self) -> None:
        for name, value in (
            ("min_context_tokens", self.min_context_tokens),
            ("estimated_input_tokens", self.estimated_input_tokens),
            ("expected_output_tokens", self.expected_output_tokens),
        ):
            if value < 0:
                raise ValueError(f"{name} must be greater than or equal to 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_calling": self.tool_calling,
            "structured_output": self.structured_output,
            "min_context_tokens": self.min_context_tokens,
            "estimated_input_tokens": self.estimated_input_tokens,
            "expected_output_tokens": self.expected_output_tokens,
        }


@dataclass(frozen=True)
class ProviderHealthSnapshot:
    provider: str
    state: CircuitState
    recent_error_rate: float
    recent_requests: int
    consecutive_failures: int
    retry_after_seconds: float | None = None

    @property
    def available(self) -> bool:
        return self.state != "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "recent_error_rate": round(self.recent_error_rate, 6),
            "recent_requests": self.recent_requests,
            "consecutive_failures": self.consecutive_failures,
            "retry_after_seconds": (
                round(self.retry_after_seconds, 3)
                if self.retry_after_seconds is not None
                else None
            ),
        }


@dataclass
class _MutableProviderHealth:
    state: CircuitState = "closed"
    outcomes: deque[bool] = field(default_factory=deque)
    consecutive_failures: int = 0
    opened_at: float | None = None


class ProviderHealthManager:
    """Thread-safe process-local provider health and circuit-breaker state."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        error_window_size: int = 20,
        error_rate_min_requests: int = 5,
        error_rate_threshold: float = 0.5,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds must be positive")
        if error_window_size <= 0:
            raise ValueError("error_window_size must be positive")
        if error_rate_min_requests <= 0:
            raise ValueError("error_rate_min_requests must be positive")
        if error_rate_min_requests > error_window_size:
            raise ValueError(
                "error_rate_min_requests must not exceed error_window_size"
            )
        if not 0.0 <= error_rate_threshold <= 1.0:
            raise ValueError("error_rate_threshold must be between 0 and 1")
        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._error_window_size = error_window_size
        self._error_rate_min_requests = error_rate_min_requests
        self._error_rate_threshold = error_rate_threshold
        self._clock = clock
        self._states: dict[str, _MutableProviderHealth] = {}
        self._lock = RLock()

    def snapshot(self, provider: str) -> ProviderHealthSnapshot:
        with self._lock:
            state = self._states.setdefault(
                provider,
                _MutableProviderHealth(outcomes=deque(maxlen=self._error_window_size)),
            )
            now = self._clock()
            if (
                state.state == "open"
                and state.opened_at is not None
                and now - state.opened_at >= self._recovery_timeout_seconds
            ):
                state.state = "half_open"
            retry_after: float | None = None
            if state.state == "open" and state.opened_at is not None:
                retry_after = max(
                    0.0,
                    self._recovery_timeout_seconds - (now - state.opened_at),
                )
            return self._snapshot(provider, state, retry_after)

    def record_success(self, provider: str) -> ProviderHealthSnapshot:
        with self._lock:
            state = self._state(provider)
            if state.state == "half_open":
                state.outcomes.clear()
            state.outcomes.append(True)
            state.consecutive_failures = 0
            state.state = "closed"
            state.opened_at = None
            return self._snapshot(provider, state, None)

    def record_failure(self, provider: str) -> ProviderHealthSnapshot:
        with self._lock:
            state = self._state(provider)
            was_half_open = state.state == "half_open"
            state.outcomes.append(False)
            state.consecutive_failures += 1
            error_rate = self._error_rate(state)
            error_rate_open = (
                len(state.outcomes) >= self._error_rate_min_requests
                and error_rate >= self._error_rate_threshold
            )
            if (
                was_half_open
                or state.consecutive_failures >= self._failure_threshold
                or error_rate_open
            ):
                state.state = "open"
                state.opened_at = self._clock()
            return self._snapshot(
                provider,
                state,
                self._recovery_timeout_seconds if state.state == "open" else None,
            )

    def reset(self, provider: str | None = None) -> None:
        with self._lock:
            if provider is None:
                self._states.clear()
            else:
                self._states.pop(provider, None)

    def _state(self, provider: str) -> _MutableProviderHealth:
        return self._states.setdefault(
            provider,
            _MutableProviderHealth(outcomes=deque(maxlen=self._error_window_size)),
        )

    def _snapshot(
        self,
        provider: str,
        state: _MutableProviderHealth,
        retry_after: float | None,
    ) -> ProviderHealthSnapshot:
        return ProviderHealthSnapshot(
            provider=provider,
            state=state.state,
            recent_error_rate=self._error_rate(state),
            recent_requests=len(state.outcomes),
            consecutive_failures=state.consecutive_failures,
            retry_after_seconds=retry_after,
        )

    @staticmethod
    def _error_rate(state: _MutableProviderHealth) -> float:
        if not state.outcomes:
            return 0.0
        return sum(not outcome for outcome in state.outcomes) / len(state.outcomes)


@dataclass
class RouteCandidateTrace:
    config: ModelConfig
    health: ProviderHealthSnapshot
    eligible: bool
    rejection_reasons: list[str]
    estimated_cost_usd: float
    rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        value = self.config.to_dict()
        value.update(
            {
                "eligible": self.eligible,
                "rejection_reasons": list(self.rejection_reasons),
                "estimated_cost_usd": round(self.estimated_cost_usd, 8),
                "rank": self.rank,
                "health": self.health.to_dict(),
            }
        )
        return value


@dataclass
class RouteFailureTrace:
    provider: str
    model: str
    code: str
    message: str
    retryable: bool
    after_stream_start: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "after_stream_start": self.after_stream_start,
        }


@dataclass
class ModelRouteTrace:
    policy: RoutingPolicy
    requirements: RoutingRequirements
    candidates: list[RouteCandidateTrace]
    selection_reason: str | None
    requested_provider: str | None = None
    requested_model: str | None = None
    failures: list[RouteFailureTrace] = field(default_factory=list)
    final_provider: str | None = None
    final_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "requirements": self.requirements.to_dict(),
            "requested_provider": self.requested_provider,
            "requested_model": self.requested_model,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "selection_reason": self.selection_reason,
            "failures": [failure.to_dict() for failure in self.failures],
            "final_model": (
                {
                    "provider": self.final_provider,
                    "model": self.final_model,
                }
                if self.final_provider and self.final_model
                else None
            ),
        }


@dataclass(frozen=True)
class RoutePlan:
    candidates: tuple[ModelConfig, ...]
    trace: ModelRouteTrace


class ModelRouter:
    """Filter models by requirements and health, then rank by one policy."""

    def __init__(
        self,
        models: Sequence[ModelConfig],
        *,
        default_policy: RoutingPolicy = "quality",
        health: ProviderHealthManager | None = None,
    ) -> None:
        if default_policy not in {"quality", "cost", "latency"}:
            raise ValueError(f"unsupported routing policy: {default_policy}")
        if not models:
            raise ValueError("model router requires at least one model")
        keys = [model.key for model in models]
        if len(keys) != len(set(keys)):
            raise ValueError("model catalog contains duplicate provider:model entries")
        self._models = tuple(models)
        self.default_policy = default_policy
        self.health = health or ProviderHealthManager()

    @property
    def models(self) -> tuple[ModelConfig, ...]:
        return self._models

    def route(
        self,
        requirements: RoutingRequirements,
        *,
        policy: RoutingPolicy | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> RoutePlan:
        selected_policy = policy or self.default_policy
        if selected_policy not in {"quality", "cost", "latency"}:
            raise ValueError(f"unsupported routing policy: {selected_policy}")

        traces: list[RouteCandidateTrace] = []
        eligible: list[RouteCandidateTrace] = []
        for config in self._models:
            health = self.health.snapshot(config.provider)
            rejection_reasons = _rejection_reasons(
                config,
                health,
                requirements,
                requested_provider=provider,
                requested_model=model,
            )
            candidate = RouteCandidateTrace(
                config=config,
                health=health,
                eligible=not rejection_reasons,
                rejection_reasons=rejection_reasons,
                estimated_cost_usd=_estimated_cost(config, requirements),
            )
            traces.append(candidate)
            if candidate.eligible:
                eligible.append(candidate)

        eligible.sort(key=lambda item: _sort_key(item, selected_policy))
        for index, candidate in enumerate(eligible, start=1):
            candidate.rank = index
        ranked_configs = tuple(candidate.config for candidate in eligible)
        selection_reason = (
            _selection_reason(selected_policy, eligible[0], len(eligible))
            if eligible
            else None
        )
        trace = ModelRouteTrace(
            policy=selected_policy,
            requirements=requirements,
            candidates=traces,
            selection_reason=selection_reason,
            requested_provider=provider,
            requested_model=model,
        )
        return RoutePlan(candidates=ranked_configs, trace=trace)

    def record_failure(
        self,
        trace: ModelRouteTrace,
        candidate: ModelConfig,
        *,
        code: str,
        message: str,
        retryable: bool,
        after_stream_start: bool,
    ) -> None:
        if retryable:
            self.health.record_failure(candidate.provider)
        trace.failures.append(
            RouteFailureTrace(
                provider=candidate.provider,
                model=candidate.model,
                code=code,
                message=message,
                retryable=retryable,
                after_stream_start=after_stream_start,
            )
        )
        if after_stream_start:
            trace.final_provider = candidate.provider
            trace.final_model = candidate.model

    def record_success(
        self,
        trace: ModelRouteTrace,
        candidate: ModelConfig,
    ) -> None:
        self.health.record_success(candidate.provider)
        self.mark_selected(trace, candidate)

    @staticmethod
    def mark_selected(
        trace: ModelRouteTrace,
        candidate: ModelConfig,
    ) -> None:
        trace.final_provider = candidate.provider
        trace.final_model = candidate.model
        rank = next(
            (
                item.rank
                for item in trace.candidates
                if item.config.key == candidate.key
            ),
            None,
        )
        if rank and rank > 1:
            trace.selection_reason = (
                f"fallback candidate rank {rank} selected after "
                f"{len(trace.failures)} earlier failure(s)"
            )


def load_model_catalog(
    catalog_json: str | None,
    *,
    default_provider: str,
    default_model: str,
    default_context_window_tokens: int,
) -> tuple[ModelConfig, ...]:
    """Load a JSON model table, or derive one conservative primary entry."""

    if catalog_json:
        try:
            raw = json.loads(catalog_json)
        except json.JSONDecodeError as exc:
            raise ValueError("llm_model_catalog_json must be valid JSON") from exc
        if not isinstance(raw, list) or not raw:
            raise ValueError("llm_model_catalog_json must be a non-empty JSON array")
        if not all(isinstance(item, Mapping) for item in raw):
            raise ValueError("each model catalog entry must be an object")
        return tuple(ModelConfig.from_mapping(item) for item in raw)

    real_provider = default_provider != "fake"
    return (
        ModelConfig(
            provider=default_provider,
            model=default_model,
            context_window_tokens=default_context_window_tokens,
            capabilities=ModelCapabilities(
                tool_calling=real_provider,
                structured_output=real_provider,
            ),
        ),
    )


def _rejection_reasons(
    config: ModelConfig,
    health: ProviderHealthSnapshot,
    requirements: RoutingRequirements,
    *,
    requested_provider: str | None,
    requested_model: str | None,
) -> list[str]:
    reasons: list[str] = []
    if not config.enabled:
        reasons.append("model_disabled")
    if requested_provider is not None and config.provider != requested_provider:
        reasons.append("provider_not_requested")
    if requested_model is not None and config.model != requested_model:
        reasons.append("model_not_requested")
    if requirements.tool_calling and not config.capabilities.tool_calling:
        reasons.append("tool_calling_not_supported")
    if (
        requirements.structured_output
        and not config.capabilities.structured_output
    ):
        reasons.append("structured_output_not_supported")
    if config.context_window_tokens < requirements.min_context_tokens:
        reasons.append("context_window_too_small")
    if not health.available:
        reasons.append("provider_circuit_open")
    return reasons


def _estimated_cost(
    config: ModelConfig,
    requirements: RoutingRequirements,
) -> float:
    input_tokens = requirements.estimated_input_tokens or 1000
    output_tokens = requirements.expected_output_tokens or 1000
    return (
        input_tokens * config.input_cost_per_million
        + output_tokens * config.output_cost_per_million
    ) / 1_000_000


def _sort_key(
    candidate: RouteCandidateTrace,
    policy: RoutingPolicy,
) -> tuple[Any, ...]:
    config = candidate.config
    stable = (config.provider, config.model)
    if policy == "quality":
        return (
            -config.quality_score,
            candidate.estimated_cost_usd,
            config.latency_ms,
            *stable,
        )
    if policy == "cost":
        return (
            candidate.estimated_cost_usd,
            -config.quality_score,
            config.latency_ms,
            *stable,
        )
    return (
        config.latency_ms,
        -config.quality_score,
        candidate.estimated_cost_usd,
        *stable,
    )


def _selection_reason(
    policy: RoutingPolicy,
    candidate: RouteCandidateTrace,
    eligible_count: int,
) -> str:
    config = candidate.config
    if policy == "quality":
        metric = f"quality_score={config.quality_score:.3f}"
    elif policy == "cost":
        metric = f"estimated_cost_usd={candidate.estimated_cost_usd:.8f}"
    else:
        metric = f"latency_ms={config.latency_ms}"
    return (
        f"{policy} policy ranked {config.key} first by {metric} "
        f"among {eligible_count} healthy capable candidate(s)"
    )
