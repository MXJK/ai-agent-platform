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
    KnowledgeBaseRecord,
    Message,
    Session,
    TokenUsageRecord,
    WorkspaceRecord,
)
from ai_agent_platform.integrations.rag import DocumentChunk, ParsedDocument
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
                    root_path = EXCLUDED.root_path,
                    updated_at = NOW()
                RETURNING id, root_path, created_at, updated_at
                """,
                (workspace_id, root_path),
            ).fetchone()
        return _workspace_from_row(row)

    def get(self, workspace_id: str) -> WorkspaceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, root_path, created_at, updated_at
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
                SELECT id, root_path, created_at, updated_at
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
