from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from math import ceil
from pathlib import Path
import re
import sys
from time import perf_counter
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.rag import (
    RetrievedDocument,
    build_rag_prompt_messages,
)
from ai_agent_platform.model_registry import ModelSelection, model_selection_scope
from ai_agent_platform.runtime import ApplicationFactory
from evals.run_rag_evals import (
    DEFAULT_CASES_PATH,
    _fixture_content,
    load_rag_eval_suite,
    validate_rag_eval_suite,
)


DEFAULT_ANSWER_CASES_PATH = Path(__file__).with_name("rag_answer_cases.json")
CITATION_PATTERN = re.compile(r"\[(\d+)]")


@dataclass(frozen=True)
class GenerationResult:
    answer: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int
    latency_ms: float
    error: str = ""


@dataclass(frozen=True)
class FactResult:
    label: str
    matched: bool
    attributed: bool
    source_documents: tuple[str, ...]


@dataclass(frozen=True)
class RAGAnswerCaseResult:
    case_id: str
    category: str
    answerable: bool
    passed: bool
    answer: str
    context_documents: tuple[str, ...]
    fact_results: tuple[FactResult, ...]
    citation_indices: tuple[int, ...]
    citation_valid: bool
    abstained: bool
    route_matched: bool
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int
    latency_ms: float
    error: str = ""


@dataclass(frozen=True)
class RAGAnswerReport:
    dataset_id: str
    retrieval_dataset_id: str
    evidence_mode: str
    provider: str
    model: str
    cases: tuple[RAGAnswerCaseResult, ...]
    metrics: dict[str, float | int]
    gate_failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.gate_failures

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


Generator = Callable[[list[dict[str, str]], str], GenerationResult]


def load_rag_answer_suite(
    path: Path = DEFAULT_ANSWER_CASES_PATH,
) -> dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    return suite


def validate_rag_answer_suite(
    answer_suite: dict[str, Any],
    retrieval_suite: dict[str, Any],
) -> None:
    validate_rag_eval_suite(retrieval_suite)
    if answer_suite.get("schema_version") != 1:
        raise ValueError("rag answer eval schema_version must be 1")
    if answer_suite.get("retrieval_dataset_id") != retrieval_suite.get("dataset_id"):
        raise ValueError("rag answer eval must reference the loaded retrieval dataset")
    for name in ("dataset_id", "evidence_mode"):
        if not str(answer_suite.get(name) or "").strip():
            raise ValueError(f"rag answer eval requires {name}")

    abstention_patterns = answer_suite.get("abstention_patterns")
    if not isinstance(abstention_patterns, list) or not abstention_patterns:
        raise ValueError("rag answer eval requires abstention_patterns")
    _validate_patterns(abstention_patterns, label="abstention_patterns")

    gates = answer_suite.get("quality_gates")
    required_gates = {
        "min_case_pass_rate",
        "min_fact_coverage",
        "min_fact_attribution_rate",
        "min_citation_validity_rate",
        "min_abstention_accuracy",
        "max_route_mismatch_rate",
    }
    if not isinstance(gates, dict) or set(gates) != required_gates:
        raise ValueError(
            "rag answer quality_gates must contain exactly "
            f"{sorted(required_gates)}"
        )
    if any(
        not isinstance(value, (int, float)) or not 0 <= float(value) <= 1
        for value in gates.values()
    ):
        raise ValueError("rag answer quality gates must be numbers between 0 and 1")

    fixture_names = {
        str(fixture["filename"]) for fixture in retrieval_suite["fixtures"]
    }
    retrieval_cases = {
        str(case["id"]): case for case in retrieval_suite["cases"]
    }
    annotations = answer_suite.get("cases")
    if not isinstance(annotations, list):
        raise ValueError("rag answer eval cases must be a list")
    annotation_ids = [str(case.get("id") or "") for case in annotations]
    if set(annotation_ids) != set(retrieval_cases) or len(annotation_ids) != len(
        retrieval_cases
    ):
        raise ValueError("rag answer eval must annotate every retrieval case exactly once")

    for annotation in annotations:
        case_id = str(annotation["id"])
        retrieval_case = retrieval_cases[case_id]
        expected_abstain = bool(annotation.get("expected_abstain", False))
        if expected_abstain != (not bool(retrieval_case["answerable"])):
            raise ValueError(
                f"case {case_id} expected_abstain must match retrieval answerability"
            )
        required_facts = annotation.get("required_facts")
        if not isinstance(required_facts, list):
            raise ValueError(f"case {case_id} required_facts must be a list")
        if retrieval_case["answerable"] and not required_facts:
            raise ValueError(f"answerable case {case_id} requires factual assertions")
        if not retrieval_case["answerable"] and required_facts:
            raise ValueError(f"unanswerable case {case_id} cannot require facts")

        context_documents = _context_document_names(retrieval_case, annotation)
        unknown_context = sorted(set(context_documents) - fixture_names)
        if unknown_context:
            raise ValueError(
                f"case {case_id} references unknown context documents: {unknown_context}"
            )
        for fact in required_facts:
            label = str(fact.get("label") or "").strip()
            patterns = fact.get("patterns")
            sources = fact.get("sources")
            if not label:
                raise ValueError(f"case {case_id} has an unlabeled fact")
            if not isinstance(patterns, list) or not patterns:
                raise ValueError(f"case {case_id} fact {label!r} requires patterns")
            _validate_patterns(patterns, label=f"case {case_id} fact {label!r}")
            if not isinstance(sources, list) or not sources:
                raise ValueError(f"case {case_id} fact {label!r} requires sources")
            unknown_sources = sorted(set(sources) - set(context_documents))
            if unknown_sources:
                raise ValueError(
                    f"case {case_id} fact {label!r} sources are absent from context: "
                    f"{unknown_sources}"
                )


def run_rag_answer_suite(
    retrieval_suite: dict[str, Any],
    answer_suite: dict[str, Any],
    *,
    provider: str,
    model: str,
    generate: Generator,
    case_ids: set[str] | None = None,
    context_overrides: dict[str, tuple[str, ...]] | None = None,
    evidence_mode: str | None = None,
) -> RAGAnswerReport:
    validate_rag_answer_suite(answer_suite, retrieval_suite)
    retrieval_cases = {
        str(case["id"]): case for case in retrieval_suite["cases"]
    }
    fixtures = {
        str(item["filename"]): _fixture_content(item)
        for item in retrieval_suite["fixtures"]
    }
    annotations = [
        item
        for item in answer_suite["cases"]
        if case_ids is None or str(item["id"]) in case_ids
    ]
    if case_ids is not None:
        unknown = sorted(case_ids - {str(item["id"]) for item in annotations})
        if unknown:
            raise ValueError(f"unknown rag answer case ids: {unknown}")
    if not annotations:
        raise ValueError("rag answer eval selected no cases")

    results: list[RAGAnswerCaseResult] = []
    for annotation in annotations:
        case = retrieval_cases[str(annotation["id"])]
        context_documents = (
            context_overrides[str(case["id"])]
            if context_overrides is not None
            else _context_document_names(case, annotation)
        )
        unknown_documents = sorted(set(context_documents) - set(fixtures))
        if unknown_documents:
            raise ValueError(
                f"case {case['id']} retrieved unknown fixtures: {unknown_documents}"
            )
        citations = _build_citations(
            knowledge_base_id=str(retrieval_suite["knowledge_base_id"]),
            context_documents=context_documents,
            fixtures=fixtures,
        )
        messages = build_rag_prompt_messages(
            question=str(case["query"]),
            citations=citations,
        )
        try:
            generated = generate(messages, str(case["id"]))
        except Exception as exc:  # noqa: BLE001 - preserve later case coverage
            generated = GenerationResult(
                answer="",
                provider="",
                model="",
                input_tokens=0,
                output_tokens=0,
                thoughts_tokens=0,
                latency_ms=0.0,
                error=f"{type(exc).__name__}: {exc}",
            )
        results.append(
            _score_case(
                case=case,
                annotation=annotation,
                context_documents=context_documents,
                generated=generated,
                requested_provider=provider,
                requested_model=model,
                abstention_patterns=answer_suite["abstention_patterns"],
            )
        )

    metrics = _aggregate_metrics(results)
    failures = _quality_gate_failures(metrics, answer_suite["quality_gates"])
    return RAGAnswerReport(
        dataset_id=str(answer_suite["dataset_id"]),
        retrieval_dataset_id=str(answer_suite["retrieval_dataset_id"]),
        evidence_mode=evidence_mode or str(answer_suite["evidence_mode"]),
        provider=provider,
        model=model,
        cases=tuple(results),
        metrics=metrics,
        gate_failures=tuple(failures),
    )


def format_rag_answer_report(report: RAGAnswerReport) -> str:
    metrics = report.metrics
    lines = [
        "RAG Answer Quality Eval Report",
        f"Dataset: {report.dataset_id}; retrieval={report.retrieval_dataset_id}",
        f"Evidence: {report.evidence_mode}; cases={len(report.cases)}",
        f"Requested model: {report.provider}/{report.model}",
        (
            "Quality: "
            f"pass={metrics['case_pass_rate']:.3f}; "
            f"fact_coverage={metrics['fact_coverage']:.3f}; "
            f"fact_attribution={metrics['fact_attribution_rate']:.3f}; "
            f"citation_validity={metrics['citation_validity_rate']:.3f}; "
            f"abstention={metrics['abstention_accuracy']:.3f}; "
            f"route_mismatch={metrics['route_mismatch_rate']:.3f}"
        ),
        (
            "Usage: "
            f"input={metrics['input_tokens']}; output={metrics['output_tokens']}; "
            f"thoughts={metrics['thoughts_tokens']}"
        ),
        (
            "Generation latency: "
            f"p50={metrics['latency_p50_ms']:.1f}ms; "
            f"p95={metrics['latency_p95_ms']:.1f}ms"
        ),
    ]
    lines.extend(f"- FAIL quality_gate: {failure}" for failure in report.gate_failures)
    for result in report.cases:
        if result.passed:
            continue
        missed = [item.label for item in result.fact_results if not item.matched]
        unattributed = [
            item.label for item in result.fact_results if item.matched and not item.attributed
        ]
        lines.append(
            f"- DIAG {result.case_id} [{result.category}]: "
            f"missed={missed}; unattributed={unattributed}; "
            f"citation_valid={result.citation_valid}; abstained={result.abstained}; "
            f"route_matched={result.route_matched}; error={result.error or '-'}"
        )
    return "\n".join(lines)


def _context_document_names(
    retrieval_case: dict[str, Any],
    annotation: dict[str, Any],
) -> tuple[str, ...]:
    explicit = annotation.get("context_documents")
    if explicit is not None:
        if not isinstance(explicit, list) or not explicit:
            raise ValueError(
                f"case {annotation.get('id')} context_documents must be a non-empty list"
            )
        return tuple(dict.fromkeys(str(item) for item in explicit))
    ranked_relevance = sorted(
        retrieval_case["relevance"].items(),
        key=lambda item: -int(item[1]),
    )
    names = [str(name) for name, _grade in ranked_relevance]
    names.extend(str(name) for name in retrieval_case["must_not_retrieve"])
    return tuple(dict.fromkeys(names))


def _build_citations(
    *,
    knowledge_base_id: str,
    context_documents: tuple[str, ...],
    fixtures: dict[str, str],
) -> list[RetrievedDocument]:
    return [
        RetrievedDocument(
            id=f"answer-eval-{index}",
            knowledge_base_id=knowledge_base_id,
            document_id=f"answer-eval-{index}",
            filename=filename,
            chunk_index=0,
            text=fixtures[filename],
            score=1.0,
        )
        for index, filename in enumerate(context_documents, start=1)
    ]


def _score_case(
    *,
    case: dict[str, Any],
    annotation: dict[str, Any],
    context_documents: tuple[str, ...],
    generated: GenerationResult,
    requested_provider: str,
    requested_model: str,
    abstention_patterns: list[str],
) -> RAGAnswerCaseResult:
    answer = generated.answer.strip()
    citation_indices = tuple(int(value) for value in CITATION_PATTERN.findall(answer))
    citation_valid = bool(citation_indices) and all(
        1 <= index <= len(context_documents) for index in citation_indices
    )
    fact_results = tuple(
        _score_fact(answer, fact, context_documents)
        for fact in annotation["required_facts"]
    )
    abstained = any(
        re.search(pattern, answer, flags=re.IGNORECASE | re.DOTALL)
        for pattern in abstention_patterns
    )
    route_matched = (
        generated.provider == requested_provider and generated.model == requested_model
    )
    answerable = bool(case["answerable"])
    if answerable:
        passed = bool(
            not generated.error
            and route_matched
            and citation_valid
            and fact_results
            and all(item.matched and item.attributed for item in fact_results)
        )
    else:
        passed = bool(not generated.error and route_matched and abstained)
    return RAGAnswerCaseResult(
        case_id=str(case["id"]),
        category=str(case["category"]),
        answerable=answerable,
        passed=passed,
        answer=answer,
        context_documents=context_documents,
        fact_results=fact_results,
        citation_indices=citation_indices,
        citation_valid=citation_valid if answerable else all(
            1 <= index <= len(context_documents) for index in citation_indices
        ),
        abstained=abstained,
        route_matched=route_matched,
        provider=generated.provider,
        model=generated.model,
        input_tokens=generated.input_tokens,
        output_tokens=generated.output_tokens,
        thoughts_tokens=generated.thoughts_tokens,
        latency_ms=generated.latency_ms,
        error=generated.error,
    )


def _score_fact(
    answer: str,
    fact: dict[str, Any],
    context_documents: tuple[str, ...],
) -> FactResult:
    match = next(
        (
            candidate
            for pattern in fact["patterns"]
            if (
                candidate := re.search(
                    pattern,
                    answer,
                    flags=re.IGNORECASE | re.DOTALL,
                )
            )
            is not None
        ),
        None,
    )
    sources = tuple(str(item) for item in fact["sources"])
    if match is None:
        return FactResult(
            label=str(fact["label"]),
            matched=False,
            attributed=False,
            source_documents=sources,
        )
    source_indices = {
        index
        for index, filename in enumerate(context_documents, start=1)
        if filename in sources
    }
    citation_scope = _fact_citation_scope(answer, match.start(), match.end())
    nearby_citations = {
        int(value) for value in CITATION_PATTERN.findall(citation_scope)
    }
    return FactResult(
        label=str(fact["label"]),
        matched=True,
        attributed=bool(source_indices & nearby_citations),
        source_documents=sources,
    )


def _fact_citation_scope(answer: str, start: int, end: int) -> str:
    """Return the sentence or clause that owns a matched atomic fact."""

    separators = "。！？!?；;\n"
    left = max((answer.rfind(mark, 0, start) for mark in separators), default=-1)
    right_candidates = [
        position
        for mark in separators
        if (position := answer.find(mark, end)) >= 0
    ]
    right = min(right_candidates) if right_candidates else len(answer)
    scope = answer[left + 1 : right]
    if right < len(answer):
        trailing = re.match(r"\s*((?:\[\d+]\s*)+)", answer[right + 1 :])
        if trailing is not None:
            scope += trailing.group(1)
    return scope


def _aggregate_metrics(
    results: list[RAGAnswerCaseResult],
) -> dict[str, float | int]:
    answerable = [item for item in results if item.answerable]
    unanswerable = [item for item in results if not item.answerable]
    facts = [fact for item in answerable for fact in item.fact_results]
    latencies = sorted(item.latency_ms for item in results if not item.error)
    return {
        "case_pass_rate": sum(item.passed for item in results) / len(results),
        "fact_coverage": _rate(sum(item.matched for item in facts), len(facts)),
        "fact_attribution_rate": _rate(
            sum(item.attributed for item in facts), len(facts)
        ),
        "citation_validity_rate": _rate(
            sum(item.citation_valid for item in answerable), len(answerable)
        ),
        "abstention_accuracy": _rate(
            sum(item.abstained for item in unanswerable), len(unanswerable)
        ),
        "route_mismatch_rate": sum(not item.route_matched for item in results)
        / len(results),
        "input_tokens": sum(item.input_tokens for item in results),
        "output_tokens": sum(item.output_tokens for item in results),
        "thoughts_tokens": sum(item.thoughts_tokens for item in results),
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "error_count": sum(bool(item.error) for item in results),
    }


def _quality_gate_failures(
    metrics: dict[str, float | int],
    gates: dict[str, float],
) -> list[str]:
    metric_names = {
        "min_case_pass_rate": "case_pass_rate",
        "min_fact_coverage": "fact_coverage",
        "min_fact_attribution_rate": "fact_attribution_rate",
        "min_citation_validity_rate": "citation_validity_rate",
        "min_abstention_accuracy": "abstention_accuracy",
        "max_route_mismatch_rate": "route_mismatch_rate",
    }
    failures: list[str] = []
    for gate, metric_name in metric_names.items():
        expected = float(gates[gate])
        actual = float(metrics[metric_name])
        upper = gate.startswith("max_")
        breached = actual > expected if upper else actual < expected
        if breached:
            comparator = "<=" if upper else ">="
            failures.append(
                f"{metric_name} expected{comparator}{expected:.3f} actual={actual:.3f}"
            )
    return failures


def _validate_patterns(patterns: list[Any], *, label: str) -> None:
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"{label} must contain non-empty regex strings")
        try:
            re.compile(pattern, flags=re.IGNORECASE | re.DOTALL)
        except re.error as exc:
            raise ValueError(f"{label} contains invalid regex {pattern!r}: {exc}") from exc


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    index = max(0, ceil(len(values) * quantile) - 1)
    return values[index]


def _rate(numerator: int, denominator: int) -> float:
    # A deliberately filtered run should not fail gates for a category it did
    # not execute. The full 30-case run always exercises every denominator.
    return numerator / denominator if denominator else 1.0


def _registered_generator(
    *,
    provider: str,
    model: str,
) -> tuple[Generator, Callable[[], None]]:
    settings = Settings.from_env()
    factory = ApplicationFactory()
    llm_client = factory.create_llm_client(settings)
    secret_store = factory.create_secret_store(settings)
    registry = factory.create_model_registry(
        settings,
        llm_client,
        secret_store=secret_store,
    )
    matches = [
        item
        for item in registry.list_models()
        if item.get("provider") == provider
        and item.get("model") == model
        and item.get("enabled")
    ]
    if not matches:
        registry.close()
        raise ValueError(
            f"enabled model {provider}/{model} is not present in the model registry"
        )
    registered = matches[0]
    model_id = str(registered["id"])

    def generate(messages: list[dict[str, str]], _case_id: str) -> GenerationResult:
        started_at = perf_counter()
        answer_parts: list[str] = []
        actual_provider = ""
        actual_model = ""
        input_tokens = 0
        output_tokens = 0
        thoughts_tokens = 0
        selection = ModelSelection(
            mode="manual",
            preferred_model_id=model_id,
            preferred_provider=provider,
            preferred_model=model,
            fallback_enabled=False,
        )
        with model_selection_scope(selection):
            for event in llm_client.stream_chat(
                messages,
                provider=provider,
                model=model,
            ):
                if event.type == "route":
                    actual_provider = str(event.provider or "")
                    actual_model = str(event.model or "")
                elif event.type == "delta":
                    answer_parts.append(event.text)
                elif event.type == "usage" and event.usage is not None:
                    input_tokens = event.usage.input_tokens
                    output_tokens = event.usage.output_tokens
                    thoughts_tokens = event.usage.thoughts_tokens
        return GenerationResult(
            answer="".join(answer_parts).strip(),
            provider=actual_provider,
            model=actual_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thoughts_tokens=thoughts_tokens,
            latency_ms=round((perf_counter() - started_at) * 1000, 3),
        )

    return generate, registry.close


def _replay_generator(
    path: Path,
    *,
    provider: str,
    model: str,
) -> Generator:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("provider") != provider or payload.get("model") != model:
        raise ValueError(
            "replay report provider/model does not match the requested model"
        )
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("replay report requires cases")
    by_id = {str(item.get("case_id") or ""): item for item in cases}

    def generate(_messages: list[dict[str, str]], case_id: str) -> GenerationResult:
        item = by_id.get(case_id)
        if item is None:
            raise ValueError(f"replay report has no result for case {case_id!r}")
        return GenerationResult(
            answer=str(item.get("answer") or ""),
            provider=str(item.get("provider") or ""),
            model=str(item.get("model") or ""),
            input_tokens=int(item.get("input_tokens") or 0),
            output_tokens=int(item.get("output_tokens") or 0),
            thoughts_tokens=int(item.get("thoughts_tokens") or 0),
            latency_ms=float(item.get("latency_ms") or 0.0),
            error=str(item.get("error") or ""),
        )

    return generate


def _load_retrieved_contexts(
    path: Path,
    *,
    limit: int,
) -> tuple[dict[str, tuple[str, ...]], str]:
    if limit <= 0:
        raise ValueError("retrieval context limit must be positive")
    payload = json.loads(path.read_text(encoding="utf-8"))
    mode_reports = payload.get("mode_reports")
    hybrid = mode_reports.get("hybrid") if isinstance(mode_reports, dict) else None
    results = hybrid.get("results") if isinstance(hybrid, dict) else None
    if not isinstance(results, list) or not results:
        raise ValueError("retrieval report requires Hybrid per-case results")
    contexts: dict[str, tuple[str, ...]] = {}
    for item in results:
        case_id = str(item.get("case_id") or "")
        ranking = item.get("ranking")
        if not case_id or not isinstance(ranking, list):
            raise ValueError("retrieval report contains a malformed case result")
        contexts[case_id] = tuple(str(name) for name in ranking[:limit])
    profile = str(payload.get("profile") or "unknown")
    return contexts, f"retrieved:{profile}:hybrid@{limit}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate RAG answer quality with oracle-plus-adversarial evidence and "
            "an enabled model from the product registry."
        )
    )
    parser.add_argument("--retrieval-cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--answer-cases", type=Path, default=DEFAULT_ANSWER_CASES_PATH)
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run one named case; repeat to select multiple cases.",
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument(
        "--replay",
        type=Path,
        help=(
            "Rescore answers from a prior JSON report without calling the model. "
            "Useful when deterministic annotations change."
        ),
    )
    parser.add_argument(
        "--retrieval-report",
        type=Path,
        help="Use Hybrid rankings from a run_rag_evals.py JSON report as evidence.",
    )
    parser.add_argument(
        "--retrieval-limit",
        type=int,
        default=5,
        help="Number of ranked documents to inject from --retrieval-report.",
    )
    parser.add_argument(
        "--diagnostic-only",
        action="store_true",
        help="Always exit zero after a completed run even when quality gates fail.",
    )
    args = parser.parse_args(argv)
    close = lambda: None
    try:
        retrieval_suite = load_rag_eval_suite(args.retrieval_cases)
        answer_suite = load_rag_answer_suite(args.answer_cases)
        validate_rag_answer_suite(answer_suite, retrieval_suite)
        if args.replay is not None:
            generate = _replay_generator(
                args.replay,
                provider=args.provider,
                model=args.model,
            )
        else:
            generate, close = _registered_generator(
                provider=args.provider,
                model=args.model,
            )
        context_overrides = None
        evidence_mode = None
        if args.retrieval_report is not None:
            context_overrides, evidence_mode = _load_retrieved_contexts(
                args.retrieval_report,
                limit=args.retrieval_limit,
            )
        report = run_rag_answer_suite(
            retrieval_suite,
            answer_suite,
            provider=args.provider,
            model=args.model,
            generate=generate,
            case_ids=set(args.case_id) or None,
            context_overrides=context_overrides,
            evidence_mode=evidence_mode,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"RAG Answer Eval: ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        close()
    print(format_rag_answer_report(report))
    if args.json_output is not None:
        args.json_output.write_text(
            json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0 if args.diagnostic_only or report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
