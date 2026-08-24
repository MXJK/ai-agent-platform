"""Run-local, fail-closed tool-result Artifact creation and readback."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from ai_agent_platform.integrations.tools import ToolRegistry, ToolSpec
from ai_agent_platform.token_counting import estimate_text_tokens


RUN_ARTIFACT_READ_TOOL = "run.read_artifact"
RUN_ARTIFACT_ID_RE = re.compile(r"^tool_result_[0-9a-f]{20}$")
RUN_ARTIFACT_DEFAULT_MAX_TOKENS = 800
RUN_ARTIFACT_MIN_TOKENS = 64
RUN_ARTIFACT_MAX_TOKENS = 2000


RUN_ARTIFACT_READ_SPEC = ToolSpec(
    name=RUN_ARTIFACT_READ_TOOL,
    description=(
        "Read a verified page or head/tail view of a model-readable tool-result "
        "Artifact inherited by this Run's current checkpoint."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "artifact_id": {
                "type": "string",
                "pattern": r"^tool_result_[0-9a-f]{20}$",
            },
            "view": {"type": "string", "enum": ["page", "head_tail"]},
            "offset_chars": {"type": "integer", "minimum": 0},
            "max_tokens": {
                "type": "integer",
                "minimum": RUN_ARTIFACT_MIN_TOKENS,
                "maximum": RUN_ARTIFACT_MAX_TOKENS,
            },
        },
        "required": ["artifact_id"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "additionalProperties": True,
    },
    provider="runtime",
    permission_level="read_only",
    requires_approval=False,
    idempotent=True,
    permission_source="runtime_checkpoint",
)


def register_run_artifact_tool(registry: ToolRegistry) -> None:
    """Register the model-visible capability; execution stays graph-state local."""

    spec = RUN_ARTIFACT_READ_SPEC
    registry.register(
        spec.name,
        _unbound_read_artifact,
        description=spec.description,
        input_schema=spec.input_schema,
        output_schema=spec.output_schema,
        provider=spec.provider,
        permission_level=spec.permission_level,
        requires_approval=spec.requires_approval,
        accepts_context=False,
        risk_summary=spec.risk_summary,
        max_output_chars=12_000,
        idempotent=spec.idempotent,
        permission_source=spec.permission_source,
    )


def _unbound_read_artifact(**arguments: Any) -> dict[str, Any]:
    del arguments
    raise RuntimeError(
        "run.read_artifact must be handled by the current LangGraph checkpoint"
    )


def canonical_tool_result(value: Any) -> str:
    """Return the exact canonical JSON persisted by the Agent Harness."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def tool_result_artifact_id(canonical: str) -> str:
    return "tool_result_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def build_run_tool_result_artifact(result: Mapping[str, Any]) -> dict[str, Any]:
    """Build a content-addressed Artifact from a complete Harness ToolResult."""

    content = dict(result)
    canonical = canonical_tool_result(content)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "type": "tool_result",
        "id": "tool_result_" + digest[:20],
        "call_id": content.get("call_id"),
        "name": content.get("name"),
        "estimated_tokens": estimate_text_tokens(canonical),
        "content_chars": len(canonical),
        "content_sha256": "sha256:" + digest,
        "runtime_created": True,
        "model_readable": True,
        "content": content,
    }


def read_run_artifact(
    artifacts: Sequence[Mapping[str, Any]],
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Read only a verified Artifact present in the current checkpoint state."""

    parsed = _validated_arguments(arguments)
    if parsed is None:
        return _error("artifact_not_found")
    artifact_id, view, offset_chars, max_tokens = parsed
    artifact = next(
        (
            item
            for item in artifacts
            if str(item.get("id") or "") == artifact_id
        ),
        None,
    )
    canonical = _verified_canonical(artifact, artifact_id=artifact_id)
    if canonical is None:
        return _error("artifact_not_found", artifact_id=artifact_id)
    if offset_chars >= len(canonical):
        return _error(
            "artifact_offset_out_of_range",
            artifact_id=artifact_id,
            offset_chars=offset_chars,
            total_chars=len(canonical),
        )
    if view == "head_tail":
        result = _head_tail_view(
            canonical,
            artifact_id=artifact_id,
            offset_chars=offset_chars,
            max_tokens=max_tokens,
        )
    else:
        result = _page_view(
            canonical,
            artifact_id=artifact_id,
            offset_chars=offset_chars,
            max_tokens=max_tokens,
        )
    return {"ok": True, "result": result}


def _validated_arguments(
    arguments: Mapping[str, Any],
) -> tuple[str, str, int, int] | None:
    if not isinstance(arguments, Mapping):
        return None
    if set(arguments).difference(
        {"artifact_id", "view", "offset_chars", "max_tokens"}
    ):
        return None
    artifact_id = arguments.get("artifact_id")
    view = arguments.get("view", "page")
    offset = arguments.get("offset_chars", 0)
    max_tokens = arguments.get("max_tokens", RUN_ARTIFACT_DEFAULT_MAX_TOKENS)
    if not isinstance(artifact_id, str) or RUN_ARTIFACT_ID_RE.fullmatch(artifact_id) is None:
        return None
    if view not in {"page", "head_tail"}:
        return None
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return None
    if (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or not RUN_ARTIFACT_MIN_TOKENS <= max_tokens <= RUN_ARTIFACT_MAX_TOKENS
    ):
        return None
    return artifact_id, str(view), offset, max_tokens


def _verified_canonical(
    artifact: Mapping[str, Any] | None,
    *,
    artifact_id: str,
) -> str | None:
    if artifact is None:
        return None
    if (
        artifact.get("type") != "tool_result"
        or artifact.get("runtime_created") is not True
        or artifact.get("model_readable") is not True
    ):
        return None
    content = artifact.get("content")
    if not isinstance(content, Mapping):
        return None
    canonical = canonical_tool_result(content)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if artifact_id != "tool_result_" + digest[:20]:
        return None
    if artifact.get("content_sha256") != "sha256:" + digest:
        return None
    if artifact.get("content_chars") != len(canonical):
        return None
    return canonical


def _page_view(
    canonical: str,
    *,
    artifact_id: str,
    offset_chars: int,
    max_tokens: int,
) -> dict[str, Any]:
    end = _max_end_for_tokens(canonical, offset_chars, len(canonical), max_tokens)
    content = canonical[offset_chars:end]
    return {
        "artifact_id": artifact_id,
        "view": "page",
        "content": content,
        "range": {"start_char": offset_chars, "end_char": end},
        "total_chars": len(canonical),
        "returned_chars": len(content),
        "estimated_tokens": estimate_text_tokens(content),
        "next_offset_chars": end if end < len(canonical) else None,
        "content_sha256": "sha256:"
        + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "ephemeral": True,
    }


def _head_tail_view(
    canonical: str,
    *,
    artifact_id: str,
    offset_chars: int,
    max_tokens: int,
) -> dict[str, Any]:
    remaining = canonical[offset_chars:]
    if estimate_text_tokens(remaining) <= max_tokens:
        head = remaining
        tail = ""
        ranges = [{"start_char": offset_chars, "end_char": len(canonical)}]
    else:
        head_budget = (max_tokens + 1) // 2
        tail_budget = max_tokens - head_budget
        head_end = _max_end_for_tokens(
            canonical, offset_chars, len(canonical), head_budget
        )
        tail_start = _min_start_for_tokens(
            canonical, max(head_end, offset_chars), len(canonical), tail_budget
        )
        head = canonical[offset_chars:head_end]
        tail = canonical[tail_start:]
        ranges = [
            {"start_char": offset_chars, "end_char": head_end},
            {"start_char": tail_start, "end_char": len(canonical)},
        ]
    return {
        "artifact_id": artifact_id,
        "view": "head_tail",
        "head": head,
        "tail": tail,
        "ranges": ranges,
        "total_chars": len(canonical),
        "returned_chars": len(head) + len(tail),
        "estimated_tokens": estimate_text_tokens(head) + estimate_text_tokens(tail),
        "content_sha256": "sha256:"
        + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "ephemeral": True,
    }


def _max_end_for_tokens(text: str, start: int, end: int, max_tokens: int) -> int:
    low, high = start + 1, end
    best = start
    while low <= high:
        candidate = (low + high) // 2
        if estimate_text_tokens(text[start:candidate]) <= max_tokens:
            best = candidate
            low = candidate + 1
        else:
            high = candidate - 1
    return best


def _min_start_for_tokens(text: str, floor: int, end: int, max_tokens: int) -> int:
    low, high = floor, end
    best = end
    while low <= high:
        candidate = (low + high) // 2
        if estimate_text_tokens(text[candidate:end]) <= max_tokens:
            best = candidate
            high = candidate - 1
        else:
            low = candidate + 1
    return best


def _error(code: str, **metadata: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "error": code,
        "error_code": code,
        **metadata,
    }


__all__ = [
    "RUN_ARTIFACT_READ_SPEC",
    "RUN_ARTIFACT_READ_TOOL",
    "build_run_tool_result_artifact",
    "canonical_tool_result",
    "read_run_artifact",
    "register_run_artifact_tool",
    "tool_result_artifact_id",
]
