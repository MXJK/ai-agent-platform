"""Build process-local service instances used by Celery workers."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from ai_agent_platform.agents import (
    CodingAgentRuntime,
    GameAgentRuntime,
    LLMStructuredAgentPlanner,
    create_coding_tool_registry,
)
from ai_agent_platform.core import MetricsRegistry, Settings, TaskQueueError
from ai_agent_platform.integrations import LLMClient, create_rag_service
from ai_agent_platform.main import (
    _create_agent_run_store,
    _create_document_store,
    _create_langgraph_checkpointer,
    _create_mcp_providers,
    _create_repository_index_store,
    _create_session_repository,
)
from ai_agent_platform.services import (
    AgentRunService,
    RepositoryIndexingService,
    SessionService,
)


class WorkerOnlyTaskQueue:
    """Prevents a worker handler from recursively publishing more tasks."""

    def submit(
        self,
        task_name: str,
        function: Callable[..., None],
        **kwargs: Any,
    ) -> Any:
        del function, kwargs
        raise TaskQueueError(f"worker cannot recursively submit {task_name}")

    def close(self) -> None:
        return None


@dataclass
class WorkerServices:
    agent_run_service: AgentRunService
    repository_indexing_service: RepositoryIndexingService
    close_callbacks: list[Callable[[], None]]

    def close(self) -> None:
        for callback in reversed(self.close_callbacks):
            callback()


_services: WorkerServices | None = None
_services_lock = Lock()


def get_worker_services() -> WorkerServices:
    global _services
    if _services is None:
        with _services_lock:
            if _services is None:
                _services = _create_worker_services()
    return _services


def close_worker_services() -> None:
    global _services
    with _services_lock:
        services = _services
        _services = None
    if services is not None:
        services.close()


def _create_worker_services() -> WorkerServices:
    settings = Settings.from_env()
    if settings.task_queue_backend != "celery":
        raise RuntimeError("Celery worker requires TASK_QUEUE_BACKEND=celery")

    metrics = MetricsRegistry()
    session_repository = _create_session_repository(settings)
    llm_client = LLMClient(settings)
    rag_service = create_rag_service(
        settings,
        document_store=_create_document_store(settings),
    )
    mcp_providers = _create_mcp_providers(settings)
    tool_registry = create_coding_tool_registry(
        mcp_providers=mcp_providers,
        sandbox_mode=settings.sandbox_mode,
        sandbox_docker_image=settings.sandbox_docker_image,
        sandbox_command_timeout_seconds=settings.sandbox_command_timeout_seconds,
        sandbox_workspace_parent=settings.sandbox_workspace_parent,
    )
    checkpointer, close_checkpointer = _create_langgraph_checkpointer(settings)
    coding_runtime = CodingAgentRuntime(
        rag_service=rag_service,
        tool_registry=tool_registry,
        run_store=_create_agent_run_store(settings),
        checkpointer=checkpointer,
        planner=LLMStructuredAgentPlanner(llm_client),
    )
    session_service = SessionService(
        repository=session_repository,
        agent_runtime=GameAgentRuntime(),
    )
    worker_queue = WorkerOnlyTaskQueue()
    agent_run_service = AgentRunService(
        runtime=coding_runtime,
        session_service=session_service,
        metrics=metrics,
        task_queue=worker_queue,
    )
    repository_indexing_service = RepositoryIndexingService(
        rag_service=rag_service,
        index_store=_create_repository_index_store(settings),
        task_queue=worker_queue,
    )
    close_callbacks = [provider.close for provider in mcp_providers]
    if close_checkpointer is not None:
        close_callbacks.append(close_checkpointer)
    return WorkerServices(
        agent_run_service=agent_run_service,
        repository_indexing_service=repository_indexing_service,
        close_callbacks=close_callbacks,
    )
