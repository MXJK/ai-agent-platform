from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from ai_agent_platform.domain import (
    ConversationSummary,
    KnowledgeBaseRecord,
    Message,
    Session,
    TokenUsageRecord,
    UserPreferences,
    WorkspaceRecord,
)


class SessionNotFoundError(Exception):
    pass


class SessionArchivedError(Exception):
    pass


class InMemorySessionRepository:
    """Stores sessions and messages in process memory for the first backend version."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._messages: dict[str, list[Message]] = defaultdict(list)
        self._conversation_summaries: dict[str, ConversationSummary] = {}
        self._token_usage: dict[str, TokenUsageRecord] = {}
        self._user_preferences: dict[str, UserPreferences] = {}
        self._lock = Lock()

    def create_session(
        self,
        user_id: str,
        preferences: UserPreferences | None = None,
    ) -> Session:
        with self._lock:
            now = _now()
            preferences = preferences or self._user_preferences.get(user_id)
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
            self._sessions[session.id] = session
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
        with self._lock:
            normalized_query = (query or "").strip().casefold()
            sessions = []
            for session in self._sessions.values():
                if user_id is not None and session.user_id != user_id:
                    continue
                if session.message_count <= 0:
                    continue
                if archived is not None and (session.archived_at is not None) != archived:
                    continue
                updated_at = session.updated_at or session.created_at
                if before is not None and (updated_at, session.id) >= before:
                    continue
                if normalized_query:
                    searchable = [session.title]
                    searchable.extend(
                        message.content for message in self._messages[session.id]
                    )
                    if not any(
                        normalized_query in value.casefold() for value in searchable
                    ):
                        continue
                sessions.append(session)
            sessions.sort(
                key=lambda item: (item.updated_at or item.created_at, item.id),
                reverse=True,
            )
            return sessions[:limit] if limit is not None else sessions

    def get_session(self, session_id: str) -> Session:
        with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError as exc:
                raise SessionNotFoundError(session_id) from exc

    def save_session(
        self,
        session: Session,
        *,
        preferences: UserPreferences | None = None,
    ) -> Session:
        with self._lock:
            if session.id not in self._sessions:
                raise SessionNotFoundError(session.id)
            self._sessions[session.id] = session
            if preferences is not None:
                self._user_preferences[preferences.user_id] = preferences
            return session

    def get_user_preferences(self, user_id: str) -> UserPreferences | None:
        with self._lock:
            return self._user_preferences.get(user_id)

    def save_user_preferences(
        self, preferences: UserPreferences
    ) -> UserPreferences:
        with self._lock:
            self._user_preferences[preferences.user_id] = preferences
            return preferences

    def add_message(self, session_id: str, role: str, content: str) -> Message:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)

            session = self._sessions[session_id]
            if session.archived_at is not None:
                raise SessionArchivedError(session_id)

            now = _now()
            message = Message(
                id=f"msg_{uuid4().hex[:12]}",
                session_id=session_id,
                role=role,
                content=content,
                created_at=now,
            )
            self._messages[session_id].append(message)
            title = session.title
            title_source = session.title_source
            if role == "user" and title_source == "default":
                title = _derive_session_title(content)
                title_source = "auto"
            self._sessions[session_id] = replace(
                session,
                title=title,
                title_source=title_source,
                updated_at=now,
                message_count=session.message_count + 1,
                last_message_preview=_message_preview(content),
            )
            return message

    def list_messages(self, session_id: str) -> list[Message]:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)
            return list(self._messages[session_id])

    def get_conversation_summary(
        self, session_id: str
    ) -> ConversationSummary | None:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)
            return self._conversation_summaries.get(session_id)

    def upsert_conversation_summary(
        self,
        summary: ConversationSummary,
        *,
        expected_version: int,
    ) -> ConversationSummary | None:
        with self._lock:
            if summary.session_id not in self._sessions:
                raise SessionNotFoundError(summary.session_id)
            current = self._conversation_summaries.get(summary.session_id)
            current_version = current.version if current is not None else 0
            if current_version != expected_version:
                return None
            self._conversation_summaries[summary.session_id] = summary
            return summary

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
        with self._lock:
            if session_id is not None and session_id not in self._sessions:
                raise SessionNotFoundError(session_id)

            usage_id = record_id or f"usage_{uuid4().hex[:12]}"
            existing = self._token_usage.get(usage_id)
            record = TokenUsageRecord(
                id=usage_id,
                session_id=session_id,
                workspace_id=workspace_id,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thoughts_tokens=thoughts_tokens,
                total_tokens=input_tokens + output_tokens + thoughts_tokens,
                created_at=existing.created_at if existing is not None else _now(),
                operation=operation,
                resource_id=resource_id,
                requested_provider=requested_provider,
                requested_model=requested_model,
                input_count_method=input_count_method,
                budget_decision=budget_decision,
            )
            self._token_usage[usage_id] = record
            return record

    def list_token_usage(self, session_id: str) -> list[TokenUsageRecord]:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)
            return sorted(
                (
                    record
                    for record in self._token_usage.values()
                    if record.session_id == session_id
                ),
                key=lambda record: (record.created_at, record.id),
            )

    def list_workspace_token_usage(
        self, workspace_id: str
    ) -> list[TokenUsageRecord]:
        with self._lock:
            return [
                record
                for record in self._token_usage.values()
                if record.workspace_id == workspace_id
            ]

    def list_all_token_usage(self) -> list[TokenUsageRecord]:
        with self._lock:
            return sorted(
                self._token_usage.values(),
                key=lambda record: (record.created_at, record.id),
            )


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
                revision=(
                    existing.revision + 1
                    if existing is not None and existing.root_path != root_path
                    else existing.revision if existing is not None else 1
                ),
            )
            self._workspaces[workspace_id] = record
            return record

    def get(self, workspace_id: str) -> WorkspaceRecord | None:
        with self._lock:
            return self._workspaces.get(workspace_id)

    def get_by_root_path(self, root_path: str) -> WorkspaceRecord | None:
        with self._lock:
            return next(
                (
                    record
                    for record in self._workspaces.values()
                    if record.root_path == root_path
                ),
                None,
            )

    def list(self) -> list[WorkspaceRecord]:
        with self._lock:
            return sorted(
                self._workspaces.values(),
                key=lambda record: record.id,
            )


class InMemoryKnowledgeBaseRepository:
    """Stores knowledge-base catalog metadata for local and test runtimes."""

    def __init__(self) -> None:
        self._knowledge_bases: dict[str, KnowledgeBaseRecord] = {}
        self._document_ids: dict[str, set[str]] = defaultdict(set)
        self._lock = Lock()

    def create(
        self,
        *,
        knowledge_base_id: str,
        name: str,
        description: str,
        tags: list[str],
    ) -> KnowledgeBaseRecord | None:
        with self._lock:
            if knowledge_base_id in self._knowledge_bases:
                return None
            now = _now()
            record = KnowledgeBaseRecord(
                id=knowledge_base_id,
                name=name,
                description=description,
                tags=list(tags),
                document_count=0,
                created_at=now,
                updated_at=now,
            )
            self._knowledge_bases[knowledge_base_id] = record
            return record

    def get(self, knowledge_base_id: str) -> KnowledgeBaseRecord | None:
        with self._lock:
            record = self._knowledge_bases.get(knowledge_base_id)
            return self._with_count(record) if record is not None else None

    def list(self) -> list[KnowledgeBaseRecord]:
        with self._lock:
            return [
                self._with_count(self._knowledge_bases[item_id])
                for item_id in sorted(self._knowledge_bases)
            ]

    def update(
        self,
        *,
        knowledge_base_id: str,
        name: str,
        description: str,
        tags: list[str],
    ) -> KnowledgeBaseRecord | None:
        with self._lock:
            existing = self._knowledge_bases.get(knowledge_base_id)
            if existing is None:
                return None
            record = KnowledgeBaseRecord(
                id=existing.id,
                name=name,
                description=description,
                tags=list(tags),
                document_count=len(self._document_ids[knowledge_base_id]),
                created_at=existing.created_at,
                updated_at=_now(),
            )
            self._knowledge_bases[knowledge_base_id] = record
            return record

    def delete(self, knowledge_base_id: str) -> bool:
        with self._lock:
            if knowledge_base_id not in self._knowledge_bases:
                return False
            del self._knowledge_bases[knowledge_base_id]
            self._document_ids.pop(knowledge_base_id, None)
            return True

    def record_document(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        with self._lock:
            if knowledge_base_id in self._knowledge_bases:
                self._document_ids[knowledge_base_id].add(document_id)

    def _with_count(self, record: KnowledgeBaseRecord) -> KnowledgeBaseRecord:
        return KnowledgeBaseRecord(
            id=record.id,
            name=record.name,
            description=record.description,
            tags=list(record.tags),
            document_count=len(self._document_ids[record.id]),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _derive_session_title(content: str) -> str:
    normalized = " ".join(content.split()).strip()
    if not normalized:
        return "新会话"
    return normalized[:48]


def _message_preview(content: str) -> str:
    return " ".join(content.split()).strip()[:120]
