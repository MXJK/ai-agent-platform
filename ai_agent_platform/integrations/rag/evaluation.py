"""Small, provider-independent retrieval quality metrics."""

from __future__ import annotations

from dataclasses import dataclass
from math import log2


@dataclass(frozen=True)
class RetrievalMetrics:
    evaluated_cases: int
    recall_at_k: float
    precision_at_k: float
    mean_reciprocal_rank: float
    ndcg_at_k: float
    hit_rate_at_k: float
    k: int


@dataclass(frozen=True)
class GradedRetrievalMetrics:
    """File-level retrieval metrics for 0–3 relevance judgements."""

    evaluated_cases: int
    recall_at_k: float
    precision_at_k: float
    core_mean_reciprocal_rank: float
    ndcg_at_k: float
    hit_rate_at_k: float
    k: int


def evaluate_retrieval(
    *,
    rankings: list[list[str]],
    relevant_documents: list[set[str]],
    k: int = 5,
) -> RetrievalMetrics:
    """Compute macro Recall@k and MRR for document-id or filename rankings."""

    if k <= 0:
        raise ValueError("k must be positive")
    if len(rankings) != len(relevant_documents):
        raise ValueError("rankings and relevant_documents must have equal lengths")

    recalls: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    hits: list[float] = []
    for ranking, relevant in zip(rankings, relevant_documents):
        if not relevant:
            continue
        top_k = ranking[:k]
        retrieved_relevant = relevant & set(top_k)
        recalls.append(len(retrieved_relevant) / len(relevant))
        precisions.append(len(retrieved_relevant) / k)
        hits.append(1.0 if retrieved_relevant else 0.0)
        reciprocal_ranks.append(
            next(
                (
                    1.0 / rank
                    for rank, document in enumerate(top_k, start=1)
                    if document in relevant
                ),
                0.0,
            )
        )
        discounted_gain = sum(
            1.0 / log2(rank + 1)
            for rank, document in enumerate(top_k, start=1)
            if document in relevant
        )
        ideal_gain = sum(
            1.0 / log2(rank + 1)
            for rank in range(1, min(len(relevant), k) + 1)
        )
        ndcgs.append(discounted_gain / ideal_gain if ideal_gain else 0.0)

    evaluated_cases = len(recalls)
    if not evaluated_cases:
        return RetrievalMetrics(
            evaluated_cases=0,
            recall_at_k=0.0,
            precision_at_k=0.0,
            mean_reciprocal_rank=0.0,
            ndcg_at_k=0.0,
            hit_rate_at_k=0.0,
            k=k,
        )
    return RetrievalMetrics(
        evaluated_cases=evaluated_cases,
        recall_at_k=sum(recalls) / evaluated_cases,
        precision_at_k=sum(precisions) / evaluated_cases,
        mean_reciprocal_rank=sum(reciprocal_ranks) / evaluated_cases,
        ndcg_at_k=sum(ndcgs) / evaluated_cases,
        hit_rate_at_k=sum(hits) / evaluated_cases,
        k=k,
    )


def evaluate_graded_retrieval(
    *,
    rankings: list[list[str]],
    relevance_judgements: list[dict[str, int]],
    k: int = 5,
    relevant_grade: int = 2,
    core_grade: int = 3,
) -> GradedRetrievalMetrics:
    """Compute macro file-level metrics with duplicate chunks collapsed.

    Recall, precision and hit rate treat grades at or above ``relevant_grade``
    as relevant. Reciprocal rank uses the first core document. NDCG retains the
    full 0–3 judgement and uses exponential gain.
    """

    if k <= 0:
        raise ValueError("k must be positive")
    if not 1 <= relevant_grade <= core_grade <= 3:
        raise ValueError("grades must satisfy 1 <= relevant <= core <= 3")
    if len(rankings) != len(relevance_judgements):
        raise ValueError("rankings and relevance_judgements must have equal lengths")

    recalls: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    hits: list[float] = []
    for raw_ranking, judgements in zip(rankings, relevance_judgements):
        invalid_grades = [
            grade
            for grade in judgements.values()
            if type(grade) is not int or grade not in range(4)
        ]
        if invalid_grades:
            raise ValueError("relevance grades must be integers from 0 to 3")
        relevant = {
            document for document, grade in judgements.items() if grade >= relevant_grade
        }
        if not relevant:
            continue
        ranking = _deduplicate(raw_ranking)[:k]
        retrieved_relevant = relevant & set(ranking)
        recalls.append(len(retrieved_relevant) / len(relevant))
        precisions.append(len(retrieved_relevant) / k)
        hits.append(1.0 if retrieved_relevant else 0.0)

        core_documents = {
            document for document, grade in judgements.items() if grade >= core_grade
        }
        reciprocal_ranks.append(
            next(
                (
                    1.0 / rank
                    for rank, document in enumerate(ranking, start=1)
                    if document in core_documents
                ),
                0.0,
            )
        )

        discounted_gain = sum(
            ((2**judgements.get(document, 0)) - 1) / log2(rank + 1)
            for rank, document in enumerate(ranking, start=1)
            if judgements.get(document, 0) > 0
        )
        ideal_grades = sorted(judgements.values(), reverse=True)[:k]
        ideal_gain = sum(
            ((2**grade) - 1) / log2(rank + 1)
            for rank, grade in enumerate(ideal_grades, start=1)
            if grade > 0
        )
        ndcgs.append(discounted_gain / ideal_gain if ideal_gain else 0.0)

    evaluated_cases = len(recalls)
    if not evaluated_cases:
        return GradedRetrievalMetrics(
            evaluated_cases=0,
            recall_at_k=0.0,
            precision_at_k=0.0,
            core_mean_reciprocal_rank=0.0,
            ndcg_at_k=0.0,
            hit_rate_at_k=0.0,
            k=k,
        )
    return GradedRetrievalMetrics(
        evaluated_cases=evaluated_cases,
        recall_at_k=sum(recalls) / evaluated_cases,
        precision_at_k=sum(precisions) / evaluated_cases,
        core_mean_reciprocal_rank=sum(reciprocal_ranks) / evaluated_cases,
        ndcg_at_k=sum(ndcgs) / evaluated_cases,
        hit_rate_at_k=sum(hits) / evaluated_cases,
        k=k,
    )


def _deduplicate(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
