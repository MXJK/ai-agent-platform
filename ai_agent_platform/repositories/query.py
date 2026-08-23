"""Atomic persistence boundary for Query start and final assistant messages."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Protocol

from ai_agent_platform.agents.coding.models import AgentRunRecord
from ai_agent_platform.agents.coding.store import events_for_record
from ai_agent_platform.domain import Message, UserPreferences


class QueryUnitOfWork(Protocol):
    atomic: bool

    def persist_start(
        self,
        *,
        record: AgentRunRecord,
        message_id: str,
        message: str,
        preferences: UserPreferences | None,
    ) -> Message:
        ...

    def persist_assistant_once(
        self,
        *,
        run_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> Message | None:
        ...


class InMemoryQueryUnitOfWork:
    """Coordinates both in-memory stores under their re-entrant locks."""

    atomic = True

    def __init__(self, *, session_service, session_repository, run_store) -> None:
        self._session_service = session_service
        self._sessions = session_repository
        self._runs = run_store

    def persist_start(
        self,
        *,
        record: AgentRunRecord,
        message_id: str,
        message: str,
        preferences: UserPreferences | None,
    ) -> Message:
        del preferences
        with self._sessions._lock, self._runs._lock:
            snapshot = self._snapshot()
            try:
                self._runs.save(record)
                messages = self._session_service.add_message(
                    session_id=record.conversation_id,
                    role="user",
                    content=message,
                    message_id=message_id,
                    source_run_id=record.run_id,
                )
            except BaseException:
                self._restore(snapshot)
                raise
        return messages[0]

    def persist_assistant_once(
        self,
        *,
        run_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> Message | None:
        with self._sessions._lock, self._runs._lock:
            existing = next(
                (
                    message
                    for message in self._sessions._messages.get(conversation_id, [])
                    if message.source_run_id == run_id and message.role == "assistant"
                ),
                None,
            )
            if existing is not None:
                return None
            messages = self._session_service.add_message(
                session_id=conversation_id,
                role="assistant",
                content=content,
                message_id=message_id,
                source_run_id=run_id,
            )
            return messages[0]

    def _snapshot(self):
        return (
            dict(self._sessions._sessions),
            defaultdict(
                list,
                {
                    session_id: list(messages)
                    for session_id, messages in self._sessions._messages.items()
                },
            ),
            dict(self._sessions._user_preferences),
            dict(self._runs._runs),
            {
                run_id: list(events)
                for run_id, events in self._runs._events.items()
            },
            {
                run_id: set(keys)
                for run_id, keys in self._runs._event_keys.items()
            },
        )

    def _restore(self, snapshot) -> None:
        (
            self._sessions._sessions,
            self._sessions._messages,
            self._sessions._user_preferences,
            self._runs._runs,
            self._runs._events,
            self._runs._event_keys,
        ) = snapshot


class PostgresQueryUnitOfWork:
    """Commits the initial Run, queued event, and user message in one transaction."""

    atomic = True

    def __init__(self, *, session_repository, run_store) -> None:
        self._sessions = session_repository
        self._runs = run_store

    def persist_start(
        self,
        *,
        record: AgentRunRecord,
        message_id: str,
        message: str,
        preferences: UserPreferences | None,
    ) -> Message:
        from ai_agent_platform.repositories.postgres import _require_jsonb

        Jsonb = _require_jsonb()
        with self._runs._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs (
                    id, thread_id, conversation_id, workspace_id, workspace_root,
                    status, checkpoint_id, latest_node, next_nodes, trace, result,
                    error, pending_approval, errors, control_action,
                    steering_messages, run_context_snapshot, created_at, updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, NOW(), NOW()
                )
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
                    None,
                    record.error,
                    Jsonb(record.pending_approval),
                    Jsonb(record.errors),
                    record.control_action,
                    Jsonb(record.steering_messages),
                    Jsonb(
                        record.context_snapshot.to_dict()
                        if record.context_snapshot is not None
                        else None
                    ),
                ),
            )
            for event_key, event in events_for_record(record):
                conn.execute(
                    """
                    INSERT INTO agent_run_events (
                        run_id, event_key, type, status, node, summary, output
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.run_id,
                        event_key,
                        event.type,
                        event.status,
                        event.node,
                        event.summary,
                        Jsonb(event.output),
                    ),
                )
            stored = self._sessions.add_message_in_transaction(
                conn,
                session_id=record.conversation_id,
                role="user",
                content=message,
                message_id=message_id,
                source_run_id=record.run_id,
            )
            if stored is None:
                raise RuntimeError("Query start message already exists")
            if preferences is not None:
                self._sessions._save_user_preferences(
                    conn,
                    replace(
                        preferences,
                        last_active_session_id=record.conversation_id,
                        updated_at=stored.created_at,
                    ),
                )
        return stored

    def persist_assistant_once(
        self,
        *,
        run_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> Message | None:
        with self._runs._connect() as conn:
            return self._sessions.add_message_in_transaction(
                conn,
                session_id=conversation_id,
                role="assistant",
                content=content,
                message_id=message_id,
                source_run_id=run_id,
            )


class SQLiteQueryUnitOfWork:
    """Commits the initial Run, user message, and preferences in one SQLite transaction."""

    atomic = True

    def __init__(self, *, session_repository, run_store) -> None:
        self._sessions = session_repository
        self._runs = run_store

    def persist_start(
        self,
        *,
        record: AgentRunRecord,
        message_id: str,
        message: str,
        preferences: UserPreferences | None,
    ) -> Message:
        with self._runs.database.transaction(immediate=True) as conn:
            self._runs.save_in_transaction(conn, record)
            stored = self._sessions.add_message_in_transaction(
                conn,
                session_id=record.conversation_id,
                role="user",
                content=message,
                message_id=message_id,
                source_run_id=record.run_id,
            )
            if stored is None:
                raise RuntimeError("Query start message already exists")
            if preferences is not None:
                self._sessions._save_user_preferences(
                    conn,
                    replace(
                        preferences,
                        last_active_session_id=record.conversation_id,
                        updated_at=stored.created_at,
                    ),
                )
        return stored

    def persist_assistant_once(
        self,
        *,
        run_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> Message | None:
        with self._runs.database.transaction(immediate=True) as conn:
            return self._sessions.add_message_in_transaction(
                conn,
                session_id=conversation_id,
                role="assistant",
                content=content,
                message_id=message_id,
                source_run_id=run_id,
            )


def create_query_unit_of_work(
    *,
    session_service,
    session_repository,
    run_store,
) -> QueryUnitOfWork | None:
    from ai_agent_platform.agents.coding.store import InMemoryAgentRunStore
    from ai_agent_platform.repositories.memory import InMemorySessionRepository
    from ai_agent_platform.repositories.postgres import (
        PostgresAgentRunRepository,
        PostgresSessionRepository,
    )
    from ai_agent_platform.repositories.sqlite import (
        SQLiteAgentRunRepository,
        SQLiteSessionRepository,
    )

    if isinstance(session_repository, InMemorySessionRepository) and isinstance(
        run_store, InMemoryAgentRunStore
    ):
        return InMemoryQueryUnitOfWork(
            session_service=session_service,
            session_repository=session_repository,
            run_store=run_store,
        )
    if isinstance(session_repository, PostgresSessionRepository) and isinstance(
        run_store, PostgresAgentRunRepository
    ):
        if session_repository._database_url != run_store._database_url:
            raise ValueError("Query stores must use the same PostgreSQL database")
        return PostgresQueryUnitOfWork(
            session_repository=session_repository,
            run_store=run_store,
        )
    if isinstance(session_repository, SQLiteSessionRepository) and isinstance(
        run_store, SQLiteAgentRunRepository
    ):
        if session_repository.database.path != run_store.database.path:
            raise ValueError("Query stores must use the same SQLite database")
        return SQLiteQueryUnitOfWork(
            session_repository=session_repository,
            run_store=run_store,
        )
    return None


__all__ = [
    "InMemoryQueryUnitOfWork",
    "PostgresQueryUnitOfWork",
    "QueryUnitOfWork",
    "create_query_unit_of_work",
]
