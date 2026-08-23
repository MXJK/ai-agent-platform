"""Build the auditable ledger of files an Eval run actually read."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Iterable

from ai_agent_platform.evaluation.trajectory import RunObservation


INITIAL_READ_KINDS = frozenset({"file", "project_instruction"})


@dataclass(frozen=True)
class ReadEvidence:
    path: str
    start_line: int
    end_line: int
    content: str
    content_hash: str
    truncated: bool
    call_id: str
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "read_evidence",
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "text": self.content,
            "content": self.content,
            "content_hash": self.content_hash,
            "truncated": self.truncated,
            "call_id": self.call_id,
            "source": self.source,
        }


def build_read_evidence_ledger(
    *,
    observation: RunObservation,
    workspace_root: Path,
    context_sources: Iterable[dict[str, Any]] | None = None,
) -> tuple[ReadEvidence, ...]:
    """Merge initialization reads with successful native read ToolResults.

    Search matches are intentionally excluded: they prove a matched line, not
    that the Agent opened the file. Calls without ToolResults never appear in
    ``executed_calls`` and therefore cannot create evidence here.
    """

    ledger: list[ReadEvidence] = []
    for source in context_sources or observation.context_sources:
        if not isinstance(source, dict):
            continue
        if str(source.get("kind") or "") not in INITIAL_READ_KINDS:
            continue
        evidence = _evidence_from_payload(
            source,
            workspace_root=workspace_root,
            call_id=str(source.get("call_id") or ""),
            source="initial_context",
        )
        if evidence is not None:
            ledger.append(evidence)

    for call in observation.executed_calls:
        if call.name != "repo.read_file" or call.ok is not True:
            continue
        evidence = _evidence_from_payload(
            call.result,
            workspace_root=workspace_root,
            call_id=call.call_id,
            source="tool_result",
        )
        if evidence is not None:
            ledger.append(evidence)

    # Default and explicitly equivalent read ranges collapse to the same
    # evidence key because the ToolResult contains the normalized actual range.
    unique: dict[tuple[str, int, int, str], ReadEvidence] = {}
    for item in ledger:
        key = (item.path, item.start_line, item.end_line, item.content)
        if key not in unique or item.source == "tool_result":
            unique[key] = item
    return tuple(unique.values())


def normalize_workspace_path(path: str, workspace_root: Path) -> str | None:
    root = workspace_root.resolve()
    candidate = (root / path.replace("\\", "/")).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    normalized = relative.as_posix()
    return normalized if normalized not in {"", "."} else None


def _evidence_from_payload(
    payload: dict[str, Any],
    *,
    workspace_root: Path,
    call_id: str,
    source: str,
) -> ReadEvidence | None:
    path = normalize_workspace_path(
        str(payload.get("path") or ""),
        workspace_root,
    )
    content = str(payload.get("content") or payload.get("text") or "")
    start_line = payload.get("start_line")
    end_line = payload.get("end_line")
    if (
        path is None
        or not content
        or not isinstance(start_line, int)
        or not isinstance(end_line, int)
        or start_line < 1
        or end_line < start_line
    ):
        return None
    return ReadEvidence(
        path=path,
        start_line=start_line,
        end_line=end_line,
        content=content,
        content_hash=str(
            payload.get("content_hash")
            or hashlib.sha256(content.encode("utf-8")).hexdigest()
        ),
        truncated=bool(payload.get("truncated")),
        call_id=call_id,
        source=source,
    )
