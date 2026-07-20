"""Small, provider-independent retrieval quality metrics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalMetrics:
    evaluated_cases: int
    recall_at_k: float
    mean_reciprocal_rank: float
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
    reciprocal_ranks: list[float] = []
    for ranking, relevant in zip(rankings, relevant_documents):
        if not relevant:
            continue
        top_k = ranking[:k]
        retrieved_relevant = relevant & set(top_k)
        recalls.append(len(retrieved_relevant) / len(relevant))
        reciprocal_ranks.append(
            next(
                (
                    1.0 / rank
                    for rank, document in enumerate(ranking, start=1)
                    if document in relevant
                ),
                0.0,
            )
        )

    evaluated_cases = len(recalls)
    if not evaluated_cases:
        return RetrievalMetrics(
            evaluated_cases=0,
            recall_at_k=0.0,
            mean_reciprocal_rank=0.0,
            k=k,
        )
    return RetrievalMetrics(
        evaluated_cases=evaluated_cases,
        recall_at_k=sum(recalls) / evaluated_cases,
        mean_reciprocal_rank=sum(reciprocal_ranks) / evaluated_cases,
        k=k,
    )
