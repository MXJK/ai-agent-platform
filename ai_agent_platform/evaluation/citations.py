"""Programmatic citation verification for L1.

The platform's claim is trustworthy context: every answer is supposed to rest on
evidence the agent actually read. That claim is checkable without a judge and
without a model, which is what this module does:

1. every cited path exists in the workspace;
2. the cited line range holds the file's real content, not invented code;
3. every path the answer cites was actually read into ``context_sources``.

Check 3 is the hallucinated-citation detector: an answer naming a file the run
never opened is wrong even when the sentence around it happens to be true.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable, Sequence


# Context source kinds that point at a file inside the workspace. Knowledge
# chunks, project memories and project instructions carry a path that is not a
# workspace path, so they are reported but never scored.
WORKSPACE_SOURCE_KINDS = frozenset({"file", "search_match"})

# Extensions a bare token must carry to be read as a file citation. Without
# this, prose like "e.g." or a version number would be mistaken for a path.
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
class CitationReport:
    verdicts: tuple[CitationVerdict, ...]
    ungrounded_paths: tuple[str, ...]

    @property
    def scored_count(self) -> int:
        return sum(1 for verdict in self.verdicts if verdict.scored)

    @property
    def verified_count(self) -> int:
        return sum(1 for verdict in self.verdicts if verdict.verified)

    @property
    def accuracy(self) -> float | None:
        return (
            self.verified_count / self.scored_count if self.scored_count else None
        )

    @property
    def passed(self) -> bool:
        return (
            all(verdict.verified for verdict in self.verdicts if verdict.scored)
            and not self.ungrounded_paths
        )

    @property
    def failures(self) -> tuple[CitationVerdict, ...]:
        return tuple(
            verdict
            for verdict in self.verdicts
            if verdict.scored and not verdict.verified
        )


def verify_citations(
    *,
    context_sources: Iterable[dict[str, Any]],
    answer: str,
    workspace_root: Path,
) -> CitationReport:
    sources = list(context_sources)
    return CitationReport(
        verdicts=tuple(
            verify_context_source(source, workspace_root) for source in sources
        ),
        ungrounded_paths=ungrounded_answer_paths(
            answer=answer,
            context_sources=sources,
            workspace_root=workspace_root,
        ),
    )


def verify_context_source(
    source: dict[str, Any],
    workspace_root: Path,
) -> CitationVerdict:
    kind = str(source.get("kind") or "")
    path = str(source.get("path") or "")
    start_line = source.get("start_line")
    end_line = source.get("end_line")
    if kind not in WORKSPACE_SOURCE_KINDS:
        return CitationVerdict(
            kind=kind,
            path=path,
            start_line=start_line,
            end_line=end_line,
            status=STATUS_UNVERIFIABLE,
            detail=f"{kind} sources do not point at a workspace file",
        )
    file_path = workspace_root / path
    if not file_path.is_file():
        return CitationVerdict(
            kind=kind,
            path=path,
            start_line=start_line,
            end_line=end_line,
            status=STATUS_MISSING_FILE,
            detail="cited path does not exist in the workspace",
        )
    if not isinstance(start_line, int) or not isinstance(end_line, int):
        return CitationVerdict(
            kind=kind,
            path=path,
            start_line=start_line,
            end_line=end_line,
            status=STATUS_UNVERIFIABLE,
            detail="source carries no line range",
        )
    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if start_line < 1 or end_line < start_line or start_line > len(lines):
        return CitationVerdict(
            kind=kind,
            path=path,
            start_line=start_line,
            end_line=end_line,
            status=STATUS_OUT_OF_RANGE,
            detail=f"file has {len(lines)} lines",
        )
    disk_slice = "".join(lines[start_line - 1 : end_line])
    cited = str(source.get("text") or "")
    if _content_matches(disk_slice, cited, bool(source.get("truncated"))):
        return CitationVerdict(
            kind=kind,
            path=path,
            start_line=start_line,
            end_line=end_line,
            status=STATUS_VERIFIED,
            detail="cited text equals the file content at that range",
        )
    return CitationVerdict(
        kind=kind,
        path=path,
        start_line=start_line,
        end_line=end_line,
        status=STATUS_CONTENT_MISMATCH,
        detail=f"disk={_preview(disk_slice)} cited={_preview(cited)}",
    )


def answer_citation_paths(answer: str) -> tuple[str, ...]:
    """Pull file citations out of an answer.

    Only tokens carrying a known source or documentation extension count, so a
    version number or an abbreviation is not mistaken for a path.
    """

    found: list[str] = []
    for match in _CITATION_PATTERN.finditer(answer):
        candidate = match.group(1)
        suffix = candidate.rsplit(".", 1)[-1].lower()
        if suffix not in CITABLE_SUFFIXES:
            continue
        if candidate not in found:
            found.append(candidate)
    return tuple(found)


def ungrounded_answer_paths(
    *,
    answer: str,
    context_sources: Sequence[dict[str, Any]],
    workspace_root: Path,
) -> tuple[str, ...]:
    """Paths the answer cites that the run never read.

    A path is grounded when it is the path of a context source, or when it
    appears inside the text of one. The second rule matters: a README that names
    ``docs/runbook.md`` makes an answer repeating that name a faithful report of
    what was read, not an invention. What stays flagged is the dangerous case —
    a path that appears nowhere in the evidence and was never opened.
    """

    grounded = {
        str(source.get("path") or "")
        for source in context_sources
        if source.get("path")
    }
    grounded_names = {Path(item).name for item in grounded}
    evidence_text = "\n".join(
        str(source.get("text") or "") for source in context_sources
    )
    quoted = set(answer_citation_paths(evidence_text))
    ungrounded: list[str] = []
    for candidate in answer_citation_paths(answer):
        if candidate in grounded or candidate in quoted:
            continue
        # A bare filename is grounded when a read path ends with it; answers
        # routinely shorten `a/b/c.py` to `c.py`.
        if "/" not in candidate and candidate in grounded_names:
            continue
        ungrounded.append(candidate)
    return tuple(ungrounded)


def _content_matches(disk_slice: str, cited: str, truncated: bool) -> bool:
    """Compare a cited snippet with the file, tolerating per-line stripping.

    ``repo.read_file`` returns lines verbatim while ``repo.search_code`` strips
    the matched line, so comparing stripped line lists is the tightest rule that
    accepts both. Wrong line numbers and invented content still fail.
    """

    disk_lines = [line.strip() for line in disk_slice.splitlines()]
    cited_lines = [line.strip() for line in cited.splitlines()]
    if disk_lines == cited_lines:
        return True
    if not truncated or not cited_lines or len(cited_lines) > len(disk_lines):
        return False
    head = disk_lines[: len(cited_lines)]
    return (
        head[:-1] == cited_lines[:-1]
        and head[-1].startswith(cited_lines[-1])
    )


def _preview(value: str, limit: int = 80) -> str:
    collapsed = " ".join(value.split())
    return repr(collapsed[:limit])
