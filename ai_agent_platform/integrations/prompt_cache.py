"""Provider-neutral prompt-prefix normalization and cache routing helpers."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from ai_agent_platform.integrations.tools import ToolSpec
from ai_agent_platform.token_counting import estimate_text_tokens


PROMPT_CACHE_VERSION = "agent-prefix/v1"


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value with byte-stable ordering."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_tool_specs(specs: Sequence[ToolSpec]) -> list[ToolSpec]:
    """Return copied tools in a stable order with canonical JSON Schemas."""

    return [
        replace(
            spec,
            input_schema=json.loads(canonical_json(spec.input_schema)),
            output_schema=json.loads(canonical_json(spec.output_schema)),
        )
        for spec in sorted(specs, key=lambda item: item.name)
    ]


def stable_instruction_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return only the leading stable system/developer instruction prefix.

    Runtime state, workspace details, user content, tool results, retries, and
    steering are suffix data by construction. A later system message therefore
    appends configuration without rewriting the already cacheable prefix.
    """

    stable: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role not in {"system", "developer"}:
            break
        content = message.get("content")
        if not isinstance(content, (str, list)):
            break
        stable.append({"role": role, "content": content})
    return stable


def stable_prefix_payload(
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[ToolSpec],
) -> dict[str, Any]:
    """Build the canonical, provider-neutral model prefix representation."""

    return {
        "version": PROMPT_CACHE_VERSION,
        "instructions": stable_instruction_messages(messages),
        "tools": [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "output_schema": spec.output_schema,
            }
            for spec in canonical_tool_specs(tools)
        ],
    }


def stable_prefix_bytes(
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[ToolSpec],
) -> bytes:
    return canonical_json(stable_prefix_payload(messages, tools)).encode("utf-8")


def stable_prefix_digest(
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[ToolSpec],
) -> str:
    return hashlib.sha256(stable_prefix_bytes(messages, tools)).hexdigest()


def prompt_cache_key(
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[ToolSpec],
    usage_context: Any,
) -> str:
    """Return a privacy-preserving workspace/run routing key for OpenAI."""

    workspace_id = str(getattr(usage_context, "workspace_id", "") or "")
    run_id = str(getattr(usage_context, "resource_id", "") or "")
    scope = f"workspace:{workspace_id}" if workspace_id else f"run:{run_id}"
    digest = hashlib.sha256(
        f"{PROMPT_CACHE_VERSION}\0{scope}\0".encode("utf-8")
        + stable_prefix_bytes(messages, tools)
    ).hexdigest()
    return f"agent-pcv1-{digest[:40]}"


def prefix_token_estimates(
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[ToolSpec],
) -> tuple[int, int]:
    """Estimate stable instruction and tool-schema tokens separately."""

    instructions = canonical_json(stable_instruction_messages(messages))
    canonical_tools = canonical_tool_specs(tools)
    tool_payload = canonical_json(
        [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "output_schema": spec.output_schema,
            }
            for spec in canonical_tools
        ]
    )
    return (
        estimate_text_tokens(instructions),
        estimate_text_tokens(tool_payload) if canonical_tools else 0,
    )


def supports_openai_explicit_cache(model: str) -> bool:
    """Only GPT-5.6+ accepts prompt_cache_options/breakpoint fields."""

    match = re.match(r"^gpt-(\d+)\.(\d+)", str(model).casefold())
    if match is None:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (5, 6)


__all__ = [
    "PROMPT_CACHE_VERSION",
    "canonical_json",
    "canonical_tool_specs",
    "prefix_token_estimates",
    "prompt_cache_key",
    "stable_instruction_messages",
    "stable_prefix_bytes",
    "stable_prefix_digest",
    "stable_prefix_payload",
    "supports_openai_explicit_cache",
]
