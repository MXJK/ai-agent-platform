"""Storage for trajectory eval runs and per-provider baselines."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any, Protocol

from ai_agent_platform.evaluation.models import (
    EvalAlert,
    EvalBaseline,
    EvalCaseRecord,
    EvalRunRecord,
    EvalSuiteMetrics,
)


class EvalRepository(Protocol):
    def create_run(self, record: EvalRunRecord) -> EvalRunRecord: ...

    def update_run(self, record: EvalRunRecord) -> EvalRunRecord: ...

    def get_run(self, run_id: str) -> EvalRunRecord | None: ...

    def list_runs(
        self,
        *,
        provider: str | None = None,
        limit: int = 20,
    ) -> list[EvalRunRecord]: ...

    def get_baseline(self, provider: str) -> EvalBaseline | None: ...

    def set_baseline(self, baseline: EvalBaseline) -> EvalBaseline: ...


class InMemoryEvalRepository:
    def __init__(self) -> None:
        self._runs: dict[str, EvalRunRecord] = {}
        self._order: list[str] = []
        self._baselines: dict[str, EvalBaseline] = {}
        self._lock = Lock()

    def create_run(self, record: EvalRunRecord) -> EvalRunRecord:
        with self._lock:
            self._runs[record.run_id] = record
            self._order.append(record.run_id)
            return record

    def update_run(self, record: EvalRunRecord) -> EvalRunRecord:
        with self._lock:
            self._runs[record.run_id] = record
            if record.run_id not in self._order:
                self._order.append(record.run_id)
            return record

    def get_run(self, run_id: str) -> EvalRunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(
        self,
        *,
        provider: str | None = None,
        limit: int = 20,
    ) -> list[EvalRunRecord]:
        with self._lock:
            records = [self._runs[run_id] for run_id in reversed(self._order)]
        if provider:
            records = [item for item in records if item.provider == provider]
        return records[: max(1, limit)]

    def get_baseline(self, provider: str) -> EvalBaseline | None:
        with self._lock:
            return self._baselines.get(provider)

    def set_baseline(self, baseline: EvalBaseline) -> EvalBaseline:
        with self._lock:
            self._baselines[baseline.provider] = baseline
            return baseline


class PostgresEvalRepository:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        _require_psycopg()

    def create_run(self, record: EvalRunRecord) -> EvalRunRecord:
        return self.update_run(record)

    def update_run(self, record: EvalRunRecord) -> EvalRunRecord:
        Jsonb = _require_jsonb()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO eval_runs (
                    run_id, suite_id, provider, model, status, started_at,
                    finished_at, total_cases, completed_cases, passed_cases,
                    metrics, cases, alerts, baseline_run_id, is_baseline,
                    fault_injection_enabled, total_tokens, elapsed_ms, error
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (run_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    finished_at = EXCLUDED.finished_at,
                    total_cases = EXCLUDED.total_cases,
                    completed_cases = EXCLUDED.completed_cases,
                    passed_cases = EXCLUDED.passed_cases,
                    metrics = EXCLUDED.metrics,
                    cases = EXCLUDED.cases,
                    alerts = EXCLUDED.alerts,
                    baseline_run_id = EXCLUDED.baseline_run_id,
                    is_baseline = EXCLUDED.is_baseline,
                    fault_injection_enabled = EXCLUDED.fault_injection_enabled,
                    total_tokens = EXCLUDED.total_tokens,
                    elapsed_ms = EXCLUDED.elapsed_ms,
                    error = EXCLUDED.error
                """,
                (
                    record.run_id,
                    record.suite_id,
                    record.provider,
                    record.model,
                    record.status,
                    record.started_at,
                    record.finished_at,
                    record.total_cases,
                    record.completed_cases,
                    record.passed_cases,
                    Jsonb(record.metrics.as_dict()) if record.metrics else None,
                    Jsonb([item.as_dict() for item in record.cases]),
                    Jsonb([item.as_dict() for item in record.alerts]),
                    record.baseline_run_id,
                    record.is_baseline,
                    record.fault_injection_enabled,
                    record.total_tokens,
                    record.elapsed_ms,
                    record.error,
                ),
            )
        return record

    def get_run(self, run_id: str) -> EvalRunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_RUN_COLUMNS} FROM eval_runs WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        return _run_from_row(row) if row else None

    def list_runs(
        self,
        *,
        provider: str | None = None,
        limit: int = 20,
    ) -> list[EvalRunRecord]:
        clause = "WHERE provider = %s " if provider else ""
        params: tuple[Any, ...] = (provider, limit) if provider else (limit,)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_RUN_COLUMNS} FROM eval_runs {clause}"
                "ORDER BY started_at DESC LIMIT %s",
                params,
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def get_baseline(self, provider: str) -> EvalBaseline | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT provider, run_id, metrics, pinned_at "
                "FROM eval_baselines WHERE provider = %s",
                (provider,),
            ).fetchone()
        if row is None:
            return None
        return EvalBaseline(
            provider=str(row[0]),
            run_id=str(row[1]),
            metrics=EvalSuiteMetrics.from_dict(row[2] or {}),
            pinned_at=_as_datetime(row[3]),
        )

    def set_baseline(self, baseline: EvalBaseline) -> EvalBaseline:
        Jsonb = _require_jsonb()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO eval_baselines (provider, run_id, metrics, pinned_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (provider) DO UPDATE SET
                    run_id = EXCLUDED.run_id,
                    metrics = EXCLUDED.metrics,
                    pinned_at = EXCLUDED.pinned_at
                """,
                (
                    baseline.provider,
                    baseline.run_id,
                    Jsonb(baseline.metrics.as_dict()),
                    baseline.pinned_at,
                ),
            )
        return baseline

    def _connect(self):
        psycopg = _require_psycopg()
        return psycopg.connect(self._database_url)


_RUN_COLUMNS = """
    run_id, suite_id, provider, model, status, started_at, finished_at,
    total_cases, completed_cases, passed_cases, metrics, cases, alerts,
    baseline_run_id, is_baseline, fault_injection_enabled, total_tokens,
    elapsed_ms, error
"""


def _run_from_row(row: tuple[Any, ...]) -> EvalRunRecord:
    return EvalRunRecord(
        run_id=str(row[0]),
        suite_id=str(row[1]),
        provider=str(row[2]),
        model=str(row[3] or ""),
        status=str(row[4]),
        started_at=_as_datetime(row[5]),
        finished_at=_as_datetime(row[6]) if row[6] else None,
        total_cases=int(row[7] or 0),
        completed_cases=int(row[8] or 0),
        passed_cases=int(row[9] or 0),
        metrics=EvalSuiteMetrics.from_dict(row[10]) if row[10] else None,
        cases=tuple(EvalCaseRecord.from_dict(item) for item in (row[11] or [])),
        alerts=tuple(EvalAlert.from_dict(item) for item in (row[12] or [])),
        baseline_run_id=str(row[13] or ""),
        is_baseline=bool(row[14]),
        fault_injection_enabled=bool(row[15]),
        total_tokens=int(row[16] or 0),
        elapsed_ms=int(row[17] or 0),
        error=str(row[18] or ""),
    )


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value))


def _require_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required for PostgreSQL eval storage"
        ) from exc
    return psycopg


def _require_jsonb():
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError(
            "psycopg JSON support is required for PostgreSQL eval storage"
        ) from exc
    return Jsonb
