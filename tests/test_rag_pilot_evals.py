from copy import deepcopy
import unittest

from ai_agent_platform.integrations.rag import evaluate_graded_retrieval
from evals.run_rag_evals import (
    EXPECTED_CATEGORY_QUOTAS,
    format_rag_eval_report,
    load_rag_eval_suite,
    run_rag_eval_suite,
    validate_rag_eval_suite,
    _settings_for_profile,
)


class GradedRetrievalMetricTests(unittest.TestCase):
    def test_uses_graded_ndcg_and_core_document_for_mrr(self) -> None:
        metrics = evaluate_graded_retrieval(
            rankings=[["supporting.md", "core.md", "noise.md"]],
            relevance_judgements=[{"core.md": 3, "supporting.md": 2}],
            k=3,
        )

        self.assertEqual(metrics.evaluated_cases, 1)
        self.assertEqual(metrics.recall_at_k, 1.0)
        self.assertEqual(metrics.precision_at_k, 2 / 3)
        self.assertEqual(metrics.core_mean_reciprocal_rank, 0.5)
        self.assertGreater(metrics.ndcg_at_k, 0.7)
        self.assertLess(metrics.ndcg_at_k, 1.0)
        self.assertEqual(metrics.hit_rate_at_k, 1.0)

    def test_collapses_duplicate_chunks_before_file_level_metrics(self) -> None:
        metrics = evaluate_graded_retrieval(
            rankings=[["same.md", "same.md", "other.md"]],
            relevance_judgements=[{"same.md": 3, "other.md": 2}],
            k=2,
        )

        self.assertEqual(metrics.recall_at_k, 1.0)
        self.assertEqual(metrics.precision_at_k, 1.0)
        self.assertEqual(metrics.core_mean_reciprocal_rank, 1.0)
        self.assertEqual(metrics.ndcg_at_k, 1.0)

    def test_skips_unanswerable_cases_from_positive_quality_metrics(self) -> None:
        metrics = evaluate_graded_retrieval(
            rankings=[["noise.md"], ["answer.md"]],
            relevance_judgements=[{}, {"answer.md": 3}],
            k=1,
        )

        self.assertEqual(metrics.evaluated_cases, 1)
        self.assertEqual(metrics.recall_at_k, 1.0)

    def test_rejects_out_of_range_relevance_grade(self) -> None:
        with self.assertRaisesRegex(ValueError, "0 to 3"):
            evaluate_graded_retrieval(
                rankings=[["answer.md"]],
                relevance_judgements=[{"answer.md": 4}],
                k=1,
            )

    def test_rejects_non_integer_relevance_grade(self) -> None:
        with self.assertRaisesRegex(ValueError, "0 to 3"):
            evaluate_graded_retrieval(
                rankings=[["answer.md"]],
                relevance_judgements=[{"answer.md": 3.0}],
                k=1,
            )


class RAGPilotSuiteTests(unittest.TestCase):
    def test_default_pilot_has_exact_quota_and_valid_annotations(self) -> None:
        suite = load_rag_eval_suite()

        self.assertEqual(len(suite["cases"]), 30)
        self.assertEqual(suite["category_quotas"], EXPECTED_CATEGORY_QUOTAS)
        self.assertTrue(
            all(
                not case["answerable"] or 3 in case["relevance"].values()
                for case in suite["cases"]
            )
        )

    def test_rejects_unknown_fixture_reference(self) -> None:
        suite = deepcopy(load_rag_eval_suite())
        suite["cases"][0]["relevance"] = {"missing.md": 3}

        with self.assertRaisesRegex(ValueError, "unknown fixtures"):
            validate_rag_eval_suite(suite)

    def test_rejects_unanswerable_case_with_invented_relevance(self) -> None:
        suite = deepcopy(load_rag_eval_suite())
        case = next(item for item in suite["cases"] if not item["answerable"])
        case["relevance"] = {suite["fixtures"][0]["filename"]: 1}

        with self.assertRaisesRegex(ValueError, "must not invent"):
            validate_rag_eval_suite(suite)

    def test_rejects_hard_negative_without_forbidden_document(self) -> None:
        suite = deepcopy(load_rag_eval_suite())
        case = next(
            item for item in suite["cases"] if item["category"] == "hard_negative"
        )
        case["must_not_retrieve"] = []

        with self.assertRaisesRegex(ValueError, "requires must_not_retrieve"):
            validate_rag_eval_suite(suite)

    def test_deterministic_runner_reports_all_metric_layers(self) -> None:
        report = run_rag_eval_suite(load_rag_eval_suite())

        self.assertEqual(len(report.results), 30)
        self.assertEqual(set(report.mode_reports), {"dense", "lexical", "hybrid"})
        self.assertTrue(
            all(len(mode_report.results) == 30 for mode_report in report.mode_reports.values())
        )
        self.assertEqual(set(report.metrics_by_k), {1, 3, 5, 10})
        self.assertEqual(report.metrics_by_k[5].evaluated_cases, 27)
        self.assertGreaterEqual(report.latency_p50_ms, 0.0)
        self.assertGreaterEqual(report.latency_p95_ms, report.latency_p50_ms)
        self.assertFalse(report.gates_enforced)
        self.assertEqual(report.gate_failures, ())
        formatted = format_rag_eval_report(report)
        self.assertIn("[Dense-only]", formatted)
        self.assertIn("[Lexical-only]", formatted)
        self.assertIn("[Hybrid (weighted RRF)]", formatted)
        self.assertIn("diagnostic only", formatted)

    def test_bge_m3_profile_is_explicit_and_isolated(self) -> None:
        settings = _settings_for_profile("bge-m3")

        self.assertEqual(settings.embedding_provider, "sentence_transformer")
        self.assertEqual(settings.embedding_model, "BAAI/bge-m3")
        self.assertEqual(settings.sentence_transformer_embedding_device, "cpu")
        self.assertEqual(settings.rag_vector_store, "memory")
        self.assertEqual(settings.rag_reranker_provider, "none")

    def test_bge_m3_rerank_profile_fixes_both_models(self) -> None:
        settings = _settings_for_profile("bge-m3-rerank")

        self.assertEqual(settings.embedding_model, "BAAI/bge-m3")
        self.assertEqual(settings.rag_vector_store, "memory")
        self.assertEqual(settings.rag_reranker_provider, "sentence_transformer")
        self.assertEqual(
            settings.sentence_transformer_reranker_model,
            "BAAI/bge-reranker-base",
        )
        self.assertTrue(settings.rag_rerank_default_enabled)

    def test_hybrid_only_skips_diagnostic_retrieval_modes(self) -> None:
        report = run_rag_eval_suite(load_rag_eval_suite(), hybrid_only=True)

        self.assertEqual(set(report.mode_reports), {"hybrid"})
        self.assertEqual(len(report.results), 30)

    def test_draft_gates_can_be_explicitly_enforced(self) -> None:
        report = run_rag_eval_suite(
            load_rag_eval_suite(),
            enforce_gates=True,
        )

        self.assertTrue(report.gates_enforced)
        self.assertFalse(report.passed)
        self.assertTrue(report.gate_failures)


if __name__ == "__main__":
    unittest.main()
