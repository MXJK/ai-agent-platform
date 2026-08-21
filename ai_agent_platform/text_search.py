"""Aligned lexical tokenization for SQLite FTS, including CJK text."""

from __future__ import annotations

import re


_PARTS = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]+")


def fts_index_text(value: str) -> str:
    """Return index terms whose CJK segmentation matches the query path."""
    terms: list[str] = []
    for part in _PARTS.findall(value.casefold()):
        if _is_cjk(part):
            for width in range(1, min(4, len(part)) + 1):
                terms.extend(
                    part[index : index + width]
                    for index in range(len(part) - width + 1)
                )
        else:
            terms.append(part)
    return " ".join(dict.fromkeys(terms))


def fts_match_query(value: str) -> str:
    """Build an FTS query using the same CJK n-grams as writes."""
    terms: list[str] = []
    for part in _PARTS.findall(value.casefold()):
        if _is_cjk(part) and len(part) > 4:
            terms.extend(part[index : index + 4] for index in range(len(part) - 3))
        else:
            terms.append(part)
    escaped = [
        f'"{term.replace(chr(34), chr(34) * 2)}"'
        for term in dict.fromkeys(terms)
    ]
    return " AND ".join(escaped[:24])


def _is_cjk(value: str) -> bool:
    return bool(value) and "\u3400" <= value[0] <= "\u9fff"


__all__ = ["fts_index_text", "fts_match_query"]
