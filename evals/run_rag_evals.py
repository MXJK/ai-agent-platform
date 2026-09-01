from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, replace
import json
from math import ceil
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.rag import (
    GradedRetrievalMetrics,
    RAGError,
    create_rag_service,
    evaluate_graded_retrieval,
)


DEFAULT_CASES_PATH = Path(__file__).with_name("rag_cases.json")
EXPECTED_CATEGORY_QUOTAS = {
    "exact_keyword": 5,
    "paraphrase_colloquial": 5,
    "multi_document": 5,
    "long_document_multi_chunk": 5,
    "hard_negative": 4,
    "unanswerable": 3,
    "conflicting_sources": 3,
}


@dataclass(frozen=True)
class RAGPilotCaseResult:
    case_id: str
    category: str
    ranking: tuple[str, ...]
    relevance: dict[str, int]
    must_not_retrieve: tuple[str, ...]
    answerable: bool
    duration_ms: float


@dataclass(frozen=True)
class RAGPilotReport:
    dataset_id: str
    snapshot_id: str
    profile: str
    profile_settings: dict[str, object]
    results: tuple[RAGPilotCaseResult, ...]
    metrics_by_k: dict[int, GradedRetrievalMetrics]
    hard_negative_violation_rate: float
    unanswerable_nonempty_rate: float
    conflict_preferred_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    gate_k: int
    gates_enforced: bool
    gate_failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.gate_failures


def load_rag_eval_suite(path: Path = DEFAULT_CASES_PATH) -> dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    validate_rag_eval_suite(suite)
    return suite


def validate_rag_eval_suite(suite: dict[str, Any]) -> None:
    if suite.get("schema_version") != 1:
        raise ValueError("rag eval schema_version must be 1")
    if suite.get("split") != "pilot":
        raise ValueError("the 30-case seed set must use split='pilot'")
    for name in ("dataset_id", "snapshot_id", "knowledge_base_id"):
        if not str(suite.get(name) or "").strip():
            raise ValueError(f"rag eval suite requires {name}")

    quotas = suite.get("category_quotas")
    if quotas != EXPECTED_CATEGORY_QUOTAS:
        raise ValueError(
            "category_quotas must match the 30-case pilot allocation "
            f"{EXPECTED_CATEGORY_QUOTAS}"
        )
    k_values = suite.get("k_values")
    if (
        not isinstance(k_values, list)
        or not k_values
        or any(not isinstance(value, int) or value <= 0 for value in k_values)
        or k_values != sorted(set(k_values))
    ):
        raise ValueError("k_values must be unique positive integers in ascending order")

    fixtures = suite.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("rag eval suite requires fixtures")
    fixture_names = [str(item.get("filename") or "") for item in fixtures]
    if any(not name for name in fixture_names):
        raise ValueError("every fixture requires a filename")
    if len(fixture_names) != len(set(fixture_names)):
        raise ValueError("fixture filenames must be unique")
    for fixture in fixtures:
        if not str(fixture.get("content") or "").strip():
            raise ValueError(f"fixture {fixture['filename']} requires content")
        padding_lines = fixture.get("padding_lines", 0)
        if not isinstance(padding_lines, int) or padding_lines < 0:
            raise ValueError(f"fixture {fixture['filename']} has invalid padding_lines")

    cases = suite.get("cases")
    if not isinstance(cases, list) or len(cases) != 30:
        raise ValueError("rag pilot suite must contain exactly 30 cases")
    case_ids = [str(case.get("id") or "") for case in cases]
    if any(not case_id for case_id in case_ids):
        raise ValueError("every rag eval case requires an id")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("rag eval case ids must be unique")
    category_counts = Counter(str(case.get("category") or "") for case in cases)
    if dict(category_counts) != EXPECTED_CATEGORY_QUOTAS:
        raise ValueError(
            "case categories do not match category_quotas: "
            f"actual={dict(category_counts)}"
        )

    fixture_set = set(fixture_names)
    for case in cases:
        case_id = str(case["id"])
        if not str(case.get("query") or "").strip():
            raise ValueError(f"case {case_id} requires a query")
        if not isinstance(case.get("answerable"), bool):
            raise ValueError(f"case {case_id} requires boolean answerable")
        if not str(case.get("notes") or "").strip():
            raise ValueError(f"case {case_id} requires annotation notes")
        relevance = case.get("relevance")
        must_not = case.get("must_not_retrieve")
        if not isinstance(relevance, dict):
            raise ValueError(f"case {case_id} relevance must be an object")
        if not isinstance(must_not, list) or any(
            not isinstance(item, str) for item in must_not
        ):
            raise ValueError(f"case {case_id} must_not_retrieve must be a string list")
        if len(must_not) != len(set(must_not)):
            raise ValueError(f"case {case_id} has duplicate must_not_retrieve entries")
        referenced = set(relevance) | set(must_not)
        unknown = sorted(referenced - fixture_set)
        if unknown:
            raise ValueError(f"case {case_id} references unknown fixtures: {unknown}")
        if any(type(grade) is not int or grade not in range(4) for grade in relevance.values()):
            raise ValueError(f"case {case_id} relevance grades must be integers 0..3")
        if any(relevance.get(filename, 0) >= 2 for filename in must_not):
            raise ValueError(f"case {case_id} marks a relevant document as must-not-retrieve")

        if case["answerable"]:
            if 3 not in relevance.values():
                raise ValueError(f"answerable case {case_id} requires a grade-3 core document")
        elif relevance:
            raise ValueError(f"unanswerable case {case_id} must not invent relevant documents")
        if case["category"] == "hard_negative" and not must_not:
            raise ValueError(f"hard-negative case {case_id} requires must_not_retrieve")
        if case["category"] == "conflicting_sources":
            grades = list(relevance.values())
            if len(grades) < 2 or max(grades) != 3 or min(grades) >= 2:
                raise ValueError(
                    f"conflicting case {case_id} requires a grade-3 current source "
                    "and a lower-grade stale source"
                )

    gates = suite.get("draft_quality_gates")
    if not isinstance(gates, dict) or gates.get("k") not in k_values:
        raise ValueError("draft_quality_gates.k must be one of k_values")


def run_rag_eval_suite(
    suite: dict[str, Any],
    *,
    profile: str = "deterministic",
    enforce_gates: bool = False,
) -> RAGPilotReport:
    validate_rag_eval_suite(suite)
    settings = _settings_for_profile(profile)
    service = create_rag_service(settings)
    knowledge_base_id = str(suite["knowledge_base_id"])
    for fixture in suite["fixtures"]:
        service.ingest_document(
            knowledge_base_id=knowledge_base_id,
            filename=str(fixture["filename"]),
            content=_fixture_content(fixture),
        )

    k_values = [int(value) for value in suite["k_values"]]
    max_k = max(k_values)
    results: list[RAGPilotCaseResult] = []
    for case in suite["cases"]:
        started_at = perf_counter()
        search = service.search_with_metadata(
            knowledge_base_id=knowledge_base_id,
            query=str(case["query"]),
            limit=max_k,
        )
        duration_ms = round((perf_counter() - started_at) * 1000, 3)
        results.append(
            RAGPilotCaseResult(
                case_id=str(case["id"]),
                category=str(case["category"]),
                ranking=tuple(_deduplicate(item.filename for item in search.results)),
                relevance={str(name): int(grade) for name, grade in case["relevance"].items()},
                must_not_retrieve=tuple(str(item) for item in case["must_not_retrieve"]),
                answerable=bool(case["answerable"]),
                duration_ms=duration_ms,
            )
        )

    positive_results = [result for result in results if result.answerable]
    metrics_by_k = {
        k: evaluate_graded_retrieval(
            rankings=[list(result.ranking) for result in positive_results],
            relevance_judgements=[result.relevance for result in positive_results],
            k=k,
        )
        for k in k_values
    }
    gates = dict(suite["draft_quality_gates"])
    gate_k = int(gates["k"])
    hard_negative_violation_rate = _hard_negative_violation_rate(results, k=gate_k)
    unanswerable_nonempty_rate = _unanswerable_nonempty_rate(results, k=gate_k)
    conflict_preferred_rate = _conflict_preferred_rate(results, k=gate_k)
    durations = sorted(result.duration_ms for result in results)
    all_gate_failures = _quality_gate_failures(
        metrics_by_k[gate_k],
        hard_negative_violation_rate=hard_negative_violation_rate,
        conflict_preferred_rate=conflict_preferred_rate,
        gates=gates,
    )
    return RAGPilotReport(
        dataset_id=str(suite["dataset_id"]),
        snapshot_id=str(suite["snapshot_id"]),
        profile=profile,
        profile_settings=_profile_settings(settings),
        results=tuple(results),
        metrics_by_k=metrics_by_k,
        hard_negative_violation_rate=hard_negative_violation_rate,
        unanswerable_nonempty_rate=unanswerable_nonempty_rate,
        conflict_preferred_rate=conflict_preferred_rate,
        latency_p50_ms=_percentile(durations, 0.50),
        latency_p95_ms=_percentile(durations, 0.95),
        gate_k=gate_k,
        gates_enforced=enforce_gates,
        gate_failures=tuple(all_gate_failures if enforce_gates else ()),
    )


def format_rag_eval_report(report: RAGPilotReport) -> str:
    lines = [
        "RAG Pilot Eval Report",
        f"Dataset: {report.dataset_id}; snapshot={report.snapshot_id}",
        f"Profile: {report.profile}; cases={len(report.results)}",
        "Settings: "
        + "; ".join(f"{name}={value}" for name, value in report.profile_settings.items()),
    ]
    for k, metrics in report.metrics_by_k.items():
        lines.append(
            f"K={k}: Recall={metrics.recall_at_k:.3f}; "
            f"Precision={metrics.precision_at_k:.3f}; "
            f"CoreMRR={metrics.core_mean_reciprocal_rank:.3f}; "
            f"NDCG={metrics.ndcg_at_k:.3f}; "
            f"HitRate={metrics.hit_rate_at_k:.3f}; "
            f"positive_cases={metrics.evaluated_cases}"
        )
    lines.extend(
        [
            f"Hard-negative violation@{report.gate_k}: "
            f"{report.hard_negative_violation_rate:.3f}",
            f"Unanswerable non-empty@{report.gate_k}: "
            f"{report.unanswerable_nonempty_rate:.3f} (diagnostic; no abstain contract)",
            f"Conflict preferred@{report.gate_k}: {report.conflict_preferred_rate:.3f}",
            f"Search latency: p50={report.latency_p50_ms:.3f}ms; "
            f"p95={report.latency_p95_ms:.3f}ms",
            (
                "Draft quality gates: enforced"
                if report.gates_enforced
                else "Draft quality gates: diagnostic only (use --enforce-gates)"
            ),
        ]
    )
    lines.extend(f"- FAIL quality_gate: {failure}" for failure in report.gate_failures)
    for result in report.results:
        relevant = {name for name, grade in result.relevance.items() if grade >= 2}
        hits = relevant & set(result.ranking[: report.gate_k])
        forbidden = set(result.must_not_retrieve) & set(
            result.ranking[: report.gate_k]
        )
        if result.answerable and (not hits or forbidden):
            lines.append(
                f"- DIAG {result.case_id} [{result.category}]: hits={sorted(hits)}; "
                f"forbidden={sorted(forbidden)}; top={list(result.ranking[:report.gate_k])}"
            )
    return "\n".join(lines)


def _settings_for_profile(profile: str) -> Settings:
    if profile == "deterministic":
        return Settings(
            llm_provider="fake",
            embedding_provider="local",
            embedding_model="local-hashing",
            local_embedding_dimensions=128,
            rag_vector_store="memory",
            rag_chunk_size=800,
            rag_chunk_overlap=120,
            rag_recall_limit=20,
            rag_lexical_weight=0.35,
            rag_rrf_k=60,
            rag_reranker_provider="none",
            rag_rerank_default_enabled=False,
            rag_max_prompt_chars=6000,
        )
    if profile == "current":
        return replace(Settings.from_env(), rag_vector_store="memory")
    raise ValueError(f"unknown RAG eval profile: {profile}")


def _profile_settings(settings: Settings) -> dict[str, object]:
    return {
        "vector_store": settings.rag_vector_store,
        "embedding": f"{settings.embedding_provider}/{settings.embedding_model}",
        "dimensions": settings.local_embedding_dimensions,
        "chunk": f"{settings.rag_chunk_size}/{settings.rag_chunk_overlap}",
        "recall_limit": settings.rag_recall_limit,
        "lexical_weight": settings.rag_lexical_weight,
        "rrf_k": settings.rag_rrf_k,
        "reranker": settings.rag_reranker_provider,
        "rerank_default": settings.rag_rerank_default_enabled,
    }


def _fixture_content(fixture: dict[str, Any]) -> str:
    padding = str(fixture.get("padding") or "") * int(fixture.get("padding_lines") or 0)
    return "\n".join(
        part.strip()
        for part in (str(fixture["content"]), padding, str(fixture.get("tail") or ""))
        if part.strip()
    )


def _deduplicate(values) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def _hard_negative_violation_rate(
    results: list[RAGPilotCaseResult], *, k: int
) -> float:
    cases = [result for result in results if result.category == "hard_negative"]
    violations = sum(
        bool(set(result.ranking[:k]) & set(result.must_not_retrieve))
        for result in cases
    )
    return violations / len(cases) if cases else 0.0


def _unanswerable_nonempty_rate(
    results: list[RAGPilotCaseResult], *, k: int
) -> float:
    cases = [result for result in results if not result.answerable]
    nonempty = sum(bool(result.ranking[:k]) for result in cases)
    return nonempty / len(cases) if cases else 0.0


def _conflict_preferred_rate(
    results: list[RAGPilotCaseResult], *, k: int
) -> float:
    cases = [result for result in results if result.category == "conflicting_sources"]
    preferred = 0
    for result in cases:
        judged_ranking = [
            name for name in result.ranking[:k] if name in result.relevance
        ]
        if judged_ranking and result.relevance[judged_ranking[0]] == 3:
            preferred += 1
    return preferred / len(cases) if cases else 0.0


def _quality_gate_failures(
    metrics: GradedRetrievalMetrics,
    *,
    hard_negative_violation_rate: float,
    conflict_preferred_rate: float,
    gates: dict[str, Any],
) -> list[str]:
    checks = {
        "min_recall_at_k": metrics.recall_at_k,
        "min_core_mrr": metrics.core_mean_reciprocal_rank,
        "min_ndcg_at_k": metrics.ndcg_at_k,
        "min_hit_rate_at_k": metrics.hit_rate_at_k,
        "min_conflict_preferred_rate": conflict_preferred_rate,
    }
    failures = [
        f"{name} expected>={float(gates[name]):.3f} actual={actual:.3f}"
        for name, actual in checks.items()
        if name in gates and actual < float(gates[name])
    ]
    maximum = gates.get("max_hard_negative_violation_rate")
    if maximum is not None and hard_negative_violation_rate > float(maximum):
        failures.append(
            "max_hard_negative_violation_rate "
            f"expected<={float(maximum):.3f} actual={hard_negative_violation_rate:.3f}"
        )
    return failures


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    index = max(0, ceil(len(values) * quantile) - 1)
    return values[index]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the isolated 30-case graded RAG pilot evaluation."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument(
        "--profile",
        choices=("deterministic", "current"),
        default="deterministic",
        help="Use fixed local settings or current retrieval settings with an isolated memory index.",
    )
    parser.add_argument(
        "--enforce-gates",
        action="store_true",
        help="Fail when draft quality gates are not met.",
    )
    args = parser.parse_args(argv)
    try:
        report = run_rag_eval_suite(
            load_rag_eval_suite(args.cases),
            profile=args.profile,
            enforce_gates=args.enforce_gates,
        )
    except (OSError, ValueError, json.JSONDecodeError, RAGError) as exc:
        print(f"RAG Pilot Eval: ERROR: {exc}", file=sys.stderr)
        return 2
    print(format_rag_eval_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
