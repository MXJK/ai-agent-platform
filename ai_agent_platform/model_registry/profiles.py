"""Backend-owned routing priors for newly discovered model registrations."""

from __future__ import annotations

from dataclasses import dataclass

from .discovery import DiscoveredModel, _humanize_model_id


@dataclass(frozen=True)
class ModelRegistrationProfile:
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
    quality_tier: str
    cost_tier: str
    metadata_source: str


_PROVIDER_PRIORS = {
    "openai": (128_000, 16_384, 1.25, 10.0, 0.80, 1_100),
    "deepseek": (128_000, 8_192, 0.30, 1.20, 0.76, 1_000),
    "anthropic": (200_000, 16_384, 3.0, 15.0, 0.82, 1_300),
    "google": (1_000_000, 16_384, 0.50, 3.0, 0.78, 900),
    "fake": (128_000, 4_096, 0.0, 0.0, 0.50, 10),
}


def build_registration_profile(
    provider: str,
    model: str,
    discovered: DiscoveredModel | None = None,
) -> ModelRegistrationProfile:
    context, max_output, input_cost, output_cost, quality, latency = _PROVIDER_PRIORS.get(
        provider,
        (128_000, 8_192, 1.0, 4.0, 0.70, 1_200),
    )
    value = model.lower()
    quality_tier = "balanced"
    cost_tier = "standard"

    if any(token in value for token in ("nano", "lite", "haiku")):
        quality = max(0.50, quality - 0.16)
        latency = max(350, int(latency * 0.55))
        input_cost *= 0.25
        output_cost *= 0.25
        quality_tier = "efficient"
        cost_tier = "low"
    elif any(token in value for token in ("mini", "flash", "chat")):
        quality = max(0.60, quality - 0.08)
        latency = max(450, int(latency * 0.70))
        input_cost *= 0.50
        output_cost *= 0.50
        quality_tier = "balanced"
        cost_tier = "low"
    elif any(token in value for token in ("opus", "pro", "reasoner")) or (
        len(value) > 1 and value[0] == "o" and value[1].isdigit()
    ):
        quality = min(0.95, quality + 0.10)
        latency = int(latency * 1.55)
        input_cost *= 2.0
        output_cost *= 2.0
        quality_tier = "advanced"
        cost_tier = "high"

    if discovered is not None and discovered.context_window_tokens:
        context = discovered.context_window_tokens
    if discovered is not None and discovered.max_output_tokens:
        max_output = discovered.max_output_tokens

    return ModelRegistrationProfile(
        provider=provider,
        model=model,
        display_name=(
            discovered.display_name
            if discovered is not None and discovered.display_name
            else _humanize_model_id(model)
        ),
        context_window_tokens=context,
        max_output_tokens=max_output,
        tool_calling=(
            discovered.tool_calling
            if discovered is not None and discovered.tool_calling is not None
            else provider != "fake"
        ),
        structured_output=(
            discovered.structured_output
            if discovered is not None and discovered.structured_output is not None
            else provider != "fake"
        ),
        input_cost_per_million=round(input_cost, 6),
        output_cost_per_million=round(output_cost, 6),
        quality_score=round(quality, 3),
        configured_latency_ms=latency,
        quality_tier=quality_tier,
        cost_tier=cost_tier,
        metadata_source=(
            "provider_and_backend_profile" if discovered is not None else "backend_profile"
        ),
    )
