"""Application service for global model configuration and passive telemetry."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import math
from threading import Lock
from typing import Any, Callable, Iterable

from ai_agent_platform.integrations.model_router import (
    ModelCapabilities,
    ModelConfig,
    ModelRouter,
)

from .models import (
    REAL_PROVIDERS,
    SUPPORTED_PROVIDERS,
    ModelRuntimeStats,
    ProviderConnection,
    RegisteredModel,
    SessionModelPreference,
)
from .discovery import (
    DiscoveredModel,
    ModelDiscovery,
    ModelDiscoveryError,
    ProviderModelDiscovery,
)
from .profiles import build_registration_profile
from .repository import ModelRegistryRepository
from .secrets import SecretStore, SecretStoreError
from .selection import ModelSelection


class ModelRegistryNotFoundError(KeyError):
    pass


class ModelRegistryConflictError(ValueError):
    pass


class ModelConnectionTestError(RuntimeError):
    pass


class ModelRegistryService:
    """Keeps persistent registration and the in-process router in sync."""

    _MAX_LATENCY_SAMPLES = 100

    def __init__(
        self,
        repository: ModelRegistryRepository,
        secret_store: SecretStore,
        *,
        initial_models: Iterable[ModelConfig],
        model_discovery: ModelDiscovery | None = None,
    ) -> None:
        self._repository = repository
        self._secret_store = secret_store
        self._router: ModelRouter | None = None
        self._catalog_changed: Callable[[tuple[ModelConfig, ...]], None] | None = None
        self._test_connection: Callable[[str, str], dict[str, Any]] | None = None
        self._model_discovery = model_discovery or ProviderModelDiscovery()
        self._discovered_models: dict[str, dict[str, DiscoveredModel]] = {}
        self._stats_lock = Lock()
        self._bootstrap(tuple(initial_models))

    def bind_runtime(
        self,
        *,
        router: ModelRouter,
        catalog_changed: Callable[[tuple[ModelConfig, ...]], None],
        test_connection: Callable[[str, str], dict[str, Any]],
    ) -> None:
        self._router = router
        self._catalog_changed = catalog_changed
        self._test_connection = test_connection
        self._notify_catalog_changed()

    def list_connections(self) -> list[dict[str, Any]]:
        models = self._repository.list_models()
        by_provider: dict[str, list[RegisteredModel]] = {}
        for model in models:
            by_provider.setdefault(model.provider, []).append(model)
        return [
            self._connection_view(connection, by_provider.get(connection.provider, []))
            for connection in self._repository.list_connections()
            if connection.provider in SUPPORTED_PROVIDERS
        ]

    def list_models(self) -> list[dict[str, Any]]:
        return [self._model_view(model) for model in self._repository.list_models()]

    def registry_view(self) -> dict[str, Any]:
        return {
            "connections": self.list_connections(),
            "models": self.list_models(),
            "routing_policies": ["smart", "quality", "cost", "latency"],
        }

    def upsert_connection(
        self,
        *,
        provider: str,
        display_name: str,
        api_key: str | None,
        enabled: bool,
    ) -> dict[str, Any]:
        provider = provider.strip().lower()
        if provider not in REAL_PROVIDERS:
            raise ValueError(f"unsupported provider: {provider}")
        now = _now()
        existing = self._repository.get_connection(provider)
        secret_ref = existing.secret_ref if existing else None
        previous_secret_ref = secret_ref
        if api_key is not None:
            if not api_key.strip():
                raise ValueError("API key must not be blank")
            secret_ref = f"model-provider:{provider}"
            normalized_api_key = api_key.strip()
            self._secret_store.set(secret_ref, normalized_api_key)
            if self._secret_store.get(secret_ref) != normalized_api_key:
                raise SecretStoreError("stored API key could not be read back")
        connection = ProviderConnection(
            provider=provider,
            display_name=display_name.strip() or _default_provider_name(provider),
            secret_ref=secret_ref,
            enabled=enabled,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        stored = self._repository.upsert_connection(connection)
        if (
            api_key is not None
            and previous_secret_ref
            and previous_secret_ref != secret_ref
            and not previous_secret_ref.startswith("env:")
        ):
            self._secret_store.delete(previous_secret_ref)
        self._notify_catalog_changed()
        provider_models = [
            model for model in self._repository.list_models() if model.provider == provider
        ]
        return self._connection_view(stored, provider_models)

    def delete_connection(self, provider: str) -> None:
        connection = self._repository.get_connection(provider)
        if connection is None:
            raise ModelRegistryNotFoundError(provider)
        self._repository.delete_connection(provider)
        self._discovered_models.pop(provider, None)
        if connection.secret_ref and not connection.secret_ref.startswith("env:"):
            self._secret_store.delete(connection.secret_ref)
        self._notify_catalog_changed()

    def discover_models(self, provider: str) -> dict[str, Any]:
        provider = provider.strip().lower()
        connection = self._repository.get_connection(provider)
        if connection is None:
            raise ModelRegistryNotFoundError(provider)
        if not connection.enabled:
            raise ValueError("provider connection is disabled")
        try:
            api_key = self.credential_for_provider(provider)
        except SecretStoreError as exc:
            raise ModelDiscoveryError("configured API key could not be read") from exc
        if not api_key:
            raise ValueError("API key is not configured")
        discovered = self._model_discovery.discover(provider, api_key)
        self._discovered_models[provider] = {item.model: item for item in discovered}
        registered = {
            item.model
            for item in self._repository.list_models()
            if item.provider == provider
        }
        return {
            "provider": provider,
            "models": [
                self._discovered_model_view(
                    provider,
                    item,
                    already_registered=item.model in registered,
                )
                for item in discovered
            ],
        }

    def register_model(
        self,
        *,
        provider: str,
        model: str,
        enabled: bool = True,
        auto_eligible: bool = True,
    ) -> dict[str, Any]:
        provider = provider.strip().lower()
        model = model.strip()
        connection = self._repository.get_connection(provider)
        if connection is None:
            raise ModelRegistryNotFoundError(provider)
        if not model:
            raise ValueError("model ID must not be blank")
        discovered = self._discovered_models.get(provider, {}).get(model)
        profile = build_registration_profile(provider, model, discovered)
        return self.create_model(
            provider=profile.provider,
            model=profile.model,
            display_name=profile.display_name,
            context_window_tokens=profile.context_window_tokens,
            tool_calling=profile.tool_calling,
            structured_output=profile.structured_output,
            input_cost_per_million=profile.input_cost_per_million,
            output_cost_per_million=profile.output_cost_per_million,
            quality_score=profile.quality_score,
            configured_latency_ms=profile.configured_latency_ms,
            enabled=enabled,
            auto_eligible=auto_eligible,
        )

    def create_model(self, **values: Any) -> dict[str, Any]:
        provider = str(values["provider"]).strip().lower()
        if self._repository.get_connection(provider) is None:
            raise ModelRegistryNotFoundError(provider)
        model_name = str(values["model"]).strip()
        if self._repository.get_model_by_key(provider, model_name) is not None:
            raise ModelRegistryConflictError(f"model already exists: {provider}:{model_name}")
        now = _now()
        model = _registered_model(
            model_id=_stable_model_id(provider, model_name),
            created_at=now,
            updated_at=now,
            **values,
        )
        stored = self._repository.upsert_model(model)
        self._notify_catalog_changed()
        return self._model_view(stored)

    def update_model(self, model_id: str, **values: Any) -> dict[str, Any]:
        existing = self._repository.get_model(model_id)
        if existing is None:
            raise ModelRegistryNotFoundError(model_id)
        provider = str(values.get("provider", existing.provider)).strip().lower()
        if self._repository.get_connection(provider) is None:
            raise ModelRegistryNotFoundError(provider)
        payload = {
            "provider": provider,
            "model": values.get("model", existing.model),
            "display_name": values.get("display_name", existing.display_name),
            "context_window_tokens": values.get(
                "context_window_tokens", existing.context_window_tokens
            ),
            "tool_calling": values.get("tool_calling", existing.tool_calling),
            "structured_output": values.get(
                "structured_output", existing.structured_output
            ),
            "input_cost_per_million": values.get(
                "input_cost_per_million", existing.input_cost_per_million
            ),
            "output_cost_per_million": values.get(
                "output_cost_per_million", existing.output_cost_per_million
            ),
            "quality_score": values.get("quality_score", existing.quality_score),
            "configured_latency_ms": values.get(
                "configured_latency_ms", existing.configured_latency_ms
            ),
            "enabled": values.get("enabled", existing.enabled),
            "auto_eligible": values.get("auto_eligible", existing.auto_eligible),
        }
        model = _registered_model(
            model_id=existing.id,
            created_at=existing.created_at,
            updated_at=_now(),
            **payload,
        )
        duplicate = self._repository.get_model_by_key(model.provider, model.model)
        if duplicate is not None and duplicate.id != model.id:
            raise ModelRegistryConflictError(f"model already exists: {model.key}")
        stored = self._repository.upsert_model(model)
        self._notify_catalog_changed()
        return self._model_view(stored)

    def delete_model(self, model_id: str) -> None:
        if self._repository.get_model(model_id) is None:
            raise ModelRegistryNotFoundError(model_id)
        self._repository.delete_model(model_id)
        self._notify_catalog_changed()

    def get_preference(self, session_id: str) -> SessionModelPreference:
        preference = self._repository.get_session_preference(
            session_id
        ) or SessionModelPreference(session_id=session_id)
        if preference.mode == "manual" and (
            not preference.preferred_model_id
            or self._repository.get_model(preference.preferred_model_id) is None
        ):
            preference = replace(
                preference,
                mode="auto",
                preferred_model_id=None,
                fallback_enabled=True,
                updated_at=_now(),
            )
            return self._repository.upsert_session_preference(preference)
        return preference

    def set_preference(
        self,
        *,
        session_id: str,
        mode: str,
        routing_policy: str,
        preferred_model_id: str | None,
        fallback_enabled: bool,
    ) -> SessionModelPreference:
        if mode not in {"auto", "manual"}:
            raise ValueError("mode must be auto or manual")
        if routing_policy not in {"smart", "quality", "cost", "latency"}:
            raise ValueError("unsupported routing policy")
        if mode == "manual":
            if not preferred_model_id:
                raise ValueError("manual mode requires preferred_model_id")
            model = self._repository.get_model(preferred_model_id)
            if model is None:
                raise ModelRegistryNotFoundError(preferred_model_id)
            if not self.is_model_available(model.provider, model.model):
                raise ValueError(
                    "preferred model and provider connection must both be enabled"
                )
        else:
            preferred_model_id = None
        preference = SessionModelPreference(
            session_id=session_id,
            mode=mode,  # type: ignore[arg-type]
            routing_policy=routing_policy,  # type: ignore[arg-type]
            preferred_model_id=preferred_model_id,
            fallback_enabled=fallback_enabled,
            updated_at=_now(),
        )
        return self._repository.upsert_session_preference(preference)

    def selection_for_session(self, session_id: str) -> ModelSelection:
        return self.resolve_preference(self.get_preference(session_id))

    def snapshot_run_preference(self, run_id: str, session_id: str) -> ModelSelection:
        preference = self.get_preference(session_id)
        self._repository.upsert_run_preference(run_id, preference)
        return self.resolve_preference(preference)

    def snapshot_run_selection(
        self,
        run_id: str,
        session_id: str,
        selection: ModelSelection,
    ) -> ModelSelection:
        self._repository.upsert_run_selection(run_id, session_id, selection)
        return selection

    def selection_for_run(self, run_id: str, session_id: str) -> ModelSelection:
        selection = self._repository.get_run_selection(run_id)
        if selection is not None:
            return selection
        preference = self._repository.get_run_preference(run_id)
        return self.resolve_preference(preference or self.get_preference(session_id))

    def resolve_preference(self, preference: SessionModelPreference) -> ModelSelection:
        model = (
            self._repository.get_model(preference.preferred_model_id)
            if preference.preferred_model_id
            else None
        )
        return ModelSelection(
            mode=preference.mode,
            routing_policy=preference.routing_policy,
            preferred_model_id=preference.preferred_model_id,
            preferred_provider=model.provider if model else None,
            preferred_model=model.model if model else None,
            fallback_enabled=preference.fallback_enabled,
        )

    def credential_for_provider(self, provider: str) -> str | None:
        connection = self._repository.get_connection(provider)
        if connection is None or not connection.enabled or not connection.secret_ref:
            return None
        if connection.secret_ref.startswith("env:"):
            return None
        return self._secret_store.get(connection.secret_ref)

    def is_model_available(self, provider: str, model: str) -> bool:
        connection = self._repository.get_connection(provider)
        registered = self._repository.get_model_by_key(provider, model)
        return bool(
            connection
            and connection.enabled
            and registered
            and registered.enabled
        )

    def model_configs(self) -> tuple[ModelConfig, ...]:
        connections = {
            connection.provider: connection
            for connection in self._repository.list_connections()
        }
        configs = []
        for model in self._repository.list_models():
            connection = connections.get(model.provider)
            stats = self._repository.get_runtime_stats(model.id)
            observed_latency = (
                _percentile(stats.total_latency_samples_ms, 0.50)
                if stats is not None and stats.success_count > 0
                else None
            )
            configs.append(
                ModelConfig(
                    provider=model.provider,
                    model=model.model,
                    context_window_tokens=model.context_window_tokens,
                    capabilities=ModelCapabilities(
                        tool_calling=model.tool_calling,
                        structured_output=model.structured_output,
                    ),
                    input_cost_per_million=model.input_cost_per_million,
                    output_cost_per_million=model.output_cost_per_million,
                    quality_score=model.quality_score,
                    latency_ms=observed_latency or model.configured_latency_ms,
                    enabled=bool(connection and connection.enabled and model.enabled),
                    auto_eligible=model.auto_eligible,
                )
            )
        return tuple(configs)

    def record_success(
        self,
        provider: str,
        model: str,
        *,
        total_latency_ms: int,
        ttft_ms: int | None,
    ) -> None:
        registered = self._repository.get_model_by_key(provider, model)
        if registered is None:
            return
        with self._stats_lock:
            current = self._repository.get_runtime_stats(registered.id) or ModelRuntimeStats(
                model_id=registered.id
            )
            ttft = list(current.ttft_samples_ms)
            total = list(current.total_latency_samples_ms)
            if ttft_ms is not None:
                ttft.append(max(0, int(ttft_ms)))
            total.append(max(0, int(total_latency_ms)))
            now = _now()
            self._repository.upsert_runtime_stats(
                replace(
                    current,
                    sample_count=current.sample_count + 1,
                    success_count=current.success_count + 1,
                    ttft_samples_ms=tuple(ttft[-self._MAX_LATENCY_SAMPLES :]),
                    total_latency_samples_ms=tuple(total[-self._MAX_LATENCY_SAMPLES :]),
                    last_success_at=now,
                    last_error=None,
                    updated_at=now,
                )
            )
        self._notify_catalog_changed()

    def record_failure(
        self,
        provider: str,
        model: str,
        *,
        total_latency_ms: int,
        error: str,
    ) -> None:
        registered = self._repository.get_model_by_key(provider, model)
        if registered is None:
            return
        with self._stats_lock:
            current = self._repository.get_runtime_stats(registered.id) or ModelRuntimeStats(
                model_id=registered.id
            )
            total = list(current.total_latency_samples_ms)
            total.append(max(0, int(total_latency_ms)))
            now = _now()
            self._repository.upsert_runtime_stats(
                replace(
                    current,
                    sample_count=current.sample_count + 1,
                    failure_count=current.failure_count + 1,
                    total_latency_samples_ms=tuple(total[-self._MAX_LATENCY_SAMPLES :]),
                    last_failure_at=now,
                    last_error=error[:500],
                    updated_at=now,
                )
            )

    def test_provider_connection(self, provider: str) -> dict[str, Any]:
        connection = self._repository.get_connection(provider)
        if connection is None:
            raise ModelRegistryNotFoundError(provider)
        if not connection.enabled:
            raise ModelConnectionTestError("provider connection is disabled")
        if provider in REAL_PROVIDERS and not self.credential_for_provider(provider):
            raise ModelConnectionTestError("API key is not configured")
        model = next(
            (
                item
                for item in self._repository.list_models()
                if item.provider == provider and item.enabled
            ),
            None,
        )
        if model is None:
            raise ModelConnectionTestError("register an enabled model before testing")
        if self._test_connection is None:
            raise ModelConnectionTestError("model runtime is not attached")
        try:
            return self._test_connection(provider, model.model)
        except Exception as exc:
            raise ModelConnectionTestError(str(exc)) from exc

    def _bootstrap(self, initial_models: tuple[ModelConfig, ...]) -> None:
        now = _now()
        connections = {item.provider for item in initial_models}
        for provider in sorted(connections):
            if self._repository.get_connection(provider) is None:
                self._repository.upsert_connection(
                    ProviderConnection(
                        provider=provider,
                        display_name=_default_provider_name(provider),
                        secret_ref=None,
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
        for config in initial_models:
            if self._repository.get_model_by_key(config.provider, config.model) is not None:
                continue
            self._repository.upsert_model(
                RegisteredModel(
                    id=_stable_model_id(config.provider, config.model),
                    provider=config.provider,
                    model=config.model,
                    display_name=config.model,
                    context_window_tokens=config.context_window_tokens,
                    tool_calling=config.capabilities.tool_calling,
                    structured_output=config.capabilities.structured_output,
                    input_cost_per_million=config.input_cost_per_million,
                    output_cost_per_million=config.output_cost_per_million,
                    quality_score=config.quality_score,
                    configured_latency_ms=config.latency_ms,
                    enabled=config.enabled,
                    auto_eligible=config.auto_eligible,
                    created_at=now,
                    updated_at=now,
                )
            )

    def _notify_catalog_changed(self) -> None:
        if self._catalog_changed is not None:
            self._catalog_changed(self.model_configs())

    def _connection_view(
        self,
        connection: ProviderConnection,
        models: list[RegisteredModel],
    ) -> dict[str, Any]:
        credential_configured, credential_error = self._credential_status(connection)
        health = (
            self._router.health.snapshot(connection.provider)
            if self._router is not None
            else None
        )
        stats = [
            self._repository.get_runtime_stats(model.id)
            for model in models
        ]
        samples = sum(item.sample_count for item in stats if item is not None)
        failures = sum(item.failure_count for item in stats if item is not None)
        if not connection.enabled:
            status = "disabled"
        elif not credential_configured:
            status = "unavailable"
        elif health is not None and not health.available:
            status = "unavailable"
        elif samples == 0:
            status = "unknown"
        elif failures / samples >= 0.5:
            status = "degraded"
        else:
            status = "available"
        return {
            "provider": connection.provider,
            "display_name": connection.display_name,
            "enabled": connection.enabled,
            "credential_configured": credential_configured,
            "credential_error": credential_error,
            "status": status,
            "health": health.to_dict() if health is not None else None,
            "model_count": len(models),
            "created_at": connection.created_at,
            "updated_at": connection.updated_at,
        }

    def _model_view(self, model: RegisteredModel) -> dict[str, Any]:
        stats = self._repository.get_runtime_stats(model.id) or ModelRuntimeStats(
            model_id=model.id
        )
        health = (
            self._router.health.snapshot(model.provider)
            if self._router is not None
            else None
        )
        connection = self._repository.get_connection(model.provider)
        observed_latency = (
            _percentile(stats.total_latency_samples_ms, 0.50)
            if stats.success_count > 0
            else None
        )
        profile = build_registration_profile(model.provider, model.model)
        credential_configured = False
        if connection is not None:
            credential_configured, _ = self._credential_status(connection)
        if not model.enabled or not connection or not connection.enabled:
            status = "disabled"
        elif not credential_configured:
            status = "unavailable"
        elif health is not None and not health.available:
            status = "unavailable"
        elif stats.sample_count == 0:
            status = "unknown"
        elif stats.success_rate is not None and stats.success_rate < 0.5:
            status = "degraded"
        else:
            status = "available"
        return {
            "id": model.id,
            "provider": model.provider,
            "model": model.model,
            "display_name": model.display_name,
            "capabilities": {
                "tool_calling": model.tool_calling,
                "structured_output": model.structured_output,
            },
            "context_window_tokens": model.context_window_tokens,
            "input_cost_per_million": model.input_cost_per_million,
            "output_cost_per_million": model.output_cost_per_million,
            "quality_score": model.quality_score,
            "configured_latency_ms": model.configured_latency_ms,
            "routing_metadata": {
                "quality_tier": profile.quality_tier,
                "cost_tier": profile.cost_tier,
                "metadata_source": profile.metadata_source,
                "routing_latency_ms": observed_latency
                or model.configured_latency_ms,
                "latency_source": (
                    "observed_p50" if observed_latency is not None else "backend_prior"
                ),
            },
            "enabled": model.enabled,
            "auto_eligible": model.auto_eligible,
            "status": status,
            "health": health.to_dict() if health is not None else None,
            "telemetry": {
                "sample_count": stats.sample_count,
                "success_count": stats.success_count,
                "failure_count": stats.failure_count,
                "success_rate": (
                    round(stats.success_rate, 6)
                    if stats.success_rate is not None
                    else None
                ),
                "ttft_p50_ms": _percentile(stats.ttft_samples_ms, 0.50),
                "ttft_p95_ms": _percentile(stats.ttft_samples_ms, 0.95),
                "total_latency_p50_ms": _percentile(
                    stats.total_latency_samples_ms, 0.50
                ),
                "total_latency_p95_ms": _percentile(
                    stats.total_latency_samples_ms, 0.95
                ),
                "last_success_at": stats.last_success_at,
                "last_failure_at": stats.last_failure_at,
                "last_error": stats.last_error,
            },
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }

    def _discovered_model_view(
        self,
        provider: str,
        discovered: DiscoveredModel,
        *,
        already_registered: bool,
    ) -> dict[str, Any]:
        profile = build_registration_profile(provider, discovered.model, discovered)
        return {
            "provider": provider,
            "model": profile.model,
            "display_name": profile.display_name,
            "context_window_tokens": profile.context_window_tokens,
            "capabilities": {
                "tool_calling": profile.tool_calling,
                "structured_output": profile.structured_output,
            },
            "quality_tier": profile.quality_tier,
            "cost_tier": profile.cost_tier,
            "metadata_source": profile.metadata_source,
            "already_registered": already_registered,
        }

    def _credential_status(
        self,
        connection: ProviderConnection,
    ) -> tuple[bool, str | None]:
        if connection.provider == "fake":
            return True, None
        if not connection.secret_ref:
            return False, None
        if connection.secret_ref.startswith("env:"):
            return (
                False,
                "environment-based Provider credentials are disabled; "
                "re-enter the API key in model management",
            )
        try:
            return bool(self._secret_store.get(connection.secret_ref)), None
        except SecretStoreError as exc:
            return False, str(exc)


def _registered_model(
    *,
    model_id: str,
    provider: str,
    model: str,
    display_name: str,
    context_window_tokens: int,
    tool_calling: bool,
    structured_output: bool,
    input_cost_per_million: float,
    output_cost_per_million: float,
    quality_score: float,
    configured_latency_ms: int,
    enabled: bool,
    auto_eligible: bool,
    created_at: datetime,
    updated_at: datetime,
) -> RegisteredModel:
    provider = str(provider).strip().lower()
    model = str(model).strip()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    if not model:
        raise ValueError("model ID must not be blank")
    if int(context_window_tokens) <= 0:
        raise ValueError("context window must be positive")
    if float(input_cost_per_million) < 0 or float(output_cost_per_million) < 0:
        raise ValueError("model prices must not be negative")
    if not 0 <= float(quality_score) <= 1:
        raise ValueError("quality score must be between 0 and 1")
    if int(configured_latency_ms) <= 0:
        raise ValueError("configured latency must be positive")
    return RegisteredModel(
        id=model_id,
        provider=provider,
        model=model,
        display_name=str(display_name).strip() or model,
        context_window_tokens=int(context_window_tokens),
        tool_calling=bool(tool_calling),
        structured_output=bool(structured_output),
        input_cost_per_million=float(input_cost_per_million),
        output_cost_per_million=float(output_cost_per_million),
        quality_score=float(quality_score),
        configured_latency_ms=int(configured_latency_ms),
        enabled=bool(enabled),
        auto_eligible=bool(auto_eligible),
        created_at=created_at,
        updated_at=updated_at,
    )


def _stable_model_id(provider: str, model: str) -> str:
    digest = hashlib.sha256(f"{provider}:{model}".encode("utf-8")).hexdigest()[:16]
    return f"mdl_{digest}"


def _default_provider_name(provider: str) -> str:
    return {
        "anthropic": "Anthropic",
        "deepseek": "DeepSeek",
        "fake": "Local Fake",
        "google": "Google",
        "openai": "OpenAI",
    }.get(provider, provider.title())


def _percentile(values: tuple[int, ...], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return int(ordered[rank])


def _now() -> datetime:
    return datetime.now(timezone.utc)
