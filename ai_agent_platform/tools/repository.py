from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from ai_agent_platform.integrations.tools import ToolRegistry


SUPPORTED_TEXT_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".ini",
    ".env",
    ".example",
}
IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}


class RepositoryToolKit:
    def __init__(self, root_path: Path) -> None:
        self._root_path = root_path.resolve()

    def list_files(self, path: str = "", max_results: int = 100) -> dict[str, Any]:
        base_path = self._resolve_path(path or ".")
        if not base_path.exists():
            raise ValueError(f"path does not exist: {path}")
        if not base_path.is_dir():
            raise ValueError(f"path is not a directory: {path}")

        files: list[str] = []
        for candidate in sorted(base_path.rglob("*")):
            if len(files) >= max_results:
                break
            if not candidate.is_file() or _is_ignored(candidate):
                continue
            files.append(_relative_to(candidate, self._root_path))
        return {
            "root": str(self._root_path),
            "path": path or ".",
            "files": files,
            "count": len(files),
            "truncated": len(files) >= max_results,
        }

    def read_file(self, path: str, max_chars: int = 4000) -> dict[str, Any]:
        file_path = self._resolve_path(path)
        if not file_path.exists():
            raise ValueError(f"file does not exist: {path}")
        if not file_path.is_file():
            raise ValueError(f"path is not a file: {path}")
        if not _is_supported_text_file(file_path):
            raise ValueError(f"unsupported text file type: {path}")

        content = file_path.read_text(encoding="utf-8")
        truncated = len(content) > max_chars
        return {
            "path": _relative_to(file_path, self._root_path),
            "content": content[:max_chars],
            "chars": min(len(content), max_chars),
            "truncated": truncated,
        }

    def search_code(
        self,
        query: str,
        path: str = "",
        max_results: int = 20,
        context_lines: int = 0,
    ) -> dict[str, Any]:
        base_path = self._resolve_path(path or ".")
        if not base_path.exists():
            raise ValueError(f"path does not exist: {path}")

        terms = _query_terms(query)
        if not terms:
            raise ValueError("query did not contain searchable terms")

        matches: list[dict[str, Any]] = []
        candidates = [base_path] if base_path.is_file() else sorted(base_path.rglob("*"))
        for candidate in candidates:
            if len(matches) >= max_results:
                break
            if (
                not candidate.is_file()
                or _is_ignored(candidate)
                or not _is_supported_text_file(candidate)
            ):
                continue
            file_matches = self._search_file(candidate, terms, context_lines)
            for match in file_matches:
                matches.append(match)
                if len(matches) >= max_results:
                    break

        return {
            "query": query,
            "path": path or ".",
            "terms": terms,
            "matches": matches,
            "count": len(matches),
            "truncated": len(matches) >= max_results,
        }

    def _search_file(
        self, file_path: Path, terms: list[str], context_lines: int
    ) -> list[dict[str, Any]]:
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return []

        matches: list[dict[str, Any]] = []
        lowered_terms = [term.lower() for term in terms]
        for index, line in enumerate(lines):
            lowered_line = line.lower()
            if not any(term in lowered_line for term in lowered_terms):
                continue
            start = max(index - context_lines, 0)
            end = min(index + context_lines + 1, len(lines))
            matches.append(
                {
                    "path": _relative_to(file_path, self._root_path),
                    "line": index + 1,
                    "text": line.strip(),
                    "context": [
                        {"line": line_index + 1, "text": lines[line_index]}
                        for line_index in range(start, end)
                    ],
                }
            )
        return matches

    def _resolve_path(self, path: str) -> Path:
        resolved = (self._root_path / path).resolve()
        if resolved != self._root_path and self._root_path not in resolved.parents:
            raise ValueError(f"path escapes repository root: {path}")
        return resolved


def register_repository_tools(
    registry: ToolRegistry, root_path: Path | str | None = None
) -> None:
    toolkit = RepositoryToolKit(Path(root_path or "."))
    registry.register(
        "repo.list_files",
        toolkit.list_files,
        description="List text-oriented files inside the configured repository root.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_results": {"type": "integer"},
            },
        },
        provider="local",
        permission_level="read_only",
        risk_summary="Lists repository file paths under the configured root only.",
    )
    registry.register(
        "repo.read_file",
        toolkit.read_file,
        description="Read a UTF-8 text file inside the configured repository root.",
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "max_chars": {"type": "integer"},
            },
        },
        provider="local",
        permission_level="read_only",
        risk_summary="Reads a UTF-8 text file under the configured repository root.",
        max_output_chars=12000,
    )
    registry.register(
        "repo.search_code",
        toolkit.search_code,
        description="Search repository text files by path, symbol, or keyword.",
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
        provider="local",
        permission_level="read_only",
        risk_summary="Searches text files under the configured repository root.",
        max_output_chars=12000,
    )


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[A-Za-z_][A-Za-z0-9_./-]{1,}|[\u4e00-\u9fff]{2,}", query)
    cleaned: list[str] = []
    for term in terms:
        normalized = term.strip("`'\".,:;()[]{}")
        if normalized and normalized.lower() not in {"def", "class", "return"}:
            cleaned.append(normalized)
    return _unique(cleaned)[:8]


def _is_supported_text_file(path: Path) -> bool:
    if path.name in {".env", ".env.example"}:
        return True
    return path.suffix in SUPPORTED_TEXT_EXTENSIONS


def _is_ignored(path: Path) -> bool:
    return any(part in IGNORED_DIRECTORIES for part in path.parts)


def _relative_to(path: Path, root_path: Path) -> str:
    return str(path.relative_to(root_path))


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_values.append(value)
    return unique_values
