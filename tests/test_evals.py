import unittest

from evals.run_evals import (
    CheckResult,
    CaseResult,
    EvalReport,
    format_report,
    load_eval_suite,
    run_eval_suite,
    _retrieval_quality_failures,
)
from ai_agent_platform.integrations.rag import RetrievalMetrics


class EvalRunnerTests(unittest.TestCase):
    def test_loads_default_eval_suite(self) -> None:
        suite = load_eval_suite()

        self.assertEqual(suite["workspace_id"], "workspace_main")
        self.assertGreaterEqual(len(suite["fixtures"]), 1)
        self.assertGreaterEqual(len(suite["cases"]), 1)
        self.assertTrue(all("id" in item for item in suite["cases"]))

    def test_formats_report_with_pass_rate(self) -> None:
        report = EvalReport(
            results=[
                CaseResult(
                    case_id="case_1",
                    case_type="agent",
                    checks=[CheckResult(name="intent", passed=True, detail="ok")],
                )
            ]
        )

        text = format_report(report)

        self.assertIn("Passed: 1/1 (100%)", text)
        self.assertIn("PASS case_1", text)

    def test_default_eval_suite_passes_offline(self) -> None:
        report = run_eval_suite(load_eval_suite())

        self.assertTrue(report.passed)
        self.assertEqual(report.passed_count, report.total_count)
        self.assertIsNotNone(report.retrieval_metrics)
        self.assertGreater(report.retrieval_metrics.recall_at_k, 0.0)
        self.assertEqual(report.retrieval_metrics.evaluated_cases, 4)
        self.assertEqual(report.quality_failures, ())

    def test_quality_gate_reports_metric_regression(self) -> None:
        metrics = RetrievalMetrics(
            evaluated_cases=2,
            recall_at_k=0.5,
            precision_at_k=0.2,
            mean_reciprocal_rank=0.4,
            ndcg_at_k=0.45,
            hit_rate_at_k=0.5,
            k=5,
        )

        failures = _retrieval_quality_failures(
            metrics,
            {"min_recall_at_k": 0.8, "min_mrr": 0.7},
        )

        self.assertEqual(len(failures), 2)
        self.assertIn("min_recall_at_k", failures[0])


if __name__ == "__main__":
    unittest.main()
