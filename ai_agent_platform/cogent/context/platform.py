from __future__ import annotations

import json
from typing import Any

from ..conversation import Message, ToolResultBlock, ToolUseBlock, ThinkingBlock, estimate_tokens
from .manager import _compute_keep_start_index, _build_prefix_text


def canonical_messages(messages: list[dict[str, Any]]) -> list[Message]:
    result = []
    for item in messages:
        content = item.get('content', '')
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        role = item.get('role', 'user')
        if role == 'tool':
            result.append(Message(role='user', content='', metadata=dict(item), tool_results=[
                ToolResultBlock(str(item.get('call_id', '')), text, bool(item.get('is_error')))
            ]))
        else:
            provider = str(item.get('provider') or '')
            native = [dict(block) for block in item.get('provider_items', []) if isinstance(block, dict)]
            result.append(Message(role=role, content=text, provider=provider,
                metadata=dict(item), thinking_blocks=[
                    ThinkingBlock('', '', provider, block) for block in native
                ], tool_uses=[
                ToolUseBlock(str(call.get('call_id', '')), str(call.get('name', '')), dict(call.get('arguments') or {}))
                for call in item.get('tool_calls', [])
            ]))
    return result


def token_estimate(messages: list[dict[str, Any]]) -> int:
    return estimate_tokens(canonical_messages(messages))


def compact_prefix(messages: list[dict[str, Any]]) -> tuple[int, str]:
    canonical = canonical_messages(messages)
    start = _compute_keep_start_index(canonical)
    # Platform transcripts store each result separately; keep the complete batch.
    while start > 0 and messages[start].get('role') == 'tool':
        start -= 1
    return start, _build_prefix_text(canonical[:start])
