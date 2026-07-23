from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Iterable

from ai_agent_platform.integrations.tools import ToolExecutionContext, ToolRegistry


IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
}
SENSITIVE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024


class RepositoryToolKit:
    """Read the live filesystem rooted at the workspace captured for this run."""

    def list_files(
        self,
        path: str = "",
        max_results: int = 100,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        root = _workspace_root(context)
        base_path = _resolve_path(root, path or ".")
        if not base_path.is_dir():
            raise ValueError(f"path is not an existing directory: {path or '.'}")
        files = [
            _relative_to(candidate, root)
            for candidate in _iter_files(base_path)
        ][: max(1, max_results)]
        return {
            "path": path or ".",
            "files": files,
            "count": len(files),
            "truncated": len(files) >= max(1, max_results),
        }

    def find_files(
        self,
        query: str,
        path: str = "",
        max_results: int = 40,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        root = _workspace_root(context)
        base_path = _resolve_path(root, path or ".")
        if not base_path.is_dir():
            raise ValueError(f"path is not an existing directory: {path or '.'}")
        terms = [term.lower() for term in _query_terms(query)]
        if not terms:
            raise ValueError("query did not contain searchable terms")
        ranked: list[tuple[int, str]] = []
        for candidate in _iter_files(base_path):
            relative = _relative_to(candidate, root)
            lowered = relative.lower()
            name = candidate.name.lower()
            if not any(term in lowered for term in terms):
                continue
            score = sum(3 if term in name else 1 for term in terms)
            ranked.append((-score, relative))
        matches = [item[1] for item in sorted(ranked)[: max(1, max_results)]]
        return {
            "query": query,
            "path": path or ".",
            "matches": matches,
            "count": len(matches),
            "truncated": len(ranked) > len(matches),
        }

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_chars: int = 8000,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        root = _workspace_root(context)
        file_path = _resolve_path(root, path)
        _validate_readable_file(file_path, root)
        if start_line < 1:
            raise ValueError("start_line must be at least 1")
        if end_line is not None and end_line < start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        raw = file_path.read_bytes()
        text = _decode_text(raw, path)
        lines = text.splitlines(keepends=True)
        requested_end = end_line or len(lines)
        selected = "".join(lines[start_line - 1 : requested_end])
        content = selected[: max(1, max_chars)]
        actual_end = min(requested_end, len(lines))
        return {
            "path": _relative_to(file_path, root),
            "start_line": start_line,
            "end_line": actual_end,
            "content": content,
            "chars": len(content),
            "content_hash": hashlib.sha256(raw).hexdigest(),
            "truncated": len(selected) > len(content) or requested_end < len(lines),
        }

    def search_code(
        self,
        query: str,
        path: str = "",
        max_results: int = 20,
        context_lines: int = 0,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        root = _workspace_root(context)
        base_path = _resolve_path(root, path or ".")
        if not base_path.exists():
            raise ValueError(f"path does not exist: {path or '.'}")
        terms = _query_terms(query)
        if not terms:
            raise ValueError("query did not contain searchable terms")
        limit = max(1, max_results)
        matches = _search_with_rg(
            root=root,
            base_path=base_path,
            terms=terms,
            max_results=limit,
            context_lines=max(0, context_lines),
        )
        engine = "rg"
        if matches is None:
            matches = _search_with_python(
                root=root,
                base_path=base_path,
                terms=terms,
                max_results=limit,
                context_lines=max(0, context_lines),
            )
            engine = "python"
        return {
            "query": query,
            "path": path or ".",
            "terms": terms,
            "matches": matches,
            "count": len(matches),
            "truncated": len(matches) >= limit,
            "engine": engine,
        }


def register_repository_tools(registry: ToolRegistry) -> None:
    toolkit = RepositoryToolKit()
    shared = {
        "provider": "local",
        "permission_level": "read_only",
        "accepts_context": True,
    }
    registry.register(
        "repo.list_files",
        toolkit.list_files,
        description="List files under the current run's registered workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_results": {"type": "integer"},
            },
        },
        risk_summary="Lists paths inside the captured workspace root.",
        **shared,
    )
    registry.register(
        "repo.find_files",
        toolkit.find_files,
        description="Find files by filename or relative path fragment.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "max_results": {"type": "integer"},
            },
        },
        risk_summary="Finds paths inside the captured workspace root.",
        **shared,
    )
    registry.register(
        "repo.read_file",
        toolkit.read_file,
        description="Read a UTF-8 line range from a workspace file.",
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "max_chars": {"type": "integer"},
            },
        },
        risk_summary="Reads a non-sensitive UTF-8 file inside the workspace.",
        max_output_chars=12000,
        **shared,
    )
    registry.register(
        "repo.search_code",
        toolkit.search_code,
        description="Search live workspace text using ripgrep with a Python fallback.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "max_results": {"type": "integer"},
                "context_lines": {"type": "integer"},
            },
        },
        risk_summary="Searches non-sensitive text files inside the workspace.",
        max_output_chars=12000,
        **shared,
    )


def _workspace_root(context: ToolExecutionContext | None) -> Path:
    if context is None or not context.workspace_root:
        raise ValueError("workspace context is required")
    root = Path(context.workspace_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("workspace_unavailable: captured workspace root is inaccessible")
    return root


def _resolve_path(root: Path, path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        raise ValueError("workspace paths must be relative")
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path escapes workspace root: {path}")
    return resolved


def _iter_files(base_path: Path) -> Iterable[Path]:
    if base_path.is_file():
        if not _is_ignored(base_path):
            yield base_path
        return
    for current_root, directory_names, filenames in os.walk(base_path):
        directory_names[:] = sorted(
            name for name in directory_names if name not in IGNORED_DIRECTORIES
        )
        current = Path(current_root)
        for filename in sorted(filenames):
            candidate = current / filename
            if not _is_ignored(candidate):
                yield candidate


def _validate_readable_file(path: Path, root: Path) -> None:
    if not path.exists() or not path.is_file():
        raise ValueError(f"file does not exist: {_relative_to(path, root)}")
    if _is_sensitive(path):
        raise ValueError(f"sensitive file is not readable: {_relative_to(path, root)}")
    if path.stat().st_size > MAX_TEXT_FILE_BYTES:
        raise ValueError(f"file exceeds {MAX_TEXT_FILE_BYTES} bytes")


def _decode_text(raw: bytes, path: str) -> str:
    if b"\x00" in raw[:8192]:
        raise ValueError(f"binary file is not readable: {path}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"file is not valid UTF-8 text: {path}") from exc


def _search_with_rg(
    *,
    root: Path,
    base_path: Path,
    terms: list[str],
    max_results: int,
    context_lines: int,
) -> list[dict[str, Any]] | None:
    executable = shutil.which("rg")
    if executable is None:
        return None
    pattern = "|".join(re.escape(term) for term in terms)
    command = [
        executable,
        "--json",
        "--ignore-case",
        "--line-number",
        "--max-filesize",
        str(MAX_TEXT_FILE_BYTES),
        "--glob",
        "!.env",
        "--glob",
        "!*.pem",
        "--glob",
        "!*.key",
        pattern,
        str(base_path),
    ]
    result = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        return None
    matches: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event["data"]
        file_path = Path(data["path"]["text"]).resolve()
        if _is_sensitive(file_path):
            continue
        line_number = int(data["line_number"])
        text = data["lines"]["text"].rstrip("\r\n")
        matches.append(
            {
                "path": _relative_to(file_path, root),
                "line": line_number,
                "text": text,
                "start_line": max(1, line_number - context_lines),
                "end_line": line_number + context_lines,
            }
        )
        if len(matches) >= max_results:
            break
    return matches


def _search_with_python(
    *,
    root: Path,
    base_path: Path,
    terms: list[str],
    max_results: int,
    context_lines: int,
) -> list[dict[str, Any]]:
    lowered_terms = [term.lower() for term in terms]
    matches: list[dict[str, Any]] = []
    for candidate in _iter_files(base_path):
        if _is_sensitive(candidate) or candidate.stat().st_size > MAX_TEXT_FILE_BYTES:
            continue
        try:
            lines = _decode_text(candidate.read_bytes(), str(candidate)).splitlines()
        except ValueError:
            continue
        for index, line in enumerate(lines):
            if not any(term in line.lower() for term in lowered_terms):
                continue
            matches.append(
                {
                    "path": _relative_to(candidate.resolve(), root),
                    "line": index + 1,
                    "text": line,
                    "start_line": max(1, index + 1 - context_lines),
                    "end_line": min(len(lines), index + 1 + context_lines),
                }
            )
            if len(matches) >= max_results:
                return matches
    return matches


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[A-Za-z_][A-Za-z0-9_./-]{1,}|[\u4e00-\u9fff]{2,}", query)
    cleaned: list[str] = []
    seen: set[str] = set()
    for term in terms:
        normalized = term.strip("`'\".,:;()[]{}").lower()
        if not normalized or normalized in seen:
            continue
        if normalized in {"def", "class", "return", "the", "this", "that"}:
            continue
        seen.add(normalized)
        cleaned.append(term.strip("`'\".,:;()[]{}"))
    return cleaned[:8]


def _is_ignored(path: Path) -> bool:
    return any(part in IGNORED_DIRECTORIES for part in path.parts)


def _is_sensitive(path: Path) -> bool:
    lowered = path.name.lower()
    safe_env_examples = {".env.example", ".env.sample", ".env.template"}
    return (
        lowered in SENSITIVE_FILENAMES
        or path.suffix.lower() in SENSITIVE_SUFFIXES
        or (
            lowered.startswith(".env.")
            and lowered not in safe_env_examples
        )
    )


def _relative_to(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root).as_posix()
