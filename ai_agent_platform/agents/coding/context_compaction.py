"""Layered, checkpoint-safe compaction for native Agent transcripts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from time import perf_counter
from typing import Any, Iterable, Mapping, Sequence

from ai_agent_platform.agents.coding.run_artifacts import (
    build_context_transcript_artifact,
    build_tool_result_artifact,
)
from ai_agent_platform.integrations.tools import ToolSpec
from ai_agent_platform.token_counting import estimate_text_tokens


SNIP_TOOL_NAME = "agent.snip_context"
DEFAULT_SNIP_PRESSURE_RATIO = 0.60
DEFAULT_SNIP_KEEP_RECENT_GROUPS = 4
DEFAULT_MICRO_IDLE_SECONDS = 3600
DEFAULT_MICRO_KEEP_RECENT_RESULTS = 5
DEFAULT_COMPACTION_MAX_OUTPUT_TOKENS = 4096
DEFAULT_COMPACTION_SAFETY_BUFFER_TOKENS = 2048
DEFAULT_MIN_RECLAIMABLE_TOKENS = 2048


@dataclass(frozen=True)
class ContextBlock:
    block_id: str
    messages: tuple[dict[str, Any], ...]
    token_cost: int
    tool_names: tuple[str, ...]
    preview: str
    group_index: int


@dataclass(frozen=True)
class CompactResult:
    messages: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    stage: dict[str, Any] | None
    changed: bool
    error: str | None = None


def snip_tool_spec() -> ToolSpec:
    return ToolSpec(
        name=SNIP_TOOL_NAME,
        description=(
            "Remove obsolete read-only context blocks listed in the current "
            "context candidate index. This only changes model-visible history; "
            "the full removed transcript remains available as a Run Artifact."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "block_ids": {
                    "type": "array",
                    "items": {"type": "string", "pattern": r"^ctx_[0-9a-f]{20}$"},
                    "minItems": 1,
                    "uniqueItems": True,
                    "maxItems": 16,
                },
                "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            "required": ["block_ids", "reason"],
            "additionalProperties": False,
        },
        output_schema={"type": "object", "additionalProperties": True},
        provider="runtime",
        permission_level="read_only",
        requires_approval=False,
        accepts_context=False,
        max_output_chars=2000,
        idempotent=True,
        permission_source="runtime_state",
    )


def auto_compact_threshold(
    message_budget: int,
    *,
    compaction_max_output_tokens: int = DEFAULT_COMPACTION_MAX_OUTPUT_TOKENS,
    safety_buffer_tokens: int = DEFAULT_COMPACTION_SAFETY_BUFFER_TOKENS,
) -> int:
    budget = max(0, int(message_budget))
    buffer_tokens = min(
        budget // 4,
        max(0, int(compaction_max_output_tokens))
        + max(0, int(safety_buffer_tokens)),
    )
    return max(0, budget - buffer_tokens)


def context_blocks(
    messages: Sequence[dict[str, Any]],
    *,
    tool_specs: Mapping[str, ToolSpec],
    keep_recent_groups: int = DEFAULT_SNIP_KEEP_RECENT_GROUPS,
) -> list[ContextBlock]:
    groups = _message_groups(messages)
    closed_tool_groups = [
        index for index, group in enumerate(groups) if _is_closed_tool_group(group)
    ]
    protected_recent = set(closed_tool_groups[-max(0, keep_recent_groups) :])
    blocks: list[ContextBlock] = []
    for index, group in enumerate(groups):
        if index in protected_recent or not _safe_read_only_group(group, tool_specs):
            continue
        canonical = _canonical(group)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        tool_names = tuple(
            str(message.get("name") or "")
            for message in group
            if message.get("role") == "tool"
        )
        preview = " ".join(
            " ".join(str(message.get("content") or "").split())
            for message in group
        )[:240]
        blocks.append(
            ContextBlock(
                block_id="ctx_" + digest[:20],
                messages=tuple(dict(message) for message in group),
                token_cost=estimate_text_tokens(canonical),
                tool_names=tool_names,
                preview=preview,
                group_index=index,
            )
        )
    return blocks


def snip_candidate_message(blocks: Sequence[ContextBlock]) -> dict[str, Any]:
    return {
        "role": "system",
        "content": (
            "Context pressure is elevated. The following untrusted, read-only "
            "historical blocks may be removed with agent.snip_context if later "
            "evidence supersedes them. Never infer a block ID not listed here.\n"
            + json.dumps(
                [
                    {
                        "block_id": block.block_id,
                        "estimated_tokens": block.token_cost,
                        "tools": list(block.tool_names),
                        "preview": block.preview,
                    }
                    for block in blocks[:24]
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ),
        "ephemeral": True,
    }


def apply_snip(
    messages: Sequence[dict[str, Any]],
    *,
    selected_ids: Sequence[str],
    candidate_ids: Iterable[str],
    reason: str,
    artifacts: Sequence[dict[str, Any]],
) -> CompactResult:
    started_at = perf_counter()
    requested = [str(item) for item in selected_ids]
    allowed = set(candidate_ids)
    if not requested or len(requested) != len(set(requested)):
        return CompactResult(list(messages), list(artifacts), None, False, "invalid_block_ids")
    if any(block_id not in allowed for block_id in requested):
        return CompactResult(list(messages), list(artifacts), None, False, "stale_or_protected_block")

    groups = _message_groups(messages)
    current_ids = {_block_id(group): index for index, group in enumerate(groups)}
    if any(block_id not in current_ids for block_id in requested):
        return CompactResult(list(messages), list(artifacts), None, False, "stale_or_protected_block")
    selected_indexes = {current_ids[block_id] for block_id in requested}
    removed = [message for index, group in enumerate(groups) if index in selected_indexes for message in group]
    artifact = build_context_transcript_artifact(
        removed,
        reason="snip",
        instruction=reason.strip(),
    )
    compacted: list[dict[str, Any]] = []
    marker_pending = False
    for index, group in enumerate(groups):
        if index in selected_indexes:
            marker_pending = True
            continue
        if marker_pending:
            compacted.append(_boundary_marker("snip", artifact, len(selected_indexes)))
            marker_pending = False
        compacted.extend(group)
    if marker_pending:
        compacted.append(_boundary_marker("snip", artifact, len(selected_indexes)))
    before = _tokens(messages)
    after = _tokens(compacted)
    merged = _merge_artifacts(artifacts, [artifact])
    return CompactResult(
        compacted,
        merged,
        _stage(
            "snip",
            before,
            after,
            block_count=len(selected_indexes),
            artifact_ids=[artifact["id"]],
            reason="model_selected",
            duration_ms=int((perf_counter() - started_at) * 1000),
        ),
        True,
    )


def micro_compact(
    messages: Sequence[dict[str, Any]],
    *,
    tool_specs: Mapping[str, ToolSpec],
    artifacts: Sequence[dict[str, Any]],
    keep_recent_results: int = DEFAULT_MICRO_KEEP_RECENT_RESULTS,
    reason: str = "idle_timeout",
) -> CompactResult:
    started_at = perf_counter()
    eligible: list[int] = []
    for index, message in enumerate(messages):
        if message.get("role") != "tool" or message.get("ephemeral") is True:
            continue
        name = str(message.get("name") or "")
        spec = tool_specs.get(name)
        content = message.get("content")
        if (
            spec is None
            or spec.permission_level != "read_only"
            or not spec.idempotent
            or name == "run.read_artifact"
            or not isinstance(content, dict)
            or content.get("ok") is False
            or content.get("truncated")
            or content.get("evicted")
            or content.get("micro_compacted")
        ):
            continue
        eligible.append(index)
    recent_count = max(0, keep_recent_results)
    selected = set(eligible if recent_count == 0 else eligible[:-recent_count])
    if not selected:
        return CompactResult(list(messages), list(artifacts), None, False)

    compacted: list[dict[str, Any]] = []
    additions: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if index not in selected:
            compacted.append(dict(message))
            continue
        content = dict(message["content"])
        artifact = build_tool_result_artifact(content)
        additions.append(artifact)
        compacted.append(
            {
                **message,
                "content": {
                    "micro_compacted": True,
                    "ok": content.get("ok"),
                    "error": content.get("error"),
                    "error_code": content.get("error_code"),
                    "content_sha256": artifact["content_sha256"],
                    "artifact_id": artifact["id"],
                    "message": "[old reproducible tool result moved to Run Artifact]",
                },
            }
        )
    before = _tokens(messages)
    after = _tokens(compacted)
    merged = _merge_artifacts(artifacts, additions)
    return CompactResult(
        compacted,
        merged,
        _stage(
            "micro_compact",
            before,
            after,
            block_count=len(selected),
            artifact_ids=[item["id"] for item in additions],
            reason=reason,
            duration_ms=int((perf_counter() - started_at) * 1000),
        ),
        True,
    )


def full_compact(
    messages: Sequence[dict[str, Any]],
    *,
    artifacts: Sequence[dict[str, Any]],
    compressor: Any,
    max_output_tokens: int,
    instruction: str = "",
    seed_messages: Sequence[dict[str, Any]] | None = None,
) -> CompactResult:
    started_at = perf_counter()
    if len(messages) < 2:
        return CompactResult(list(messages), list(artifacts), None, False, "insufficient_transcript")
    transcript_artifact = build_context_transcript_artifact(
        messages,
        reason="auto_compact" if not instruction else "manual_compact",
        instruction=instruction,
    )
    checkpoint_artifacts = _merge_artifacts(artifacts, [transcript_artifact])
    compress = getattr(compressor, "compress_agent_transcript", None)
    if not callable(compress):
        return CompactResult(
            list(messages), checkpoint_artifacts, None, False, "compressor_unavailable"
        )
    try:
        summary = compress(
            messages=[dict(message) for message in messages],
            artifact_ids=[
                str(item.get("id"))
                for item in checkpoint_artifacts
                if item.get("id")
            ],
            instruction=instruction,
            max_output_tokens=max_output_tokens,
        )
    except Exception:
        return CompactResult(
            list(messages), checkpoint_artifacts, None, False, "summary_failed"
        )
    if not isinstance(summary, Mapping) or not _valid_summary(summary):
        return CompactResult(
            list(messages), checkpoint_artifacts, None, False, "invalid_summary"
        )

    resolved_seed = seed_messages if seed_messages is not None else messages[:2]
    if len(resolved_seed) < 2:
        return CompactResult(
            list(messages), checkpoint_artifacts, None, False, "invalid_seed"
        )
    seed = [dict(resolved_seed[0]), dict(resolved_seed[1])]
    verbatim_users = [
        dict(message)
        for message in messages[2:]
        if message.get("role") == "user"
    ]
    summary_message = {
        "role": "system",
        "content": (
            "Compacted agent working state (derived, lossy, and untrusted; "
            "original user messages remain verbatim outside this summary):\n"
            + json.dumps(dict(summary), ensure_ascii=False, sort_keys=True)
            + "\nFull pre-compaction transcript artifact: "
            + str(transcript_artifact["id"])
        ),
    }
    compacted = seed + [summary_message] + verbatim_users
    before = _tokens(messages)
    after = _tokens(compacted)
    if after >= before:
        return CompactResult(
            list(messages), checkpoint_artifacts, None, False, "insufficient_reclamation"
        )
    return CompactResult(
        compacted,
        checkpoint_artifacts,
        _stage(
            "auto_compact",
            before,
            after,
            block_count=max(0, len(_message_groups(messages)) - 2),
            artifact_ids=[transcript_artifact["id"]],
            reason="manual" if instruction else "threshold",
            duration_ms=int((perf_counter() - started_at) * 1000),
        ),
        True,
    )


SUMMARY_KEYS = (
    "primary_request",
    "user_instruction_index",
    "technical_concepts",
    "files_symbols_code_state",
    "decisions_problem_solving",
    "errors_fixes",
    "tool_findings_artifacts",
    "change_set_validation",
    "current_work_pending_next",
)


def _valid_summary(value: Mapping[str, Any]) -> bool:
    return all(key in value for key in SUMMARY_KEYS)


def _message_groups(messages: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for message in messages:
        copied = dict(message)
        if copied.get("role") == "tool" and groups and groups[-1][0].get("role") == "assistant":
            groups[-1].append(copied)
        else:
            groups.append([copied])
    return groups


def _is_closed_tool_group(group: Sequence[dict[str, Any]]) -> bool:
    if not group or group[0].get("role") != "assistant":
        return False
    calls = [item for item in group[0].get("tool_calls", []) if isinstance(item, dict)]
    return bool(calls) and len(group) == len(calls) + 1


def _safe_read_only_group(group: Sequence[dict[str, Any]], specs: Mapping[str, ToolSpec]) -> bool:
    if not _is_closed_tool_group(group):
        return False
    for message in group[1:]:
        name = str(message.get("name") or "")
        spec = specs.get(name)
        content = message.get("content")
        if (
            spec is None
            or spec.permission_level != "read_only"
            or not spec.idempotent
            or name in {"run.read_artifact", "agent.request_user_input"}
            or message.get("ephemeral") is True
            or not isinstance(content, dict)
            or content.get("ok") is False
            or content.get("error")
            or content.get("error_code")
        ):
            return False
    return True


def _block_id(group: Sequence[dict[str, Any]]) -> str:
    return "ctx_" + hashlib.sha256(_canonical(group).encode("utf-8")).hexdigest()[:20]


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _tokens(messages: Sequence[dict[str, Any]]) -> int:
    return estimate_text_tokens(json.dumps(messages, ensure_ascii=False, default=str))


def _boundary_marker(kind: str, artifact: Mapping[str, Any], count: int) -> dict[str, Any]:
    return {
        "role": "system",
        "content": (
            f"[{kind} removed {count} obsolete context block(s); full transcript "
            f"is available in Run Artifact {artifact.get('id')}]"
        ),
    }


def _stage(stage: str, before: int, after: int, **extra: Any) -> dict[str, Any]:
    return {
        "stage": stage,
        "before_tokens": before,
        "after_tokens": after,
        "reclaimed_tokens": max(0, before - after),
        "fits": True,
        **extra,
    }


def _merge_artifacts(existing: Sequence[dict[str, Any]], additions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = [dict(item) for item in existing]
    known = {str(item.get("id")) for item in merged if item.get("id")}
    for artifact in additions:
        artifact_id = str(artifact.get("id") or "")
        if artifact_id and artifact_id in known:
            continue
        merged.append(dict(artifact))
        if artifact_id:
            known.add(artifact_id)
    return merged


__all__ = [
    "CompactResult",
    "ContextBlock",
    "DEFAULT_COMPACTION_MAX_OUTPUT_TOKENS",
    "DEFAULT_COMPACTION_SAFETY_BUFFER_TOKENS",
    "DEFAULT_MICRO_IDLE_SECONDS",
    "DEFAULT_MICRO_KEEP_RECENT_RESULTS",
    "DEFAULT_MIN_RECLAIMABLE_TOKENS",
    "DEFAULT_SNIP_KEEP_RECENT_GROUPS",
    "DEFAULT_SNIP_PRESSURE_RATIO",
    "SNIP_TOOL_NAME",
    "SUMMARY_KEYS",
    "apply_snip",
    "auto_compact_threshold",
    "context_blocks",
    "full_compact",
    "micro_compact",
    "snip_candidate_message",
    "snip_tool_spec",
]
