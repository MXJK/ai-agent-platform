"""Response and request shapes for the trajectory eval API."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from ai_agent_platform.evaluation.models import (
    METRIC_DIRECTIONS,
    EvalBaseline,
    EvalRunRecord,
)


class EvalRunStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="fake", max_length=64)
    model: str = Field(default="", max_length=200)


class EvalMetricsResponse(BaseModel):
    pass_rate: float
    invalid_action_rate: float
    mean_step_efficiency: Optional[float]
    budget_cap_rate: float
    failure_recovery_rate: Optional[float]
    citation_accuracy: Optional[float]


class EvalAlertResponse(BaseModel):
    kind: str
    severity: str
    metric: str
    message: str
    actual: Optional[float]
    expected: Optional[float]


class EvalCaseResponse(BaseModel):
    case_id: str
    passed: bool
    status: str
    agent_run_id: str
    constraints: list[dict[str, Any]]
    metrics: dict[str, Any]
    citations: Optional[dict[str, Any]]
    trace_nodes: list[str]
    error: str


class EvalBaselineResponse(BaseModel):
    provider: str
    run_id: str
    metrics: EvalMetricsResponse
    pinned_at: str

    @classmethod
    def from_domain(cls, baseline: EvalBaseline) -> "EvalBaselineResponse":
        return cls(
            provider=baseline.provider,
            run_id=baseline.run_id,
            metrics=EvalMetricsResponse(**baseline.metrics.as_dict()),
            pinned_at=baseline.pinned_at.isoformat(),
        )


class EvalRunSummaryResponse(BaseModel):
    run_id: str
    suite_id: str
    provider: str
    model: str
    status: str
    started_at: str
    finished_at: Optional[str]
    total_cases: int
    completed_cases: int
    passed_cases: int
    progress: float
    metrics: Optional[EvalMetricsResponse]
    alert_count: int
    critical_alert_count: int
    baseline_run_id: str
    is_baseline: bool
    fault_injection_enabled: bool
    total_tokens: int
    elapsed_ms: int
    error: str

    @classmethod
    def from_domain(cls, record: EvalRunRecord) -> "EvalRunSummaryResponse":
        return cls(
            run_id=record.run_id,
            suite_id=record.suite_id,
            provider=record.provider,
            model=record.model,
            status=record.status,
            started_at=record.started_at.isoformat(),
            finished_at=(
                record.finished_at.isoformat() if record.finished_at else None
            ),
            total_cases=record.total_cases,
            completed_cases=record.completed_cases,
            passed_cases=record.passed_cases,
            progress=record.progress,
            metrics=(
                EvalMetricsResponse(**record.metrics.as_dict())
                if record.metrics
                else None
            ),
            alert_count=len(record.alerts),
            critical_alert_count=sum(
                1 for item in record.alerts if item.severity == "critical"
            ),
            baseline_run_id=record.baseline_run_id,
            is_baseline=record.is_baseline,
            fault_injection_enabled=record.fault_injection_enabled,
            total_tokens=record.total_tokens,
            elapsed_ms=record.elapsed_ms,
            error=record.error,
        )


class EvalRunDetailResponse(EvalRunSummaryResponse):
    alerts: list[EvalAlertResponse]
    cases: list[EvalCaseResponse]
    baseline: Optional[EvalBaselineResponse]
    deltas: dict[str, float]

    @classmethod
    def from_domain(
        cls,
        record: EvalRunRecord,
        baseline: EvalBaseline | None = None,
    ) -> "EvalRunDetailResponse":
        summary = EvalRunSummaryResponse.from_domain(record).model_dump()
        return cls(
            **summary,
            alerts=[EvalAlertResponse(**item.as_dict()) for item in record.alerts],
            cases=[EvalCaseResponse(**item.as_dict()) for item in record.cases],
            baseline=(
                EvalBaselineResponse.from_domain(baseline)
                if baseline is not None
                else None
            ),
            deltas=_deltas(record, baseline),
        )


class EvalRunListResponse(BaseModel):
    runs: list[EvalRunSummaryResponse]
    active_run_id: str


class EvalCaseDefinitionResponse(BaseModel):
    id: str
    message: str
    required_tools: list[str]
    forbidden_tools: list[str]
    order_constraints: list[list[str]]
    max_steps: Optional[int]
    reference_steps: Optional[int]
    verify_citations: bool
    injects_fault: bool


class EvalProviderResponse(BaseModel):
    provider: str
    model: str
    display_name: str


class EvalCatalogueResponse(BaseModel):
    suite_id: str
    fault_injection_enabled: bool
    metric_thresholds: dict[str, float]
    regression_tolerance: dict[str, float]
    metric_directions: dict[str, str]
    cases: list[EvalCaseDefinitionResponse]
    active_run_id: str
    baselines: list[EvalBaselineResponse]
    providers: list[EvalProviderResponse]


def _deltas(
    record: EvalRunRecord,
    baseline: EvalBaseline | None,
) -> dict[str, float]:
    """Signed change against the baseline, positive meaning worse.

    One sign convention for every metric keeps the page from having to know
    which direction is good for which number.
    """

    if baseline is None or record.metrics is None:
        return {}
    current = record.metrics.as_dict()
    reference = baseline.metrics.as_dict()
    deltas: dict[str, float] = {}
    for metric, direction in METRIC_DIRECTIONS.items():
        actual = current.get(metric)
        previous = reference.get(metric)
        if actual is None or previous is None:
            continue
        change = actual - previous
        deltas[metric] = change if direction == "lower_is_better" else -change
    return deltas
