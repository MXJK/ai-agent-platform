"""Persistence adapters for the global model registry and session preferences."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Protocol

from .models import (
    ModelProbeStats,
    ModelRuntimeStats,
    ProviderConnection,
    RegisteredModel,
    SessionModelPreference,
)
from .selection import ModelSelection


class ModelRegistryRepository(Protocol):
    def list_connections(self) -> list[ProviderConnection]: ...
    def get_connection(self, provider: str) -> ProviderConnection | None: ...
    def upsert_connection(self, connection: ProviderConnection) -> ProviderConnection: ...
    def delete_connection(self, provider: str) -> None: ...
    def list_models(self) -> list[RegisteredModel]: ...
    def get_model(self, model_id: str) -> RegisteredModel | None: ...
    def get_model_by_key(self, provider: str, model: str) -> RegisteredModel | None: ...
    def upsert_model(self, model: RegisteredModel) -> RegisteredModel: ...
    def delete_model(self, model_id: str) -> None: ...
    def get_session_preference(self, session_id: str) -> SessionModelPreference | None: ...
    def upsert_session_preference(self, preference: SessionModelPreference) -> SessionModelPreference: ...
    def get_run_preference(self, run_id: str) -> SessionModelPreference | None: ...
    def upsert_run_preference(self, run_id: str, preference: SessionModelPreference) -> None: ...
    def get_run_selection(self, run_id: str) -> ModelSelection | None: ...
    def upsert_run_selection(
        self, run_id: str, session_id: str, selection: ModelSelection
    ) -> None: ...
    def get_runtime_stats(self, model_id: str) -> ModelRuntimeStats | None: ...
    def upsert_runtime_stats(self, stats: ModelRuntimeStats) -> ModelRuntimeStats: ...
    def get_probe_stats(self, model_id: str) -> ModelProbeStats | None: ...
    def upsert_probe_stats(self, stats: ModelProbeStats) -> ModelProbeStats: ...


class InMemoryModelRegistryRepository:
    def __init__(self) -> None:
        self._connections: dict[str, ProviderConnection] = {}
        self._models: dict[str, RegisteredModel] = {}
        self._session_preferences: dict[str, SessionModelPreference] = {}
        self._run_preferences: dict[str, SessionModelPreference] = {}
        self._run_selections: dict[str, ModelSelection] = {}
        self._stats: dict[str, ModelRuntimeStats] = {}
        self._probe_stats: dict[str, ModelProbeStats] = {}
        self._lock = Lock()

    def list_connections(self) -> list[ProviderConnection]:
        with self._lock:
            return sorted(self._connections.values(), key=lambda item: item.provider)

    def get_connection(self, provider: str) -> ProviderConnection | None:
        with self._lock:
            return self._connections.get(provider)

    def upsert_connection(self, connection: ProviderConnection) -> ProviderConnection:
        with self._lock:
            self._connections[connection.provider] = connection
            return connection

    def delete_connection(self, provider: str) -> None:
        with self._lock:
            self._connections.pop(provider, None)
            model_ids = [
                model_id
                for model_id, model in self._models.items()
                if model.provider == provider
            ]
            for model_id in model_ids:
                self._models.pop(model_id, None)
                self._stats.pop(model_id, None)
                self._probe_stats.pop(model_id, None)

    def list_models(self) -> list[RegisteredModel]:
        with self._lock:
            return sorted(
                self._models.values(),
                key=lambda item: (item.provider, item.display_name.lower(), item.model),
            )

    def get_model(self, model_id: str) -> RegisteredModel | None:
        with self._lock:
            return self._models.get(model_id)

    def get_model_by_key(self, provider: str, model: str) -> RegisteredModel | None:
        with self._lock:
            return next(
                (
                    item
                    for item in self._models.values()
                    if item.provider == provider and item.model == model
                ),
                None,
            )

    def upsert_model(self, model: RegisteredModel) -> RegisteredModel:
        with self._lock:
            duplicate = next(
                (
                    item
                    for item in self._models.values()
                    if item.provider == model.provider
                    and item.model == model.model
                    and item.id != model.id
                ),
                None,
            )
            if duplicate is not None:
                raise ValueError("model provider and ID must be unique")
            self._models[model.id] = model
            return model

    def delete_model(self, model_id: str) -> None:
        with self._lock:
            self._models.pop(model_id, None)
            self._stats.pop(model_id, None)
            self._probe_stats.pop(model_id, None)

    def get_session_preference(self, session_id: str) -> SessionModelPreference | None:
        with self._lock:
            return self._session_preferences.get(session_id)

    def upsert_session_preference(
        self, preference: SessionModelPreference
    ) -> SessionModelPreference:
        with self._lock:
            self._session_preferences[preference.session_id] = preference
            return preference

    def get_run_preference(self, run_id: str) -> SessionModelPreference | None:
        with self._lock:
            return self._run_preferences.get(run_id)

    def upsert_run_preference(
        self, run_id: str, preference: SessionModelPreference
    ) -> None:
        with self._lock:
            self._run_preferences[run_id] = replace(preference)

    def get_run_selection(self, run_id: str) -> ModelSelection | None:
        with self._lock:
            return self._run_selections.get(run_id)

    def upsert_run_selection(
        self,
        run_id: str,
        session_id: str,
        selection: ModelSelection,
    ) -> None:
        del session_id
        with self._lock:
            self._run_selections[run_id] = replace(selection)

    def get_runtime_stats(self, model_id: str) -> ModelRuntimeStats | None:
        with self._lock:
            return self._stats.get(model_id)

    def upsert_runtime_stats(self, stats: ModelRuntimeStats) -> ModelRuntimeStats:
        with self._lock:
            self._stats[stats.model_id] = stats
            return stats

    def get_probe_stats(self, model_id: str) -> ModelProbeStats | None:
        with self._lock:
            return self._probe_stats.get(model_id)

    def upsert_probe_stats(self, stats: ModelProbeStats) -> ModelProbeStats:
        with self._lock:
            self._probe_stats[stats.model_id] = stats
            return stats


class PostgresModelRegistryRepository:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        _require_psycopg()

    def list_connections(self) -> list[ProviderConnection]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT provider, display_name, secret_ref, enabled, created_at, updated_at
                   FROM model_provider_connections ORDER BY provider"""
            ).fetchall()
        return [_connection_from_row(row) for row in rows]

    def get_connection(self, provider: str) -> ProviderConnection | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT provider, display_name, secret_ref, enabled, created_at, updated_at
                   FROM model_provider_connections WHERE provider = %s""",
                (provider,),
            ).fetchone()
        return _connection_from_row(row) if row else None

    def upsert_connection(self, connection: ProviderConnection) -> ProviderConnection:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO model_provider_connections
                    (provider, display_name, secret_ref, enabled, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (provider) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    secret_ref = EXCLUDED.secret_ref,
                    enabled = EXCLUDED.enabled,
                    updated_at = EXCLUDED.updated_at
                RETURNING provider, display_name, secret_ref, enabled, created_at, updated_at
                """,
                (
                    connection.provider,
                    connection.display_name,
                    connection.secret_ref,
                    connection.enabled,
                    connection.created_at,
                    connection.updated_at,
                ),
            ).fetchone()
        return _connection_from_row(row)

    def delete_connection(self, provider: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM model_provider_connections WHERE provider = %s",
                (provider,),
            )

    def list_models(self) -> list[RegisteredModel]:
        with self._connect() as conn:
            rows = conn.execute(_MODEL_SELECT + " ORDER BY provider, display_name, model").fetchall()
        return [_model_from_row(row) for row in rows]

    def get_model(self, model_id: str) -> RegisteredModel | None:
        with self._connect() as conn:
            row = conn.execute(_MODEL_SELECT + " WHERE id = %s", (model_id,)).fetchone()
        return _model_from_row(row) if row else None

    def get_model_by_key(self, provider: str, model: str) -> RegisteredModel | None:
        with self._connect() as conn:
            row = conn.execute(
                _MODEL_SELECT + " WHERE provider = %s AND model = %s",
                (provider, model),
            ).fetchone()
        return _model_from_row(row) if row else None

    def upsert_model(self, model: RegisteredModel) -> RegisteredModel:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO registered_models (
                    id, provider, model, display_name, context_window_tokens,
                    max_output_tokens,
                    tool_calling, structured_output, input_cost_per_million,
                    output_cost_per_million, quality_score, configured_latency_ms,
                    enabled, auto_eligible, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    model = EXCLUDED.model,
                    display_name = EXCLUDED.display_name,
                    context_window_tokens = EXCLUDED.context_window_tokens,
                    max_output_tokens = EXCLUDED.max_output_tokens,
                    tool_calling = EXCLUDED.tool_calling,
                    structured_output = EXCLUDED.structured_output,
                    input_cost_per_million = EXCLUDED.input_cost_per_million,
                    output_cost_per_million = EXCLUDED.output_cost_per_million,
                    quality_score = EXCLUDED.quality_score,
                    configured_latency_ms = EXCLUDED.configured_latency_ms,
                    enabled = EXCLUDED.enabled,
                    auto_eligible = EXCLUDED.auto_eligible,
                    updated_at = EXCLUDED.updated_at
                RETURNING id, provider, model, display_name, context_window_tokens,
                    max_output_tokens,
                    tool_calling, structured_output, input_cost_per_million,
                    output_cost_per_million, quality_score, configured_latency_ms,
                    enabled, auto_eligible, created_at, updated_at
                """,
                _model_values(model),
            ).fetchone()
        return _model_from_row(row)

    def delete_model(self, model_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM registered_models WHERE id = %s", (model_id,))

    def get_session_preference(self, session_id: str) -> SessionModelPreference | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT session_id, mode, routing_policy, preferred_model_id,
                          fallback_enabled, updated_at
                   FROM session_model_preferences WHERE session_id = %s""",
                (session_id,),
            ).fetchone()
        return _preference_from_row(row) if row else None

    def upsert_session_preference(
        self, preference: SessionModelPreference
    ) -> SessionModelPreference:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO session_model_preferences
                    (session_id, mode, routing_policy, preferred_model_id, fallback_enabled, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET
                    mode = EXCLUDED.mode,
                    routing_policy = EXCLUDED.routing_policy,
                    preferred_model_id = EXCLUDED.preferred_model_id,
                    fallback_enabled = EXCLUDED.fallback_enabled,
                    updated_at = EXCLUDED.updated_at
                RETURNING session_id, mode, routing_policy, preferred_model_id,
                          fallback_enabled, updated_at
                """,
                (
                    preference.session_id,
                    preference.mode,
                    preference.routing_policy,
                    preference.preferred_model_id,
                    preference.fallback_enabled,
                    preference.updated_at,
                ),
            ).fetchone()
        return _preference_from_row(row)

    def get_run_preference(self, run_id: str) -> SessionModelPreference | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT session_id, mode, routing_policy, preferred_model_id,
                          fallback_enabled, updated_at
                   FROM agent_run_model_preferences WHERE run_id = %s""",
                (run_id,),
            ).fetchone()
        return _preference_from_row(row) if row else None

    def upsert_run_preference(
        self, run_id: str, preference: SessionModelPreference
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_run_model_preferences
                    (run_id, session_id, mode, routing_policy, preferred_model_id,
                     fallback_enabled, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    mode = EXCLUDED.mode,
                    routing_policy = EXCLUDED.routing_policy,
                    preferred_model_id = EXCLUDED.preferred_model_id,
                    fallback_enabled = EXCLUDED.fallback_enabled,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    run_id,
                    preference.session_id,
                    preference.mode,
                    preference.routing_policy,
                    preference.preferred_model_id,
                    preference.fallback_enabled,
                    preference.updated_at,
                ),
            )

    def get_run_selection(self, run_id: str) -> ModelSelection | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT mode, routing_policy, preferred_model_id,
                          preferred_provider, preferred_model, thinking_level,
                          fallback_enabled
                   FROM agent_run_model_preferences WHERE run_id = %s""",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return ModelSelection(
            mode=str(row[0]),  # type: ignore[arg-type]
            routing_policy=str(row[1]),  # type: ignore[arg-type]
            preferred_model_id=(str(row[2]) if row[2] is not None else None),
            preferred_provider=(str(row[3]) if row[3] is not None else None),
            preferred_model=(str(row[4]) if row[4] is not None else None),
            thinking_level=(str(row[5]) if row[5] is not None else None),
            fallback_enabled=bool(row[6]),
        )

    def upsert_run_selection(
        self,
        run_id: str,
        session_id: str,
        selection: ModelSelection,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_run_model_preferences
                    (run_id, session_id, mode, routing_policy,
                     preferred_model_id, preferred_provider, preferred_model,
                     thinking_level, fallback_enabled, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    session_id = EXCLUDED.session_id,
                    mode = EXCLUDED.mode,
                    routing_policy = EXCLUDED.routing_policy,
                    preferred_model_id = EXCLUDED.preferred_model_id,
                    preferred_provider = EXCLUDED.preferred_provider,
                    preferred_model = EXCLUDED.preferred_model,
                    thinking_level = EXCLUDED.thinking_level,
                    fallback_enabled = EXCLUDED.fallback_enabled,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    run_id,
                    session_id,
                    selection.mode,
                    selection.routing_policy,
                    selection.preferred_model_id,
                    selection.preferred_provider,
                    selection.preferred_model,
                    selection.thinking_level,
                    selection.fallback_enabled,
                    datetime.now(timezone.utc),
                ),
            )

    def get_runtime_stats(self, model_id: str) -> ModelRuntimeStats | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT model_id, sample_count, success_count, failure_count,
                          ttft_samples_ms, total_latency_samples_ms, last_success_at,
                          last_failure_at, last_error, updated_at
                   FROM model_runtime_stats WHERE model_id = %s""",
                (model_id,),
            ).fetchone()
        return _stats_from_row(row) if row else None

    def upsert_runtime_stats(self, stats: ModelRuntimeStats) -> ModelRuntimeStats:
        Jsonb = _require_jsonb()
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO model_runtime_stats (
                    model_id, sample_count, success_count, failure_count,
                    ttft_samples_ms, total_latency_samples_ms, last_success_at,
                    last_failure_at, last_error, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (model_id) DO UPDATE SET
                    sample_count = EXCLUDED.sample_count,
                    success_count = EXCLUDED.success_count,
                    failure_count = EXCLUDED.failure_count,
                    ttft_samples_ms = EXCLUDED.ttft_samples_ms,
                    total_latency_samples_ms = EXCLUDED.total_latency_samples_ms,
                    last_success_at = EXCLUDED.last_success_at,
                    last_failure_at = EXCLUDED.last_failure_at,
                    last_error = EXCLUDED.last_error,
                    updated_at = EXCLUDED.updated_at
                RETURNING model_id, sample_count, success_count, failure_count,
                          ttft_samples_ms, total_latency_samples_ms, last_success_at,
                          last_failure_at, last_error, updated_at
                """,
                (
                    stats.model_id,
                    stats.sample_count,
                    stats.success_count,
                    stats.failure_count,
                    Jsonb(list(stats.ttft_samples_ms)),
                    Jsonb(list(stats.total_latency_samples_ms)),
                    stats.last_success_at,
                    stats.last_failure_at,
                    stats.last_error,
                    stats.updated_at,
                ),
            ).fetchone()
        return _stats_from_row(row)

    def get_probe_stats(self, model_id: str) -> ModelProbeStats | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT model_id, sample_count, success_count, failure_count,
                          latency_samples_ms, last_latency_ms, last_success_at,
                          last_failure_at, last_error, updated_at
                   FROM model_probe_stats WHERE model_id = %s""",
                (model_id,),
            ).fetchone()
        return _probe_stats_from_row(row) if row else None

    def upsert_probe_stats(self, stats: ModelProbeStats) -> ModelProbeStats:
        Jsonb = _require_jsonb()
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO model_probe_stats (
                    model_id, sample_count, success_count, failure_count,
                    latency_samples_ms, last_latency_ms, last_success_at,
                    last_failure_at, last_error, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (model_id) DO UPDATE SET
                    sample_count = EXCLUDED.sample_count,
                    success_count = EXCLUDED.success_count,
                    failure_count = EXCLUDED.failure_count,
                    latency_samples_ms = EXCLUDED.latency_samples_ms,
                    last_latency_ms = EXCLUDED.last_latency_ms,
                    last_success_at = EXCLUDED.last_success_at,
                    last_failure_at = EXCLUDED.last_failure_at,
                    last_error = EXCLUDED.last_error,
                    updated_at = EXCLUDED.updated_at
                RETURNING model_id, sample_count, success_count, failure_count,
                          latency_samples_ms, last_latency_ms, last_success_at,
                          last_failure_at, last_error, updated_at
                """,
                (
                    stats.model_id,
                    stats.sample_count,
                    stats.success_count,
                    stats.failure_count,
                    Jsonb(list(stats.latency_samples_ms)),
                    stats.last_latency_ms,
                    stats.last_success_at,
                    stats.last_failure_at,
                    stats.last_error,
                    stats.updated_at,
                ),
            ).fetchone()
        return _probe_stats_from_row(row)

    def _connect(self):
        return _require_psycopg().connect(self._database_url)


_MODEL_SELECT = """SELECT id, provider, model, display_name, context_window_tokens,
    max_output_tokens,
    tool_calling, structured_output, input_cost_per_million,
    output_cost_per_million, quality_score, configured_latency_ms,
    enabled, auto_eligible, created_at, updated_at FROM registered_models"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connection_from_row(row: tuple[Any, ...]) -> ProviderConnection:
    return ProviderConnection(
        provider=str(row[0]), display_name=str(row[1]), secret_ref=row[2],
        enabled=bool(row[3]), created_at=row[4], updated_at=row[5]
    )


def _model_from_row(row: tuple[Any, ...]) -> RegisteredModel:
    return RegisteredModel(
        id=str(row[0]), provider=str(row[1]), model=str(row[2]),
        display_name=str(row[3]), context_window_tokens=int(row[4]),
        max_output_tokens=int(row[5]), tool_calling=bool(row[6]),
        structured_output=bool(row[7]), input_cost_per_million=float(row[8]),
        output_cost_per_million=float(row[9]), quality_score=float(row[10]),
        configured_latency_ms=int(row[11]), enabled=bool(row[12]),
        auto_eligible=bool(row[13]), created_at=row[14], updated_at=row[15]
    )


def _model_values(model: RegisteredModel) -> tuple[object, ...]:
    return (
        model.id, model.provider, model.model, model.display_name,
        model.context_window_tokens, model.max_output_tokens, model.tool_calling,
        model.structured_output,
        model.input_cost_per_million, model.output_cost_per_million,
        model.quality_score, model.configured_latency_ms, model.enabled,
        model.auto_eligible, model.created_at, model.updated_at,
    )


def _preference_from_row(row: tuple[Any, ...]) -> SessionModelPreference:
    return SessionModelPreference(
        session_id=str(row[0]), mode=str(row[1]), routing_policy=str(row[2]),
        preferred_model_id=str(row[3]) if row[3] is not None else None,
        fallback_enabled=bool(row[4]), updated_at=row[5]
    )


def _stats_from_row(row: tuple[Any, ...]) -> ModelRuntimeStats:
    return ModelRuntimeStats(
        model_id=str(row[0]), sample_count=int(row[1]), success_count=int(row[2]),
        failure_count=int(row[3]), ttft_samples_ms=tuple(row[4] or []),
        total_latency_samples_ms=tuple(row[5] or []), last_success_at=row[6],
        last_failure_at=row[7], last_error=row[8], updated_at=row[9]
    )


def _probe_stats_from_row(row: tuple[Any, ...]) -> ModelProbeStats:
    return ModelProbeStats(
        model_id=str(row[0]), sample_count=int(row[1]), success_count=int(row[2]),
        failure_count=int(row[3]), latency_samples_ms=tuple(row[4] or []),
        last_latency_ms=int(row[5]) if row[5] is not None else None,
        last_success_at=row[6], last_failure_at=row[7], last_error=row[8],
        updated_at=row[9],
    )


def _require_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for PostgreSQL model registry storage") from exc
    return psycopg


def _require_jsonb():
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError("psycopg JSON support is required") from exc
    return Jsonb
