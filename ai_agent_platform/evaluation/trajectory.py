"""L1 trajectory analysis over explicit tool-call lifecycle evidence."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from typing import Any, Iterable, Sequence


BUDGET_STOP_REASONS = frozenset(
    {"hard_tool_round_budget", "hard_tool_call_budget", "max_elapsed_time"}
)

FAILURE_RECOVERY_NOT_TRIGGERED = "not_triggered"
FAILURE_RECOVERY_RECOVERED = "recovered"
FAILURE_RECOVERY_RETRY_LOOP = "retry_loop"
FAILURE_RECOVERY_GAVE_UP = "gave_up"


@dataclass(frozen=True)
class ToolCallRecord:
    """One call at a named lifecycle stage.

    ``ok`` is optional by design. Only records joined to a real ToolResult are
    executed and therefore have a success outcome.
    """

    call_id: str
    name: str
    arguments: dict[str, Any]
    source: str
    lifecycle: str = "proposed"
    ok: bool | None = None
    error_code: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    @property
    def signature(self) -> str:
        canonical = json.dumps(
            _canonical_call_arguments(self),
            sort_keys=True,
            ensure_ascii=False,
        )
        return f"{self.name}({canonical})"

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "arguments": dict(self.arguments),
            "source": self.source,
            "lifecycle": self.lifecycle,
            "ok": self.ok,
            "error_code": self.error_code,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RunObservation:
    """The lifecycle facts needed to grade one Agent run."""

    case_id: str
    status: str
    proposed_calls: tuple[ToolCallRecord, ...]
    executed_calls: tuple[ToolCallRecord, ...]
    suppressed_calls: tuple[ToolCallRecord, ...]
    denied_calls: tuple[ToolCallRecord, ...]
    pending_approval_calls: tuple[ToolCallRecord, ...]
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
        raw_results = [
            item
            for item in result.get("tool_results", [])
            if isinstance(item, dict) and item.get("call_id")
        ]
        results_by_call_id = {
            str(item.get("call_id")): item for item in raw_results
        }
        proposed = tuple(
            ToolCallRecord(
                call_id=str(item.get("call_id") or ""),
                name=str(item.get("name") or ""),
                arguments=dict(item.get("arguments") or {}),
                source=str(item.get("source") or ""),
            )
            for item in result.get("tool_calls", [])
            if isinstance(item, dict)
        )
        proposed_by_id = {
            item.call_id: item for item in proposed if item.call_id
        }

        executed: list[ToolCallRecord] = []
        joined_ids: set[str] = set()
        for call in proposed:
            if not call.call_id or call.call_id in joined_ids:
                continue
            outcome = results_by_call_id.get(call.call_id)
            if outcome is not None:
                executed.append(_executed_record(call, outcome))
                joined_ids.add(call.call_id)
        for outcome in raw_results:
            call_id = str(outcome.get("call_id") or "")
            if call_id in joined_ids:
                continue
            executed.append(
                _executed_record(
                    ToolCallRecord(
                        call_id=call_id,
                        name=str(outcome.get("name") or ""),
                        arguments={},
                        source="tool_result",
                    ),
                    outcome,
                )
            )

        trace = tuple(
            item for item in result.get("trace", []) if isinstance(item, dict)
        )
        suppressed = _trace_lifecycle_calls(
            trace,
            "suppressed_tools",
            lifecycle="suppressed",
            proposed_by_id=proposed_by_id,
        )
        denied = _trace_lifecycle_calls(
            trace,
            "denied_tools",
            lifecycle="denied",
            proposed_by_id=proposed_by_id,
        )
        pending_payload = (
            status_body.get("pending_approval")
            or result.get("pending_approval")
            or {}
        )
        pending = _payload_lifecycle_calls(
            pending_payload.get("approval_required_tools", [])
            if isinstance(pending_payload, dict)
            else [],
            lifecycle="pending_approval",
            proposed_by_id=proposed_by_id,
        )
        return cls(
            case_id=case_id,
            status=str(status_body.get("status") or ""),
            proposed_calls=proposed,
            executed_calls=tuple(executed),
            suppressed_calls=tuple(suppressed),
            denied_calls=tuple(denied),
            pending_approval_calls=tuple(pending),
            trace=trace,
            context_sources=tuple(
                item
                for item in result.get("context_sources", [])
                if isinstance(item, dict)
            ),
            answer=str(result.get("answer") or ""),
        )

    @property
    def executed_tool_names(self) -> tuple[str, ...]:
        return tuple(call.name for call in self.executed_calls)

    @property
    def proposed_tool_names(self) -> tuple[str, ...]:
        return tuple(call.name for call in self.proposed_calls)

    @property
    def succeeded_calls(self) -> tuple[ToolCallRecord, ...]:
        return tuple(call for call in self.executed_calls if call.ok is True)

    @property
    def failed_calls(self) -> tuple[ToolCallRecord, ...]:
        return tuple(call for call in self.executed_calls if call.ok is False)

    @property
    def accepted_calls(self) -> tuple[ToolCallRecord, ...]:
        excluded = {
            item.call_id
            for group in (
                self.suppressed_calls,
                self.denied_calls,
                self.pending_approval_calls,
            )
            for item in group
            if item.call_id
        }
        return tuple(
            replace(item, lifecycle="accepted")
            for item in self.proposed_calls
            if not item.call_id or item.call_id not in excluded
        )

    @property
    def trace_nodes(self) -> tuple[str, ...]:
        return tuple(str(step.get("node") or "") for step in self.trace)


@dataclass(frozen=True)
class ConstraintVerdict:
    name: str
    passed: bool
    detail: str
    severity: str = "critical"


@dataclass(frozen=True)
class TrajectoryMetrics:
    proposed_calls: int
    accepted_calls: int
    executed_calls: int
    succeeded_calls: int
    failed_calls: int
    repeated_calls: int
    retries_after_failure: int
    suppressed_calls: int
    denied_calls: int
    pending_approval_calls: int
    invalid_action_rate: float | None
    reference_steps: int | None
    step_efficiency: float | None
    budget_capped: bool
    budget_reasons: tuple[str, ...]
    failure_recovery: str


def check_constraints(
    observation: RunObservation,
    case: dict[str, Any],
    *,
    provider: str = "",
) -> list[ConstraintVerdict]:
    verdicts: list[ConstraintVerdict | None] = [
        _status_verdict(observation, case.get("expected_status")),
        _required_tools_verdict(observation, case.get("required_tools") or []),
        *_forbidden_tools_verdicts(
            observation, case.get("forbidden_tools") or []
        ),
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
    """Measure only calls that produced ToolResults plus suppressed calls."""

    calls = observation.executed_calls
    signatures = [call.signature for call in calls]
    repeated_indices = {
        index
        for index, signature in enumerate(signatures)
        if signature in signatures[:index]
    }
    retry_indices = {
        index
        for index in repeated_indices
        if any(
            earlier.ok is False and earlier.signature == signatures[index]
            for earlier in calls[:index]
        )
    }
    suppressed = len(observation.suppressed_calls)
    denominator = len(calls) + suppressed
    numerator = len(repeated_indices) + suppressed
    budget_reasons = _budget_reasons(observation)
    return TrajectoryMetrics(
        proposed_calls=len(observation.proposed_calls),
        accepted_calls=len(observation.accepted_calls),
        executed_calls=len(calls),
        succeeded_calls=len(observation.succeeded_calls),
        failed_calls=len(observation.failed_calls),
        repeated_calls=len(repeated_indices),
        retries_after_failure=len(retry_indices),
        suppressed_calls=suppressed,
        denied_calls=len(observation.denied_calls),
        pending_approval_calls=len(observation.pending_approval_calls),
        invalid_action_rate=(numerator / denominator) if denominator else None,
        reference_steps=reference_steps,
        step_efficiency=(len(calls) / reference_steps if reference_steps else None),
        budget_capped=bool(budget_reasons),
        budget_reasons=budget_reasons,
        failure_recovery=classify_failure_recovery(calls),
    )


def classify_failure_recovery(calls: Sequence[ToolCallRecord]) -> str:
    first_failure = next(
        (index for index, call in enumerate(calls) if call.ok is False),
        None,
    )
    if first_failure is None:
        return FAILURE_RECOVERY_NOT_TRIGGERED
    failed_signature = calls[first_failure].signature
    subsequent = calls[first_failure + 1 :]
    if not subsequent:
        return FAILURE_RECOVERY_GAVE_UP
    progressed = any(
        call.ok is True and call.signature != failed_signature
        for call in subsequent
    )
    return FAILURE_RECOVERY_RECOVERED if progressed else FAILURE_RECOVERY_RETRY_LOOP


def aggregate_invalid_action_rate(
    metrics: Iterable[TrajectoryMetrics],
) -> float | None:
    numerator = 0
    denominator = 0
    for item in metrics:
        numerator += item.repeated_calls + item.suppressed_calls
        denominator += item.executed_calls + item.suppressed_calls
    return (numerator / denominator) if denominator else None


def aggregate_step_efficiency(
    metrics: Iterable[TrajectoryMetrics],
) -> float | None:
    ratios = [
        item.step_efficiency
        for item in metrics
        if item.step_efficiency is not None
    ]
    return (sum(ratios) / len(ratios)) if ratios else None


def aggregate_budget_cap_rate(metrics: Iterable[TrajectoryMetrics]) -> float | None:
    items = list(metrics)
    if not items:
        return None
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
    called = set(observation.executed_tool_names)
    missing = [str(name) for name in required if str(name) not in called]
    return ConstraintVerdict(
        name="required_tools",
        passed=not missing,
        detail=f"missing={missing} executed={sorted(called)}",
    )


def _forbidden_tools_verdicts(
    observation: RunObservation,
    forbidden: Sequence[Any],
) -> list[ConstraintVerdict]:
    if not forbidden:
        return []
    proposed = set(observation.proposed_tool_names)
    executed = set(observation.executed_tool_names)
    proposed_present = [str(name) for name in forbidden if str(name) in proposed]
    executed_present = [str(name) for name in forbidden if str(name) in executed]
    return [
        ConstraintVerdict(
            name="forbidden_tools_proposed",
            passed=not proposed_present,
            detail=f"proposed_forbidden={proposed_present}",
            severity="warning",
        ),
        ConstraintVerdict(
            name="forbidden_tools_executed",
            passed=not executed_present,
            detail=f"executed_forbidden={executed_present}",
        ),
    ]


def _order_verdict(
    observation: RunObservation,
    constraints: Sequence[Any],
) -> ConstraintVerdict | None:
    if not constraints:
        return None
    names = observation.executed_tool_names
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
        detail=f"violations={violations} executed_order={list(names)}",
    )


def _max_steps_verdict(
    observation: RunObservation,
    max_steps: Any,
) -> ConstraintVerdict | None:
    if max_steps is None:
        return None
    limit = int(max_steps)
    actual = len(observation.executed_calls)
    return ConstraintVerdict(
        name="max_steps",
        passed=actual <= limit,
        detail=f"limit={limit} executed={actual}",
    )


def _first_index(names: Sequence[str], target: str) -> int | None:
    for index, name in enumerate(names):
        if name == target:
            return index
    return None


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


def _executed_record(
    call: ToolCallRecord,
    outcome: dict[str, Any],
) -> ToolCallRecord:
    raw_result = outcome.get("result")
    return replace(
        call,
        lifecycle="executed",
        ok=bool(outcome.get("ok")),
        error_code=str(outcome.get("error_code") or ""),
        result=dict(raw_result) if isinstance(raw_result, dict) else {},
        reason=str(outcome.get("error") or ""),
    )


def _trace_lifecycle_calls(
    trace: Sequence[dict[str, Any]],
    key: str,
    *,
    lifecycle: str,
    proposed_by_id: dict[str, ToolCallRecord],
) -> list[ToolCallRecord]:
    values: list[dict[str, Any]] = []
    for step in trace:
        output = step.get("output")
        if not isinstance(output, dict):
            continue
        items = output.get(key)
        if isinstance(items, list):
            values.extend(item for item in items if isinstance(item, dict))
    return _payload_lifecycle_calls(
        values,
        lifecycle=lifecycle,
        proposed_by_id=proposed_by_id,
    )


def _payload_lifecycle_calls(
    values: Sequence[dict[str, Any]],
    *,
    lifecycle: str,
    proposed_by_id: dict[str, ToolCallRecord],
) -> list[ToolCallRecord]:
    calls: list[ToolCallRecord] = []
    seen: set[str] = set()
    for index, item in enumerate(values):
        call_id = str(item.get("call_id") or "")
        dedupe_key = call_id or f"{index}:{item.get('name')}:{item.get('reason')}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        proposed = proposed_by_id.get(call_id)
        reason = str(item.get("reason") or item.get("matched_rule") or "")
        if proposed is not None:
            calls.append(replace(proposed, lifecycle=lifecycle, reason=reason))
            continue
        calls.append(
            ToolCallRecord(
                call_id=call_id,
                name=str(item.get("name") or ""),
                arguments=dict(item.get("arguments") or {}),
                source=str(item.get("source") or "trace"),
                lifecycle=lifecycle,
                reason=reason,
            )
        )
    return calls


def _canonical_call_arguments(call: ToolCallRecord) -> dict[str, Any]:
    arguments = dict(call.arguments)
    if call.name != "repo.read_file":
        return arguments
    output = call.result
    path = str(output.get("path") or arguments.get("path") or "")
    normalized_path = "/".join(
        part
        for part in path.replace("\\", "/").split("/")
        if part not in {"", "."}
    )
    return {
        "path": normalized_path,
        "start_line": int(
            output.get("start_line") or arguments.get("start_line") or 1
        ),
        "end_line": output.get("end_line", arguments.get("end_line")),
    }
