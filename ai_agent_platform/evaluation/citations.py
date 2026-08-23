"""Verify citation content separately from answer-path read grounding."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

from ai_agent_platform.evaluation.evidence import normalize_workspace_path


WORKSPACE_SOURCE_KINDS = frozenset(
    {"file", "search_match", "project_instruction", "read_evidence"}
)
CITABLE_SUFFIXES = frozenset(
    {
        "c", "cfg", "cpp", "css", "go", "h", "html", "ini", "java", "js",
        "json", "jsx", "kt", "md", "php", "py", "rb", "rs", "rst", "scss",
        "sh", "sql", "swift", "toml", "ts", "tsx", "txt", "vue", "yaml", "yml",
    }
)

_CITATION_PATTERN = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z][A-Za-z0-9]{0,7})"
    r"(?::\d+(?:-\d+)?)?"
)

STATUS_VERIFIED = "verified"
STATUS_MISSING_FILE = "missing_file"
STATUS_OUT_OF_RANGE = "line_range_out_of_bounds"
STATUS_CONTENT_MISMATCH = "content_mismatch"
STATUS_UNVERIFIABLE = "unverifiable"
STATUS_UNGROUNDED = "not_read"
STATUS_AMBIGUOUS_BASENAME = "ambiguous_basename"


@dataclass(frozen=True)
class CitationVerdict:
    kind: str
    path: str
    start_line: int | None
    end_line: int | None
    status: str
    detail: str

    @property
    def scored(self) -> bool:
        return self.status != STATUS_UNVERIFIABLE

    @property
    def verified(self) -> bool:
        return self.status == STATUS_VERIFIED


@dataclass(frozen=True)
class AnswerPathVerdict:
    cited_path: str
    resolved_path: str
    status: str
    detail: str

    @property
    def grounded(self) -> bool:
        return self.status == STATUS_VERIFIED


@dataclass(frozen=True)
class CitationReport:
    verdicts: tuple[CitationVerdict, ...]
    answer_paths: tuple[AnswerPathVerdict, ...]

    @property
    def scored_count(self) -> int:
        return sum(1 for verdict in self.verdicts if verdict.scored)

    @property
    def verified_count(self) -> int:
        return sum(1 for verdict in self.verdicts if verdict.verified)

    @property
    def content_accuracy(self) -> float | None:
        return self.verified_count / self.scored_count if self.scored_count else None

    @property
    def accuracy(self) -> float | None:
        """Backward-compatible alias for the now explicit content metric."""

        return self.content_accuracy

    @property
    def grounded_path_count(self) -> int:
        return sum(1 for item in self.answer_paths if item.grounded)

    @property
    def answer_path_grounding_rate(self) -> float | None:
        return (
            self.grounded_path_count / len(self.answer_paths)
            if self.answer_paths
            else None
        )

    @property
    def scoreable(self) -> bool:
        return bool(self.scored_count or self.answer_paths)

    @property
    def fully_grounded(self) -> bool:
        return (
            all(verdict.verified for verdict in self.verdicts if verdict.scored)
            and all(item.grounded for item in self.answer_paths)
        )

    @property
    def passed(self) -> bool:
        return self.fully_grounded

    @property
    def failures(self) -> tuple[CitationVerdict, ...]:
        return tuple(
            verdict
            for verdict in self.verdicts
            if verdict.scored and not verdict.verified
        )

    @property
    def ungrounded_paths(self) -> tuple[str, ...]:
        return tuple(
            item.cited_path for item in self.answer_paths if not item.grounded
        )


def verify_citations(
    *,
    context_sources: Iterable[dict[str, Any]],
    answer: str,
    workspace_root: Path,
    read_evidence: Iterable[dict[str, Any]] | None = None,
) -> CitationReport:
    sources = list(context_sources)
    reads = list(read_evidence) if read_evidence is not None else [
        item
        for item in sources
        if str(item.get("kind") or "") in {"file", "project_instruction", "read_evidence"}
    ]
    content_sources = _dedupe_sources([*sources, *reads])
    return CitationReport(
        verdicts=tuple(
            verify_context_source(source, workspace_root)
            for source in content_sources
        ),
        answer_paths=answer_path_verdicts(
            answer=answer,
            read_evidence=reads,
            workspace_root=workspace_root,
        ),
    )


def verify_context_source(
    source: dict[str, Any],
    workspace_root: Path,
) -> CitationVerdict:
    kind = str(source.get("kind") or "")
    raw_path = str(source.get("path") or "")
    path = normalize_workspace_path(raw_path, workspace_root)
    start_line = source.get("start_line")
    end_line = source.get("end_line")
    if kind not in WORKSPACE_SOURCE_KINDS:
        return CitationVerdict(
            kind, path, start_line, end_line, STATUS_UNVERIFIABLE,
            f"{kind} sources do not point at a workspace file",
        )
    if path is None:
        return CitationVerdict(
            kind, raw_path, start_line, end_line, STATUS_MISSING_FILE,
            "cited path is empty or escapes the workspace",
        )
    file_path = workspace_root / path
    if not file_path.is_file():
        return CitationVerdict(
            kind, path, start_line, end_line, STATUS_MISSING_FILE,
            "cited path does not exist in the workspace",
        )
    if not isinstance(start_line, int) or not isinstance(end_line, int):
        return CitationVerdict(
            kind, path, start_line, end_line, STATUS_UNVERIFIABLE,
            "source carries no line range",
        )
    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if start_line < 1 or end_line < start_line or start_line > len(lines):
        return CitationVerdict(
            kind, path, start_line, end_line, STATUS_OUT_OF_RANGE,
            f"file has {len(lines)} lines",
        )
    disk_slice = "".join(lines[start_line - 1 : end_line])
    cited = str(source.get("content") or source.get("text") or "")
    if _content_matches(
        disk_slice,
        cited,
        bool(source.get("truncated")),
        allow_stripped_lines=kind == "search_match",
    ):
        return CitationVerdict(
            kind, path, start_line, end_line, STATUS_VERIFIED,
            "cited text equals the file content at that range",
        )
    return CitationVerdict(
        kind, path, start_line, end_line, STATUS_CONTENT_MISMATCH,
        f"disk={_preview(disk_slice)} cited={_preview(cited)}",
    )


def answer_citation_paths(answer: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in _CITATION_PATTERN.finditer(answer):
        candidate = match.group(1)
        suffix = candidate.rsplit(".", 1)[-1].lower()
        if suffix in CITABLE_SUFFIXES and candidate not in found:
            found.append(candidate)
    return tuple(found)


def answer_path_verdicts(
    *,
    answer: str,
    read_evidence: Sequence[dict[str, Any]],
    workspace_root: Path,
) -> tuple[AnswerPathVerdict, ...]:
    read_paths = {
        normalized
        for item in read_evidence
        if (normalized := normalize_workspace_path(
            str(item.get("path") or ""), workspace_root
        ))
    }
    basenames: dict[str, list[str]] = {}
    for path in sorted(read_paths):
        basenames.setdefault(Path(path).name, []).append(path)

    verdicts: list[AnswerPathVerdict] = []
    for candidate in answer_citation_paths(answer):
        resolved = normalize_workspace_path(candidate, workspace_root)
        if "/" not in candidate:
            matches = basenames.get(candidate, [])
            if len(matches) > 1:
                verdicts.append(
                    AnswerPathVerdict(
                        candidate, "", STATUS_AMBIGUOUS_BASENAME,
                        f"basename maps to multiple read paths: {matches}",
                    )
                )
                continue
            if len(matches) == 1:
                resolved = matches[0]
        if resolved is None or not (workspace_root / resolved).is_file():
            verdicts.append(
                AnswerPathVerdict(
                    candidate, resolved or "", STATUS_MISSING_FILE,
                    "answer path does not exist in the workspace",
                )
            )
        elif resolved not in read_paths:
            verdicts.append(
                AnswerPathVerdict(
                    candidate, resolved, STATUS_UNGROUNDED,
                    "answer path has no successful read evidence",
                )
            )
        else:
            verdicts.append(
                AnswerPathVerdict(
                    candidate, resolved, STATUS_VERIFIED,
                    "answer path exists and has successful read evidence",
                )
            )
    return tuple(verdicts)


def ungrounded_answer_paths(
    *,
    answer: str,
    context_sources: Sequence[dict[str, Any]],
    workspace_root: Path,
) -> tuple[str, ...]:
    """Compatibility wrapper using only true read-like context sources."""

    report = verify_citations(
        context_sources=context_sources,
        answer=answer,
        workspace_root=workspace_root,
    )
    return report.ungrounded_paths


def _dedupe_sources(values: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in values:
        key = (
            item.get("path"), item.get("start_line"), item.get("end_line"),
            item.get("content") or item.get("text"),
        )
        unique.setdefault(key, item)
    return list(unique.values())


def _content_matches(
    disk_slice: str,
    cited: str,
    truncated: bool,
    *,
    allow_stripped_lines: bool,
) -> bool:
    if not allow_stripped_lines:
        if disk_slice == cited:
            return True
        return truncated and bool(cited) and disk_slice.startswith(cited)
    disk_lines = [line.strip() for line in disk_slice.splitlines()]
    cited_lines = [line.strip() for line in cited.splitlines()]
    if disk_lines == cited_lines:
        return True
    if not truncated or not cited_lines or len(cited_lines) > len(disk_lines):
        return False
    head = disk_lines[: len(cited_lines)]
    return head[:-1] == cited_lines[:-1] and head[-1].startswith(cited_lines[-1])


def _preview(value: str, limit: int = 80) -> str:
    collapsed = " ".join(value.split())
    return repr(collapsed[:limit])
