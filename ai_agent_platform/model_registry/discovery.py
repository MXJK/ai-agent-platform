"""Provider-backed discovery of text-generation models available to one API key."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

import httpx


class ModelDiscoveryError(RuntimeError):
    """A sanitized provider-discovery failure safe to return through the API."""


@dataclass(frozen=True)
class DiscoveredModel:
    model: str
    display_name: str
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    tool_calling: bool | None = None
    structured_output: bool | None = None


DOUBAO_MODEL_CATALOG = {
    "doubao-seed-evolving": DiscoveredModel(
        model="doubao-seed-evolving",
        display_name="Doubao-Seed-Evolving",
        context_window_tokens=1_024_000,
        max_output_tokens=256_000,
        tool_calling=True,
        structured_output=True,
    ),
    "doubao-seed-2.1-turbo": DiscoveredModel(
        model="doubao-seed-2.1-turbo",
        display_name="Doubao-Seed-2.1-turbo",
        context_window_tokens=256_000,
        max_output_tokens=256_000,
        tool_calling=True,
        structured_output=True,
    ),
    "doubao-seed-2.0-lite": DiscoveredModel(
        model="doubao-seed-2.0-lite",
        display_name="Doubao-Seed-2.0-lite",
        context_window_tokens=256_000,
        max_output_tokens=128_000,
        tool_calling=True,
        structured_output=True,
    ),
}


JsonGetter = Callable[
    [str, Mapping[str, str], Mapping[str, str | int]],
    Mapping[str, Any],
]


class ModelDiscovery(Protocol):
    def discover(
        self,
        provider: str,
        api_key: str,
    ) -> tuple[DiscoveredModel, ...]: ...


class ProviderModelDiscovery:
    """Calls fixed official model-list endpoints and normalizes their responses."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        json_getter: JsonGetter | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._json_getter = json_getter or self._get_json

    def discover(self, provider: str, api_key: str) -> tuple[DiscoveredModel, ...]:
        if provider == "openai":
            payload = self._json_getter(
                "https://api.openai.com/v1/models",
                {"Authorization": f"Bearer {api_key}"},
                {},
            )
            models = _parse_openai_compatible_models(payload, provider=provider)
        elif provider == "deepseek":
            payload = self._json_getter(
                "https://api.deepseek.com/models",
                {"Authorization": f"Bearer {api_key}"},
                {},
            )
            models = _parse_openai_compatible_models(payload, provider=provider)
        elif provider == "glm":
            payload = self._json_getter(
                "https://open.bigmodel.cn/api/paas/v4/models",
                {"Authorization": f"Bearer {api_key}"},
                {},
            )
            models = _parse_openai_compatible_models(payload, provider=provider)
        elif provider == "minimax":
            payload = self._json_getter(
                "https://api.minimaxi.com/v1/models",
                {"Authorization": f"Bearer {api_key}"},
                {},
            )
            models = _parse_openai_compatible_models(payload, provider=provider)
        elif provider == "doubao":
            payload = self._json_getter(
                "https://ark.cn-beijing.volces.com/api/v3/models",
                {"Authorization": f"Bearer {api_key}"},
                {},
            )
            models = _parse_openai_compatible_models(payload, provider=provider)
        elif provider == "anthropic":
            payload = self._json_getter(
                "https://api.anthropic.com/v1/models",
                {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                {"limit": 1000},
            )
            models = _parse_anthropic_models(payload)
        elif provider == "google":
            payload = self._json_getter(
                "https://generativelanguage.googleapis.com/v1beta/models",
                {"x-goog-api-key": api_key},
                {"pageSize": 1000},
            )
            models = _parse_google_models(payload)
        else:
            raise ModelDiscoveryError(f"model discovery is not supported for {provider}")

        unique = {item.model: item for item in models}
        return tuple(
            sorted(
                unique.values(),
                key=lambda item: (item.display_name.lower(), item.model),
            )
        )

    def _get_json(
        self,
        url: str,
        headers: Mapping[str, str],
        params: Mapping[str, str | int],
    ) -> Mapping[str, Any]:
        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.get(url, headers=dict(headers), params=dict(params))
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                message = "provider rejected the configured API key"
            elif status_code == 429:
                message = "provider rate-limited model discovery"
            else:
                message = f"provider model discovery failed with HTTP {status_code}"
            raise ModelDiscoveryError(message) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise ModelDiscoveryError(
                "provider model discovery could not be completed"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ModelDiscoveryError("provider returned an invalid model catalog")
        return payload


def _parse_openai_compatible_models(
    payload: Mapping[str, Any],
    *,
    provider: str,
) -> tuple[DiscoveredModel, ...]:
    raw_models = payload.get("data", [])
    if not isinstance(raw_models, list):
        raise ModelDiscoveryError("provider returned an invalid model catalog")
    models = []
    for raw in raw_models:
        if not isinstance(raw, Mapping):
            continue
        model_id = str(raw.get("id", "")).strip()
        if not model_id or not _is_text_generation_model(provider, model_id):
            continue
        if provider == "doubao":
            models.append(DOUBAO_MODEL_CATALOG[model_id.lower()])
            continue
        models.append(
            DiscoveredModel(
                model=model_id,
                display_name=_humanize_model_id(model_id),
                tool_calling=True,
                structured_output=True,
            )
        )
    return tuple(models)


def _parse_anthropic_models(
    payload: Mapping[str, Any],
) -> tuple[DiscoveredModel, ...]:
    raw_models = payload.get("data", [])
    if not isinstance(raw_models, list):
        raise ModelDiscoveryError("provider returned an invalid model catalog")
    models = []
    for raw in raw_models:
        if not isinstance(raw, Mapping):
            continue
        model_id = str(raw.get("id", "")).strip()
        if not model_id:
            continue
        capabilities = raw.get("capabilities", {})
        if not isinstance(capabilities, Mapping):
            capabilities = {}
        structured = capabilities.get("structured_outputs", {})
        structured_supported = (
            bool(structured.get("supported"))
            if isinstance(structured, Mapping)
            else True
        )
        max_input_tokens = _positive_int(raw.get("max_input_tokens"))
        models.append(
            DiscoveredModel(
                model=model_id,
                display_name=str(raw.get("display_name") or "").strip()
                or _humanize_model_id(model_id),
                context_window_tokens=max_input_tokens,
                max_output_tokens=_positive_int(raw.get("max_output_tokens")),
                tool_calling=True,
                structured_output=structured_supported,
            )
        )
    return tuple(models)


def _parse_google_models(payload: Mapping[str, Any]) -> tuple[DiscoveredModel, ...]:
    raw_models = payload.get("models", [])
    if not isinstance(raw_models, list):
        raise ModelDiscoveryError("provider returned an invalid model catalog")
    models = []
    for raw in raw_models:
        if not isinstance(raw, Mapping):
            continue
        methods = raw.get("supportedGenerationMethods", [])
        if not isinstance(methods, list) or "generateContent" not in methods:
            continue
        resource_name = str(raw.get("name", "")).strip()
        model_id = resource_name.removeprefix("models/")
        if not model_id or _has_non_text_marker(model_id):
            continue
        models.append(
            DiscoveredModel(
                model=model_id,
                display_name=str(raw.get("displayName") or "").strip()
                or _humanize_model_id(model_id),
                context_window_tokens=_positive_int(raw.get("inputTokenLimit")),
                max_output_tokens=_positive_int(raw.get("outputTokenLimit")),
                tool_calling=True,
                structured_output=True,
            )
        )
    return tuple(models)


def _is_text_generation_model(provider: str, model_id: str) -> bool:
    if _has_non_text_marker(model_id):
        return False
    value = model_id.lower()
    if provider == "deepseek":
        return value.startswith("deepseek-")
    if provider == "glm":
        return value.startswith("glm-")
    if provider == "doubao":
        return value in DOUBAO_MODEL_CATALOG
    if provider == "minimax":
        return value.startswith(("minimax-", "abab"))
    if value.startswith("ft:"):
        value = value[3:]
    return value.startswith(("chatgpt-", "codex-", "gpt-")) or (
        len(value) > 1 and value[0] == "o" and value[1].isdigit()
    )


def _has_non_text_marker(model_id: str) -> bool:
    value = model_id.lower()
    return any(
        marker in value
        for marker in (
            "audio",
            "embedding",
            "image",
            "live",
            "moderation",
            "ocr",
            "realtime",
            "transcribe",
            "tts",
            "veo",
        )
    )


def _humanize_model_id(model_id: str) -> str:
    words = model_id.replace(":", " ").replace("_", " ").replace("-", " ").split()
    aliases = {
        "ai": "AI",
        "api": "API",
        "glm": "GLM",
        "gpt": "GPT",
        "minimax": "MiniMax",
        "doubao": "Doubao",
        "tts": "TTS",
    }
    return " ".join(aliases.get(word.lower(), word.title()) for word in words)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
