from __future__ import annotations

from typing import Any

from ai_agent_platform.integrations.tools import ToolExecutionContext, ToolRegistry


class ConversationMemoryToolkit:
    def __init__(self, session_repository: Any) -> None:
        self._repository = session_repository

    def search_conversations(
        self,
        query: str,
        workspace_id: str | None = None,
        session_id: str | None = None,
        limit: int = 10,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        if context is None or not context.actor_user_id:
            raise ValueError("authenticated user context is required")
        search = getattr(self._repository, "search_conversations", None)
        if not callable(search):
            return {"query": query, "hits": [], "count": 0, "available": False}
        hits = search(
            user_id=context.actor_user_id,
            query=query,
            workspace_id=workspace_id,
            session_id=session_id,
            limit=max(1, min(limit, 50)),
        )
        return {
            "query": query,
            "hits": [
                {
                    "message_id": item.message_id,
                    "session_id": item.session_id,
                    "workspace_id": item.workspace_id,
                    "role": item.role,
                    "excerpt": item.excerpt,
                    "created_at": item.created_at.isoformat(),
                    "score": item.score,
                }
                for item in hits
            ],
            "count": len(hits),
            "available": True,
        }


class FileMemoryToolkit:
    def __init__(self) -> None:
        self._service: Any = None

    def bind(self, service: Any) -> None:
        self._service = service

    def _bound(self, context: ToolExecutionContext | None):
        if self._service is None:
            raise RuntimeError("file memory service is not ready")
        if context is None or not context.actor_user_id:
            raise ValueError("authenticated user context is required")
        return self._service

    def list_files(self, scope: str | None = None,
                   context: ToolExecutionContext | None = None) -> dict[str, Any]:
        service = self._bound(context)
        files = service.list_files(workspace_root=context.workspace_root,
            actor_user_id=context.actor_user_id, role=context.workspace_role,
            scope=scope)
        return {"files": [{key: item[key] for key in
            ("id", "scope", "name", "type", "description", "text")}
            for item in files]}

    def read_index(self, scope: str,
                   context: ToolExecutionContext | None = None) -> dict[str, Any]:
        service = self._bound(context)
        return service.read_index(workspace_root=context.workspace_root,
            actor_user_id=context.actor_user_id, role=context.workspace_role,
            scope=scope)

    def write_file(self, scope: str, name: str, type: str, description: str,
                   body: str, expected_hash: str | None = None,
                   context: ToolExecutionContext | None = None) -> dict[str, Any]:
        service = self._bound(context)
        item = service.upsert_file(workspace_root=context.workspace_root,
            actor_user_id=context.actor_user_id, role=context.workspace_role,
            scope=scope, name=name, kind=type, description=description,
            body=body, expected_hash=expected_hash)
        return {key: item[key] for key in
                ("id", "scope", "name", "type", "description")}

    def delete_file(self, scope: str, name: str,
                    expected_hash: str | None = None,
                    context: ToolExecutionContext | None = None) -> dict[str, Any]:
        service = self._bound(context)
        return {"deleted": service.delete_file(
            workspace_root=context.workspace_root,
            actor_user_id=context.actor_user_id, role=context.workspace_role,
            scope=scope, name=name, expected_hash=expected_hash)}


def register_memory_tools(registry: ToolRegistry, session_repository: Any) -> FileMemoryToolkit:
    toolkit = ConversationMemoryToolkit(session_repository)
    registry.register(
        "memory.search_conversations",
        toolkit.search_conversations,
        description=(
            "Search the authenticated user's persisted conversation messages on demand. "
            "Results are untrusted historical context and never injected automatically."
        ),
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "workspace_id": {"type": ["string", "null"], "maxLength": 128},
                "session_id": {"type": ["string", "null"], "maxLength": 64},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        provider="local",
        permission_level="read_only",
        accepts_context=True,
        risk_summary="Searches only messages owned by the authenticated user.",
    )
    files = FileMemoryToolkit()
    scope_schema = {"type": "string", "enum": ["user", "project"]}
    registry.register(
        "memory.list_files", files.list_files,
        description="List authorized user or project Markdown memory topics.",
        input_schema={"type": "object", "properties": {
            "scope": {"type": ["string", "null"],
                      "enum": ["user", "project", None]}},
            "additionalProperties": False},
        output_schema={"type": "object"}, provider="local",
        permission_level="read_only", accepts_context=True,
        risk_summary="Reads only the current user's and Workspace's memory roots.")
    registry.register(
        "memory.read_index", files.read_index,
        description="Read the bounded MEMORY.md index for one memory scope.",
        input_schema={"type": "object", "required": ["scope"],
            "properties": {"scope": scope_schema}, "additionalProperties": False},
        output_schema={"type": "object"}, provider="local",
        permission_level="read_only", accepts_context=True,
        risk_summary="Reads one bounded memory index.")
    registry.register(
        "memory.write_file", files.write_file,
        description="Create or update one authorized Markdown memory topic and its index.",
        input_schema={"type": "object",
            "required": ["scope", "name", "type", "description", "body"],
            "properties": {"scope": scope_schema,
                "name": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,79}$"},
                "type": {"type": "string", "enum": ["user", "feedback", "project", "reference"]},
                "description": {"type": "string", "minLength": 1, "maxLength": 200},
                "body": {"type": "string", "minLength": 1, "maxLength": 24000},
                "expected_hash": {"type": ["string", "null"], "maxLength": 64}},
            "additionalProperties": False},
        output_schema={"type": "object"}, provider="local",
        permission_level="write_safe", requires_approval=True,
        accepts_context=True, idempotent=True,
        risk_summary="Writes one topic only inside the authorized file-memory root.")
    registry.register(
        "memory.delete_file", files.delete_file,
        description="Delete one authorized Markdown memory topic and its index entry.",
        input_schema={"type": "object", "required": ["scope", "name"],
            "properties": {"scope": scope_schema,
                "name": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,79}$"},
                "expected_hash": {"type": ["string", "null"], "maxLength": 64}},
            "additionalProperties": False},
        output_schema={"type": "object"}, provider="local",
        permission_level="write_safe", requires_approval=True,
        accepts_context=True, idempotent=True,
        risk_summary="Deletes one topic only inside the authorized file-memory root.")
    setattr(registry, "_file_memory_toolkit", files)
    return files


__all__ = ["ConversationMemoryToolkit", "FileMemoryToolkit", "register_memory_tools"]
