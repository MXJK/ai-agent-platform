"""SQLite adapters for the single-file local runtime profile."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import re
import sqlite3
from typing import Any
from uuid import uuid4

from ai_agent_platform.agents.coding.models import (
    AgentRunEvent,
    AgentRunRecord,
    AgentToolExecution,
)
from ai_agent_platform.agents.coding.store import events_for_record
from ai_agent_platform.domain import (
    ConversationSummary,
    Message,
    RunContextSnapshot,
    Session,
    TokenUsageRecord,
    UserPreferences,
    WorkspaceRecord,
)
from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.memory import ConversationMemoryHit
from ai_agent_platform.repositories.memory import (
    SessionArchivedError,
    SessionNotFoundError,
)
from ai_agent_platform.repositories.postgres import _agent_result_from_json
from ai_agent_platform.text_search import fts_index_text, fts_match_query


class SQLiteSessionRepository:
    def __init__(self, *, database: LocalStateDatabase) -> None:
        self.database = database

    def create_session(
        self,
        user_id: str,
        preferences: UserPreferences | None = None,
    ) -> Session:
        preferences = preferences or self.get_user_preferences(user_id)
        now = _now()
        session = Session(
            id=f"sess_{uuid4().hex[:12]}",
            user_id=user_id,
            created_at=now,
            updated_at=now,
            workspace_id=preferences.default_workspace_id if preferences else None,
            provider=preferences.default_provider if preferences else None,
            model=preferences.default_model if preferences else None,
            thinking_level=preferences.default_thinking_level if preferences else None,
            composer_mode=preferences.default_composer_mode if preferences else "chat",
        )
        with self.database.transaction(immediate=True) as conn:
            self._save_session(conn, session)
        return session

    def delete_session(self, session_id: str) -> bool:
        with self.database.transaction(immediate=True) as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return bool(cursor.rowcount)

    def list_sessions(
        self,
        *,
        user_id: str | None = None,
        query: str | None = None,
        archived: bool | None = None,
        limit: int | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> list[Session]:
        clauses = ["EXISTS (SELECT 1 FROM messages m0 WHERE m0.session_id = s.id)"]
        params: list[object] = []
        if user_id is not None:
            clauses.append("s.user_id = ?")
            params.append(user_id)
        if archived is True:
            clauses.append("s.archived_at IS NOT NULL")
        elif archived is False or archived is None:
            clauses.append("s.archived_at IS NULL")
        normalized = (query or "").strip()
        if normalized:
            clauses.append(
                "(LOWER(s.title) LIKE ? ESCAPE '\\' OR EXISTS ("
                "SELECT 1 FROM messages mq WHERE mq.session_id = s.id "
                "AND LOWER(mq.content) LIKE ? ESCAPE '\\'))"
            )
            like = f"%{_escape_like(normalized.casefold())}%"
            params.extend((like, like))
        if before is not None:
            clauses.append("(s.updated_at < ? OR (s.updated_at = ? AND s.id < ?))")
            stamp = _iso(before[0])
            params.extend((stamp, stamp, before[1]))
        sql = f"""
            SELECT s.*,
                   (SELECT COUNT(*) FROM messages mc WHERE mc.session_id = s.id) AS message_count,
                   (SELECT mp.content FROM messages mp WHERE mp.session_id = s.id
                    ORDER BY mp.created_at DESC, mp.id DESC LIMIT 1) AS preview
            FROM sessions s
            WHERE {' AND '.join(clauses)}
            ORDER BY s.updated_at DESC, s.id DESC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.database.connect() as conn:
            return [_session_from_row(row) for row in conn.execute(sql, params).fetchall()]

    def get_session(self, session_id: str) -> Session:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM messages mc WHERE mc.session_id = s.id) AS message_count,
                       (SELECT mp.content FROM messages mp WHERE mp.session_id = s.id
                        ORDER BY mp.created_at DESC, mp.id DESC LIMIT 1) AS preview
                FROM sessions s WHERE s.id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)
        return _session_from_row(row)

    def save_session(
        self,
        session: Session,
        preferences: UserPreferences | None = None,
    ) -> Session:
        with self.database.transaction(immediate=True) as conn:
            self._save_session(conn, session)
            if preferences is not None:
                self._save_user_preferences(conn, preferences)
        return session

    def _save_session(self, conn: sqlite3.Connection, session: Session) -> None:
        conn.execute(
            """
            INSERT INTO sessions (
                id, user_id, title, title_source, created_at, updated_at,
                archived_at, workspace_id, provider, model, thinking_level, composer_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, title_source=excluded.title_source,
                updated_at=excluded.updated_at, archived_at=excluded.archived_at,
                workspace_id=excluded.workspace_id, provider=excluded.provider,
                model=excluded.model, thinking_level=excluded.thinking_level,
                composer_mode=excluded.composer_mode
            """,
            (
                session.id,
                session.user_id,
                session.title,
                session.title_source,
                _iso(session.created_at),
                _iso(session.updated_at or session.created_at),
                _iso(session.archived_at),
                session.workspace_id,
                session.provider,
                session.model,
                session.thinking_level,
                session.composer_mode,
            ),
        )

    def get_user_preferences(self, user_id: str) -> UserPreferences | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_preferences WHERE user_id = ?", (user_id,)
            ).fetchone()
        return _preferences_from_row(row) if row is not None else None

    def save_user_preferences(self, preferences: UserPreferences) -> UserPreferences:
        with self.database.transaction(immediate=True) as conn:
            self._save_user_preferences(conn, preferences)
        return preferences

    def _save_user_preferences(
        self, conn: sqlite3.Connection, preferences: UserPreferences
    ) -> None:
        conn.execute(
            """
            INSERT INTO user_preferences (
                user_id, default_provider, default_model, default_thinking_level,
                default_workspace_id, default_composer_mode,
                last_active_session_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                default_provider=excluded.default_provider,
                default_model=excluded.default_model,
                default_thinking_level=excluded.default_thinking_level,
                default_workspace_id=excluded.default_workspace_id,
                default_composer_mode=excluded.default_composer_mode,
                last_active_session_id=excluded.last_active_session_id,
                updated_at=excluded.updated_at
            """,
            (
                preferences.user_id,
                preferences.default_provider,
                preferences.default_model,
                preferences.default_thinking_level,
                preferences.default_workspace_id,
                preferences.default_composer_mode,
                preferences.last_active_session_id,
                _iso(preferences.updated_at or _now()),
            ),
        )

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        message_id: str | None = None,
        source_run_id: str | None = None,
    ) -> Message:
        with self.database.transaction(immediate=True) as conn:
            stored = self.add_message_in_transaction(
                conn,
                session_id=session_id,
                role=role,
                content=content,
                message_id=message_id,
                source_run_id=source_run_id,
            )
        if stored is None:
            raise sqlite3.IntegrityError("message already exists")
        return stored

    def add_message_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        role: str,
        content: str,
        message_id: str | None = None,
        source_run_id: str | None = None,
    ) -> Message | None:
        session_row = conn.execute(
            "SELECT archived_at, title_source FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if session_row is None:
            raise SessionNotFoundError(session_id)
        if session_row["archived_at"] is not None:
            raise SessionArchivedError(session_id)
        now = _now()
        message = Message(
            id=message_id or f"msg_{uuid4().hex[:12]}",
            session_id=session_id,
            role=role,
            content=content,
            created_at=now,
            source_run_id=source_run_id,
        )
        try:
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, created_at, source_run_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    message.id,
                    message.session_id,
                    message.role,
                    message.content,
                    _iso(message.created_at),
                    message.source_run_id,
                ),
            )
        except sqlite3.IntegrityError:
            return None
        if self.database.fts5_available:
            conn.execute(
                "INSERT INTO messages_fts(message_id, session_id, content) VALUES (?, ?, ?)",
                (message.id, session_id, fts_index_text(content)),
            )
        title_sql = ""
        params: list[object] = [_iso(now)]
        if role == "user" and session_row["title_source"] == "default":
            title_sql = ", title = ?, title_source = 'auto'"
            params.append(_derive_title(content))
        params.append(session_id)
        conn.execute(
            f"UPDATE sessions SET updated_at = ?{title_sql} WHERE id = ?",
            params,
        )
        return message

    def list_messages(self, session_id: str) -> list[Message]:
        with self.database.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if exists is None:
                raise SessionNotFoundError(session_id)
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
        return [_message_from_row(row) for row in rows]

    def get_conversation_summary(self, session_id: str) -> ConversationSummary | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_summaries WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return _summary_from_row(row) if row is not None else None

    def upsert_conversation_summary(
        self,
        summary: ConversationSummary,
        *,
        expected_version: int | None,
    ) -> ConversationSummary | None:
        with self.database.transaction(immediate=True) as conn:
            current = conn.execute(
                "SELECT version FROM conversation_summaries WHERE session_id = ?",
                (summary.session_id,),
            ).fetchone()
            version = int(current[0]) if current is not None else None
            if version != expected_version:
                return None
            conn.execute(
                """
                INSERT INTO conversation_summaries (
                    session_id, content, summarized_message_count,
                    through_message_id, version, source_chars, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    content=excluded.content,
                    summarized_message_count=excluded.summarized_message_count,
                    through_message_id=excluded.through_message_id,
                    version=excluded.version,
                    source_chars=excluded.source_chars,
                    updated_at=excluded.updated_at
                """,
                (
                    summary.session_id,
                    summary.content,
                    summary.summarized_message_count,
                    summary.through_message_id,
                    summary.version,
                    summary.source_chars,
                    _iso(summary.created_at),
                    _iso(summary.updated_at),
                ),
            )
        return summary

    def add_token_usage(
        self,
        *,
        session_id: str | None,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        workspace_id: str | None = None,
        thoughts_tokens: int = 0,
        total_tokens: int | None = None,
        record_id: str | None = None,
        operation: str = "chat",
        resource_id: str | None = None,
        requested_provider: str | None = None,
        requested_model: str | None = None,
        input_count_method: str = "provider_usage",
        budget_decision: str = "allowed",
    ) -> TokenUsageRecord:
        usage_id = record_id or f"usage_{uuid4().hex[:12]}"
        with self.database.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT created_at FROM token_usage_records WHERE id = ?", (usage_id,)
            ).fetchone()
            created_at = _dt(existing[0]) if existing is not None else _now()
            record = TokenUsageRecord(
                id=usage_id,
                session_id=session_id,
                workspace_id=workspace_id,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thoughts_tokens=thoughts_tokens,
                total_tokens=(
                    total_tokens
                    if total_tokens is not None
                    else input_tokens + output_tokens + thoughts_tokens
                ),
                created_at=created_at,
                operation=operation,
                resource_id=resource_id,
                requested_provider=requested_provider,
                requested_model=requested_model,
                input_count_method=input_count_method,
                budget_decision=budget_decision,
            )
            conn.execute(
                """
                INSERT INTO token_usage_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    session_id=excluded.session_id, workspace_id=excluded.workspace_id,
                    provider=excluded.provider, model=excluded.model,
                    input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens,
                    thoughts_tokens=excluded.thoughts_tokens, total_tokens=excluded.total_tokens,
                    operation=excluded.operation, resource_id=excluded.resource_id,
                    requested_provider=excluded.requested_provider,
                    requested_model=excluded.requested_model,
                    input_count_method=excluded.input_count_method,
                    budget_decision=excluded.budget_decision
                """,
                _token_values(record),
            )
        return record

    def list_token_usage(self, session_id: str) -> list[TokenUsageRecord]:
        self.get_session(session_id)
        return self._list_usage("session_id = ?", (session_id,))

    def list_workspace_token_usage(self, workspace_id: str) -> list[TokenUsageRecord]:
        return self._list_usage("workspace_id = ?", (workspace_id,))

    def list_all_token_usage(self) -> list[TokenUsageRecord]:
        return self._list_usage("1 = 1", ())

    def _list_usage(self, clause: str, params: tuple[object, ...]) -> list[TokenUsageRecord]:
        with self.database.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM token_usage_records WHERE {clause} ORDER BY created_at, id",
                params,
            ).fetchall()
        return [_usage_from_row(row) for row in rows]

    def search_conversations(
        self,
        *,
        user_id: str,
        query: str,
        workspace_id: str | None = None,
        session_id: str | None = None,
        limit: int = 10,
    ) -> list[ConversationMemoryHit]:
        text = query.strip()
        filters = ["s.user_id = ?"]
        params: list[object] = [user_id]
        if workspace_id is not None:
            filters.append("s.workspace_id = ?")
            params.append(workspace_id)
        if session_id is not None:
            filters.append("m.session_id = ?")
            params.append(session_id)
        if text and self.database.fts5_available:
            match = fts_match_query(text)
            if not match:
                return []
            sql = f"""
                SELECT m.id, m.session_id, s.workspace_id, m.role, m.content,
                       m.created_at, bm25(messages_fts) AS rank
                FROM messages_fts
                JOIN messages m ON m.id = messages_fts.message_id
                JOIN sessions s ON s.id = m.session_id
                WHERE messages_fts MATCH ? AND {' AND '.join(filters)}
                ORDER BY rank ASC, m.created_at DESC LIMIT ?
            """
            query_params = [match, *params, max(1, min(limit, 50))]
            with self.database.connect() as conn:
                rows = conn.execute(sql, query_params).fetchall()
            return [
                ConversationMemoryHit(
                    message_id=str(row[0]),
                    session_id=str(row[1]),
                    workspace_id=str(row[2]) if row[2] is not None else None,
                    role=str(row[3]),
                    excerpt=_excerpt(str(row[4]), text),
                    created_at=_dt(row[5]),
                    score=1.0 / (1.0 + abs(float(row[6]))),
                )
                for row in rows
            ]
        if text:
            filters.append("LOWER(m.content) LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(text.casefold())}%")
        params.append(max(1, min(limit, 50)))
        with self.database.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT m.id, m.session_id, s.workspace_id, m.role, m.content, m.created_at
                FROM messages m JOIN sessions s ON s.id = m.session_id
                WHERE {' AND '.join(filters)}
                ORDER BY m.created_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            ConversationMemoryHit(
                message_id=str(row[0]),
                session_id=str(row[1]),
                workspace_id=str(row[2]) if row[2] is not None else None,
                role=str(row[3]),
                excerpt=_excerpt(str(row[4]), text),
                created_at=_dt(row[5]),
                score=1.0,
            )
            for row in rows
        ]


class SQLiteWorkspaceRepository:
    def __init__(self, *, database: LocalStateDatabase) -> None:
        self.database = database

    def upsert(self, *, workspace_id: str, root_path: str) -> WorkspaceRecord:
        with self.database.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
            now = _now()
            record = WorkspaceRecord(
                id=workspace_id,
                root_path=root_path,
                created_at=_dt(existing["created_at"]) if existing else now,
                updated_at=now,
                revision=(
                    int(existing["revision"]) + 1
                    if existing and existing["root_path"] != root_path
                    else int(existing["revision"]) if existing else 1
                ),
                removed_at=None,
            )
            conn.execute(
                """
                INSERT INTO workspaces VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(id) DO UPDATE SET root_path=excluded.root_path,
                    updated_at=excluded.updated_at, revision=excluded.revision,
                    removed_at=NULL
                """,
                (
                    record.id,
                    record.root_path,
                    _iso(record.created_at),
                    _iso(record.updated_at),
                    record.revision,
                ),
            )
        return record

    def get(self, workspace_id: str) -> WorkspaceRecord | None:
        return self._get("id = ? AND removed_at IS NULL", (workspace_id,))

    def get_including_removed(self, workspace_id: str) -> WorkspaceRecord | None:
        return self._get("id = ?", (workspace_id,))

    def get_by_root_path(self, root_path: str) -> WorkspaceRecord | None:
        return self._get("root_path = ?", (root_path,))

    def _get(self, clause: str, params: tuple[object, ...]) -> WorkspaceRecord | None:
        with self.database.connect() as conn:
            row = conn.execute(f"SELECT * FROM workspaces WHERE {clause}", params).fetchone()
        return _workspace_from_row(row) if row is not None else None

    def list(self) -> list[WorkspaceRecord]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workspaces WHERE removed_at IS NULL ORDER BY id"
            ).fetchall()
        return [_workspace_from_row(row) for row in rows]

    def list_including_removed(self) -> list[WorkspaceRecord]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workspaces ORDER BY id"
            ).fetchall()
        return [_workspace_from_row(row) for row in rows]

    def remove(self, workspace_id: str) -> WorkspaceRecord | None:
        with self.database.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ? AND removed_at IS NULL",
                (workspace_id,),
            ).fetchone()
            if row is None:
                return None
            now = _now()
            conn.execute(
                "UPDATE workspaces SET removed_at = ?, updated_at = ? WHERE id = ?",
                (_iso(now), _iso(now), workspace_id),
            )
        return replace(_workspace_from_row(row), removed_at=now, updated_at=now)

    def purge(self, workspace_id: str) -> bool:
        with self.database.transaction(immediate=True) as conn:
            cursor = conn.execute(
                "DELETE FROM workspaces WHERE id = ?",
                (workspace_id,),
            )
        return bool(cursor.rowcount)


class SQLiteAgentRunRepository:
    def __init__(self, *, database: LocalStateDatabase) -> None:
        self.database = database

    def save(self, record: AgentRunRecord) -> None:
        with self.database.transaction(immediate=True) as conn:
            self.save_in_transaction(conn, record)

    def save_in_transaction(self, conn: sqlite3.Connection, record: AgentRunRecord) -> None:
        current = conn.execute(
            "SELECT status, created_at FROM agent_runs WHERE id = ?", (record.run_id,)
        ).fetchone()
        if current is not None and current["status"] in {
            "completed", "partial", "blocked", "cancelled", "failed"
        }:
            return
        now = _now()
        conn.execute(
            """
            INSERT INTO agent_runs (
                id, thread_id, conversation_id, workspace_id, workspace_root,
                status, checkpoint_id, latest_node, next_nodes_json, trace_json,
                result_json, error, pending_approval_json, errors_json,
                control_action, steering_messages_json, run_context_snapshot_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                thread_id=excluded.thread_id, conversation_id=excluded.conversation_id,
                workspace_id=excluded.workspace_id, workspace_root=excluded.workspace_root,
                status=excluded.status, checkpoint_id=excluded.checkpoint_id,
                latest_node=excluded.latest_node, next_nodes_json=excluded.next_nodes_json,
                trace_json=excluded.trace_json, result_json=excluded.result_json,
                error=excluded.error, pending_approval_json=excluded.pending_approval_json,
                errors_json=excluded.errors_json, control_action=excluded.control_action,
                steering_messages_json=excluded.steering_messages_json,
                run_context_snapshot_json=excluded.run_context_snapshot_json,
                updated_at=excluded.updated_at
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
                _json(record.next_nodes),
                _json(record.trace),
                _json(asdict(record.result)) if record.result is not None else None,
                record.error,
                _json(record.pending_approval) if record.pending_approval is not None else None,
                _json(record.errors),
                record.control_action,
                _json(record.steering_messages),
                _json(record.context_snapshot.to_dict()) if record.context_snapshot else None,
                current["created_at"] if current is not None else _iso(now),
                _iso(now),
            ),
        )
        for event_key, event in events_for_record(record):
            conn.execute(
                """
                INSERT OR IGNORE INTO agent_run_events
                    (run_id, event_key, type, status, node, summary, output_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    event_key,
                    event.type,
                    event.status,
                    event.node,
                    event.summary,
                    _json(event.output),
                ),
            )

    def get(self, run_id: str) -> AgentRunRecord:
        with self.database.connect() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _run_from_row(row)

    def get_latest_for_conversation(self, conversation_id: str) -> AgentRunRecord | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE conversation_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return _run_from_row(row) if row is not None else None

    def list_recent(self, *, limit: int = 50) -> list[AgentRunRecord]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_runs "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def list_events(self, run_id: str, *, after: int = 0) -> list[AgentRunEvent]:
        self.get(run_id)
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_run_events WHERE run_id = ? AND id > ? ORDER BY id",
                (run_id, after),
            ).fetchall()
        return [
            AgentRunEvent(
                sequence=int(row["id"]),
                type=str(row["type"]),
                status=str(row["status"]),
                node=str(row["node"]) if row["node"] is not None else None,
                summary=str(row["summary"]),
                output=_json_load(row["output_json"], {}),
            )
            for row in rows
        ]

    def append_event(self, run_id: str, event: AgentRunEvent) -> AgentRunEvent:
        with self.database.transaction(immediate=True) as conn:
            if conn.execute("SELECT 1 FROM agent_runs WHERE id = ?", (run_id,)).fetchone() is None:
                raise KeyError(run_id)
            cursor = conn.execute(
                "INSERT INTO agent_run_events "
                "(run_id, event_key, type, status, node, summary, output_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    f"manual:{uuid4().hex}",
                    event.type,
                    event.status,
                    event.node,
                    event.summary,
                    _json(event.output),
                ),
            )
            sequence = int(cursor.lastrowid)
        return replace(event, sequence=sequence)

    def append_event_once(
        self,
        run_id: str,
        event_key: str,
        event: AgentRunEvent,
    ) -> AgentRunEvent:
        with self.database.transaction(immediate=True) as conn:
            if conn.execute("SELECT 1 FROM agent_runs WHERE id = ?", (run_id,)).fetchone() is None:
                raise KeyError(run_id)
            conn.execute(
                "INSERT OR IGNORE INTO agent_run_events "
                "(run_id, event_key, type, status, node, summary, output_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    event_key,
                    event.type,
                    event.status,
                    event.node,
                    event.summary,
                    _json(event.output),
                ),
            )
            row = conn.execute(
                "SELECT id, type, status, node, summary, output_json "
                "FROM agent_run_events WHERE run_id = ? AND event_key = ?",
                (run_id, event_key),
            ).fetchone()
        assert row is not None
        return AgentRunEvent(
            sequence=int(row["id"]),
            type=str(row["type"]),
            status=str(row["status"]),
            node=str(row["node"]) if row["node"] is not None else None,
            summary=str(row["summary"]),
            output=_json_load(row["output_json"], {}),
        )

    def get_tool_execution(self, run_id: str, call_id: str) -> AgentToolExecution | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_tool_executions WHERE run_id = ? AND call_id = ?",
                (run_id, call_id),
            ).fetchone()
        if row is None:
            return None
        return AgentToolExecution(
            run_id=str(row["run_id"]),
            call_id=str(row["call_id"]),
            name=str(row["name"]),
            arguments_hash=str(row["arguments_hash"]),
            status=str(row["status"]),
            response=_json_load(row["response_json"], None),
        )

    def save_tool_execution(self, execution: AgentToolExecution) -> None:
        now = _iso(_now())
        with self.database.transaction(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO agent_tool_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, call_id) DO UPDATE SET
                    name=excluded.name, arguments_hash=excluded.arguments_hash,
                    status=excluded.status, response_json=excluded.response_json,
                    updated_at=excluded.updated_at
                """,
                (
                    execution.run_id,
                    execution.call_id,
                    execution.name,
                    execution.arguments_hash,
                    execution.status,
                    _json(execution.response) if execution.response is not None else None,
                    now,
                    now,
                ),
            )


def _run_from_row(row: sqlite3.Row) -> AgentRunRecord:
    result_data = _json_load(row["result_json"], None)
    snapshot_data = _json_load(row["run_context_snapshot_json"], None)
    return AgentRunRecord(
        run_id=str(row["id"]),
        thread_id=str(row["thread_id"]),
        conversation_id=str(row["conversation_id"]),
        workspace_id=str(row["workspace_id"]),
        workspace_root=str(row["workspace_root"]),
        status=row["status"],
        checkpoint_id=row["checkpoint_id"],
        latest_node=row["latest_node"],
        next_nodes=_json_load(row["next_nodes_json"], []),
        trace=_json_load(row["trace_json"], []),
        result=_agent_result_from_json(result_data),
        error=row["error"],
        pending_approval=_json_load(row["pending_approval_json"], None),
        errors=_json_load(row["errors_json"], []),
        control_action=row["control_action"],
        steering_messages=_json_load(row["steering_messages_json"], []),
        context_snapshot=RunContextSnapshot.from_dict(snapshot_data) if snapshot_data else None,
    )


def _session_from_row(row: sqlite3.Row) -> Session:
    preview = row["preview"] if "preview" in row.keys() else None
    return Session(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        created_at=_dt(row["created_at"]),
        title=str(row["title"]),
        title_source=str(row["title_source"]),
        updated_at=_dt(row["updated_at"]),
        archived_at=_dt(row["archived_at"]),
        workspace_id=str(row["workspace_id"]) if row["workspace_id"] is not None else None,
        provider=str(row["provider"]) if row["provider"] is not None else None,
        model=str(row["model"]) if row["model"] is not None else None,
        thinking_level=str(row["thinking_level"]) if row["thinking_level"] is not None else None,
        composer_mode=str(row["composer_mode"]),
        message_count=int(row["message_count"]) if "message_count" in row.keys() else 0,
        last_message_preview=_excerpt(str(preview), "") if preview is not None else None,
    )


def _preferences_from_row(row: sqlite3.Row) -> UserPreferences:
    return UserPreferences(
        user_id=str(row["user_id"]),
        default_provider=row["default_provider"],
        default_model=row["default_model"],
        default_thinking_level=row["default_thinking_level"],
        default_workspace_id=row["default_workspace_id"],
        default_composer_mode=str(row["default_composer_mode"]),
        last_active_session_id=row["last_active_session_id"],
        updated_at=_dt(row["updated_at"]),
    )


def _message_from_row(row: sqlite3.Row) -> Message:
    return Message(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        role=str(row["role"]),
        content=str(row["content"]),
        created_at=_dt(row["created_at"]),
        source_run_id=row["source_run_id"],
    )


def _summary_from_row(row: sqlite3.Row) -> ConversationSummary:
    return ConversationSummary(
        session_id=str(row["session_id"]),
        content=str(row["content"]),
        summarized_message_count=int(row["summarized_message_count"]),
        through_message_id=str(row["through_message_id"]),
        version=int(row["version"]),
        source_chars=int(row["source_chars"]),
        created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
    )


def _workspace_from_row(row: sqlite3.Row) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=str(row["id"]),
        root_path=str(row["root_path"]),
        created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
        revision=int(row["revision"]),
        removed_at=_dt(row["removed_at"]),
    )


def _usage_from_row(row: sqlite3.Row) -> TokenUsageRecord:
    return TokenUsageRecord(
        id=str(row["id"]), session_id=row["session_id"], workspace_id=row["workspace_id"],
        provider=str(row["provider"]), model=str(row["model"]),
        input_tokens=int(row["input_tokens"]), output_tokens=int(row["output_tokens"]),
        thoughts_tokens=int(row["thoughts_tokens"]), total_tokens=int(row["total_tokens"]),
        created_at=_dt(row["created_at"]), operation=str(row["operation"]),
        resource_id=row["resource_id"], requested_provider=row["requested_provider"],
        requested_model=row["requested_model"], input_count_method=str(row["input_count_method"]),
        budget_decision=str(row["budget_decision"]),
    )


def _token_values(record: TokenUsageRecord) -> tuple[object, ...]:
    return (
        record.id, record.session_id, record.workspace_id, record.provider, record.model,
        record.input_tokens, record.output_tokens, record.thoughts_tokens, record.total_tokens,
        record.operation, record.resource_id, record.requested_provider, record.requested_model,
        record.input_count_method, record.budget_decision, _iso(record.created_at),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _dt(value: object | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load(value: object | None, default):
    if value is None:
        return default
    return json.loads(str(value))


def _derive_title(content: str) -> str:
    return " ".join(content.split())[:48] or "新会话"


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _excerpt(content: str, query: str, limit: int = 240) -> str:
    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact
    index = compact.casefold().find(query.casefold()) if query else 0
    start = max(0, index - limit // 3) if index >= 0 else 0
    text = compact[start : start + limit]
    return ("…" if start else "") + text + ("…" if start + limit < len(compact) else "")


__all__ = [
    "SQLiteAgentRunRepository",
    "SQLiteSessionRepository",
    "SQLiteWorkspaceRepository",
]
