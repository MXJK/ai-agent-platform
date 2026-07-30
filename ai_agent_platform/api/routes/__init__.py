"""Resource-oriented FastAPI router factories."""

from ai_agent_platform.api.routes.agent_runs import create_agent_runs_router
from ai_agent_platform.api.routes.chat import create_chat_router
from ai_agent_platform.api.routes.health import create_health_router
from ai_agent_platform.api.routes.knowledge_bases import create_knowledge_bases_router
from ai_agent_platform.api.routes.sessions import create_sessions_router
from ai_agent_platform.api.routes.workspaces import create_workspaces_router
from ai_agent_platform.api.routes.project_memories import (
    create_project_memories_router,
)

__all__ = [
    "create_agent_runs_router",
    "create_chat_router",
    "create_health_router",
    "create_knowledge_bases_router",
    "create_sessions_router",
    "create_workspaces_router",
    "create_project_memories_router",
]
