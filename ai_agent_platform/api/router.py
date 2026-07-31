"""Composition root for the versioned HTTP API."""

from fastapi import APIRouter

from ai_agent_platform.api.routes import (
    create_agent_runs_router,
    create_chat_router,
    create_health_router,
    create_knowledge_bases_router,
    create_sessions_router,
    create_workspaces_router,
    create_project_memories_router,
)
from ai_agent_platform.core import MetricsRegistry, Settings, TaskQueue
from ai_agent_platform.integrations import LLMClient
from ai_agent_platform.services import (
    AgentRunService,
    KnowledgeBaseService,
    SessionService,
    WorkspaceService,
)
from ai_agent_platform.project_memory import ProjectMemoryService


def create_api_router(
    session_service: SessionService,
    llm_client: LLMClient,
    knowledge_base_service: KnowledgeBaseService,
    agent_run_service: AgentRunService,
    workspace_service: WorkspaceService,
    project_memory_service: ProjectMemoryService,
    settings: Settings,
    metrics: MetricsRegistry,
    task_queue: TaskQueue,
) -> APIRouter:
    router = APIRouter()
    router.include_router(
        create_health_router(metrics, service_name=settings.app_name)
    )
    router.include_router(create_sessions_router(session_service, settings))
    router.include_router(
        create_chat_router(
            session_service,
            llm_client,
            settings,
            metrics,
            project_memory_service=project_memory_service,
            task_queue=task_queue,
        )
    )
    router.include_router(create_agent_runs_router(agent_run_service, settings))
    router.include_router(
        create_workspaces_router(
            workspace_service,
            memory_service=project_memory_service,
            session_service=session_service,
            settings=settings,
        )
    )
    router.include_router(
        create_project_memories_router(project_memory_service, settings)
    )
    router.include_router(
        create_knowledge_bases_router(knowledge_base_service, llm_client)
    )
    return router
