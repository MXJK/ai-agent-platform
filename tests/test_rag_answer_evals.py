from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from evals.run_rag_answer_evals import (
    GenerationResult,
    _load_retrieved_contexts,
    _replay_generator,
    format_rag_answer_report,
    load_rag_answer_suite,
    run_rag_answer_suite,
    validate_rag_answer_suite,
)
from evals.run_rag_evals import load_rag_eval_suite


def _generation(answer: str, *, model: str = "deepseek-v4-flash") -> GenerationResult:
    return GenerationResult(
        answer=answer,
        provider="deepseek",
        model=model,
        input_tokens=100,
        output_tokens=20,
        thoughts_tokens=0,
        latency_ms=12.5,
    )


class RAGAnswerSuiteTests(unittest.TestCase):
    def test_default_suite_annotates_all_retrieval_cases(self) -> None:
        retrieval = load_rag_eval_suite()
        answers = load_rag_answer_suite()

        validate_rag_answer_suite(answers, retrieval)

        self.assertEqual(len(answers["cases"]), 30)
        self.assertEqual(
            {item["id"] for item in answers["cases"]},
            {item["id"] for item in retrieval["cases"]},
        )

    def test_rejects_fact_source_missing_from_case_context(self) -> None:
        retrieval = load_rag_eval_suite()
        answers = deepcopy(load_rag_answer_suite())
        answers["cases"][0]["required_facts"][0]["sources"] = [
            "billing/invoices.md"
        ]

        with self.assertRaisesRegex(ValueError, "absent from context"):
            validate_rag_answer_suite(answers, retrieval)

    def test_scores_fact_attribution_and_abstention_without_model_judge(self) -> None:
        retrieval = load_rag_eval_suite()
        answers = load_rag_answer_suite()
        responses = {
            "exact_retention_code": "AURORA_RETENTION_47X 要求保留 47 个月。[1]",
            "unanswerable_phone_support": "参考资料未提供客服电话，因此无法确定。",
        }

        report = run_rag_answer_suite(
            retrieval,
            answers,
            provider="deepseek",
            model="deepseek-v4-flash",
            case_ids=set(responses),
            generate=lambda _messages, case_id: _generation(responses[case_id]),
        )

        self.assertTrue(all(item.passed for item in report.cases))
        self.assertEqual(report.metrics["case_pass_rate"], 1.0)
        self.assertEqual(report.metrics["fact_coverage"], 1.0)
        self.assertEqual(report.metrics["fact_attribution_rate"], 1.0)
        self.assertEqual(report.metrics["abstention_accuracy"], 1.0)
        self.assertEqual(report.metrics["route_mismatch_rate"], 0.0)
        self.assertIn("oracle_plus_adversarial", format_rag_answer_report(report))

    def test_fails_matched_fact_when_citation_points_to_stale_source(self) -> None:
        retrieval = load_rag_eval_suite()
        answers = load_rag_answer_suite()

        report = run_rag_answer_suite(
            retrieval,
            answers,
            provider="deepseek",
            model="deepseek-v4-flash",
            case_ids={"conflict_retention"},
            generate=lambda _messages, _case_id: _generation(
                "当前政策是 47 个月[2]；36 个月是已废止的旧政策[1]。"
            ),
        )

        result = report.cases[0]
        self.assertFalse(result.passed)
        self.assertTrue(all(item.matched for item in result.fact_results))
        self.assertFalse(result.fact_results[0].attributed)

    def test_route_mismatch_is_a_quality_failure(self) -> None:
        retrieval = load_rag_eval_suite()
        answers = load_rag_answer_suite()

        report = run_rag_answer_suite(
            retrieval,
            answers,
            provider="deepseek",
            model="deepseek-v4-flash",
            case_ids={"exact_retention_code"},
            generate=lambda _messages, _case_id: _generation(
                "保留 47 个月。[1]", model="deepseek-chat"
            ),
        )

        self.assertFalse(report.cases[0].route_matched)
        self.assertEqual(report.metrics["route_mismatch_rate"], 1.0)
        self.assertTrue(
            any("route_mismatch_rate" in item for item in report.gate_failures)
        )

    def test_replays_saved_answers_without_a_model_call(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(
                json.dumps(
                    {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "cases": [
                            {
                                "case_id": "exact_retention_code",
                                "answer": "保留 47 个月。[1]",
                                "provider": "deepseek",
                                "model": "deepseek-v4-flash",
                                "input_tokens": 10,
                                "output_tokens": 5,
                                "thoughts_tokens": 1,
                                "latency_ms": 20,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            generate = _replay_generator(
                path,
                provider="deepseek",
                model="deepseek-v4-flash",
            )
            result = generate([], "exact_retention_code")

        self.assertEqual(result.answer, "保留 47 个月。[1]")
        self.assertEqual(result.thoughts_tokens, 1)

    def test_loads_hybrid_rankings_as_retrieved_context(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "retrieval.json"
            path.write_text(
                json.dumps(
                    {
                        "profile": "bge-m3-rerank",
                        "mode_reports": {
                            "hybrid": {
                                "results": [
                                    {
                                        "case_id": "exact_retention_code",
                                        "ranking": ["answer.md", "noise.md"],
                                    }
                                ]
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            contexts, evidence_mode = _load_retrieved_contexts(path, limit=1)

        self.assertEqual(contexts["exact_retention_code"], ("answer.md",))
        self.assertEqual(evidence_mode, "retrieved:bge-m3-rerank:hybrid@1")


if __name__ == "__main__":
    unittest.main()
