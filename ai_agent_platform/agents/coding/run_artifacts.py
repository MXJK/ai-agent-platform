"""Run-scoped tool-result artifacts and bounded model readback."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from ai_agent_platform.integrations.tools import ToolRegistry, ToolSpec
from ai_agent_platform.token_counting import estimate_text_tokens


RUN_ARTIFACT_TOOL_NAME = "run.read_artifact"
RUN_ARTIFACT_READ_TOOL = RUN_ARTIFACT_TOOL_NAME
TOOL_RESULT_ARTIFACT_PREFIX = "tool_result_"
TOOL_RESULT_ARTIFACT_ID_PATTERN = r"^tool_result_[0-9a-f]{20}$"
MIN_ARTIFACT_READ_TOKENS = 64
MAX_ARTIFACT_READ_TOKENS = 2000
DEFAULT_ARTIFACT_READ_TOKENS = 800

_ARTIFACT_ID = re.compile(TOOL_RESULT_ARTIFACT_ID_PATTERN)
_READ_ARGUMENT_KEYS = frozenset(
    {"artifact_id", "view", "offset_chars", "max_tokens"}
)


class ArtifactReadError(ValueError):
    """A stable, body-free error returned by the runtime read tool."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonical_tool_result_json(result: dict[str, Any]) -> str:
    """Return the exact canonical JSON stored and paged by the Harness."""

    return json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


canonical_tool_result = canonical_tool_result_json


def build_tool_result_artifact(
    result: dict[str, Any],
    *,
    estimated_tokens: int | None = None,
) -> dict[str, Any]:
    """Create a JSON-roundtrippable, integrity-checked Run artifact."""

    canonical = canonical_tool_result_json(result)
    content_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "type": "tool_result",
        "id": TOOL_RESULT_ARTIFACT_PREFIX + content_sha256[:20],
        "call_id": result.get("call_id"),
        "name": result.get("name"),
        "estimated_tokens": (
            estimate_text_tokens(canonical)
            if estimated_tokens is None
            else max(0, int(estimated_tokens))
        ),
        "content_chars": len(canonical),
        "content_sha256": "sha256:" + content_sha256,
        "runtime_created": True,
        "model_readable": True,
        "content": dict(result),
    }


build_run_tool_result_artifact = build_tool_result_artifact


def run_artifact_tool_spec() -> ToolSpec:
    """Return the runtime-owned model contract for bounded artifact reads."""

    range_schema = {
        "type": "object",
        "properties": {
            "start_char": {"type": "integer", "minimum": 0},
            "end_char": {"type": "integer", "minimum": 0},
            "content": {"type": "string"},
        },
        "required": ["start_char", "end_char", "content"],
        "additionalProperties": False,
    }
    return ToolSpec(
        name=RUN_ARTIFACT_TOOL_NAME,
        description=(
            "Read a bounded page or exact head/tail ranges from a tool-result "
            "artifact created in the current Run. Use page with next_offset_chars "
            "to reconstruct its canonical JSON."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "pattern": TOOL_RESULT_ARTIFACT_ID_PATTERN,
                },
                "view": {"type": "string", "enum": ["page", "head_tail"]},
                "offset_chars": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                },
                "max_tokens": {
                    "type": "integer",
                    "minimum": MIN_ARTIFACT_READ_TOKENS,
                    "maximum": MAX_ARTIFACT_READ_TOKENS,
                    "default": DEFAULT_ARTIFACT_READ_TOKENS,
                },
            },
            "required": ["artifact_id"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string"},
                "view": {"type": "string", "enum": ["page", "head_tail"]},
                "offset_chars": {"type": "integer", "minimum": 0},
                "max_tokens": {"type": "integer", "minimum": 0},
                "total_chars": {"type": "integer", "minimum": 0},
                "returned_chars": {"type": "integer", "minimum": 0},
                "estimated_tokens": {"type": "integer", "minimum": 0},
                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "ranges": {"type": "array", "items": range_schema},
                "next_offset_chars": {
                    "oneOf": [
                        {"type": "integer", "minimum": 0},
                        {"type": "null"},
                    ]
                },
                "complete": {"type": "boolean"},
            },
            "required": [
                "artifact_id",
                "view",
                "offset_chars",
                "max_tokens",
                "total_chars",
                "returned_chars",
                "estimated_tokens",
                "sha256",
                "ranges",
                "next_offset_chars",
                "complete",
            ],
            "additionalProperties": False,
        },
        provider="runtime",
        permission_level="read_only",
        requires_approval=False,
        accepts_context=False,
        max_output_chars=100_000,
        idempotent=True,
        permission_source="runtime_state",
    )


def register_run_artifact_tool(registry: ToolRegistry) -> None:
    """Register the capability contract; the graph performs state-local reads."""

    spec = run_artifact_tool_spec()
    registry.register(
        spec.name,
        _runtime_artifact_tool_boundary,
        description=spec.description,
        input_schema=spec.input_schema,
        output_schema=spec.output_schema,
        provider=spec.provider,
        permission_level=spec.permission_level,
        requires_approval=spec.requires_approval,
        accepts_context=spec.accepts_context,
        risk_summary=spec.risk_summary,
        max_output_chars=spec.max_output_chars,
        timeout_seconds=spec.timeout_seconds,
        max_retries=spec.max_retries,
        idempotent=spec.idempotent,
        permission_source=spec.permission_source,
    )


def _runtime_artifact_tool_boundary(**arguments: Any) -> dict[str, Any]:
    del arguments
    raise RuntimeError(
        "run.read_artifact must be handled against the active LangGraph state"
    )


def read_run_artifact(
    artifacts: Iterable[dict[str, Any]],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Read only a valid model-readable tool result in current graph state."""

    artifact_id, view, offset, max_tokens = _validate_read_arguments(arguments)
    artifact = next(
        (
            item
            for item in artifacts
            if isinstance(item, dict) and item.get("id") == artifact_id
        ),
        None,
    )
    content, content_sha256 = _validated_artifact_content(artifact, artifact_id)
    if offset >= len(content):
        raise ArtifactReadError(
            "artifact_offset_out_of_range",
            "offset_chars is beyond the artifact body",
        )

    if view == "page":
        ranges = _page_ranges(content, offset=offset, max_tokens=max_tokens)
        end = ranges[0][1] if ranges else offset
        next_offset = end if end < len(content) else None
        complete = offset == 0 and next_offset is None
    else:
        ranges = _head_tail_ranges(content, offset=offset, max_tokens=max_tokens)
        next_offset = None
        complete = sum(end - start for start, end in ranges) == len(content) - offset

    chunks = [
        {
            "start_char": start,
            "end_char": end,
            "content": content[start:end],
        }
        for start, end in ranges
    ]
    returned = "".join(chunk["content"] for chunk in chunks)
    return {
        "artifact_id": artifact_id,
        "view": view,
        "offset_chars": offset,
        "max_tokens": max_tokens,
        "total_chars": len(content),
        "returned_chars": len(returned),
        "estimated_tokens": estimate_text_tokens(returned),
        "sha256": content_sha256,
        "ranges": chunks,
        "next_offset_chars": next_offset,
        "complete": complete,
    }


def artifact_read_trace(result: dict[str, Any]) -> dict[str, Any]:
    """Project a read result into observability without copying body text."""

    payload = result.get("result")
    payload = payload if isinstance(payload, dict) else {}
    ranges = payload.get("ranges") if isinstance(payload.get("ranges"), list) else []
    return {
        "artifact_id": payload.get("artifact_id") or result.get("artifact_id"),
        "call_id": result.get("call_id"),
        "tool": RUN_ARTIFACT_TOOL_NAME,
        "view": payload.get("view"),
        "ranges": [
            {
                "start_char": item.get("start_char"),
                "end_char": item.get("end_char"),
            }
            for item in ranges
            if isinstance(item, dict)
        ],
        "returned_chars": payload.get("returned_chars", 0),
        "estimated_tokens": payload.get("estimated_tokens", 0),
        "sha256": payload.get("sha256"),
        "error_code": result.get("error_code"),
    }


def _validate_read_arguments(
    arguments: dict[str, Any],
) -> tuple[str, str, int, int]:
    if not isinstance(arguments, dict) or set(arguments).difference(_READ_ARGUMENT_KEYS):
        raise ArtifactReadError(
            "artifact_not_found",
            "artifact is not available",
        )
    artifact_id = arguments.get("artifact_id")
    view = arguments.get("view", "page")
    offset = arguments.get("offset_chars", 0)
    max_tokens = arguments.get("max_tokens", DEFAULT_ARTIFACT_READ_TOKENS)
    if not isinstance(artifact_id, str) or _ARTIFACT_ID.fullmatch(artifact_id) is None:
        raise ArtifactReadError(
            "invalid_tool_arguments",
            "artifact_id must match tool_result_<20 lowercase hex characters>",
        )
    if view not in {"page", "head_tail"}:
        raise ArtifactReadError(
            "invalid_tool_arguments",
            "view must be page or head_tail",
        )
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ArtifactReadError(
            "invalid_tool_arguments",
            "offset_chars must be a non-negative integer",
        )
    if (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or not MIN_ARTIFACT_READ_TOKENS <= max_tokens <= MAX_ARTIFACT_READ_TOKENS
    ):
        raise ArtifactReadError(
            "invalid_tool_arguments",
            "max_tokens must be an integer from 64 through 2000",
        )
    return artifact_id, str(view), offset, max_tokens


def _validated_artifact_content(
    artifact: dict[str, Any] | None,
    artifact_id: str,
) -> tuple[str, str]:
    if (
        artifact is None
        or artifact.get("type") != "tool_result"
        or artifact.get("runtime_created") is not True
        or artifact.get("model_readable") is not True
        or not isinstance(artifact.get("content"), Mapping)
        or not isinstance(artifact.get("content_sha256"), str)
        or isinstance(artifact.get("content_chars"), bool)
        or not isinstance(artifact.get("content_chars"), int)
    ):
        raise ArtifactReadError("artifact_not_found", "artifact is not available")
    content = canonical_tool_result_json(dict(artifact["content"]))
    actual_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if (
        "sha256:" + actual_sha256 != artifact["content_sha256"]
        or len(content) != artifact["content_chars"]
        or artifact_id != TOOL_RESULT_ARTIFACT_PREFIX + actual_sha256[:20]
    ):
        raise ArtifactReadError("artifact_not_found", "artifact is not available")
    return content, actual_sha256


def _page_ranges(text: str, *, offset: int, max_tokens: int) -> list[tuple[int, int]]:
    end = _largest_fitting_end(text, start=offset, end=len(text), max_tokens=max_tokens)
    return [(offset, end)]


def _head_tail_ranges(
    text: str,
    *,
    offset: int,
    max_tokens: int,
) -> list[tuple[int, int]]:
    remaining = len(text) - offset
    if remaining <= 0:
        return [(offset, offset)]
    if estimate_text_tokens(text[offset:]) <= max_tokens:
        return [(offset, len(text))]

    low = 0
    high = remaining
    best: list[tuple[int, int]] = [(offset, offset)]
    while low <= high:
        keep = (low + high) // 2
        head_chars = (keep + 1) // 2
        tail_chars = keep // 2
        head_end = offset + head_chars
        tail_start = max(head_end, len(text) - tail_chars)
        candidate = text[offset:head_end] + text[tail_start:]
        if estimate_text_tokens(candidate) <= max_tokens:
            best = (
                [(offset, head_end)]
                if tail_start == head_end
                else [(offset, head_end), (tail_start, len(text))]
            )
            low = keep + 1
        else:
            high = keep - 1
    return best


def _largest_fitting_end(
    text: str,
    *,
    start: int,
    end: int,
    max_tokens: int,
) -> int:
    low = start
    high = end
    best = start
    while low <= high:
        candidate_end = (low + high) // 2
        if estimate_text_tokens(text[start:candidate_end]) <= max_tokens:
            best = candidate_end
            low = candidate_end + 1
        else:
            high = candidate_end - 1
    return best


__all__ = [
    "ArtifactReadError",
    "DEFAULT_ARTIFACT_READ_TOKENS",
    "MAX_ARTIFACT_READ_TOKENS",
    "MIN_ARTIFACT_READ_TOKENS",
    "RUN_ARTIFACT_TOOL_NAME",
    "RUN_ARTIFACT_READ_TOOL",
    "TOOL_RESULT_ARTIFACT_ID_PATTERN",
    "artifact_read_trace",
    "build_tool_result_artifact",
    "build_run_tool_result_artifact",
    "canonical_tool_result",
    "canonical_tool_result_json",
    "read_run_artifact",
    "register_run_artifact_tool",
    "run_artifact_tool_spec",
]
