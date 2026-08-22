"""Offline L1 trajectory eval runner.

This is the zero-cost path: a throwaway app on the fake LLM provider, driven
over HTTP the same way `run_evals.py` drives it. The analysis itself lives in
`ai_agent_platform.evaluation` because the container image only carries the
package, and the same code has to serve the in-app runs against a real model.

    .venv/bin/python evals/run_trajectory_evals.py

Cases declare constraints, not a golden node sequence. The observed sequence is
printed as a diagnostic and never decides pass or fail, so an improvement to the
exploration heuristics does not turn the suite red and pressure someone into
editing the expected value.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import time
from tempfile import TemporaryDirectory
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.evaluation import (
    CitationReport,
    ConstraintVerdict,
    DEFAULT_SUITE_PATH,
    EvalSuite,
    FaultInjectingToolRegistry,
    RunObservation,
    ToolFaultController,
    TrajectoryMetrics,
    aggregate_budget_cap_rate,
    aggregate_failure_recovery_rate,
    aggregate_invalid_action_rate,
    aggregate_step_efficiency,
    check_constraints,
    load_suite,
    measure_trajectory,
    verify_citations,
)
from ai_agent_platform.integrations.tools import ToolRegistry
from ai_agent_platform.main import create_app
from ai_agent_platform.runtime import ApplicationFactory


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


class FaultInjectingFactory(ApplicationFactory):
    """Installs the fault-injecting registry through the documented seam."""

    def __init__(self, controller: ToolFaultController) -> None:
        self._controller = controller

    def create_tool_registry(self, settings: Settings, **kwargs: Any) -> ToolRegistry:
        return FaultInjectingToolRegistry(
            super().create_tool_registry(settings, **kwargs),
            self._controller,
        )


@dataclass(frozen=True)
class CaseReport:
    case_id: str
    status: str
    constraints: tuple[ConstraintVerdict, ...]
    metrics: TrajectoryMetrics
    citations: CitationReport | None
    trace_nodes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.constraints) and (
            self.citations is None or self.citations.passed
        )


@dataclass(frozen=True)
class TrajectoryReport:
    cases: tuple[CaseReport, ...]
    gate_failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases) and not self.gate_failures

    @property
    def passed_count(self) -> int:
        return sum(1 for case in self.cases if case.passed)

    @property
    def invalid_action_rate(self) -> float:
        return aggregate_invalid_action_rate(case.metrics for case in self.cases)

    @property
    def mean_step_efficiency(self) -> float | None:
        return aggregate_step_efficiency(case.metrics for case in self.cases)

    @property
    def budget_cap_rate(self) -> float:
        return aggregate_budget_cap_rate(case.metrics for case in self.cases)

    @property
    def failure_recovery_rate(self) -> float | None:
        return aggregate_failure_recovery_rate(case.metrics for case in self.cases)

    @property
    def citation_accuracy(self) -> float | None:
        scored = sum(
            case.citations.scored_count
            for case in self.cases
            if case.citations is not None
        )
        verified = sum(
            case.citations.verified_count
            for case in self.cases
            if case.citations is not None
        )
        return (verified / scored) if scored else None


def load_trajectory_suite(path: Path = DEFAULT_SUITE_PATH) -> EvalSuite:
    return load_suite(path)


def run_trajectory_suite(suite: EvalSuite) -> TrajectoryReport:
    controller = ToolFaultController()
    with TemporaryDirectory() as temp_dir:
        workspace_root = Path(temp_dir)
        suite.materialize(workspace_root)
        app = create_app(
            settings=Settings(
                llm_provider="fake",
                embedding_provider="local",
                rag_vector_store="memory",
                workspace_allowed_roots=(str(workspace_root),),
            ),
            application_factory=FaultInjectingFactory(controller),
        )
        with TestClient(app) as client:
            client.put(
                f"/api/v1/workspaces/{suite.workspace_id}",
                json={"root_path": str(workspace_root)},
            ).raise_for_status()
            cases = [
                _run_trajectory_case(
                    client=client,
                    controller=controller,
                    workspace_id=suite.workspace_id,
                    workspace_root=workspace_root,
                    case=case,
                )
                for case in suite.cases
            ]
    report = TrajectoryReport(cases=tuple(cases), gate_failures=())
    return TrajectoryReport(
        cases=report.cases,
        gate_failures=tuple(_gate_failures(report, suite.metric_thresholds)),
    )


def format_report(report: TrajectoryReport) -> str:
    lines = [
        "Agent Trajectory Eval Report (L1)",
        f"Passed: {report.passed_count}/{len(report.cases)}",
        _format_suite_metrics(report),
    ]
    for failure in report.gate_failures:
        lines.append(f"- FAIL metric_gate: {failure}")
    for case in report.cases:
        status = "PASS" if case.passed else "FAIL"
        lines.append(f"- {status} {case.case_id} [{case.status}]")
        for constraint in case.constraints:
            mark = "ok" if constraint.passed else "miss"
            lines.append(f"  - {mark} {constraint.name}: {constraint.detail}")
        lines.append(f"  - metrics {_format_case_metrics(case.metrics)}")
        if case.citations is not None:
            lines.append(f"  - citations {_format_citations(case.citations)}")
        lines.append(f"  - trace (diagnostic) {' > '.join(case.trace_nodes)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run L1 agent trajectory eval cases offline on the fake provider."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_SUITE_PATH,
        help="Path to trajectory case JSON file.",
    )
    args = parser.parse_args(argv)
    report = run_trajectory_suite(load_trajectory_suite(args.cases))
    print(format_report(report))
    return 0 if report.passed else 1


def _run_trajectory_case(
    *,
    client: TestClient,
    controller: ToolFaultController,
    workspace_id: str,
    workspace_root: Path,
    case: dict[str, Any],
) -> CaseReport:
    case_id = str(case.get("id") or "<missing-id>")
    fault = case.get("fault_injection")
    if isinstance(fault, dict):
        controller.arm(
            str(fault["tool"]),
            workspace_id=workspace_id,
            occurrences=int(fault.get("occurrences") or 1),
        )
    else:
        controller.disarm()
    session = client.post("/api/v1/sessions", json={"user_id": "l1_eval_runner"})
    session.raise_for_status()
    run = client.post(
        "/api/v1/agent/runs",
        json={
            "conversation_id": session.json()["id"],
            "workspace_id": workspace_id,
            "message": case["message"],
        },
    )
    run.raise_for_status()
    status_body = _wait_for_run(client, run.json()["run_id"])
    controller.disarm()
    observation = RunObservation.from_run_status(case_id, status_body)
    result = status_body.get("result") or {}
    citations = (
        verify_citations(
            context_sources=result.get("context_sources", []),
            answer=str(result.get("answer") or ""),
            workspace_root=workspace_root,
        )
        if case.get("verify_citations")
        else None
    )
    return CaseReport(
        case_id=case_id,
        status=observation.status,
        constraints=tuple(check_constraints(observation, case, provider="fake")),
        metrics=measure_trajectory(
            observation,
            reference_steps=case.get("reference_steps"),
        ),
        citations=citations,
        trace_nodes=observation.trace_nodes,
    )


def _wait_for_run(client: TestClient, run_id: str) -> dict[str, Any]:
    for _ in range(300):
        response = client.get(f"/api/v1/agent/runs/{run_id}")
        response.raise_for_status()
        body = response.json()
        if body["status"] in TERMINAL_STATUSES:
            return body
        time.sleep(0.02)
    raise TimeoutError(f"agent run {run_id} did not finish")


def _gate_failures(report: TrajectoryReport, thresholds: dict[str, float]) -> list[str]:
    failures: list[str] = []
    upper_bounds = {
        "max_invalid_action_rate": report.invalid_action_rate,
        "max_mean_step_efficiency": report.mean_step_efficiency,
        "max_budget_cap_rate": report.budget_cap_rate,
    }
    lower_bounds = {
        "min_failure_recovery_rate": report.failure_recovery_rate,
        "min_citation_accuracy": report.citation_accuracy,
    }
    for name, actual in upper_bounds.items():
        configured = thresholds.get(name)
        if configured is None or actual is None:
            continue
        if actual > float(configured):
            failures.append(
                f"{name} expected<={float(configured):.3f} actual={actual:.3f}"
            )
    for name, actual in lower_bounds.items():
        configured = thresholds.get(name)
        if configured is None:
            continue
        if actual is None:
            failures.append(f"{name} configured but no case measured it")
            continue
        if actual < float(configured):
            failures.append(
                f"{name} expected>={float(configured):.3f} actual={actual:.3f}"
            )
    return failures


def _format_suite_metrics(report: TrajectoryReport) -> str:
    return (
        f"InvalidActionRate={report.invalid_action_rate:.3f}; "
        f"MeanStepEfficiency={_format_optional(report.mean_step_efficiency)}; "
        f"BudgetCapRate={report.budget_cap_rate:.3f}; "
        f"FailureRecoveryRate={_format_optional(report.failure_recovery_rate)}; "
        f"CitationAccuracy={_format_optional(report.citation_accuracy)}"
    )


def _format_case_metrics(metrics: TrajectoryMetrics) -> str:
    parts = [
        f"calls={metrics.executed_calls}",
        f"failed={metrics.failed_calls}",
        f"repeated={metrics.repeated_calls}",
        f"retries_after_failure={metrics.retries_after_failure}",
        f"suppressed={metrics.suppressed_calls}",
        f"invalid_action_rate={metrics.invalid_action_rate:.3f}",
        f"step_efficiency={_format_optional(metrics.step_efficiency)}",
        f"budget_capped={metrics.budget_capped}",
        f"failure_recovery={metrics.failure_recovery}",
    ]
    if metrics.budget_reasons:
        parts.append(f"budget_reasons={list(metrics.budget_reasons)}")
    return " ".join(parts)


def _format_citations(report: CitationReport) -> str:
    detail = (
        f"verified={report.verified_count}/{report.scored_count} "
        f"unverifiable={len(report.verdicts) - report.scored_count}"
    )
    for verdict in report.failures:
        detail += (
            f"; {verdict.status} {verdict.path}"
            f":{verdict.start_line}-{verdict.end_line} {verdict.detail}"
        )
    if report.ungrounded_paths:
        detail += f"; ungrounded={list(report.ungrounded_paths)}"
    return detail


def _format_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    sys.exit(main())
