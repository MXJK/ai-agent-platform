from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import ceil
from typing import Any


TOKEN_ESTIMATION_METHOD = "unicode_heuristic_v1"
MESSAGE_TOKEN_OVERHEAD = 4
CONVERSATION_TOKEN_OVERHEAD = 2


def estimate_text_tokens(text: str) -> int:
    """Estimate tokens without making a provider request.

    ASCII text is approximated at four characters per token. Non-ASCII
    characters are counted individually so Chinese and other compact scripts
    are not materially under-reported.
    """

    if not text:
        return 0
    ascii_chars = sum(1 for character in text if ord(character) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, ceil(ascii_chars / 4) + non_ascii_chars)


def estimate_message_tokens(
    messages: Sequence[Mapping[str, Any]],
) -> int:
    if not messages:
        return 0
    tokens = CONVERSATION_TOKEN_OVERHEAD
    for message in messages:
        tokens += MESSAGE_TOKEN_OVERHEAD
        tokens += estimate_text_tokens(str(message.get("role") or ""))
        tokens += estimate_text_tokens(_message_content(message.get("content")))
    return tokens


def _message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(value or "")
