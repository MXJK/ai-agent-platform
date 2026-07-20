import unittest

from evals.run_evals import (
    CheckResult,
    CaseResult,
    EvalReport,
    format_report,
    load_eval_suite,
    run_eval_suite,
)


class EvalRunnerTests(unittest.TestCase):
    def test_loads_default_eval_suite(self) -> None:
        suite = load_eval_suite()

        self.assertEqual(suite["repository_id"], "repo_main")
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


if __name__ == "__main__":
    unittest.main()
