from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from ai_agent_platform.domain import (
    Message,
    Session,
    TokenUsageRecord,
    WorkspaceRecord,
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


class InMemoryWorkspaceRepository:
    """Stores registered workspace roots in process memory."""

    def __init__(self) -> None:
        self._workspaces: dict[str, WorkspaceRecord] = {}
        self._lock = Lock()

    def upsert(self, *, workspace_id: str, root_path: str) -> WorkspaceRecord:
        with self._lock:
            now = _now()
            existing = self._workspaces.get(workspace_id)
            record = WorkspaceRecord(
                id=workspace_id,
                root_path=root_path,
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
            self._workspaces[workspace_id] = record
            return record

    def get(self, workspace_id: str) -> WorkspaceRecord | None:
        with self._lock:
            return self._workspaces.get(workspace_id)

    def list(self) -> list[WorkspaceRecord]:
        with self._lock:
            return sorted(
                self._workspaces.values(),
                key=lambda record: record.id,
            )


def _now() -> datetime:
    return datetime.now(timezone.utc)
