"""Select MCP schema loading for the active provider and frozen tool catalog."""
from __future__ import annotations
import json
import os
import re
from enum import Enum
from urllib.parse import urlparse

DEFAULT_EAGER_THRESHOLD_PERCENT = 10
CHARS_PER_TOKEN = 2.5


class McpLoadingMode(str, Enum):
    EAGER = 'eager'
    NATIVE = 'native'
    DISPATCH = 'dispatch'


def is_official_anthropic_endpoint(base_url: str) -> bool:
    return not base_url or (urlparse(base_url).hostname or '').lower() == 'api.anthropic.com'


def supports_native_search(provider: str, model: str, base_url: str = '') -> bool:
    return (provider == 'anthropic' and is_official_anthropic_endpoint(base_url)
            and bool(re.match(r'^claude-(?:opus|sonnet|haiku)-(?:4-[5-9]|[5-9])', model)))


def estimate_schema_tokens(schema_chars: int) -> int:
    return int(schema_chars / CHARS_PER_TOKEN)


def decide_mode(base_url: str, context_window: int, mcp_schema_chars: int,
                threshold_percent: int = DEFAULT_EAGER_THRESHOLD_PERCENT, *,
                provider: str = '', model: str = '') -> McpLoadingMode:
    override = os.environ.get('COGENT_MCP_LOADING', '').strip().lower()
    native = supports_native_search(provider, model, base_url)
    if override in {'eager', 'native', 'dispatch'}:
        return McpLoadingMode.DISPATCH if override == 'native' and not native else McpLoadingMode(override)
    if estimate_schema_tokens(mcp_schema_chars) < context_window * threshold_percent / 100:
        return McpLoadingMode.EAGER
    return McpLoadingMode.NATIVE if native else McpLoadingMode.DISPATCH


def measure_mcp_schema_chars(adapter) -> int:
    return sum(len(json.dumps({'name': name, 'description': spec.description,
                               'input_schema': spec.input_schema}, ensure_ascii=False))
               for name, spec in adapter.mcp_specs().items())


def decide_and_apply(adapter, base_url: str, context_window: int, *, provider='', model=''):
    mode = decide_mode(base_url, context_window, measure_mcp_schema_chars(adapter),
                       provider=provider, model=model)
    adapter.mcp_loading_mode = mode.value
    return mode
