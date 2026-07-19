from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from ai_agent_platform.agents.coding.models import (
    AgentRunMetrics,
    AgentRunRecord,
    AgentRunResult,
)
from ai_agent_platform.domain import (
    Message,
    RepositoryFileRecord,
    RepositoryIndexJobRecord,
    RepositoryIndexJobStatus,
    RepositoryRecord,
    Session,
    TokenUsageRecord,
)
from ai_agent_platform.integrations.rag import DocumentChunk, ParsedDocument, RetrievedDocument
from ai_agent_platform.integrations.tools import ToolCall
from ai_agent_platform.repositories.memory import SessionNotFoundError


class PostgresDependencyError(RuntimeError):
    pass


class PostgresSessionRepository:
    """PostgreSQL-backed source of truth for sessions, messages, and token usage."""

    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        _require_psycopg()

    def create_session(self, user_id: str) -> Session:
        session = Session(
            id=f"sess_{uuid4().hex[:12]}",
            user_id=user_id,
            created_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, user_id, created_at)
                VALUES (%s, %s, %s)
                """,
                (session.id, session.user_id, session.created_at),
            )
        return session

    def list_sessions(self) -> list[Session]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, created_at
                FROM sessions
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [_session_from_row(row) for row in rows]

    def get_session(self, session_id: str) -> Session:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, created_at
                FROM sessions
                WHERE id = %s
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)
        return _session_from_row(row)

    def add_message(self, session_id: str, role: str, content: str) -> Message:
        self.get_session(session_id)
        message = Message(
            id=f"msg_{uuid4().hex[:12]}",
            session_id=session_id,
            role=role,
            content=content,
            created_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (id, session_id, role, content, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    message.id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.created_at,
                ),
            )
        return message

    def list_messages(self, session_id: str) -> list[Message]:
        self.get_session(session_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, role, content, created_at
                FROM messages
                WHERE session_id = %s
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            ).fetchall()
        return [_message_from_row(row) for row in rows]

    def add_token_usage(
        self,
        session_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> TokenUsageRecord:
        self.get_session(session_id)
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
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO token_usage_records (
                    id,
                    session_id,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.id,
                    record.session_id,
                    record.provider,
                    record.model,
                    record.input_tokens,
                    record.output_tokens,
                    record.total_tokens,
                    record.created_at,
                ),
            )
        return record

    def list_token_usage(self, session_id: str) -> list[TokenUsageRecord]:
        self.get_session(session_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    session_id,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    created_at
                FROM token_usage_records
                WHERE session_id = %s
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            ).fetchall()
        return [_token_usage_from_row(row) for row in rows]

    def _connect(self):
        psycopg = _require_psycopg()
        return psycopg.connect(self._database_url)


class PostgresAgentRunRepository:
    """PostgreSQL-backed product-level state for agent runs."""

    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        _require_psycopg()

    def save(self, record: AgentRunRecord) -> None:
        Jsonb = _require_jsonb()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs (
                    id,
                    thread_id,
                    conversation_id,
                    repository_id,
                    status,
                    checkpoint_id,
                    latest_node,
                    next_nodes,
                    trace,
                    result,
                    error,
                    pending_approval,
                    errors,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (id) DO UPDATE SET
                    thread_id = EXCLUDED.thread_id,
                    conversation_id = EXCLUDED.conversation_id,
                    repository_id = EXCLUDED.repository_id,
                    status = EXCLUDED.status,
                    checkpoint_id = EXCLUDED.checkpoint_id,
                    latest_node = EXCLUDED.latest_node,
                    next_nodes = EXCLUDED.next_nodes,
                    trace = EXCLUDED.trace,
                    result = EXCLUDED.result,
                    error = EXCLUDED.error,
                    pending_approval = EXCLUDED.pending_approval,
                    errors = EXCLUDED.errors,
                    updated_at = NOW()
                """,
                (
                    record.run_id,
                    record.thread_id,
                    record.conversation_id,
                    record.repository_id,
                    record.status,
                    record.checkpoint_id,
                    record.latest_node,
                    Jsonb(record.next_nodes),
                    Jsonb(record.trace),
                    Jsonb(_agent_result_to_json(record.result)),
                    record.error,
                    Jsonb(record.pending_approval),
                    Jsonb(record.errors),
                ),
            )

    def get(self, run_id: str) -> AgentRunRecord:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    thread_id,
                    conversation_id,
                    repository_id,
                    status,
                    checkpoint_id,
                    latest_node,
                    next_nodes,
                    trace,
                    result,
                    error,
                    pending_approval,
                    errors
                FROM agent_runs
                WHERE id = %s
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _agent_run_from_row(row)

    def _connect(self):
        psycopg = _require_psycopg()
        return psycopg.connect(self._database_url)


class PostgresDocumentRepository:
    """PostgreSQL source of truth for ingested documents and chunk metadata."""

    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        _require_psycopg()

    def save_document(
        self,
        document: ParsedDocument,
        chunks: list[DocumentChunk],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (
                    id,
                    knowledge_base_id,
                    filename,
                    source_uri,
                    content_hash,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    knowledge_base_id = EXCLUDED.knowledge_base_id,
                    filename = EXCLUDED.filename,
                    source_uri = EXCLUDED.source_uri,
                    content_hash = EXCLUDED.content_hash
                """,
                (
                    document.id,
                    document.knowledge_base_id,
                    document.filename,
                    document.source_uri,
                    _sha256_text(document.text),
                ),
            )
            conn.execute(
                """
                DELETE FROM document_chunks
                WHERE document_id = %s
                """,
                (document.id,),
            )
            for chunk in chunks:
                conn.execute(
                    """
                    INSERT INTO document_chunks (
                        id,
                        document_id,
                        knowledge_base_id,
                        filename,
                        chunk_index,
                        text,
                        qdrant_point_id,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    """,
                    (
                        chunk.id,
                        chunk.document_id,
                        chunk.knowledge_base_id,
                        chunk.filename,
                        chunk.chunk_index,
                        chunk.text,
                        _qdrant_point_id(chunk.id),
                    ),
                )

    def _connect(self):
        psycopg = _require_psycopg()
        return psycopg.connect(self._database_url)


class PostgresRepositoryIndexRepository:
    """PostgreSQL source of truth for repository indexing metadata."""

    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        _require_psycopg()

    def upsert_repository(self, *, repository_id: str, root_path: str) -> RepositoryRecord:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO repositories (id, root_path, created_at, updated_at)
                VALUES (%s, %s, NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET
                    root_path = EXCLUDED.root_path,
                    updated_at = NOW()
                RETURNING id, root_path, created_at, updated_at, last_indexed_at
                """,
                (repository_id, root_path),
            ).fetchone()
        return _repository_from_row(row)

    def get_repository(self, repository_id: str) -> RepositoryRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, root_path, created_at, updated_at, last_indexed_at
                FROM repositories
                WHERE id = %s
                """,
                (repository_id,),
            ).fetchone()
        return _repository_from_row(row) if row is not None else None

    def create_index_job(
        self,
        *,
        repository_id: str,
        root_path: str,
        include_patterns: list[str],
        exclude_patterns: list[str],
        max_file_size: int,
    ) -> RepositoryIndexJobRecord:
        Jsonb = _require_jsonb()
        job_id = f"idxjob_{uuid4().hex[:12]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO repositories (id, root_path, created_at, updated_at)
                VALUES (%s, %s, NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET
                    root_path = EXCLUDED.root_path,
                    updated_at = NOW()
                """,
                (repository_id, root_path),
            )
            row = conn.execute(
                """
                INSERT INTO repository_index_jobs (
                    id,
                    repository_id,
                    root_path,
                    include_patterns,
                    exclude_patterns,
                    max_file_size,
                    status,
                    scanned_files,
                    indexed_files,
                    skipped_files,
                    failed_files,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', 0, 0, 0, 0, NOW(), NOW())
                RETURNING
                    id,
                    repository_id,
                    root_path,
                    include_patterns,
                    exclude_patterns,
                    max_file_size,
                    status,
                    scanned_files,
                    indexed_files,
                    skipped_files,
                    failed_files,
                    error,
                    created_at,
                    updated_at,
                    completed_at
                """,
                (
                    job_id,
                    repository_id,
                    root_path,
                    Jsonb(include_patterns),
                    Jsonb(exclude_patterns),
                    max_file_size,
                ),
            ).fetchone()
        return _repository_index_job_from_row(row)

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
        completed_statuses = {"completed", "completed_with_errors", "failed"}
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE repository_index_jobs
                SET
                    status = %s,
                    scanned_files = %s,
                    indexed_files = %s,
                    skipped_files = %s,
                    failed_files = %s,
                    error = %s,
                    updated_at = NOW(),
                    completed_at = CASE
                        WHEN %s THEN COALESCE(completed_at, NOW())
                        ELSE completed_at
                    END
                WHERE id = %s
                RETURNING
                    id,
                    repository_id,
                    root_path,
                    include_patterns,
                    exclude_patterns,
                    max_file_size,
                    status,
                    scanned_files,
                    indexed_files,
                    skipped_files,
                    failed_files,
                    error,
                    created_at,
                    updated_at,
                    completed_at
                """,
                (
                    status,
                    scanned_files,
                    indexed_files,
                    skipped_files,
                    failed_files,
                    error,
                    status in completed_statuses,
                    job_id,
                ),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            record = _repository_index_job_from_row(row)
            if status == "completed":
                conn.execute(
                    """
                    UPDATE repositories
                    SET last_indexed_at = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (record.completed_at or _now(), record.repository_id),
                )
        return record

    def get_index_job(self, job_id: str) -> RepositoryIndexJobRecord:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    repository_id,
                    root_path,
                    include_patterns,
                    exclude_patterns,
                    max_file_size,
                    status,
                    scanned_files,
                    indexed_files,
                    skipped_files,
                    failed_files,
                    error,
                    created_at,
                    updated_at,
                    completed_at
                FROM repository_index_jobs
                WHERE id = %s
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _repository_index_job_from_row(row)

    def get_file(
        self,
        *,
        repository_id: str,
        path: str,
    ) -> RepositoryFileRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    repository_id,
                    path,
                    content_hash,
                    size_bytes,
                    document_id,
                    indexed_at,
                    skipped_reason,
                    created_at,
                    updated_at
                FROM repository_files
                WHERE repository_id = %s AND path = %s
                """,
                (repository_id, path),
            ).fetchone()
        return _repository_file_from_row(row) if row is not None else None

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
        file_id = _repository_file_id(repository_id=repository_id, path=path)
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO repository_files (
                    id,
                    repository_id,
                    path,
                    content_hash,
                    size_bytes,
                    document_id,
                    indexed_at,
                    skipped_reason,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (repository_id, path) DO UPDATE SET
                    content_hash = EXCLUDED.content_hash,
                    size_bytes = EXCLUDED.size_bytes,
                    document_id = EXCLUDED.document_id,
                    indexed_at = EXCLUDED.indexed_at,
                    skipped_reason = EXCLUDED.skipped_reason,
                    updated_at = NOW()
                RETURNING
                    id,
                    repository_id,
                    path,
                    content_hash,
                    size_bytes,
                    document_id,
                    indexed_at,
                    skipped_reason,
                    created_at,
                    updated_at
                """,
                (
                    file_id,
                    repository_id,
                    path,
                    content_hash,
                    size_bytes,
                    document_id,
                    indexed_at,
                    skipped_reason,
                ),
            ).fetchone()
        return _repository_file_from_row(row)

    def list_files(self, repository_id: str) -> list[RepositoryFileRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    repository_id,
                    path,
                    content_hash,
                    size_bytes,
                    document_id,
                    indexed_at,
                    skipped_reason,
                    created_at,
                    updated_at
                FROM repository_files
                WHERE repository_id = %s
                ORDER BY path ASC
                """,
                (repository_id,),
            ).fetchall()
        return [_repository_file_from_row(row) for row in rows]

    def _connect(self):
        psycopg = _require_psycopg()
        return psycopg.connect(self._database_url)


def _require_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise PostgresDependencyError(
            "psycopg is required for PostgreSQL storage; "
            "install project dependencies with pip install -r requirements.txt"
        ) from exc
    return psycopg


def _require_jsonb():
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise PostgresDependencyError(
            "psycopg JSON support is required for PostgreSQL storage"
        ) from exc
    return Jsonb


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _qdrant_point_id(chunk_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, chunk_id))


def _repository_file_id(*, repository_id: str, path: str) -> str:
    return f"repofile_{uuid5(NAMESPACE_URL, f'{repository_id}:{path}').hex[:16]}"


def _session_from_row(row: tuple[Any, ...]) -> Session:
    return Session(id=str(row[0]), user_id=str(row[1]), created_at=row[2])


def _message_from_row(row: tuple[Any, ...]) -> Message:
    return Message(
        id=str(row[0]),
        session_id=str(row[1]),
        role=str(row[2]),
        content=str(row[3]),
        created_at=row[4],
    )


def _token_usage_from_row(row: tuple[Any, ...]) -> TokenUsageRecord:
    return TokenUsageRecord(
        id=str(row[0]),
        session_id=str(row[1]),
        provider=str(row[2]),
        model=str(row[3]),
        input_tokens=int(row[4]),
        output_tokens=int(row[5]),
        total_tokens=int(row[6]),
        created_at=row[7],
    )


def _agent_result_to_json(result: AgentRunResult | None) -> dict[str, Any] | None:
    return asdict(result) if result is not None else None


def _agent_result_from_json(data: dict[str, Any] | None) -> AgentRunResult | None:
    if data is None:
        return None
    payload = dict(data)
    payload["rag_context"] = [
        RetrievedDocument(**item) for item in payload.get("rag_context", [])
    ]
    payload["tool_calls"] = [
        ToolCall(**item) for item in payload.get("tool_calls", [])
    ]
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        payload["metrics"] = AgentRunMetrics(**metrics)
    payload.setdefault("errors", [])
    return AgentRunResult(**payload)


def _agent_run_from_row(row: tuple[Any, ...]) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=str(row[0]),
        thread_id=str(row[1]),
        conversation_id=str(row[2]),
        repository_id=str(row[3]),
        status=row[4],
        checkpoint_id=row[5],
        latest_node=row[6],
        next_nodes=list(row[7] or []),
        trace=list(row[8] or []),
        result=_agent_result_from_json(row[9]),
        error=row[10],
        pending_approval=row[11],
        errors=list(row[12] or []),
    )


def _repository_from_row(row: tuple[Any, ...]) -> RepositoryRecord:
    return RepositoryRecord(
        id=str(row[0]),
        root_path=str(row[1]),
        created_at=row[2],
        updated_at=row[3],
        last_indexed_at=row[4],
    )


def _repository_index_job_from_row(row: tuple[Any, ...]) -> RepositoryIndexJobRecord:
    return RepositoryIndexJobRecord(
        id=str(row[0]),
        repository_id=str(row[1]),
        root_path=str(row[2]),
        include_patterns=list(row[3] or []),
        exclude_patterns=list(row[4] or []),
        max_file_size=int(row[5]),
        status=row[6],
        scanned_files=int(row[7]),
        indexed_files=int(row[8]),
        skipped_files=int(row[9]),
        failed_files=int(row[10]),
        error=row[11],
        created_at=row[12],
        updated_at=row[13],
        completed_at=row[14],
    )


def _repository_file_from_row(row: tuple[Any, ...]) -> RepositoryFileRecord:
    return RepositoryFileRecord(
        id=str(row[0]),
        repository_id=str(row[1]),
        path=str(row[2]),
        content_hash=str(row[3]),
        size_bytes=int(row[4]),
        document_id=row[5],
        indexed_at=row[6],
        skipped_reason=row[7],
        created_at=row[8],
        updated_at=row[9],
    )
