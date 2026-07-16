from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from threading import Lock
from uuid import NAMESPACE_URL, uuid4, uuid5

from ai_agent_platform.domain import (
    Message,
    RepositoryFileRecord,
    RepositoryIndexJobRecord,
    RepositoryIndexJobStatus,
    RepositoryRecord,
    Session,
    TokenUsageRecord,
)


class SessionNotFoundError(Exception):
    pass


class InMemorySessionRepository:
    """Stores sessions and messages in process memory for the first backend version."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._messages: dict[str, list[Message]] = defaultdict(list)
        self._token_usage: dict[str, list[TokenUsageRecord]] = defaultdict(list)
        self._lock = Lock()

    def create_session(self, user_id: str) -> Session:
        with self._lock:
            session = Session(
                id=f"sess_{uuid4().hex[:12]}",
                user_id=user_id,
                created_at=_now(),
            )
            self._sessions[session.id] = session
            return session

    def list_sessions(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())

    def get_session(self, session_id: str) -> Session:
        with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError as exc:
                raise SessionNotFoundError(session_id) from exc

    def add_message(self, session_id: str, role: str, content: str) -> Message:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)

            message = Message(
                id=f"msg_{uuid4().hex[:12]}",
                session_id=session_id,
                role=role,
                content=content,
                created_at=_now(),
            )
            self._messages[session_id].append(message)
            return message

    def list_messages(self, session_id: str) -> list[Message]:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)
            return list(self._messages[session_id])

    def add_token_usage(
        self,
        session_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> TokenUsageRecord:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)

            record = TokenUsageRecord(
                id=f"usage_{uuid4().hex[:12]}",
                session_id=session_id,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                created_at=_now(),
            )
            self._token_usage[session_id].append(record)
            return record

    def list_token_usage(self, session_id: str) -> list[TokenUsageRecord]:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)
            return list(self._token_usage[session_id])


class InMemoryRepositoryIndexRepository:
    """Stores repository indexing metadata in process memory for local tests."""

    def __init__(self) -> None:
        self._repositories: dict[str, RepositoryRecord] = {}
        self._jobs: dict[str, RepositoryIndexJobRecord] = {}
        self._files: dict[tuple[str, str], RepositoryFileRecord] = {}
        self._lock = Lock()

    def upsert_repository(self, *, repository_id: str, root_path: str) -> RepositoryRecord:
        with self._lock:
            now = _now()
            existing = self._repositories.get(repository_id)
            record = RepositoryRecord(
                id=repository_id,
                root_path=root_path,
                created_at=existing.created_at if existing else now,
                updated_at=now,
                last_indexed_at=existing.last_indexed_at if existing else None,
            )
            self._repositories[repository_id] = record
            return record

    def get_repository(self, repository_id: str) -> RepositoryRecord | None:
        with self._lock:
            return self._repositories.get(repository_id)

    def create_index_job(
        self,
        *,
        repository_id: str,
        root_path: str,
        include_patterns: list[str],
        exclude_patterns: list[str],
        max_file_size: int,
    ) -> RepositoryIndexJobRecord:
        with self._lock:
            now = _now()
            existing = self._repositories.get(repository_id)
            self._repositories[repository_id] = RepositoryRecord(
                id=repository_id,
                root_path=root_path,
                created_at=existing.created_at if existing else now,
                updated_at=now,
                last_indexed_at=existing.last_indexed_at if existing else None,
            )
            record = RepositoryIndexJobRecord(
                id=f"idxjob_{uuid4().hex[:12]}",
                repository_id=repository_id,
                root_path=root_path,
                include_patterns=list(include_patterns),
                exclude_patterns=list(exclude_patterns),
                max_file_size=max_file_size,
                status="pending",
                scanned_files=0,
                indexed_files=0,
                skipped_files=0,
                failed_files=0,
                error=None,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            self._jobs[record.id] = record
            return record

    def update_index_job(
        self,
        *,
        job_id: str,
        status: RepositoryIndexJobStatus,
        scanned_files: int,
        indexed_files: int,
        skipped_files: int,
        failed_files: int,
        error: str | None = None,
    ) -> RepositoryIndexJobRecord:
        with self._lock:
            existing = self._jobs[job_id]
            now = _now()
            completed_at = existing.completed_at
            if (
                status in {"completed", "completed_with_errors", "failed"}
                and completed_at is None
            ):
                completed_at = now
            record = replace(
                existing,
                status=status,
                scanned_files=scanned_files,
                indexed_files=indexed_files,
                skipped_files=skipped_files,
                failed_files=failed_files,
                error=error,
                updated_at=now,
                completed_at=completed_at,
            )
            self._jobs[job_id] = record
            if status in {"completed", "completed_with_errors"}:
                repository = self._repositories.get(record.repository_id)
                if repository is not None:
                    self._repositories[record.repository_id] = replace(
                        repository,
                        updated_at=now,
                        last_indexed_at=completed_at or now,
                    )
            return record

    def get_index_job(self, job_id: str) -> RepositoryIndexJobRecord:
        with self._lock:
            return self._jobs[job_id]

    def get_file(
        self,
        *,
        repository_id: str,
        path: str,
    ) -> RepositoryFileRecord | None:
        with self._lock:
            return self._files.get((repository_id, path))

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
        with self._lock:
            now = _now()
            key = (repository_id, path)
            existing = self._files.get(key)
            record = RepositoryFileRecord(
                id=existing.id if existing else _repository_file_id(repository_id, path),
                repository_id=repository_id,
                path=path,
                content_hash=content_hash,
                size_bytes=size_bytes,
                document_id=document_id,
                indexed_at=indexed_at,
                skipped_reason=skipped_reason,
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
            self._files[key] = record
            return record

    def list_files(self, repository_id: str) -> list[RepositoryFileRecord]:
        with self._lock:
            return sorted(
                [
                    record
                    for record in self._files.values()
                    if record.repository_id == repository_id
                ],
                key=lambda record: record.path,
            )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _repository_file_id(repository_id: str, path: str) -> str:
    return f"repofile_{uuid5(NAMESPACE_URL, f'{repository_id}:{path}').hex[:16]}"
