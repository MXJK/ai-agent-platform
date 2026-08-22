"""Records the eval layer persists and returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


EVAL_STATUS_RUNNING = "running"
EVAL_STATUS_COMPLETED = "completed"
EVAL_STATUS_FAILED = "failed"

ALERT_THRESHOLD = "threshold"
ALERT_REGRESSION = "regression"
ALERT_CASE = "case"

SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

# Every metric the page shows, with the direction that counts as worse. Keeping
# one declaration here stops the API, the alert logic and the frontend from
# drifting apart on what "better" means.
METRIC_DIRECTIONS: dict[str, str] = {
    "pass_rate": "higher_is_better",
    "invalid_action_rate": "lower_is_better",
    "mean_step_efficiency": "lower_is_better",
    "budget_cap_rate": "lower_is_better",
    "failure_recovery_rate": "higher_is_better",
    "citation_accuracy": "higher_is_better",
}


@dataclass(frozen=True)
class EvalSuiteMetrics:
    """Suite-level numbers. ``None`` means no case measured it."""

    pass_rate: float
    invalid_action_rate: float
    mean_step_efficiency: float | None
    budget_cap_rate: float
    failure_recovery_rate: float | None
    citation_accuracy: float | None

    def as_dict(self) -> dict[str, float | None]:
        return {
            "pass_rate": self.pass_rate,
            "invalid_action_rate": self.invalid_action_rate,
            "mean_step_efficiency": self.mean_step_efficiency,
            "budget_cap_rate": self.budget_cap_rate,
            "failure_recovery_rate": self.failure_recovery_rate,
            "citation_accuracy": self.citation_accuracy,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalSuiteMetrics":
        return cls(
            pass_rate=float(payload.get("pass_rate") or 0.0),
            invalid_action_rate=float(payload.get("invalid_action_rate") or 0.0),
            mean_step_efficiency=_optional_float(payload.get("mean_step_efficiency")),
            budget_cap_rate=float(payload.get("budget_cap_rate") or 0.0),
            failure_recovery_rate=_optional_float(
                payload.get("failure_recovery_rate")
            ),
            citation_accuracy=_optional_float(payload.get("citation_accuracy")),
        )


@dataclass(frozen=True)
class EvalCaseRecord:
    """One case's outcome, kept in full so the page can explain a failure."""

    case_id: str
    passed: bool
    status: str
    agent_run_id: str
    constraints: tuple[dict[str, Any], ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
    citations: dict[str, Any] | None = None
    trace_nodes: tuple[str, ...] = ()
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "status": self.status,
            "agent_run_id": self.agent_run_id,
            "constraints": [dict(item) for item in self.constraints],
            "metrics": dict(self.metrics),
            "citations": dict(self.citations) if self.citations else None,
            "trace_nodes": list(self.trace_nodes),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalCaseRecord":
        return cls(
            case_id=str(payload.get("case_id") or ""),
            passed=bool(payload.get("passed")),
            status=str(payload.get("status") or ""),
            agent_run_id=str(payload.get("agent_run_id") or ""),
            constraints=tuple(payload.get("constraints") or ()),
            metrics=dict(payload.get("metrics") or {}),
            citations=payload.get("citations"),
            trace_nodes=tuple(payload.get("trace_nodes") or ()),
            error=str(payload.get("error") or ""),
        )


@dataclass(frozen=True)
class EvalAlert:
    """One thing worth a person's attention, ready to render."""

    kind: str
    severity: str
    metric: str
    message: str
    actual: float | None = None
    expected: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "metric": self.metric,
            "message": self.message,
            "actual": self.actual,
            "expected": self.expected,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalAlert":
        return cls(
            kind=str(payload.get("kind") or ALERT_THRESHOLD),
            severity=str(payload.get("severity") or SEVERITY_WARNING),
            metric=str(payload.get("metric") or ""),
            message=str(payload.get("message") or ""),
            actual=_optional_float(payload.get("actual")),
            expected=_optional_float(payload.get("expected")),
        )


@dataclass(frozen=True)
class EvalRunRecord:
    run_id: str
    suite_id: str
    provider: str
    model: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    total_cases: int = 0
    completed_cases: int = 0
    passed_cases: int = 0
    metrics: EvalSuiteMetrics | None = None
    cases: tuple[EvalCaseRecord, ...] = ()
    alerts: tuple[EvalAlert, ...] = ()
    baseline_run_id: str = ""
    is_baseline: bool = False
    fault_injection_enabled: bool = False
    total_tokens: int = 0
    elapsed_ms: int = 0
    error: str = ""

    @property
    def progress(self) -> float:
        if not self.total_cases:
            return 0.0
        return self.completed_cases / self.total_cases


@dataclass(frozen=True)
class EvalBaseline:
    """The run a provider's later runs are compared against."""

    provider: str
    run_id: str
    metrics: EvalSuiteMetrics
    pinned_at: datetime


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
