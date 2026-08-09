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
from ai_agent_platform.core import CeleryTaskQueue, MetricsRegistry, Settings
from ai_agent_platform.integrations import LLMClient, create_rag_service
from ai_agent_platform.project_memory.factory import create_project_memory_service
from ai_agent_platform.project_memory.service import ProjectMemoryService
from ai_agent_platform.main import (
    _create_agent_run_store,
    _create_document_store,
    _create_knowledge_base_store,
    _create_langgraph_checkpointer,
    _create_mcp_providers,
    _create_model_registry,
    _create_session_repository,
    _create_workspace_store,
)
from ai_agent_platform.services import (
    AgentRunService,
    KnowledgeBaseService,
    SessionService,
    UsageLedgerService,
    WorkspaceService,
    create_conversation_compressor,
)


@dataclass
class WorkerServices:
    agent_run_service: AgentRunService
    project_memory_service: ProjectMemoryService
    session_service: SessionService
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
    usage_ledger = UsageLedgerService(session_repository, settings)
    llm_client = LLMClient(settings, usage_ledger=usage_ledger)
    model_registry = _create_model_registry(settings, llm_client)
    workspace_service = WorkspaceService(
        store=_create_workspace_store(settings),
        allowed_roots=settings.workspace_allowed_roots,
    )
    worker_queue = CeleryTaskQueue(
        broker_url=settings.redis_url,
        result_backend_url=settings.celery_result_backend_url,
        visibility_timeout_seconds=settings.celery_visibility_timeout_seconds,
        publish_max_retries=settings.celery_task_max_retries,
        publish_retry_backoff_seconds=settings.celery_task_retry_backoff_seconds,
        publish_retry_backoff_max_seconds=(
            settings.celery_task_retry_backoff_max_seconds
        ),
        metrics=metrics,
    )
    project_memory_service = create_project_memory_service(
        settings,
        workspace_service=workspace_service,
        llm_client=llm_client,
        metrics=metrics,
        usage_ledger=usage_ledger,
    )
    project_memory_service.set_index_outbox_submitter(
        lambda trigger_id: worker_queue.submit(
            "memory_index_outbox",
            project_memory_service.process_index_outbox,
            trigger_id=trigger_id,
        )
    )
    rag_service = create_rag_service(
        settings,
        document_store=_create_document_store(settings),
        usage_ledger=usage_ledger,
    )
    knowledge_base_service = KnowledgeBaseService(
        store=_create_knowledge_base_store(settings),
        rag_service=rag_service,
    )
    mcp_providers = _create_mcp_providers(settings)
    tool_registry = create_coding_tool_registry(
        mcp_providers=mcp_providers,
        sandbox_mode=settings.sandbox_mode,
        sandbox_docker_image=settings.sandbox_docker_image,
        sandbox_command_timeout_seconds=settings.sandbox_command_timeout_seconds,
        sandbox_command_output_max_chars=(
            settings.sandbox_command_output_max_chars
        ),
        sandbox_workspace_parent=settings.sandbox_workspace_parent,
        sandbox_workspace_ttl_seconds=settings.sandbox_workspace_ttl_seconds,
        sandbox_allowed_commands=settings.sandbox_allowed_commands,
    )
    checkpointer, close_checkpointer = _create_langgraph_checkpointer(settings)
    coding_runtime = CodingAgentRuntime(
        tool_registry=tool_registry,
        run_store=_create_agent_run_store(settings),
        checkpointer=checkpointer,
        planner=LLMStructuredAgentPlanner(llm_client),
        max_exploration_rounds=settings.agent_max_exploration_rounds,
        max_read_tools_per_round=settings.agent_max_read_tools_per_round,
        max_context_files=settings.agent_max_context_files,
        max_context_chars=settings.agent_max_context_chars,
        max_instruction_chars=settings.agent_max_instruction_chars,
        soft_tool_rounds=settings.agent_soft_tool_rounds,
        max_tool_rounds=settings.agent_max_tool_rounds,
        soft_tool_calls=settings.agent_soft_tool_calls,
        max_tool_calls=settings.agent_max_tool_calls,
        max_elapsed_seconds=settings.agent_max_elapsed_seconds,
        no_progress_rounds=settings.agent_no_progress_rounds,
        max_consecutive_failures=settings.agent_max_consecutive_failures,
        native_context_max_chars=settings.agent_native_context_max_chars,
        native_context_keep_messages=(
            settings.agent_native_context_keep_messages
        ),
        graph_recursion_limit=settings.agent_graph_recursion_limit,
        approval_policy=settings.agent_approval_policy,
        max_history_messages=settings.llm_max_context_messages,
        knowledge_context_provider=knowledge_base_service,
        project_memory_provider=project_memory_service,
        max_rag_context_chars=settings.rag_max_prompt_chars,
    )
    session_service = SessionService(
        repository=session_repository,
        agent_runtime=GameAgentRuntime(),
        compressor=create_conversation_compressor(
            llm_provider=settings.llm_provider,
            llm_client=llm_client,
        ),
        summary_enabled=settings.conversation_summary_enabled,
        summary_trigger_messages=settings.conversation_summary_trigger_messages,
        summary_keep_recent_messages=(
            settings.conversation_summary_keep_recent_messages
        ),
        summary_max_chars=settings.conversation_summary_max_chars,
        summary_max_source_chars=(
            settings.conversation_summary_max_source_chars
        ),
        metrics=metrics,
        usage_ledger=usage_ledger,
        default_provider=settings.llm_provider,
        default_model=settings.llm_model,
        default_thinking_level=settings.llm_thinking_level,
    )
    agent_run_service = AgentRunService(
        runtime=coding_runtime,
        session_service=session_service,
        workspace_service=workspace_service,
        metrics=metrics,
        task_queue=worker_queue,
        project_memory_service=project_memory_service,
        max_context_messages=settings.llm_max_context_messages,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        model_registry=model_registry,
    )
    close_callbacks = [provider.close for provider in mcp_providers]
    close_callbacks.append(tool_registry.close)
    close_callbacks.append(worker_queue.close)
    if close_checkpointer is not None:
        close_callbacks.append(close_checkpointer)
    return WorkerServices(
        agent_run_service=agent_run_service,
        project_memory_service=project_memory_service,
        session_service=session_service,
        close_callbacks=close_callbacks,
    )
