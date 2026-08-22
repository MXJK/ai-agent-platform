"""Run the L1 trajectory suite inside the app, against a configured model.

Running in-process is what makes a real-model eval possible at all: the
registered provider credential lives in the app's secret store, and the model
registry lives behind the app's database. It also means every case travels the
same `QueryService.submit_run` path a user's request travels, so the eval
measures the real system rather than a parallel one.
"""

from __future__ import annotations

import logging
from pathlib import Path
import shutil
from threading import Lock, Thread
import time
from typing import Any, Callable, Protocol
import uuid

from ai_agent_platform.evaluation.citations import verify_citations
from ai_agent_platform.evaluation.faults import ToolFaultController
from ai_agent_platform.evaluation.models import (
    ALERT_CASE,
    ALERT_REGRESSION,
    ALERT_THRESHOLD,
    EVAL_STATUS_COMPLETED,
    EVAL_STATUS_FAILED,
    EVAL_STATUS_RUNNING,
    METRIC_DIRECTIONS,
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    EvalAlert,
    EvalBaseline,
    EvalCaseRecord,
    EvalRunRecord,
    EvalSuiteMetrics,
    utc_now,
)
from ai_agent_platform.evaluation.suite import EvalSuite, load_suite
from ai_agent_platform.evaluation.trajectory import (
    FAILURE_RECOVERY_NOT_TRIGGERED,
    RunObservation,
    aggregate_budget_cap_rate,
    aggregate_failure_recovery_rate,
    aggregate_invalid_action_rate,
    aggregate_step_efficiency,
    check_constraints,
    measure_trajectory,
)


logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "waiting_approval",
        "waiting_input",
        "blocked",
        "partial",
        "cancelled",
    }
)

# Correctness invariants that hold for every provider. Performance numbers are
# compared against the provider's own baseline instead, because a real model and
# the fake provider are not comparable on step counts.
ALWAYS_ENFORCED_METRICS = ("citation_accuracy",)


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


class EvalRunInProgressError(RuntimeError):
    """Raised when a second eval run is started while one is active."""


class EvalRunNotFoundError(KeyError):
    pass


class EvalProviderUnavailableError(ValueError):
    """Raised when the requested provider has no usable registered model.

    Without this the run still executes, but the tool selection narrows to
    nothing and every case fails with a permission denial that looks like a bug
    in the agent rather than a missing model registration.
    """


class EvalService:
    def __init__(
        self,
        *,
        repository: EvalRepository,
        query_service: Any,
        session_service: Any,
        workspace_service: Any,
        workspace_root: str,
        memory_service: Any = None,
        model_registry: Any = None,
        actor_user_id: str = "",
        suite: EvalSuite | None = None,
        fault_controller: ToolFaultController | None = None,
        run_timeout_seconds: float = 600.0,
        poll_interval_seconds: float = 0.2,
        status_serializer: Callable[[Any], dict[str, Any]] | None = None,
    ) -> None:
        self._repository = repository
        self._query_service = query_service
        self._session_service = session_service
        self._workspace_service = workspace_service
        self._memory_service = memory_service
        self._model_registry = model_registry
        self._workspace_root = Path(workspace_root)
        self._actor_user_id = actor_user_id
        self._suite = suite or load_suite()
        self._fault_controller = fault_controller
        self._run_timeout_seconds = run_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._status_serializer = status_serializer or _default_status_serializer
        self._lock = Lock()
        self._active_run_id = ""
        self._threads: list[Thread] = []

    @property
    def suite(self) -> EvalSuite:
        return self._suite

    @property
    def fault_injection_enabled(self) -> bool:
        return self._fault_controller is not None

    @property
    def active_run_id(self) -> str:
        with self._lock:
            return self._active_run_id

    def catalogue(self) -> dict[str, Any]:
        """What the page needs to describe the suite before anything has run."""

        return {
            "suite_id": self._suite.suite_id,
            "fault_injection_enabled": self.fault_injection_enabled,
            "metric_thresholds": dict(self._suite.metric_thresholds),
            "regression_tolerance": dict(self._suite.regression_tolerance),
            "cases": [
                {
                    "id": str(case.get("id") or ""),
                    "message": str(case.get("message") or ""),
                    "required_tools": list(case.get("required_tools") or ()),
                    "forbidden_tools": list(case.get("forbidden_tools") or ()),
                    "order_constraints": [
                        list(item) for item in (case.get("order_constraints") or ())
                    ],
                    "max_steps": case.get("max_steps"),
                    "reference_steps": case.get("reference_steps"),
                    "verify_citations": bool(case.get("verify_citations")),
                    "injects_fault": bool(case.get("fault_injection")),
                }
                for case in self._suite.cases
            ],
        }

    def start_run(
        self,
        *,
        provider: str,
        model: str = "",
        blocking: bool = False,
    ) -> EvalRunRecord:
        """Begin a run. Returns immediately with a ``running`` record.

        One at a time: a run costs real money against a real provider, takes
        minutes, and arms the shared fault controller, so two overlapping runs
        would corrupt each other's failure-recovery measurement.
        """

        model = self._resolve_model(provider, model)
        run_id = f"eval_{uuid.uuid4().hex[:12]}"
        with self._lock:
            if self._active_run_id:
                raise EvalRunInProgressError(
                    f"eval run {self._active_run_id} is still running"
                )
            self._active_run_id = run_id
        record = EvalRunRecord(
            run_id=run_id,
            suite_id=self._suite.suite_id,
            provider=provider,
            model=model,
            status=EVAL_STATUS_RUNNING,
            started_at=utc_now(),
            total_cases=len(self._suite.cases),
            fault_injection_enabled=self.fault_injection_enabled,
        )
        self._repository.create_run(record)
        if blocking:
            self._execute(record)
            stored = self._repository.get_run(run_id)
            return stored if stored is not None else record
        thread = Thread(
            target=self._execute,
            args=(record,),
            name=f"eval-{run_id}",
            daemon=True,
        )
        self._threads.append(thread)
        thread.start()
        return record

    def available_providers(self) -> list[dict[str, str]]:
        """Providers the in-app eval can actually drive, from the registry."""

        if self._model_registry is None:
            return []
        seen: dict[str, dict[str, str]] = {}
        for item in self._model_registry.list_models():
            if not item.get("enabled"):
                continue
            provider = str(item.get("provider") or "")
            if not provider or provider in seen:
                continue
            seen[provider] = {
                "provider": provider,
                "model": str(item.get("model") or ""),
                "display_name": str(item.get("display_name") or provider),
            }
        return [seen[key] for key in sorted(seen)]

    def _resolve_model(self, provider: str, model: str) -> str:
        if self._model_registry is None:
            return model
        candidates = [
            item
            for item in self._model_registry.list_models()
            if item.get("provider") == provider and item.get("enabled")
        ]
        if not candidates:
            raise EvalProviderUnavailableError(
                f"no enabled model is registered for provider {provider!r}; "
                "register one on the models page before running an eval"
            )
        if model:
            if any(item.get("model") == model for item in candidates):
                return model
            raise EvalProviderUnavailableError(
                f"model {model!r} is not registered for provider {provider!r}"
            )
        return str(candidates[0].get("model") or "")

    def get_run(self, run_id: str) -> EvalRunRecord:
        record = self._repository.get_run(run_id)
        if record is None:
            raise EvalRunNotFoundError(run_id)
        return record

    def list_runs(
        self,
        *,
        provider: str | None = None,
        limit: int = 20,
    ) -> list[EvalRunRecord]:
        return self._repository.list_runs(provider=provider, limit=limit)

    def get_baseline(self, provider: str) -> EvalBaseline | None:
        return self._repository.get_baseline(provider)

    def pin_baseline(self, run_id: str) -> EvalBaseline:
        record = self.get_run(run_id)
        if record.status != EVAL_STATUS_COMPLETED or record.metrics is None:
            raise ValueError("only a completed eval run can become a baseline")
        return self._repository.set_baseline(
            EvalBaseline(
                provider=record.provider,
                run_id=record.run_id,
                metrics=record.metrics,
                pinned_at=utc_now(),
            )
        )

    def _execute(self, record: EvalRunRecord) -> None:
        started = time.perf_counter()
        workspace_root = self._workspace_root / record.run_id
        workspace_id = f"eval_{record.run_id}"
        cases: list[EvalCaseRecord] = []
        try:
            workspace_root.mkdir(parents=True, exist_ok=True)
            self._suite.materialize(workspace_root)
            self._workspace_service.register(
                workspace_id=workspace_id,
                root_path=str(workspace_root),
            )
            if self._memory_service is not None and self._actor_user_id:
                # Same step the workspace route takes after registering: without
                # it the actor has no membership and every run is denied.
                self._memory_service.ensure_workspace_admin(
                    workspace_id=workspace_id,
                    actor_user_id=self._actor_user_id,
                )
            for case in self._suite.cases:
                cases.append(
                    self._run_case(
                        case=case,
                        workspace_id=workspace_id,
                        workspace_root=workspace_root,
                        provider=record.provider,
                        model=record.model,
                    )
                )
                record = self._replace(
                    record,
                    completed_cases=len(cases),
                    passed_cases=sum(1 for item in cases if item.passed),
                    cases=tuple(cases),
                )
                self._repository.update_run(record)
            record = self._finalize(record, cases, started)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            logger.exception("eval run %s failed", record.run_id)
            record = self._replace(
                record,
                status=EVAL_STATUS_FAILED,
                finished_at=utc_now(),
                cases=tuple(cases),
                completed_cases=len(cases),
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
            self._repository.update_run(record)
        finally:
            self._cleanup(workspace_id, workspace_root)
            with self._lock:
                if self._active_run_id == record.run_id:
                    self._active_run_id = ""

    def _run_case(
        self,
        *,
        case: dict[str, Any],
        workspace_id: str,
        workspace_root: Path,
        provider: str,
        model: str,
    ) -> EvalCaseRecord:
        case_id = str(case.get("id") or "")
        fault = case.get("fault_injection")
        if self._fault_controller is not None:
            if isinstance(fault, dict):
                self._fault_controller.arm(
                    str(fault["tool"]),
                    workspace_id=workspace_id,
                    occurrences=int(fault.get("occurrences") or 1),
                )
            else:
                self._fault_controller.disarm()
        try:
            session = self._session_service.create_session(
                self._actor_user_id or "eval_runner"
            )
            submitted = self._query_service.submit_run(
                conversation_id=session.id,
                message=str(case["message"]),
                workspace_id=workspace_id,
                actor_user_id=self._actor_user_id or None,
                provider=provider or None,
                model=model or None,
            )
            status_body = self._await_run(submitted.run_id)
        except Exception as exc:  # noqa: BLE001 - one bad case must not kill the suite
            logger.exception("eval case %s failed to run", case_id)
            return EvalCaseRecord(
                case_id=case_id,
                passed=False,
                status="error",
                agent_run_id="",
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if self._fault_controller is not None:
                self._fault_controller.disarm()

        observation = RunObservation.from_run_status(case_id, status_body)
        result = status_body.get("result") or {}
        constraints = check_constraints(observation, case, provider=provider)
        metrics = measure_trajectory(
            observation,
            reference_steps=case.get("reference_steps"),
        )
        citations = (
            verify_citations(
                context_sources=result.get("context_sources", []),
                answer=str(result.get("answer") or ""),
                workspace_root=workspace_root,
            )
            if case.get("verify_citations")
            else None
        )
        passed = all(item.passed for item in constraints) and (
            citations is None or citations.passed
        )
        return EvalCaseRecord(
            case_id=case_id,
            passed=passed,
            status=observation.status,
            agent_run_id=str(status_body.get("run_id") or ""),
            constraints=tuple(
                {
                    "name": item.name,
                    "passed": item.passed,
                    "detail": item.detail,
                }
                for item in constraints
            ),
            metrics={
                "executed_calls": metrics.executed_calls,
                "failed_calls": metrics.failed_calls,
                "repeated_calls": metrics.repeated_calls,
                "retries_after_failure": metrics.retries_after_failure,
                "suppressed_calls": metrics.suppressed_calls,
                "invalid_action_rate": metrics.invalid_action_rate,
                "reference_steps": metrics.reference_steps,
                "step_efficiency": metrics.step_efficiency,
                "budget_capped": metrics.budget_capped,
                "budget_reasons": list(metrics.budget_reasons),
                "failure_recovery": metrics.failure_recovery,
                "total_tokens": int(
                    (result.get("metrics") or {}).get("total_tokens") or 0
                ),
                "elapsed_ms": int(
                    (result.get("metrics") or {}).get("elapsed_ms") or 0
                ),
            },
            citations=(
                {
                    "verified": citations.verified_count,
                    "scored": citations.scored_count,
                    "unverifiable": len(citations.verdicts)
                    - citations.scored_count,
                    "accuracy": citations.accuracy,
                    "failures": [
                        {
                            "path": item.path,
                            "kind": item.kind,
                            "start_line": item.start_line,
                            "end_line": item.end_line,
                            "status": item.status,
                            "detail": item.detail,
                        }
                        for item in citations.failures
                    ],
                    "ungrounded_paths": list(citations.ungrounded_paths),
                }
                if citations is not None
                else None
            ),
            trace_nodes=observation.trace_nodes,
        )

    def _await_run(self, run_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self._run_timeout_seconds
        while time.monotonic() < deadline:
            record = self._query_service.get_run(run_id)
            body = self._status_serializer(record)
            if body.get("status") in TERMINAL_STATUSES:
                return body
            time.sleep(self._poll_interval_seconds)
        raise TimeoutError(f"agent run {run_id} did not finish in time")

    def _finalize(
        self,
        record: EvalRunRecord,
        cases: list[EvalCaseRecord],
        started: float,
    ) -> EvalRunRecord:
        metrics = self._aggregate(cases)
        baseline = self._repository.get_baseline(record.provider)
        alerts = self._alerts(record.provider, metrics, cases, baseline)
        finished = self._replace(
            record,
            status=EVAL_STATUS_COMPLETED,
            finished_at=utc_now(),
            cases=tuple(cases),
            completed_cases=len(cases),
            passed_cases=sum(1 for item in cases if item.passed),
            metrics=metrics,
            alerts=tuple(alerts),
            baseline_run_id=baseline.run_id if baseline else "",
            is_baseline=baseline is None,
            total_tokens=sum(
                int(item.metrics.get("total_tokens") or 0) for item in cases
            ),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
        self._repository.update_run(finished)
        if baseline is None:
            # The first completed run for a provider defines what "normal" is;
            # there is nothing else to compare it against.
            self._repository.set_baseline(
                EvalBaseline(
                    provider=finished.provider,
                    run_id=finished.run_id,
                    metrics=metrics,
                    pinned_at=utc_now(),
                )
            )
        return finished

    def _aggregate(self, cases: list[EvalCaseRecord]) -> EvalSuiteMetrics:
        measured = [_MetricsView(item.metrics) for item in cases if item.metrics]
        scored = sum(
            int((item.citations or {}).get("scored") or 0)
            for item in cases
            if item.citations
        )
        verified = sum(
            int((item.citations or {}).get("verified") or 0)
            for item in cases
            if item.citations
        )
        return EvalSuiteMetrics(
            pass_rate=(
                sum(1 for item in cases if item.passed) / len(cases)
                if cases
                else 0.0
            ),
            invalid_action_rate=aggregate_invalid_action_rate(measured),
            mean_step_efficiency=aggregate_step_efficiency(measured),
            budget_cap_rate=aggregate_budget_cap_rate(measured),
            failure_recovery_rate=aggregate_failure_recovery_rate(measured),
            citation_accuracy=(verified / scored) if scored else None,
        )

    def _alerts(
        self,
        provider: str,
        metrics: EvalSuiteMetrics,
        cases: list[EvalCaseRecord],
        baseline: EvalBaseline | None,
    ) -> list[EvalAlert]:
        alerts: list[EvalAlert] = []
        for case in cases:
            if case.passed:
                continue
            reason = case.error or _first_violation(case)
            alerts.append(
                EvalAlert(
                    kind=ALERT_CASE,
                    severity=SEVERITY_CRITICAL,
                    metric="pass_rate",
                    message=f"{case.case_id}: {reason}",
                )
            )
        values = metrics.as_dict()
        for name in ALWAYS_ENFORCED_METRICS:
            actual = values.get(name)
            if actual is not None and actual < 1.0:
                alerts.append(
                    EvalAlert(
                        kind=ALERT_THRESHOLD,
                        severity=SEVERITY_CRITICAL,
                        metric=name,
                        message=(
                            f"{name} must be 1.000 for any provider; "
                            f"measured {actual:.3f}"
                        ),
                        actual=actual,
                        expected=1.0,
                    )
                )
        if provider == "fake":
            # The checked-in thresholds were calibrated on the deterministic
            # fake provider. Applying them to a real model would only measure
            # how different that model is, not whether anything regressed.
            alerts.extend(
                _threshold_alerts(values, self._suite.metric_thresholds)
            )
        if baseline is not None:
            alerts.extend(
                _regression_alerts(
                    values,
                    baseline.metrics.as_dict(),
                    self._suite.regression_tolerance,
                )
            )
        return alerts

    def _cleanup(self, workspace_id: str, workspace_root: Path) -> None:
        try:
            self._workspace_service.remove(workspace_id)
        except Exception:  # noqa: BLE001 - cleanup must not mask the real result
            logger.warning("could not remove eval workspace %s", workspace_id)
        shutil.rmtree(workspace_root, ignore_errors=True)

    @staticmethod
    def _replace(record: EvalRunRecord, **changes: Any) -> EvalRunRecord:
        values = {
            "run_id": record.run_id,
            "suite_id": record.suite_id,
            "provider": record.provider,
            "model": record.model,
            "status": record.status,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "total_cases": record.total_cases,
            "completed_cases": record.completed_cases,
            "passed_cases": record.passed_cases,
            "metrics": record.metrics,
            "cases": record.cases,
            "alerts": record.alerts,
            "baseline_run_id": record.baseline_run_id,
            "is_baseline": record.is_baseline,
            "fault_injection_enabled": record.fault_injection_enabled,
            "total_tokens": record.total_tokens,
            "elapsed_ms": record.elapsed_ms,
            "error": record.error,
        }
        values.update(changes)
        return EvalRunRecord(**values)


class _MetricsView:
    """Adapts a persisted metrics dict back to the aggregate helpers."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.executed_calls = int(payload.get("executed_calls") or 0)
        self.repeated_calls = int(payload.get("repeated_calls") or 0)
        self.suppressed_calls = int(payload.get("suppressed_calls") or 0)
        self.budget_capped = bool(payload.get("budget_capped"))
        step_efficiency = payload.get("step_efficiency")
        self.step_efficiency = (
            float(step_efficiency) if step_efficiency is not None else None
        )
        self.failure_recovery = str(
            payload.get("failure_recovery") or FAILURE_RECOVERY_NOT_TRIGGERED
        )


def _threshold_alerts(
    values: dict[str, float | None],
    thresholds: dict[str, float],
) -> list[EvalAlert]:
    alerts: list[EvalAlert] = []
    bounds = {
        "max_invalid_action_rate": "invalid_action_rate",
        "max_mean_step_efficiency": "mean_step_efficiency",
        "max_budget_cap_rate": "budget_cap_rate",
        "min_failure_recovery_rate": "failure_recovery_rate",
        "min_citation_accuracy": "citation_accuracy",
    }
    for key, metric in bounds.items():
        expected = thresholds.get(key)
        actual = values.get(metric)
        if expected is None or actual is None:
            continue
        upper = key.startswith("max_")
        breached = actual > expected if upper else actual < expected
        if not breached:
            continue
        comparator = "<=" if upper else ">="
        alerts.append(
            EvalAlert(
                kind=ALERT_THRESHOLD,
                severity=SEVERITY_CRITICAL,
                metric=metric,
                message=(
                    f"{metric} expected{comparator}{expected:.3f}, "
                    f"measured {actual:.3f}"
                ),
                actual=actual,
                expected=expected,
            )
        )
    return alerts


def _regression_alerts(
    values: dict[str, float | None],
    baseline: dict[str, float | None],
    tolerance: dict[str, float],
) -> list[EvalAlert]:
    alerts: list[EvalAlert] = []
    for metric, direction in METRIC_DIRECTIONS.items():
        actual = values.get(metric)
        reference = baseline.get(metric)
        if actual is None or reference is None:
            continue
        allowed = float(tolerance.get(metric, 0.0))
        if direction == "lower_is_better":
            regressed = actual > reference + allowed
            delta = actual - reference
        else:
            regressed = actual < reference - allowed
            delta = reference - actual
        if not regressed:
            continue
        alerts.append(
            EvalAlert(
                kind=ALERT_REGRESSION,
                severity=SEVERITY_WARNING,
                metric=metric,
                message=(
                    f"{metric} regressed by {delta:.3f} against the baseline "
                    f"({reference:.3f} -> {actual:.3f}, tolerance {allowed:.3f})"
                ),
                actual=actual,
                expected=reference,
            )
        )
    return alerts


def _first_violation(case: EvalCaseRecord) -> str:
    for constraint in case.constraints:
        if not constraint.get("passed"):
            return f"{constraint.get('name')} {constraint.get('detail')}"
    citations = case.citations or {}
    if citations.get("ungrounded_paths"):
        return f"ungrounded citations {citations['ungrounded_paths']}"
    if citations.get("failures"):
        failure = citations["failures"][0]
        return f"citation {failure.get('status')} at {failure.get('path')}"
    return "case did not pass"


def _default_status_serializer(record: Any) -> dict[str, Any]:
    from ai_agent_platform.schemas import AgentRunStatusResponse

    return AgentRunStatusResponse.from_domain(record).model_dump()
