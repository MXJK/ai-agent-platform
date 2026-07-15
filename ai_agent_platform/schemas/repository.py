from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ai_agent_platform.services.repository_indexing_service import (
    RepositoryIndexResult,
)


class RepositoryIndexRequest(BaseModel):
    root_path: str = Field(min_length=1, max_length=2000)
    include_patterns: list[str] = Field(default_factory=list, max_length=100)
    exclude_patterns: list[str] = Field(default_factory=list, max_length=100)
    max_file_size: int = Field(default=200_000, ge=1, le=5_000_000)


class RepositoryIndexResponse(BaseModel):
    job_id: str
    repository_id: str
    root_path: str
    status: str
    scanned_files: int
    indexed_files: int
    skipped_files: int
    failed_files: int
    error: Optional[str] = None
    indexed_paths: list[str]
    skipped_paths: list[str]
    failed_paths: list[str]

    @classmethod
    def from_domain(cls, result: RepositoryIndexResult) -> "RepositoryIndexResponse":
        return cls(
            job_id=result.job.id,
            repository_id=result.job.repository_id,
            root_path=result.job.root_path,
            status=result.job.status,
            scanned_files=result.job.scanned_files,
            indexed_files=result.job.indexed_files,
            skipped_files=result.job.skipped_files,
            failed_files=result.job.failed_files,
            error=result.job.error,
            indexed_paths=result.indexed_files,
            skipped_paths=result.skipped_files,
            failed_paths=result.failed_files,
        )
