from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, model_validator


ProviderName = Literal["openai", "deepseek", "anthropic", "google"]
RoutingPolicyName = Literal["smart", "quality", "cost", "latency"]


class ProviderConnectionUpsertRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    api_key: SecretStr | None = None
    enabled: bool = True


class ProviderConnectionResponse(BaseModel):
    provider: str
    display_name: str
    enabled: bool
    credential_configured: bool
    credential_error: str | None = None
    status: str
    health: dict[str, Any] | None
    model_count: int
    created_at: datetime
    updated_at: datetime


class RegisteredModelCreateRequest(BaseModel):
    provider: ProviderName
    model: str = Field(min_length=1, max_length=128)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    enabled: bool = True
    auto_eligible: bool = True


class RegisteredModelUpdateRequest(BaseModel):
    enabled: bool
    auto_eligible: bool
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)


class DiscoveredModelResponse(BaseModel):
    provider: str
    model: str
    display_name: str
    context_window_tokens: int
    max_output_tokens: int
    capabilities: dict[str, bool]
    quality_tier: str
    cost_tier: str
    metadata_source: str
    already_registered: bool


class ModelDiscoveryResponse(BaseModel):
    provider: str
    models: list[DiscoveredModelResponse]


class RegisteredModelResponse(BaseModel):
    id: str
    provider: str
    model: str
    display_name: str
    capabilities: dict[str, bool]
    context_window_tokens: int
    max_output_tokens: int
    input_cost_per_million: float
    output_cost_per_million: float
    quality_score: float
    configured_latency_ms: int
    routing_metadata: dict[str, Any]
    enabled: bool
    auto_eligible: bool
    status: str
    health: dict[str, Any] | None
    telemetry: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ModelRegistryResponse(BaseModel):
    connections: list[ProviderConnectionResponse]
    models: list[RegisteredModelResponse]
    routing_policies: list[str]


class SessionModelPreferenceRequest(BaseModel):
    mode: Literal["auto", "manual"] = "auto"
    routing_policy: RoutingPolicyName = "smart"
    preferred_model_id: str | None = Field(default=None, max_length=80)
    fallback_enabled: bool = True

    @model_validator(mode="after")
    def validate_manual_model(self):
        if self.mode == "manual" and not self.preferred_model_id:
            raise ValueError("manual mode requires preferred_model_id")
        if self.mode == "auto":
            self.preferred_model_id = None
        return self


class SessionModelPreferenceResponse(BaseModel):
    session_id: str
    mode: str
    routing_policy: str
    preferred_model_id: str | None
    fallback_enabled: bool
    updated_at: datetime | None


class ModelConnectionTestResponse(BaseModel):
    provider: str
    model: str
    status: str
    elapsed_ms: int
