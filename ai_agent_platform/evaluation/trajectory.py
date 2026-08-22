"""L1 trajectory analysis: grade how a run reached its answer.

L0 (``run_evals.py``) proves the pipeline still works. This module grades the
process itself: which tools ran, in what order, how many were wasted, and what
the loop did after a tool failed. Everything here is a pure function over one
run's API payload, so the same analysis works for a fake-provider regression and
for a future real-model run.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable, Sequence


# Terminal reasons that mean "the loop ran out of budget", as opposed to
# "the loop gave up" (`max_consecutive_tool_failures`, `no_progress`,
# `change_not_applied`). See `agents/coding/policies.py`.
BUDGET_STOP_REASONS = frozenset(
    {
        "hard_tool_round_budget",
        "hard_tool_call_budget",
        "max_elapsed_time",
    }
)

FAILURE_RECOVERY_NOT_TRIGGERED = "not_triggered"
FAILURE_RECOVERY_RECOVERED = "recovered"
FAILURE_RECOVERY_RETRY_LOOP = "retry_loop"
FAILURE_RECOVERY_GAVE_UP = "gave_up"


@dataclass(frozen=True)
class ToolCallRecord:
    """One executed tool call joined with the result it produced."""

    name: str
    arguments: dict[str, Any]
    source: str
    ok: bool
    error_code: str

    @property
    def signature(self) -> str:
        canonical = json.dumps(self.arguments, sort_keys=True, ensure_ascii=False)
        return f"{self.name}({canonical})"


@dataclass(frozen=True)
class RunObservation:
    """Everything L1 needs from one agent run, read from the public API."""

    case_id: str
    status: str
    tool_calls: tuple[ToolCallRecord, ...]
    trace: tuple[dict[str, Any], ...]
    context_sources: tuple[dict[str, Any], ...]
    answer: str

    @classmethod
    def from_run_status(
        cls,
        case_id: str,
        status_body: dict[str, Any],
    ) -> "RunObservation":
        result = status_body.get("result") or {}
        results_by_call_id = {
            str(item.get("call_id")): item
            for item in result.get("tool_results", [])
            if isinstance(item, dict)
        }
        calls: list[ToolCallRecord] = []
        for item in result.get("tool_calls", []):
            outcome = results_by_call_id.get(str(item.get("call_id")), {})
            calls.append(
                ToolCallRecord(
                    name=str(item.get("name") or ""),
                    arguments=dict(item.get("arguments") or {}),
                    source=str(item.get("source") or ""),
                    ok=bool(outcome.get("ok", True)),
                    error_code=str(outcome.get("error_code") or ""),
                )
            )
        return cls(
            case_id=case_id,
            status=str(status_body.get("status") or ""),
            tool_calls=tuple(calls),
            trace=tuple(
                item for item in result.get("trace", []) if isinstance(item, dict)
            ),
            context_sources=tuple(
                item
                for item in result.get("context_sources", [])
                if isinstance(item, dict)
            ),
            answer=str(result.get("answer") or ""),
        )

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(call.name for call in self.tool_calls)

    @property
    def trace_nodes(self) -> tuple[str, ...]:
        return tuple(str(step.get("node") or "") for step in self.trace)


@dataclass(frozen=True)
class ConstraintVerdict:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class TrajectoryMetrics:
    """The four DESIGN.md L1 metrics for a single run."""

    executed_calls: int
    repeated_calls: int
    retries_after_failure: int
    suppressed_calls: int
    invalid_action_rate: float
    reference_steps: int | None
    step_efficiency: float | None
    budget_capped: bool
    budget_reasons: tuple[str, ...]
    failed_calls: int
    failure_recovery: str


def check_constraints(
    observation: RunObservation,
    case: dict[str, Any],
    *,
    provider: str = "",
) -> list[ConstraintVerdict]:
    """Grade a run against declared constraints instead of a golden sequence.

    A constraint the case does not declare is skipped rather than silently
    passed, so an under-specified case is visible in the report.

    Step ceilings are provider-aware. Correctness constraints — which tools must
    and must not run, and in what order — hold for every model, but how many
    calls a task takes is a property of the model driving the loop: a real model
    in the native tool loop routinely needs several times what the deterministic
    rule planner needs, and one ceiling for both would either be meaningless for
    the fake provider or permanently red for the real one.
    """

    verdicts = [
        _status_verdict(observation, case.get("expected_status")),
        _required_tools_verdict(observation, case.get("required_tools") or []),
        _forbidden_tools_verdict(observation, case.get("forbidden_tools") or []),
        _order_verdict(observation, case.get("order_constraints") or []),
        _max_steps_verdict(observation, _max_steps_for(case, provider)),
    ]
    return [verdict for verdict in verdicts if verdict is not None]


def _max_steps_for(case: dict[str, Any], provider: str) -> Any:
    overrides = case.get("max_steps_by_provider")
    if isinstance(overrides, dict) and provider in overrides:
        return overrides[provider]
    return case.get("max_steps")


def measure_trajectory(
    observation: RunObservation,
    *,
    reference_steps: int | None = None,
) -> TrajectoryMetrics:
    """Compute the four L1 metrics for one run.

    Invalid actions are counted per executed call, at most once, even when a
    call is both a repeat and a post-failure retry. Suppressed calls never
    reached execution, so they are added to both sides of the ratio.
    """

    calls = observation.tool_calls
    signatures = [call.signature for call in calls]
    repeated_indices = {
        index
        for index, signature in enumerate(signatures)
        if signature in signatures[:index]
    }
    failed_signatures = {
        signature
        for index, signature in enumerate(signatures)
        if not calls[index].ok
    }
    retry_indices = {
        index
        for index in repeated_indices
        if signatures[index] in failed_signatures
    }
    suppressed = _suppressed_call_count(observation)
    denominator = len(calls) + suppressed
    numerator = len(repeated_indices) + suppressed
    budget_reasons = _budget_reasons(observation)
    return TrajectoryMetrics(
        executed_calls=len(calls),
        repeated_calls=len(repeated_indices),
        retries_after_failure=len(retry_indices),
        suppressed_calls=suppressed,
        invalid_action_rate=(numerator / denominator) if denominator else 0.0,
        reference_steps=reference_steps,
        step_efficiency=(
            len(calls) / reference_steps
            if reference_steps
            else None
        ),
        budget_capped=bool(budget_reasons),
        budget_reasons=budget_reasons,
        failed_calls=sum(1 for call in calls if not call.ok),
        failure_recovery=classify_failure_recovery(calls),
    )


def classify_failure_recovery(calls: Sequence[ToolCallRecord]) -> str:
    """Decide whether the loop changed strategy after its first tool failure.

    ``recovered`` means a different call succeeded afterwards. ``retry_loop``
    means the loop only re-issued the call that already failed. ``gave_up``
    means it stopped calling tools entirely.
    """

    first_failure = next(
        (index for index, call in enumerate(calls) if not call.ok),
        None,
    )
    if first_failure is None:
        return FAILURE_RECOVERY_NOT_TRIGGERED
    failed_signature = calls[first_failure].signature
    subsequent = calls[first_failure + 1 :]
    if not subsequent:
        return FAILURE_RECOVERY_GAVE_UP
    progressed = any(
        call.ok and call.signature != failed_signature for call in subsequent
    )
    return (
        FAILURE_RECOVERY_RECOVERED if progressed else FAILURE_RECOVERY_RETRY_LOOP
    )


def aggregate_invalid_action_rate(
    metrics: Iterable[TrajectoryMetrics],
) -> float:
    """Pool calls across cases rather than averaging per-case rates.

    A case with two calls should not weigh as much as a case with twenty.
    """

    numerator = 0
    denominator = 0
    for item in metrics:
        numerator += item.repeated_calls + item.suppressed_calls
        denominator += item.executed_calls + item.suppressed_calls
    return (numerator / denominator) if denominator else 0.0


def aggregate_step_efficiency(
    metrics: Iterable[TrajectoryMetrics],
) -> float | None:
    ratios = [
        item.step_efficiency
        for item in metrics
        if item.step_efficiency is not None
    ]
    return (sum(ratios) / len(ratios)) if ratios else None


def aggregate_budget_cap_rate(metrics: Iterable[TrajectoryMetrics]) -> float:
    items = list(metrics)
    if not items:
        return 0.0
    return sum(1 for item in items if item.budget_capped) / len(items)


def aggregate_failure_recovery_rate(
    metrics: Iterable[TrajectoryMetrics],
) -> float | None:
    triggered = [
        item
        for item in metrics
        if item.failure_recovery != FAILURE_RECOVERY_NOT_TRIGGERED
    ]
    if not triggered:
        return None
    recovered = sum(
        1
        for item in triggered
        if item.failure_recovery == FAILURE_RECOVERY_RECOVERED
    )
    return recovered / len(triggered)


def _status_verdict(
    observation: RunObservation,
    expected: Any,
) -> ConstraintVerdict | None:
    if expected is None:
        return None
    return ConstraintVerdict(
        name="status",
        passed=observation.status == str(expected),
        detail=f"expected={expected!r} actual={observation.status!r}",
    )


def _required_tools_verdict(
    observation: RunObservation,
    required: Sequence[Any],
) -> ConstraintVerdict | None:
    if not required:
        return None
    called = set(observation.tool_names)
    missing = [str(name) for name in required if str(name) not in called]
    return ConstraintVerdict(
        name="required_tools",
        passed=not missing,
        detail=f"missing={missing} called={sorted(called)}",
    )


def _forbidden_tools_verdict(
    observation: RunObservation,
    forbidden: Sequence[Any],
) -> ConstraintVerdict | None:
    if not forbidden:
        return None
    called = set(observation.tool_names)
    present = [str(name) for name in forbidden if str(name) in called]
    return ConstraintVerdict(
        name="forbidden_tools",
        passed=not present,
        detail=f"called_forbidden={present}",
    )


def _order_verdict(
    observation: RunObservation,
    constraints: Sequence[Any],
) -> ConstraintVerdict | None:
    if not constraints:
        return None
    names = observation.tool_names
    violations: list[str] = []
    for constraint in constraints:
        before, after = str(constraint[0]), str(constraint[1])
        if after not in names:
            continue
        first_before = _first_index(names, before)
        first_after = _first_index(names, after)
        if first_before is None or first_before > first_after:
            violations.append(f"{before}->{after}")
    return ConstraintVerdict(
        name="order_constraints",
        passed=not violations,
        detail=f"violations={violations} order={list(names)}",
    )


def _max_steps_verdict(
    observation: RunObservation,
    max_steps: Any,
) -> ConstraintVerdict | None:
    if max_steps is None:
        return None
    limit = int(max_steps)
    actual = len(observation.tool_calls)
    return ConstraintVerdict(
        name="max_steps",
        passed=actual <= limit,
        detail=f"limit={limit} actual={actual}",
    )


def _first_index(names: Sequence[str], target: str) -> int | None:
    for index, name in enumerate(names):
        if name == target:
            return index
    return None


def _suppressed_call_count(observation: RunObservation) -> int:
    total = 0
    for step in observation.trace:
        output = step.get("output")
        if not isinstance(output, dict):
            continue
        suppressed = output.get("suppressed_tools")
        if isinstance(suppressed, list):
            total += len(suppressed)
    return total


def _budget_reasons(observation: RunObservation) -> tuple[str, ...]:
    reasons: list[str] = []
    for step in observation.trace:
        output = step.get("output")
        if not isinstance(output, dict):
            continue
        node = str(step.get("node") or "")
        if node == "assess_context" and output.get("budget_exhausted"):
            reason = f"exploration_round_{output.get('round')}"
            if reason not in reasons:
                reasons.append(reason)
        stop_reason = str(output.get("stop_reason") or "")
        if stop_reason in BUDGET_STOP_REASONS and stop_reason not in reasons:
            reasons.append(stop_reason)
    return tuple(reasons)
