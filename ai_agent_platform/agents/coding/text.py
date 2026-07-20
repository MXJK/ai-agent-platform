"""Small text helpers shared by coding-agent planning and presentation."""

from __future__ import annotations

import re


def extract_paths(text: str) -> list[str]:
    path_pattern = r"[\w./-]+\.(?:py|ts|tsx|js|jsx|md|toml|yaml|yml|json|go|rs|java)"
    return unique(re.findall(path_pattern, text))


def extract_symbols(text: str) -> list[str]:
    candidates = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", text)
    ignored = {
        "the",
        "and",
        "for",
        "with",
        "class",
        "def",
        "api",
        "rag",
        "sse",
    }
    symbols = [
        item
        for item in candidates
        if item.lower() not in ignored and ("_" in item or item[:1].isupper())
    ]
    return unique(symbols)


def snippet(text: str, *, limit: int = 120) -> str:
    return text.strip().replace("\n", " ")[:limit]


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
