"""Side-effect-free primitives for fitting typed context items to a budget."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar


ContextItem = TypeVar("ContextItem")

MIN_TRUNCATED_ITEM_TOKENS = 32
TRUNCATION_MARKER = "\n...[truncated to fit the context budget]...\n"


class ContextBudgetPolicy(Protocol[ContextItem]):
    """Adapt a caller-owned context item without changing its native shape."""

    def cost(self, item: ContextItem) -> int:
        """Return the item's contribution, excluding shared context overhead."""

    def truncate(
        self,
        item: ContextItem,
        *,
        overflow_tokens: int,
        minimum_tokens: int,
    ) -> ContextItem:
        """Return a fitted copy, or the original item when it cannot shrink."""

    def is_protected(
        self,
        item: ContextItem,
        *,
        index: int,
        items: Sequence[ContextItem],
    ) -> bool:
        """Return whether this item must not be dropped from ``items``."""


@dataclass(frozen=True)
class ContextReduction(Generic[ContextItem]):
    """A reduced context and the operations used to produce it."""

    items: list[ContextItem]
    dropped: int = 0
    truncated: int = 0
    compacted: int = 0
    evicted: int = 0


def fit_context_to_budget(
    items: Sequence[ContextItem],
    budget: int,
    *,
    policy: ContextBudgetPolicy[ContextItem],
    overhead_tokens: int = 0,
    minimum_truncated_tokens: int = MIN_TRUNCATED_ITEM_TOKENS,
) -> ContextReduction[ContextItem]:
    """Drop oldest unprotected items, then truncate oldest-first.

    The input sequence and its items are never mutated. The policy decides how
    each native item is costed, copied for truncation, and protected, so callers
    can preserve metadata such as tool-call IDs and error flags.
    """

    fitted = list(items)
    costs = [policy.cost(item) for item in fitted]
    total = sum(costs) + overhead_tokens
    dropped = 0
    truncated = 0

    while total > budget:
        drop_index = next(
            (
                index
                for index, item in enumerate(fitted)
                if not policy.is_protected(
                    item,
                    index=index,
                    items=fitted,
                )
            ),
            None,
        )
        if drop_index is None:
            break
        fitted.pop(drop_index)
        total -= costs.pop(drop_index)
        dropped += 1

    for index, item in enumerate(fitted):
        overflow = total - budget
        if overflow <= 0:
            break
        replacement = policy.truncate(
            item,
            overflow_tokens=overflow,
            minimum_tokens=minimum_truncated_tokens,
        )
        if replacement is item:
            continue
        fitted[index] = replacement
        replacement_cost = policy.cost(replacement)
        total -= costs[index] - replacement_cost
        costs[index] = replacement_cost
        truncated += 1

    return ContextReduction(
        items=fitted,
        dropped=dropped,
        truncated=truncated,
    )


def fit_text_to_tokens(
    text: str,
    max_tokens: int,
    *,
    estimate_tokens: Callable[[str], int],
    marker: str = TRUNCATION_MARKER,
) -> str:
    """Keep the head and tail of ``text`` within an estimated token budget."""

    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    low, high = 0, len(text)
    best = marker.strip()
    while low <= high:
        keep = (low + high) // 2
        candidate = head_tail(text, keep, marker=marker)
        if estimate_tokens(candidate) <= max_tokens:
            best = candidate
            low = keep + 1
        else:
            high = keep - 1
    return best


def head_tail(
    text: str,
    keep: int,
    *,
    marker: str = TRUNCATION_MARKER,
) -> str:
    """Keep two-thirds of available characters at the head and the rest at tail."""

    if keep <= 0:
        return marker.strip()
    head = (keep * 2) // 3
    tail = keep - head
    return text[:head] + marker + (text[-tail:] if tail else "")
