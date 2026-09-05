"""Side-effect-free primitives for fitting typed context items to a budget."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
from typing import Generic, Protocol, TypeVar


ContextItem = TypeVar("ContextItem")

MIN_TRUNCATED_ITEM_TOKENS = 32
TRUNCATION_MARKER = "\n...[truncated to fit the context budget]...\n"


@dataclass(frozen=True)
class ContextShares:
    """One resolved model-input allowance divided into named token shares."""

    total_tokens: int
    system_tokens: int
    tool_schema_tokens: int
    evidence_tokens: int
    history_tokens: int
    transcript_tokens: int

    @property
    def fixed_overhead_tokens(self) -> int:
        """Return the non-negotiable prompt and tool-schema overhead."""

        return self.system_tokens + self.tool_schema_tokens

    @property
    def message_tokens(self) -> int:
        """Return the allowance for messages measured by the transcript ladder."""

        return self.total_tokens - self.tool_schema_tokens

    @property
    def fits(self) -> bool:
        """Return whether fixed overhead and optional shares leave run capacity."""

        return self.transcript_tokens > 0

    def as_dict(self) -> dict[str, int]:
        """Serialize context shares for persisted diagnostics."""

        return {
            "total_tokens": self.total_tokens,
            "system_tokens": self.system_tokens,
            "tool_schema_tokens": self.tool_schema_tokens,
            "evidence_tokens": self.evidence_tokens,
            "history_tokens": self.history_tokens,
            "transcript_tokens": self.transcript_tokens,
            "message_tokens": self.message_tokens,
        }


def divide_context_budget(
    input_tokens: int,
    *,
    system_tokens: int = 0,
    tool_schema_tokens: int = 0,
    evidence_ratio: float,
    history_ratio: float,
) -> ContextShares:
    """Divide one resolved input allowance without re-deriving model ratios.

    Fixed system and tool-schema overhead is allocated first. Evidence and
    history receive configured shares of what remains, and the native tool
    transcript receives the exact remainder. Clamping fixed overhead in order
    keeps every named component explicit and guarantees that shares add back up
    to ``input_tokens`` even for a model window too small to start a run.
    """

    if not 0.0 <= evidence_ratio < 1.0:
        raise ValueError("evidence_ratio must be within [0, 1)")
    if not 0.0 <= history_ratio < 1.0:
        raise ValueError("history_ratio must be within [0, 1)")
    if evidence_ratio + history_ratio >= 1.0:
        raise ValueError(
            "evidence_ratio and history_ratio must leave room for the transcript"
        )

    total = max(0, int(input_tokens))
    system = min(total, max(0, int(system_tokens)))
    after_system = total - system
    schemas = min(after_system, max(0, int(tool_schema_tokens)))
    divisible = after_system - schemas
    evidence = int(divisible * evidence_ratio)
    history = int(divisible * history_ratio)
    return ContextShares(
        total_tokens=total,
        system_tokens=system,
        tool_schema_tokens=schemas,
        evidence_tokens=evidence,
        history_tokens=history,
        transcript_tokens=divisible - evidence - history,
    )


def estimate_tool_schema_tokens(
    tool_specs: Sequence[object],
    *,
    estimate_tokens: Callable[[str], int],
) -> int:
    """Estimate schemas sent beside messages on every native model request."""

    if not tool_specs:
        return 0
    payload = [
        {
            "name": getattr(spec, "name", ""),
            "description": getattr(spec, "description", ""),
            "input_schema": getattr(spec, "input_schema", None),
            "output_schema": getattr(spec, "output_schema", None),
        }
        for spec in tool_specs
    ]
    return estimate_tokens(
        json.dumps(payload, ensure_ascii=False, default=str)
    )


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
    marker_only = marker.strip()
    best = marker_only if estimate_tokens(marker_only) <= max_tokens else ""
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
