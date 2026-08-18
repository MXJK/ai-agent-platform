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


def register_memory_tools(registry: ToolRegistry, session_repository: Any) -> None:
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


__all__ = ["ConversationMemoryToolkit", "register_memory_tools"]
