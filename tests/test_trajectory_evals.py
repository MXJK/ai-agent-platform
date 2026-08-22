from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import unittest

from ai_agent_platform.evaluation.citations import (
    STATUS_CONTENT_MISMATCH,
    STATUS_MISSING_FILE,
    STATUS_OUT_OF_RANGE,
    STATUS_UNVERIFIABLE,
    STATUS_VERIFIED,
    answer_citation_paths,
    verify_citations,
)
from evals.run_trajectory_evals import (
    format_report,
    load_trajectory_suite,
    run_trajectory_suite,
)
from ai_agent_platform.evaluation.trajectory import (
    FAILURE_RECOVERY_GAVE_UP,
    FAILURE_RECOVERY_NOT_TRIGGERED,
    FAILURE_RECOVERY_RECOVERED,
    FAILURE_RECOVERY_RETRY_LOOP,
    RunObservation,
    aggregate_invalid_action_rate,
    check_constraints,
    measure_trajectory,
)


def _observation(
    *,
    calls: list[tuple[str, dict[str, Any], bool]] | None = None,
    trace: list[dict[str, Any]] | None = None,
    status: str = "completed",
    context_sources: list[dict[str, Any]] | None = None,
    answer: str = "",
) -> RunObservation:
    """Build an observation the same way the runner does, from an API payload."""

    tool_calls = []
    tool_results = []
    for index, (name, arguments, ok) in enumerate(calls or []):
        call_id = f"call_{index}"
        tool_calls.append(
            {
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
                "source": "planner",
            }
        )
        tool_results.append(
            {
                "call_id": call_id,
                "name": name,
                "ok": ok,
                "error_code": "" if ok else "tool_execution_error",
            }
        )
    return RunObservation.from_run_status(
        "case",
        {
            "status": status,
            "result": {
                "tool_calls": tool_calls,
                "tool_results": tool_results,
                "trace": trace or [],
                "context_sources": context_sources or [],
                "answer": answer,
            },
        },
    )


def _verdicts(observation: RunObservation, case: dict[str, Any]) -> dict[str, bool]:
    return {item.name: item.passed for item in check_constraints(observation, case)}


class TrajectoryConstraintTests(unittest.TestCase):
    def test_forbidden_tool_fails_the_case(self) -> None:
        observation = _observation(
            calls=[
                ("repo.read_file", {"path": "a.py"}, True),
                ("sandbox.write_file", {"path": "a.py"}, True),
            ]
        )

        verdicts = _verdicts(
            observation, {"forbidden_tools": ["sandbox.write_file"]}
        )

        self.assertFalse(verdicts["forbidden_tools"])

    def test_order_constraint_fails_when_the_prerequisite_never_ran(self) -> None:
        observation = _observation(
            calls=[("code_explainer", {"query": "why"}, True)]
        )

        verdicts = _verdicts(
            observation,
            {"order_constraints": [["repo.read_file", "code_explainer"]]},
        )

        self.assertFalse(verdicts["order_constraints"])

    def test_order_constraint_fails_when_the_prerequisite_ran_too_late(self) -> None:
        observation = _observation(
            calls=[
                ("code_explainer", {"query": "why"}, True),
                ("repo.read_file", {"path": "a.py"}, True),
            ]
        )

        verdicts = _verdicts(
            observation,
            {"order_constraints": [["repo.read_file", "code_explainer"]]},
        )

        self.assertFalse(verdicts["order_constraints"])

    def test_order_constraint_is_satisfied_by_any_earlier_occurrence(self) -> None:
        observation = _observation(
            calls=[
                ("repo.read_file", {"path": "a.py"}, True),
                ("code_explainer", {"query": "why"}, True),
                ("code_explainer", {"query": "how"}, True),
            ]
        )

        verdicts = _verdicts(
            observation,
            {"order_constraints": [["repo.read_file", "code_explainer"]]},
        )

        self.assertTrue(verdicts["order_constraints"])

    def test_order_constraint_is_skipped_when_the_later_tool_never_ran(self) -> None:
        observation = _observation(
            calls=[("repo.search_code", {"query": "x"}, True)]
        )

        verdicts = _verdicts(
            observation,
            {"order_constraints": [["repo.read_file", "code_explainer"]]},
        )

        self.assertTrue(verdicts["order_constraints"])

    def test_required_tools_and_max_steps_are_enforced(self) -> None:
        observation = _observation(
            calls=[
                ("repo.search_code", {"query": "x"}, True),
                ("repo.search_code", {"query": "y"}, True),
            ]
        )

        verdicts = _verdicts(
            observation,
            {"required_tools": ["repo.read_file"], "max_steps": 1},
        )

        self.assertFalse(verdicts["required_tools"])
        self.assertFalse(verdicts["max_steps"])

    def test_undeclared_constraints_are_omitted_rather_than_passed(self) -> None:
        observation = _observation(calls=[("repo.read_file", {}, True)])

        names = {item.name for item in check_constraints(observation, {})}

        self.assertEqual(names, set())


class TrajectoryMetricTests(unittest.TestCase):
    def test_repeated_call_raises_the_invalid_action_rate(self) -> None:
        observation = _observation(
            calls=[
                ("repo.read_file", {"path": "a.py"}, True),
                ("repo.read_file", {"path": "b.py"}, True),
                ("repo.read_file", {"path": "a.py"}, True),
                ("code_explainer", {"query": "why"}, True),
            ]
        )

        metrics = measure_trajectory(observation)

        self.assertEqual(metrics.repeated_calls, 1)
        self.assertEqual(metrics.retries_after_failure, 0)
        self.assertAlmostEqual(metrics.invalid_action_rate, 0.25)

    def test_retry_of_a_failed_call_is_reported_separately(self) -> None:
        observation = _observation(
            calls=[
                ("repo.read_file", {"path": "a.py"}, False),
                ("repo.read_file", {"path": "a.py"}, False),
            ]
        )

        metrics = measure_trajectory(observation)

        self.assertEqual(metrics.repeated_calls, 1)
        self.assertEqual(metrics.retries_after_failure, 1)
        self.assertAlmostEqual(metrics.invalid_action_rate, 0.5)

    def test_suppressed_calls_count_on_both_sides_of_the_ratio(self) -> None:
        observation = _observation(
            calls=[("repo.read_file", {"path": "a.py"}, True)],
            trace=[
                {
                    "node": "plan_tools",
                    "output": {
                        "suppressed_tools": [
                            {"name": "repo.read_file", "reason": "repeated_tool_call"}
                        ]
                    },
                }
            ],
        )

        metrics = measure_trajectory(observation)

        self.assertEqual(metrics.suppressed_calls, 1)
        self.assertAlmostEqual(metrics.invalid_action_rate, 0.5)

    def test_step_efficiency_needs_a_declared_reference(self) -> None:
        observation = _observation(
            calls=[("repo.read_file", {"path": "a.py"}, True)] * 1
        )

        self.assertIsNone(measure_trajectory(observation).step_efficiency)
        self.assertAlmostEqual(
            measure_trajectory(observation, reference_steps=2).step_efficiency,
            0.5,
        )

    def test_exploration_budget_exhaustion_is_detected(self) -> None:
        observation = _observation(
            trace=[
                {
                    "node": "assess_context",
                    "output": {"round": 3, "budget_exhausted": True},
                }
            ]
        )

        metrics = measure_trajectory(observation)

        self.assertTrue(metrics.budget_capped)
        self.assertEqual(metrics.budget_reasons, ("exploration_round_3",))

    def test_hard_tool_budget_is_detected_but_giving_up_is_not(self) -> None:
        capped = _observation(
            trace=[
                {
                    "node": "plan_tools",
                    "output": {"stop_reason": "hard_tool_round_budget"},
                }
            ]
        )
        gave_up = _observation(
            trace=[
                {
                    "node": "plan_tools",
                    "output": {"stop_reason": "max_consecutive_tool_failures"},
                }
            ]
        )

        self.assertTrue(measure_trajectory(capped).budget_capped)
        self.assertFalse(measure_trajectory(gave_up).budget_capped)

    def test_failure_recovery_distinguishes_the_four_outcomes(self) -> None:
        cases = {
            FAILURE_RECOVERY_NOT_TRIGGERED: [
                ("repo.read_file", {"path": "a.py"}, True),
            ],
            FAILURE_RECOVERY_GAVE_UP: [
                ("repo.read_file", {"path": "a.py"}, True),
                ("repo.read_file", {"path": "b.py"}, False),
            ],
            FAILURE_RECOVERY_RETRY_LOOP: [
                ("repo.read_file", {"path": "b.py"}, False),
                ("repo.read_file", {"path": "b.py"}, False),
                ("repo.read_file", {"path": "b.py"}, False),
            ],
            FAILURE_RECOVERY_RECOVERED: [
                ("repo.read_file", {"path": "b.py"}, False),
                ("repo.read_file", {"path": "c.py"}, True),
            ],
        }

        for expected, calls in cases.items():
            with self.subTest(expected=expected):
                metrics = measure_trajectory(_observation(calls=calls))
                self.assertEqual(metrics.failure_recovery, expected)

    def test_a_successful_retry_of_the_same_call_is_not_recovery(self) -> None:
        observation = _observation(
            calls=[
                ("repo.read_file", {"path": "b.py"}, False),
                ("repo.read_file", {"path": "b.py"}, True),
            ]
        )

        self.assertEqual(
            measure_trajectory(observation).failure_recovery,
            FAILURE_RECOVERY_RETRY_LOOP,
        )

    def test_invalid_action_rate_pools_calls_instead_of_averaging_rates(self) -> None:
        noisy = measure_trajectory(
            _observation(
                calls=[
                    ("repo.read_file", {"path": "a.py"}, True),
                    ("repo.read_file", {"path": "a.py"}, True),
                ]
            )
        )
        clean = measure_trajectory(
            _observation(
                calls=[
                    ("repo.read_file", {"path": f"{index}.py"}, True)
                    for index in range(18)
                ]
            )
        )

        pooled = aggregate_invalid_action_rate([noisy, clean])

        self.assertAlmostEqual(pooled, 1 / 20)


class CitationVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.root = Path(self._temp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "orders.py").write_text(
            "class OrderService:\n"
            "    def submit(self, items):\n"
            "        return items\n",
            encoding="utf-8",
        )
        self.addCleanup(self._temp.cleanup)

    def _verify(
        self,
        sources: list[dict[str, Any]],
        answer: str = "",
    ):
        return verify_citations(
            context_sources=sources,
            answer=answer,
            workspace_root=self.root,
        )

    def test_exact_file_slice_is_verified(self) -> None:
        report = self._verify(
            [
                {
                    "kind": "file",
                    "path": "src/orders.py",
                    "start_line": 1,
                    "end_line": 2,
                    "text": "class OrderService:\n    def submit(self, items):\n",
                }
            ]
        )

        self.assertEqual(report.verdicts[0].status, STATUS_VERIFIED)
        self.assertTrue(report.passed)

    def test_stripped_search_match_is_verified(self) -> None:
        report = self._verify(
            [
                {
                    "kind": "search_match",
                    "path": "src/orders.py",
                    "start_line": 2,
                    "end_line": 2,
                    "text": "def submit(self, items):",
                }
            ]
        )

        self.assertEqual(report.verdicts[0].status, STATUS_VERIFIED)

    def test_fabricated_content_is_a_mismatch(self) -> None:
        report = self._verify(
            [
                {
                    "kind": "file",
                    "path": "src/orders.py",
                    "start_line": 1,
                    "end_line": 2,
                    "text": "class OrderService:\n    def cancel(self, order_id):\n",
                }
            ]
        )

        self.assertEqual(report.verdicts[0].status, STATUS_CONTENT_MISMATCH)
        self.assertFalse(report.passed)
        self.assertEqual(report.accuracy, 0.0)

    def test_shifted_line_range_is_a_mismatch(self) -> None:
        report = self._verify(
            [
                {
                    "kind": "file",
                    "path": "src/orders.py",
                    "start_line": 2,
                    "end_line": 2,
                    "text": "class OrderService:",
                }
            ]
        )

        self.assertEqual(report.verdicts[0].status, STATUS_CONTENT_MISMATCH)

    def test_missing_file_and_out_of_range_are_reported(self) -> None:
        report = self._verify(
            [
                {
                    "kind": "file",
                    "path": "src/ghost.py",
                    "start_line": 1,
                    "end_line": 1,
                    "text": "anything",
                },
                {
                    "kind": "file",
                    "path": "src/orders.py",
                    "start_line": 40,
                    "end_line": 41,
                    "text": "anything",
                },
            ]
        )

        self.assertEqual(
            [verdict.status for verdict in report.verdicts],
            [STATUS_MISSING_FILE, STATUS_OUT_OF_RANGE],
        )

    def test_truncated_source_accepts_a_prefix(self) -> None:
        report = self._verify(
            [
                {
                    "kind": "file",
                    "path": "src/orders.py",
                    "start_line": 1,
                    "end_line": 3,
                    "text": "class OrderService:\n    def sub",
                    "truncated": True,
                }
            ]
        )

        self.assertEqual(report.verdicts[0].status, STATUS_VERIFIED)

    def test_non_workspace_kinds_are_reported_but_not_scored(self) -> None:
        report = self._verify(
            [
                {
                    "kind": "knowledge_chunk",
                    "path": "knowledge://kb/doc.md",
                    "start_line": None,
                    "end_line": None,
                    "text": "managed documentation",
                }
            ]
        )

        self.assertEqual(report.verdicts[0].status, STATUS_UNVERIFIABLE)
        self.assertEqual(report.scored_count, 0)
        self.assertIsNone(report.accuracy)
        self.assertTrue(report.passed)

    def test_answer_citing_an_unread_file_is_ungrounded(self) -> None:
        report = self._verify(
            [
                {
                    "kind": "file",
                    "path": "src/orders.py",
                    "start_line": 1,
                    "end_line": 1,
                    "text": "class OrderService:",
                }
            ],
            answer="See src/orders.py:1-1 and also src/billing.py:10-20 for pricing.",
        )

        self.assertEqual(report.ungrounded_paths, ("src/billing.py",))
        self.assertFalse(report.passed)

    def test_a_path_quoted_from_read_evidence_is_grounded(self) -> None:
        report = self._verify(
            [
                {
                    "kind": "file",
                    "path": "src/orders.py",
                    "start_line": 1,
                    "end_line": 1,
                    "text": "class OrderService:",
                },
                {
                    "kind": "file",
                    "path": "README.md",
                    "start_line": 1,
                    "end_line": 1,
                    "text": "Procedures live in docs/runbook.md.",
                },
            ],
            answer="README.md says procedures live in docs/runbook.md.",
        )

        self.assertEqual(report.ungrounded_paths, ())

    def test_a_shortened_filename_is_grounded(self) -> None:
        report = self._verify(
            [
                {
                    "kind": "file",
                    "path": "src/orders.py",
                    "start_line": 1,
                    "end_line": 1,
                    "text": "class OrderService:",
                }
            ],
            answer="The handler lives in orders.py.",
        )

        self.assertEqual(report.ungrounded_paths, ())

    def test_prose_is_not_mistaken_for_a_citation(self) -> None:
        paths = answer_citation_paths(
            "Version 3.11 is required, e.g. for src/orders.py and Makefile."
        )

        self.assertEqual(paths, ("src/orders.py",))


class TrajectorySuiteTests(unittest.TestCase):
    def test_default_trajectory_suite_passes_offline(self) -> None:
        report = run_trajectory_suite(load_trajectory_suite())

        self.assertTrue(report.passed, format_report(report))
        self.assertEqual(report.passed_count, len(report.cases))
        self.assertEqual(report.gate_failures, ())

    def test_suite_measures_every_layer_one_metric(self) -> None:
        report = run_trajectory_suite(load_trajectory_suite())

        self.assertLessEqual(report.invalid_action_rate, 0.05)
        self.assertIsNotNone(report.mean_step_efficiency)
        self.assertEqual(report.citation_accuracy, 1.0)
        self.assertEqual(report.failure_recovery_rate, 1.0)
        # The budget metric is only meaningful if some case actually reaches a
        # budget; a suite where nothing ever caps proves nothing about it.
        self.assertGreater(report.budget_cap_rate, 0.0)

    def test_injected_fault_produces_a_real_failed_call(self) -> None:
        report = run_trajectory_suite(load_trajectory_suite())

        injected = [case for case in report.cases if case.metrics.failed_calls]

        self.assertTrue(injected, format_report(report))
        for case in injected:
            self.assertEqual(
                case.metrics.failure_recovery,
                FAILURE_RECOVERY_RECOVERED,
            )

    def test_reference_trace_is_diagnostic_only(self) -> None:
        report = run_trajectory_suite(load_trajectory_suite())

        for case in report.cases:
            self.assertTrue(case.trace_nodes)
            self.assertNotIn(
                "trace",
                {constraint.name for constraint in case.constraints},
            )


if __name__ == "__main__":
    unittest.main()
