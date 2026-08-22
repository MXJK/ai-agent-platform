"""Composition root for the versioned HTTP API."""

from fastapi import APIRouter

from ai_agent_platform.api.routes import (
    create_agent_runs_router,
    create_change_sets_router,
    create_chat_router,
    create_evals_router,
    create_health_router,
    create_knowledge_bases_router,
    create_model_registry_router,
    create_mcp_registry_router,
    create_skill_registry_router,
    create_memory_router,
    create_sessions_router,
    create_workspaces_router,
    create_project_memories_router,
)
from ai_agent_platform.core import MetricsRegistry, Settings, TaskQueue
from ai_agent_platform.integrations import (
    DirectoryPicker,
    LLMClient,
    MCPConnectionManager,
    MCPRegistryService,
)
from ai_agent_platform.model_registry import ModelRegistryService
from ai_agent_platform.services import (
    ChangeSetService,
    KnowledgeBaseService,
    QueryService,
    SessionService,
    WorkspaceService,
)
from ai_agent_platform.project_memory import ProjectMemoryService
from ai_agent_platform.memory import UserMemoryService
from ai_agent_platform.skills import SkillRegistryService


def create_api_router(
    session_service: SessionService,
    llm_client: LLMClient,
    knowledge_base_service: KnowledgeBaseService,
    query_service: QueryService,
    change_set_service: ChangeSetService,
    workspace_service: WorkspaceService,
    project_memory_service: ProjectMemoryService,
    user_memory_service: UserMemoryService,
    settings: Settings,
    metrics: MetricsRegistry,
    task_queue: TaskQueue,
    model_registry: ModelRegistryService,
    directory_picker: DirectoryPicker,
    mcp_registry: MCPRegistryService,
    skill_registry: SkillRegistryService,
    mcp_connection_manager: MCPConnectionManager | None = None,
    eval_service: object | None = None,
) -> APIRouter:
    router = APIRouter()
    router.include_router(
        create_health_router(
            metrics,
            service_name=settings.app_name,
            session_storage=settings.session_repository,
            mcp_connection_manager=mcp_connection_manager,
        )
    )
    router.include_router(
        create_sessions_router(
            session_service,
            settings,
            workspace_service=workspace_service,
            memory_service=project_memory_service,
            model_registry=model_registry,
            llm_client=llm_client,
        )
    )
    router.include_router(
        create_model_registry_router(model_registry, session_service, settings)
    )
    router.include_router(create_mcp_registry_router(mcp_registry, settings))
    router.include_router(create_skill_registry_router(skill_registry, settings))
    router.include_router(
        create_chat_router(
            session_service,
            llm_client,
            settings,
            metrics,
            project_memory_service=project_memory_service,
            task_queue=task_queue,
            model_registry=model_registry,
            user_memory_service=user_memory_service,
        )
    )
    router.include_router(create_agent_runs_router(query_service, settings))
    router.include_router(create_evals_router(eval_service))
    router.include_router(create_change_sets_router(change_set_service, settings))
    router.include_router(
        create_workspaces_router(
            workspace_service,
            memory_service=project_memory_service,
            session_service=session_service,
            settings=settings,
            directory_picker=directory_picker,
        )
    )
    router.include_router(
        create_project_memories_router(project_memory_service, settings)
    )
    router.include_router(
        create_memory_router(session_service, user_memory_service, settings)
    )
    router.include_router(
        create_knowledge_bases_router(
            knowledge_base_service,
            llm_client,
            session_service=session_service,
            workspace_service=workspace_service,
            model_registry=model_registry,
        )
    )
    return router
