from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from ai_agent_platform.agents.coding.models import (
    AgentChangeSummary,
    AgentRunMetrics,
    AgentRunRecord,
    AgentRunResult,
    ContextSource,
)
from ai_agent_platform.domain import (
    ConversationSummary,
    KnowledgeBaseRecord,
    Message,
    Session,
    TokenUsageRecord,
    UserPreferences,
    WorkspaceRecord,
)
from ai_agent_platform.integrations.rag import (
    DocumentChunk,
    IndexJob,
    ParsedDocument,
    RetrievedDocument,
)
from ai_agent_platform.integrations.tools import ToolCall
from ai_agent_platform.repositories.memory import (
    SessionArchivedError,
    SessionNotFoundError,
)


class PostgresDependencyError(RuntimeError):
    pass


class PostgresSessionRepository:
    """PostgreSQL-backed source of truth for sessions, messages, and token usage."""

    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        _require_psycopg()

    def create_session(
        self,
        user_id: str,
        preferences: UserPreferences | None = None,
    ) -> Session:
        now = _now()
        session = Session(
            id=f"sess_{uuid4().hex[:12]}",
            user_id=user_id,
            created_at=now,
            updated_at=now,
            workspace_id=(
                preferences.default_workspace_id if preferences else None
            ),
            provider=preferences.default_provider if preferences else None,
            model=preferences.default_model if preferences else None,
            thinking_level=(
                preferences.default_thinking_level if preferences else None
            ),
            composer_mode=(
                preferences.default_composer_mode if preferences else "chat"
            ),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, user_id, created_at, title, title_source, updated_at,
                    archived_at, workspace_id, provider, model,
                    thinking_level, composer_mode
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                _session_values(session),
            )
        return session

    def list_sessions(
        self,
        *,
        user_id: str | None = None,
        query: str | None = None,
        archived: bool | None = None,
        limit: int | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> list[Session]:
        conditions: list[str] = [
            "EXISTS (SELECT 1 FROM messages history_messages "
            "WHERE history_messages.session_id = sessions.id)"
        ]
        params: list[Any] = []
        if user_id is not None:
            conditions.append("sessions.user_id = %s")
            params.append(user_id)
        if archived is True:
            conditions.append("sessions.archived_at IS NOT NULL")
        elif archived is False:
            conditions.append("sessions.archived_at IS NULL")
        normalized_query = (query or "").strip()
        if normalized_query:
            conditions.append(
                """
                (
                    sessions.title ILIKE %s ESCAPE '\\'
                    OR EXISTS (
                        SELECT 1
                        FROM messages searched_messages
                        WHERE searched_messages.session_id = sessions.id
                          AND searched_messages.content ILIKE %s ESCAPE '\\'
                    )
                )
                """
            )
            pattern = f"%{_escape_like(normalized_query)}%"
            params.extend((pattern, pattern))
        if before is not None:
            conditions.append("(sessions.updated_at, sessions.id) < (%s, %s)")
            params.extend(before)
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_sql = ""
        if limit is not None:
            limit_sql = "LIMIT %s"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {_session_select_columns()}
                FROM sessions
                {where_sql}
                ORDER BY sessions.updated_at DESC, sessions.id DESC
                {limit_sql}
                """,
                tuple(params),
            ).fetchall()
        return [_session_from_row(row) for row in rows]

    def get_session(self, session_id: str) -> Session:
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT {_session_select_columns()}
                FROM sessions
                WHERE sessions.id = %s
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)
        return _session_from_row(row)

    def save_session(
        self,
        session: Session,
        *,
        preferences: UserPreferences | None = None,
    ) -> Session:
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE sessions
                SET title = %s,
                    title_source = %s,
                    updated_at = %s,
                    archived_at = %s,
                    workspace_id = %s,
                    provider = %s,
                    model = %s,
                    thinking_level = %s,
                    composer_mode = %s
                WHERE id = %s
                RETURNING id
                """,
                (
                    session.title,
                    session.title_source,
                    session.updated_at or session.created_at,
                    session.archived_at,
                    session.workspace_id,
                    session.provider,
                    session.model,
                    session.thinking_level,
                    session.composer_mode,
                    session.id,
                ),
            ).fetchone()
            if row is None:
                raise SessionNotFoundError(session.id)
            if preferences is not None:
                self._save_user_preferences(conn, preferences)
        return session

    def get_user_preferences(self, user_id: str) -> UserPreferences | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, default_provider, default_model,
                       default_thinking_level, default_workspace_id,
                       default_composer_mode, last_active_session_id, updated_at
                FROM user_preferences
                WHERE user_id = %s
                """,
                (user_id,),
            ).fetchone()
        return _user_preferences_from_row(row) if row is not None else None

    def save_user_preferences(
        self, preferences: UserPreferences
    ) -> UserPreferences:
        with self._connect() as conn:
            self._save_user_preferences(conn, preferences)
        return preferences

    def _save_user_preferences(self, conn, preferences: UserPreferences) -> None:
        conn.execute(
            """
            INSERT INTO user_preferences (
                user_id, default_provider, default_model,
                default_thinking_level, default_workspace_id,
                default_composer_mode, last_active_session_id, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                default_provider = EXCLUDED.default_provider,
                default_model = EXCLUDED.default_model,
                default_thinking_level = EXCLUDED.default_thinking_level,
                default_workspace_id = EXCLUDED.default_workspace_id,
                default_composer_mode = EXCLUDED.default_composer_mode,
                last_active_session_id = EXCLUDED.last_active_session_id,
                updated_at = EXCLUDED.updated_at
            """,
            _user_preferences_values(preferences),
        )

    def add_message(self, session_id: str, role: str, content: str) -> Message:
        now = _now()
        message = Message(
            id=f"msg_{uuid4().hex[:12]}",
            session_id=session_id,
            role=role,
            content=content,
            created_at=now,
        )
        with self._connect() as conn:
            session_row = conn.execute(
                """
                SELECT id, user_id, created_at, title, title_source, updated_at,
                       archived_at, workspace_id, provider, model,
                       thinking_level, composer_mode
                FROM sessions
                WHERE id = %s
                FOR UPDATE
                """,
                (session_id,),
            ).fetchone()
            if session_row is None:
                raise SessionNotFoundError(session_id)
            session = _session_from_row(session_row)
            if session.archived_at is not None:
                raise SessionArchivedError(session_id)
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
            title = session.title
            title_source = session.title_source
            if role == "user" and title_source == "default":
                title = _derive_session_title(content)
                title_source = "auto"
            conn.execute(
                """
                UPDATE sessions
                SET title = %s,
                    title_source = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (title, title_source, now, session_id),
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

    def get_conversation_summary(
        self, session_id: str
    ) -> ConversationSummary | None:
        self.get_session(session_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT session_id, content, summarized_message_count,
                       through_message_id, version, source_chars,
                       created_at, updated_at
                FROM conversation_summaries
                WHERE session_id = %s
                """,
                (session_id,),
            ).fetchone()
        return _conversation_summary_from_row(row) if row is not None else None

    def upsert_conversation_summary(
        self,
        summary: ConversationSummary,
        *,
        expected_version: int,
    ) -> ConversationSummary | None:
        self.get_session(summary.session_id)
        with self._connect() as conn:
            if expected_version == 0:
                row = conn.execute(
                    """
                    INSERT INTO conversation_summaries (
                        session_id, content, summarized_message_count,
                        through_message_id, version, source_chars,
                        created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id) DO NOTHING
                    RETURNING session_id, content, summarized_message_count,
                              through_message_id, version, source_chars,
                              created_at, updated_at
                    """,
                    _conversation_summary_values(summary),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    UPDATE conversation_summaries
                    SET content = %s,
                        summarized_message_count = %s,
                        through_message_id = %s,
                        version = %s,
                        source_chars = %s,
                        updated_at = %s
                    WHERE session_id = %s AND version = %s
                    RETURNING session_id, content, summarized_message_count,
                              through_message_id, version, source_chars,
                              created_at, updated_at
                    """,
                    (
                        summary.content,
                        summary.summarized_message_count,
                        summary.through_message_id,
                        summary.version,
                        summary.source_chars,
                        summary.updated_at,
                        summary.session_id,
                        expected_version,
                    ),
                ).fetchone()
        return _conversation_summary_from_row(row) if row is not None else None

    def add_token_usage(
        self,
        session_id: str | None,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        workspace_id: str | None = None,
        thoughts_tokens: int = 0,
        record_id: str | None = None,
        operation: str = "chat",
        resource_id: str | None = None,
        requested_provider: str | None = None,
        requested_model: str | None = None,
        input_count_method: str = "provider_usage",
        budget_decision: str = "allowed",
    ) -> TokenUsageRecord:
        if session_id is not None:
            self.get_session(session_id)
        record = TokenUsageRecord(
            id=record_id or f"usage_{uuid4().hex[:12]}",
            session_id=session_id,
            workspace_id=workspace_id,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thoughts_tokens=thoughts_tokens,
            total_tokens=input_tokens + output_tokens + thoughts_tokens,
            created_at=_now(),
            operation=operation,
            resource_id=resource_id,
            requested_provider=requested_provider,
            requested_model=requested_model,
            input_count_method=input_count_method,
            budget_decision=budget_decision,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO token_usage_records (
                    id,
                    session_id,
                    workspace_id,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    thoughts_tokens,
                    total_tokens,
                    operation,
                    resource_id,
                    requested_provider,
                    requested_model,
                    input_count_method,
                    budget_decision,
                    created_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    session_id = EXCLUDED.session_id,
                    workspace_id = EXCLUDED.workspace_id,
                    provider = EXCLUDED.provider,
                    model = EXCLUDED.model,
                    input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    thoughts_tokens = EXCLUDED.thoughts_tokens,
                    total_tokens = EXCLUDED.total_tokens,
                    operation = EXCLUDED.operation,
                    resource_id = EXCLUDED.resource_id,
                    requested_provider = EXCLUDED.requested_provider,
                    requested_model = EXCLUDED.requested_model,
                    input_count_method = EXCLUDED.input_count_method,
                    budget_decision = EXCLUDED.budget_decision
                """,
                (
                    record.id,
                    record.session_id,
                    record.workspace_id,
                    record.provider,
                    record.model,
                    record.input_tokens,
                    record.output_tokens,
                    record.thoughts_tokens,
                    record.total_tokens,
                    record.operation,
                    record.resource_id,
                    record.requested_provider,
                    record.requested_model,
                    record.input_count_method,
                    record.budget_decision,
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
                    workspace_id,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    thoughts_tokens,
                    total_tokens,
                    operation,
                    resource_id,
                    requested_provider,
                    requested_model,
                    input_count_method,
                    budget_decision,
                    created_at
                FROM token_usage_records
                WHERE session_id = %s
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            ).fetchall()
        return [_token_usage_from_row(row) for row in rows]

    def list_workspace_token_usage(
        self, workspace_id: str
    ) -> list[TokenUsageRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    session_id,
                    workspace_id,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    thoughts_tokens,
                    total_tokens,
                    operation,
                    resource_id,
                    requested_provider,
                    requested_model,
                    input_count_method,
                    budget_decision,
                    created_at
                FROM token_usage_records
                WHERE workspace_id = %s
                ORDER BY created_at ASC, id ASC
                """,
                (workspace_id,),
            ).fetchall()
        return [_token_usage_from_row(row) for row in rows]

    def list_all_token_usage(self) -> list[TokenUsageRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    session_id,
                    workspace_id,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    thoughts_tokens,
                    total_tokens,
                    operation,
                    resource_id,
                    requested_provider,
                    requested_model,
                    input_count_method,
                    budget_decision,
                    created_at
                FROM token_usage_records
                ORDER BY created_at ASC, id ASC
                """
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
                    workspace_id,
                    workspace_root,
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
                    %s,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (id) DO UPDATE SET
                    thread_id = EXCLUDED.thread_id,
                    conversation_id = EXCLUDED.conversation_id,
                    workspace_id = EXCLUDED.workspace_id,
                    workspace_root = EXCLUDED.workspace_root,
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
                    record.workspace_id,
                    record.workspace_root,
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
                    workspace_id,
                    workspace_root,
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
        Jsonb = _require_jsonb()
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
                        start_line,
                        end_line,
                        symbols,
                        search_text,
                        qdrant_point_id,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    """,
                    (
                        chunk.id,
                        chunk.document_id,
                        chunk.knowledge_base_id,
                        chunk.filename,
                        chunk.chunk_index,
                        chunk.text,
                        chunk.start_line,
                        chunk.end_line,
                        Jsonb(chunk.symbols),
                        " ".join(
                            [
                                chunk.filename,
                                " ".join(chunk.symbols),
                                chunk.text,
                            ]
                        ),
                        _qdrant_point_id(chunk.id),
                    ),
                )

    def search_lexical(
        self,
        *,
        knowledge_base_id: str,
        query: str,
        limit: int,
    ) -> list[RetrievedDocument]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH lexical_query AS (
                    SELECT websearch_to_tsquery('simple', %s) AS value
                )
                SELECT
                    chunks.id,
                    chunks.knowledge_base_id,
                    chunks.document_id,
                    chunks.filename,
                    chunks.chunk_index,
                    chunks.text,
                    chunks.start_line,
                    chunks.end_line,
                    chunks.symbols,
                    ts_rank_cd(chunks.search_vector, lexical_query.value) AS score
                FROM document_chunks AS chunks
                CROSS JOIN lexical_query
                WHERE chunks.knowledge_base_id = %s
                  AND chunks.search_vector @@ lexical_query.value
                ORDER BY score DESC, chunks.id ASC
                LIMIT %s
                """,
                (query, knowledge_base_id, limit),
            ).fetchall()
        return [
            RetrievedDocument(
                id=str(row[0]),
                knowledge_base_id=str(row[1]),
                document_id=str(row[2]),
                filename=str(row[3]),
                chunk_index=int(row[4]),
                text=str(row[5]),
                score=float(row[9]),
                start_line=int(row[6]) if row[6] is not None else None,
                end_line=int(row[7]) if row[7] is not None else None,
                symbols=[str(item) for item in (row[8] or [])],
                lexical_score=float(row[9]),
            )
            for row in rows
        ]

    def create_index_job(self, job: IndexJob) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rag_index_jobs (
                    id,
                    knowledge_base_id,
                    filename,
                    status,
                    document_id,
                    chunk_count,
                    error,
                    created_at,
                    updated_at,
                    completed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    job.id,
                    job.knowledge_base_id,
                    job.filename,
                    job.status,
                    job.document_id,
                    job.chunk_count,
                    job.error,
                    job.created_at,
                    job.updated_at,
                    job.completed_at,
                ),
            )

    def transition_index_job(
        self,
        *,
        job_id: str,
        expected_status: str,
        status: str,
        document_id: str | None = None,
        chunk_count: int | None = None,
        error: str | None = None,
    ) -> IndexJob:
        completed = status in {"active", "failed"}
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE rag_index_jobs
                SET
                    status = %s,
                    document_id = COALESCE(%s, document_id),
                    chunk_count = COALESCE(%s, chunk_count),
                    error = %s,
                    updated_at = NOW(),
                    completed_at = CASE WHEN %s THEN NOW() ELSE NULL END
                WHERE id = %s AND status = %s
                RETURNING
                    id,
                    knowledge_base_id,
                    filename,
                    status,
                    document_id,
                    chunk_count,
                    error,
                    created_at,
                    updated_at,
                    completed_at
                """,
                (
                    status,
                    document_id,
                    chunk_count,
                    error,
                    completed,
                    job_id,
                    expected_status,
                ),
            ).fetchone()
        if row is None:
            raise KeyError(
                f"index job {job_id} was not in expected state {expected_status}"
            )
        return _index_job_from_row(row)

    def get_index_job(self, job_id: str) -> IndexJob | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    knowledge_base_id,
                    filename,
                    status,
                    document_id,
                    chunk_count,
                    error,
                    created_at,
                    updated_at,
                    completed_at
                FROM rag_index_jobs
                WHERE id = %s
                """,
                (job_id,),
            ).fetchone()
        return _index_job_from_row(row) if row is not None else None

    def list_index_jobs(
        self,
        *,
        knowledge_base_id: str,
        limit: int,
    ) -> list[IndexJob]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    knowledge_base_id,
                    filename,
                    status,
                    document_id,
                    chunk_count,
                    error,
                    created_at,
                    updated_at,
                    completed_at
                FROM rag_index_jobs
                WHERE knowledge_base_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (knowledge_base_id, limit),
            ).fetchall()
        return [_index_job_from_row(row) for row in rows]

    def _connect(self):
        psycopg = _require_psycopg()
        return psycopg.connect(self._database_url)


class PostgresKnowledgeBaseRepository:
    """PostgreSQL-backed knowledge-base catalog."""

    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        _require_psycopg()

    def create(
        self,
        *,
        knowledge_base_id: str,
        name: str,
        description: str,
        tags: list[str],
    ) -> KnowledgeBaseRecord | None:
        Jsonb = _require_jsonb()
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO knowledge_bases (
                    id, name, description, tags, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (id) DO NOTHING
                RETURNING id, name, description, tags, created_at, updated_at
                """,
                (knowledge_base_id, name, description, Jsonb(tags)),
            ).fetchone()
        return _knowledge_base_from_row(row, document_count=0) if row else None

    def get(self, knowledge_base_id: str) -> KnowledgeBaseRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    kb.id,
                    kb.name,
                    kb.description,
                    kb.tags,
                    kb.created_at,
                    kb.updated_at,
                    COUNT(documents.id)
                FROM knowledge_bases AS kb
                LEFT JOIN documents
                    ON documents.knowledge_base_id = kb.id
                WHERE kb.id = %s
                GROUP BY kb.id
                """,
                (knowledge_base_id,),
            ).fetchone()
        return _knowledge_base_from_count_row(row) if row is not None else None

    def list(self) -> list[KnowledgeBaseRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    kb.id,
                    kb.name,
                    kb.description,
                    kb.tags,
                    kb.created_at,
                    kb.updated_at,
                    COUNT(documents.id)
                FROM knowledge_bases AS kb
                LEFT JOIN documents
                    ON documents.knowledge_base_id = kb.id
                GROUP BY kb.id
                ORDER BY kb.id ASC
                """
            ).fetchall()
        return [_knowledge_base_from_count_row(row) for row in rows]

    def update(
        self,
        *,
        knowledge_base_id: str,
        name: str,
        description: str,
        tags: list[str],
    ) -> KnowledgeBaseRecord | None:
        Jsonb = _require_jsonb()
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE knowledge_bases
                SET name = %s, description = %s, tags = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING id, name, description, tags, created_at, updated_at
                """,
                (name, description, Jsonb(tags), knowledge_base_id),
            ).fetchone()
        if row is None:
            return None
        current = self.get(knowledge_base_id)
        return current or _knowledge_base_from_row(row, document_count=0)

    def delete(self, knowledge_base_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                DELETE FROM knowledge_bases
                WHERE id = %s
                RETURNING id
                """,
                (knowledge_base_id,),
            ).fetchone()
        return row is not None

    def record_document(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        del knowledge_base_id, document_id

    def _connect(self):
        psycopg = _require_psycopg()
        return psycopg.connect(self._database_url)


class PostgresWorkspaceRepository:
    """PostgreSQL source of truth for registered workspaces."""

    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        _require_psycopg()

    def upsert(self, *, workspace_id: str, root_path: str) -> WorkspaceRecord:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO workspaces (id, root_path, created_at, updated_at)
                VALUES (%s, %s, NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET
                    revision = CASE
                        WHEN workspaces.root_path <> EXCLUDED.root_path
                        THEN workspaces.revision + 1
                        ELSE workspaces.revision
                    END,
                    root_path = EXCLUDED.root_path,
                    updated_at = NOW()
                RETURNING id, root_path, created_at, updated_at, revision
                """,
                (workspace_id, root_path),
            ).fetchone()
        return _workspace_from_row(row)

    def get(self, workspace_id: str) -> WorkspaceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, root_path, created_at, updated_at, revision
                FROM workspaces
                WHERE id = %s
                """,
                (workspace_id,),
            ).fetchone()
        return _workspace_from_row(row) if row is not None else None

    def list(self) -> list[WorkspaceRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, root_path, created_at, updated_at, revision
                FROM workspaces
                ORDER BY id ASC
                """
            ).fetchall()
        return [_workspace_from_row(row) for row in rows]

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


def _session_from_row(row: tuple[Any, ...]) -> Session:
    if len(row) == 3:
        return Session(
            id=str(row[0]),
            user_id=str(row[1]),
            created_at=row[2],
            updated_at=row[2],
        )
    return Session(
        id=str(row[0]),
        user_id=str(row[1]),
        created_at=row[2],
        title=str(row[3]),
        title_source=str(row[4]),
        updated_at=row[5],
        archived_at=row[6],
        workspace_id=str(row[7]) if row[7] is not None else None,
        provider=str(row[8]) if row[8] is not None else None,
        model=str(row[9]) if row[9] is not None else None,
        thinking_level=str(row[10]) if row[10] is not None else None,
        composer_mode=str(row[11]),
        message_count=int(row[12]) if len(row) > 12 and row[12] is not None else 0,
        last_message_preview=(
            str(row[13]) if len(row) > 13 and row[13] is not None else None
        ),
    )


def _session_select_columns() -> str:
    return """
        sessions.id,
        sessions.user_id,
        sessions.created_at,
        sessions.title,
        sessions.title_source,
        sessions.updated_at,
        sessions.archived_at,
        sessions.workspace_id,
        sessions.provider,
        sessions.model,
        sessions.thinking_level,
        sessions.composer_mode,
        (SELECT COUNT(*) FROM messages counted_messages
         WHERE counted_messages.session_id = sessions.id) AS message_count,
        (SELECT LEFT(REGEXP_REPLACE(preview_messages.content, '\\s+', ' ', 'g'), 120)
         FROM messages preview_messages
         WHERE preview_messages.session_id = sessions.id
         ORDER BY preview_messages.created_at DESC, preview_messages.id DESC
         LIMIT 1) AS last_message_preview
    """


def _session_values(session: Session) -> tuple[Any, ...]:
    return (
        session.id,
        session.user_id,
        session.created_at,
        session.title,
        session.title_source,
        session.updated_at or session.created_at,
        session.archived_at,
        session.workspace_id,
        session.provider,
        session.model,
        session.thinking_level,
        session.composer_mode,
    )


def _user_preferences_from_row(row: tuple[Any, ...]) -> UserPreferences:
    return UserPreferences(
        user_id=str(row[0]),
        default_provider=str(row[1]) if row[1] is not None else None,
        default_model=str(row[2]) if row[2] is not None else None,
        default_thinking_level=(str(row[3]) if row[3] is not None else None),
        default_workspace_id=str(row[4]) if row[4] is not None else None,
        default_composer_mode=str(row[5]),
        last_active_session_id=str(row[6]) if row[6] is not None else None,
        updated_at=row[7],
    )


def _user_preferences_values(
    preferences: UserPreferences,
) -> tuple[Any, ...]:
    return (
        preferences.user_id,
        preferences.default_provider,
        preferences.default_model,
        preferences.default_thinking_level,
        preferences.default_workspace_id,
        preferences.default_composer_mode,
        preferences.last_active_session_id,
        preferences.updated_at or _now(),
    )


def _derive_session_title(content: str) -> str:
    normalized = " ".join(content.split()).strip()
    return normalized[:48] or "新会话"


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _message_from_row(row: tuple[Any, ...]) -> Message:
    return Message(
        id=str(row[0]),
        session_id=str(row[1]),
        role=str(row[2]),
        content=str(row[3]),
        created_at=row[4],
    )


def _conversation_summary_from_row(
    row: tuple[Any, ...],
) -> ConversationSummary:
    return ConversationSummary(
        session_id=str(row[0]),
        content=str(row[1]),
        summarized_message_count=int(row[2]),
        through_message_id=str(row[3]),
        version=int(row[4]),
        source_chars=int(row[5]),
        created_at=row[6],
        updated_at=row[7],
    )


def _conversation_summary_values(
    summary: ConversationSummary,
) -> tuple[object, ...]:
    return (
        summary.session_id,
        summary.content,
        summary.summarized_message_count,
        summary.through_message_id,
        summary.version,
        summary.source_chars,
        summary.created_at,
        summary.updated_at,
    )


def _token_usage_from_row(row: tuple[Any, ...]) -> TokenUsageRecord:
    return TokenUsageRecord(
        id=str(row[0]),
        session_id=str(row[1]) if row[1] is not None else None,
        workspace_id=str(row[2]) if row[2] is not None else None,
        provider=str(row[3]),
        model=str(row[4]),
        input_tokens=int(row[5]),
        output_tokens=int(row[6]),
        thoughts_tokens=int(row[7]),
        total_tokens=int(row[8]),
        operation=str(row[9]),
        resource_id=str(row[10]) if row[10] is not None else None,
        requested_provider=(
            str(row[11]) if row[11] is not None else None
        ),
        requested_model=str(row[12]) if row[12] is not None else None,
        input_count_method=str(row[13]),
        budget_decision=str(row[14]),
        created_at=row[15],
    )


def _agent_result_to_json(result: AgentRunResult | None) -> dict[str, Any] | None:
    return asdict(result) if result is not None else None


def _agent_result_from_json(data: dict[str, Any] | None) -> AgentRunResult | None:
    if data is None:
        return None
    payload = dict(data)
    payload["workspace_id"] = payload.pop(
        "workspace_id",
        payload.pop("repository_id", "legacy_workspace"),
    )
    context_sources = payload.get("context_sources")
    if context_sources is None:
        context_sources = [
            {
                "kind": "legacy_index",
                "path": str(item.get("filename") or ""),
                "start_line": item.get("start_line"),
                "end_line": item.get("end_line"),
                "text": str(item.get("text") or ""),
                "reason": "loaded from a historical indexed result",
                "content_hash": hashlib.sha256(
                    str(item.get("text") or "").encode("utf-8")
                ).hexdigest(),
                "truncated": False,
            }
            for item in payload.pop("rag_context", [])
        ]
    payload["context_sources"] = [
        item if isinstance(item, ContextSource) else ContextSource(**item)
        for item in context_sources
    ]
    payload["tool_calls"] = [
        ToolCall(**item) for item in payload.get("tool_calls", [])
    ]
    payload.setdefault("context_route", "repo")
    payload.setdefault("selected_knowledge_base_ids", [])
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        payload["metrics"] = AgentRunMetrics(**metrics)
    change_summary = payload.get("change_summary")
    if isinstance(change_summary, dict):
        payload["change_summary"] = AgentChangeSummary(**change_summary)
    payload.setdefault("artifacts", [])
    payload.setdefault("errors", [])
    return AgentRunResult(**payload)


def _agent_run_from_row(row: tuple[Any, ...]) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=str(row[0]),
        thread_id=str(row[1]),
        conversation_id=str(row[2]),
        workspace_id=str(row[3]),
        workspace_root=str(row[4]),
        status=row[5],
        checkpoint_id=row[6],
        latest_node=row[7],
        next_nodes=list(row[8] or []),
        trace=list(row[9] or []),
        result=_agent_result_from_json(row[10]),
        error=row[11],
        pending_approval=row[12],
        errors=list(row[13] or []),
    )


def _workspace_from_row(row: tuple[Any, ...]) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=str(row[0]),
        root_path=str(row[1]),
        created_at=row[2],
        updated_at=row[3],
        revision=int(row[4]) if len(row) > 4 else 1,
    )


def _knowledge_base_from_row(
    row: tuple[Any, ...],
    *,
    document_count: int,
) -> KnowledgeBaseRecord:
    return KnowledgeBaseRecord(
        id=str(row[0]),
        name=str(row[1]),
        description=str(row[2] or ""),
        tags=[str(tag) for tag in (row[3] or [])],
        document_count=document_count,
        created_at=row[4],
        updated_at=row[5],
    )


def _knowledge_base_from_count_row(row: tuple[Any, ...]) -> KnowledgeBaseRecord:
    return _knowledge_base_from_row(row[:6], document_count=int(row[6]))


def _index_job_from_row(row: tuple[Any, ...]) -> IndexJob:
    return IndexJob(
        id=str(row[0]),
        knowledge_base_id=str(row[1]),
        filename=str(row[2]),
        status=str(row[3]),
        document_id=str(row[4]) if row[4] is not None else None,
        chunk_count=int(row[5]),
        error=str(row[6]) if row[6] is not None else None,
        created_at=row[7],
        updated_at=row[8],
        completed_at=row[9],
    )
