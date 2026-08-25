"""Domain records for the local, globally shared model registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


ProviderKind = Literal["anthropic", "deepseek", "fake", "google", "openai"]
SelectionMode = Literal["auto", "manual"]
SelectionPolicy = Literal["smart", "quality", "cost", "latency"]

REAL_PROVIDERS = {"anthropic", "deepseek", "google", "openai"}
SUPPORTED_PROVIDERS = REAL_PROVIDERS | {"fake"}


@dataclass(frozen=True)
class ProviderConnection:
    provider: str
    display_name: str
    secret_ref: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RegisteredModel:
    id: str
    provider: str
    model: str
    display_name: str
    context_window_tokens: int
    max_output_tokens: int
    tool_calling: bool
    structured_output: bool
    input_cost_per_million: float
    output_cost_per_million: float
    quality_score: float
    configured_latency_ms: int
    enabled: bool
    auto_eligible: bool
    created_at: datetime
    updated_at: datetime

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class SessionModelPreference:
    session_id: str
    mode: SelectionMode = "auto"
    routing_policy: SelectionPolicy = "smart"
    preferred_model_id: str | None = None
    fallback_enabled: bool = True
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ModelRuntimeStats:
    model_id: str
    sample_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    ttft_samples_ms: tuple[int, ...] = field(default_factory=tuple)
    total_latency_samples_ms: tuple[int, ...] = field(default_factory=tuple)
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    updated_at: datetime | None = None

    @property
    def success_rate(self) -> float | None:
        if self.sample_count <= 0:
            return None
        return self.success_count / self.sample_count


@dataclass(frozen=True)
class ModelProbeStats:
    """Synthetic fixed-prompt measurements kept separate from live traffic."""

    model_id: str
    sample_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    latency_samples_ms: tuple[int, ...] = field(default_factory=tuple)
    last_latency_ms: int | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    updated_at: datetime | None = None

    @property
    def success_rate(self) -> float | None:
        if self.sample_count <= 0:
            return None
        return self.success_count / self.sample_count
