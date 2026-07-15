from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
import hashlib
from pathlib import Path
from typing import Protocol

from ai_agent_platform.domain import RepositoryFileRecord, RepositoryIndexJobRecord
from ai_agent_platform.integrations.rag import (
    RAGConfigurationError,
    RAGProviderError,
    RAGService,
    RAGValidationError,
    SUPPORTED_TEXT_EXTENSIONS,
)


DEFAULT_EXCLUDED_DIRS = {
    ".chroma",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}

DEFAULT_EXCLUDE_PATTERNS = [
    ".chroma/**",
    ".git/**",
    ".venv/**",
    "__pycache__/**",
    "node_modules/**",
]


class RepositoryIndexStore(Protocol):
    def create_index_job(
        self,
        *,
        repository_id: str,
        root_path: str,
        include_patterns: list[str],
        exclude_patterns: list[str],
        max_file_size: int,
    ) -> RepositoryIndexJobRecord:
        ...

    def update_index_job(
        self,
        *,
        job_id: str,
        status: str,
        scanned_files: int,
        indexed_files: int,
        skipped_files: int,
        failed_files: int,
        error: str | None = None,
    ) -> RepositoryIndexJobRecord:
        ...

    def get_file(
        self,
        *,
        repository_id: str,
        path: str,
    ) -> RepositoryFileRecord | None:
        ...

    def upsert_file(
        self,
        *,
        repository_id: str,
        path: str,
        content_hash: str,
        size_bytes: int,
        document_id: str | None,
        indexed_at: datetime | None = None,
        skipped_reason: str | None = None,
    ) -> RepositoryFileRecord:
        ...


@dataclass(frozen=True)
class RepositoryIndexResult:
    job: RepositoryIndexJobRecord
    indexed_files: list[str]
    skipped_files: list[str]
    failed_files: list[str]


class RepositoryIndexingService:
    def __init__(
        self,
        *,
        rag_service: RAGService,
        index_store: RepositoryIndexStore,
    ) -> None:
        self._rag_service = rag_service
        self._index_store = index_store

    def index_repository(
        self,
        *,
        repository_id: str,
        root_path: str,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_file_size: int = 200_000,
    ) -> RepositoryIndexResult:
        root = Path(root_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise RepositoryIndexingError("root_path must be an existing directory")

        normalized_include_patterns = list(include_patterns or [])
        normalized_exclude_patterns = _combined_exclude_patterns(exclude_patterns or [])
        job = self._index_store.create_index_job(
            repository_id=repository_id,
            root_path=str(root),
            include_patterns=normalized_include_patterns,
            exclude_patterns=normalized_exclude_patterns,
            max_file_size=max_file_size,
        )
        job = self._index_store.update_index_job(
            job_id=job.id,
            status="running",
            scanned_files=0,
            indexed_files=0,
            skipped_files=0,
            failed_files=0,
        )

        scanned_count = 0
        indexed_paths: list[str] = []
        skipped_paths: list[str] = []
        failed_paths: list[str] = []
        fatal_error: str | None = None

        try:
            for file_path in _iter_candidate_files(
                root=root,
                include_patterns=normalized_include_patterns,
                exclude_patterns=normalized_exclude_patterns,
                max_file_size=max_file_size,
            ):
                scanned_count += 1
                relative_path = _relative_posix_path(root=root, path=file_path)
                try:
                    file_bytes = file_path.read_bytes()
                    content_hash = hashlib.sha256(file_bytes).hexdigest()
                    existing = self._index_store.get_file(
                        repository_id=repository_id,
                        path=relative_path,
                    )
                    if existing is not None and existing.content_hash == content_hash:
                        skipped_paths.append(relative_path)
                        continue

                    content = file_bytes.decode("utf-8")
                    ingested = self._rag_service.ingest_document(
                        knowledge_base_id=repository_id,
                        filename=relative_path,
                        content=content,
                        source_uri=file_path.as_uri(),
                    )
                    self._index_store.upsert_file(
                        repository_id=repository_id,
                        path=relative_path,
                        content_hash=content_hash,
                        size_bytes=len(file_bytes),
                        document_id=ingested.document_id,
                        indexed_at=_now(),
                    )
                    indexed_paths.append(relative_path)
                except UnicodeDecodeError:
                    skipped_paths.append(relative_path)
                    self._index_store.upsert_file(
                        repository_id=repository_id,
                        path=relative_path,
                        content_hash=_file_hash(file_path),
                        size_bytes=file_path.stat().st_size,
                        document_id=None,
                        skipped_reason="not_utf8_text",
                    )
                except (OSError, RAGValidationError, RAGConfigurationError, RAGProviderError):
                    failed_paths.append(relative_path)
        except Exception as exc:
            fatal_error = str(exc)

        final_status = "failed" if fatal_error else "completed"
        job = self._index_store.update_index_job(
            job_id=job.id,
            status=final_status,
            scanned_files=scanned_count,
            indexed_files=len(indexed_paths),
            skipped_files=len(skipped_paths),
            failed_files=len(failed_paths),
            error=fatal_error,
        )
        return RepositoryIndexResult(
            job=job,
            indexed_files=indexed_paths,
            skipped_files=skipped_paths,
            failed_files=failed_paths,
        )


class RepositoryIndexingError(Exception):
    pass


def _iter_candidate_files(
    *,
    root: Path,
    include_patterns: list[str],
    exclude_patterns: list[str],
    max_file_size: int,
) -> list[Path]:
    candidates: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative_path = _relative_posix_path(root=root, path=path)
        if _is_excluded(relative_path, path, exclude_patterns):
            continue
        if include_patterns and not _matches_any(relative_path, include_patterns):
            continue
        if path.suffix.lower() not in SUPPORTED_TEXT_EXTENSIONS:
            continue
        if path.stat().st_size > max_file_size:
            continue
        candidates.append(path)
    return sorted(candidates)


def _is_excluded(relative_path: str, path: Path, exclude_patterns: list[str]) -> bool:
    if any(part in DEFAULT_EXCLUDED_DIRS for part in path.parts):
        return True
    return _matches_any(relative_path, exclude_patterns)


def _matches_any(relative_path: str, patterns: list[str]) -> bool:
    return any(_matches_pattern(relative_path, pattern) for pattern in patterns)


def _matches_pattern(relative_path: str, pattern: str) -> bool:
    normalized = pattern.strip().replace("\\", "/")
    if not normalized:
        return False
    if fnmatch(relative_path, normalized):
        return True
    if normalized.startswith("**/") and fnmatch(relative_path, normalized[3:]):
        return True
    if normalized.endswith("/**"):
        prefix = normalized[:-3].rstrip("/")
        return relative_path == prefix or relative_path.startswith(f"{prefix}/")
    return False


def _combined_exclude_patterns(exclude_patterns: list[str]) -> list[str]:
    combined = list(DEFAULT_EXCLUDE_PATTERNS)
    for pattern in exclude_patterns:
        if pattern not in combined:
            combined.append(pattern)
    return combined


def _relative_posix_path(*, root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)
