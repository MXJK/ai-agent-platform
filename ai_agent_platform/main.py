from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ai_agent_platform.agents import (
    CodingAgentRuntime,
    GameAgentRuntime,
    LLMStructuredAgentPlanner,
    create_coding_tool_registry,
)
from ai_agent_platform.api import create_api_router
from ai_agent_platform.core import (
    CeleryTaskQueue,
    InProcessTaskQueue,
    MetricsRegistry,
    RequestObservabilityMiddleware,
    Settings,
    configure_logging,
)
from ai_agent_platform.integrations import (
    LLMClient,
    MCPToolProvider,
    RAGService,
    create_mcp_providers_from_config_file,
    create_rag_service,
)
from ai_agent_platform.project_memory.factory import create_project_memory_service
from ai_agent_platform.repositories import (
    InMemoryKnowledgeBaseRepository,
    InMemorySessionRepository,
    InMemoryWorkspaceRepository,
    PostgresAgentRunRepository,
    PostgresDocumentRepository,
    PostgresKnowledgeBaseRepository,
    PostgresSessionRepository,
    PostgresWorkspaceRepository,
)
from ai_agent_platform.services import (
    AgentRunService,
    KnowledgeBaseService,
    SessionService,
    WorkspaceService,
    create_conversation_compressor,
)


def create_app(
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    rag_service: RAGService | None = None,
    coding_agent_runtime: CodingAgentRuntime | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(level=settings.log_level, log_format=settings.log_format)
    metrics = MetricsRegistry()
    if settings.task_queue_backend == "celery":
        task_queue = CeleryTaskQueue(
            broker_url=settings.redis_url,
            result_backend_url=settings.celery_result_backend_url,
            visibility_timeout_seconds=(
                settings.celery_visibility_timeout_seconds
            ),
            publish_max_retries=settings.celery_task_max_retries,
            publish_retry_backoff_seconds=(
                settings.celery_task_retry_backoff_seconds
            ),
            publish_retry_backoff_max_seconds=(
                settings.celery_task_retry_backoff_max_seconds
            ),
            metrics=metrics,
        )
    else:
        task_queue = InProcessTaskQueue(
            max_workers=settings.background_task_workers,
            max_queue_size=settings.background_task_queue_capacity,
            metrics=metrics,
        )

    repository = _create_session_repository(settings)
    agent_runtime = GameAgentRuntime()
    llm_client = llm_client or LLMClient(settings)
    workspace_service = WorkspaceService(
        store=_create_workspace_store(settings),
        allowed_roots=(
            settings.workspace_allowed_roots
            or (str(Path.cwd().resolve()),)
        ),
    )
    project_memory_service = create_project_memory_service(
        settings,
        workspace_service=workspace_service,
        llm_client=llm_client,
        metrics=metrics,
    )
    project_memory_service.set_index_outbox_submitter(
        lambda trigger_id: task_queue.submit(
            "memory_index_outbox",
            project_memory_service.process_index_outbox,
            trigger_id=trigger_id,
        )
    )
    rag_service = rag_service or create_rag_service(
        settings,
        document_store=_create_document_store(settings),
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
        sandbox_workspace_parent=settings.sandbox_workspace_parent,
    )
    close_checkpointer = None
    if coding_agent_runtime is None:
        checkpointer, close_checkpointer = _create_langgraph_checkpointer(settings)
        coding_agent_runtime = CodingAgentRuntime(
            tool_registry=tool_registry,
            run_store=_create_agent_run_store(settings),
            checkpointer=checkpointer,
            planner=LLMStructuredAgentPlanner(llm_client),
            max_exploration_rounds=settings.agent_max_exploration_rounds,
            max_read_tools_per_round=settings.agent_max_read_tools_per_round,
            max_context_files=settings.agent_max_context_files,
            max_context_chars=settings.agent_max_context_chars,
            max_instruction_chars=settings.agent_max_instruction_chars,
            max_tool_rounds=settings.agent_max_tool_rounds,
            max_tool_calls=settings.agent_max_tool_calls,
            max_history_messages=settings.llm_max_context_messages,
            knowledge_context_provider=knowledge_base_service,
            project_memory_provider=project_memory_service,
            max_rag_context_chars=settings.rag_max_prompt_chars,
        )
    session_service = SessionService(
        repository=repository,
        agent_runtime=agent_runtime,
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
    )
    agent_run_service = AgentRunService(
        runtime=coding_agent_runtime,
        session_service=session_service,
        workspace_service=workspace_service,
        project_memory_service=project_memory_service,
        metrics=metrics,
        task_queue=task_queue,
        max_context_messages=settings.llm_max_context_messages,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            app.state.agent_run_service.close()
            app.state.task_queue.close()
            if close_checkpointer is not None:
                close_checkpointer()
            for provider in app.state.mcp_providers:
                provider.close()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(RequestObservabilityMiddleware, metrics=metrics)
    app.state.metrics = metrics
    app.state.mcp_providers = mcp_providers
    app.state.tool_registry = tool_registry
    app.state.agent_run_service = agent_run_service
    app.state.session_service = session_service
    app.state.workspace_service = workspace_service
    app.state.knowledge_base_service = knowledge_base_service
    app.state.project_memory_service = project_memory_service
    app.state.task_queue = task_queue
    static_dir = Path(__file__).parent / "static"

    app.include_router(
        create_api_router(
            session_service=session_service,
            llm_client=llm_client,
            knowledge_base_service=knowledge_base_service,
            agent_run_service=agent_run_service,
            workspace_service=workspace_service,
            project_memory_service=project_memory_service,
            settings=settings,
            metrics=metrics,
            task_queue=task_queue,
        ),
        prefix=settings.api_prefix,
    )
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def frontend() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


def _create_mcp_providers(settings: Settings) -> list[MCPToolProvider]:
    if not settings.mcp_enabled:
        return []
    if not settings.mcp_config_path:
        raise ValueError("MCP_CONFIG_PATH is required when MCP_ENABLED=true")
    return create_mcp_providers_from_config_file(
        settings.mcp_config_path,
        request_timeout_seconds=settings.mcp_request_timeout_seconds,
    )


def _create_session_repository(settings: Settings):
    if settings.session_repository == "memory":
        return InMemorySessionRepository()
    if settings.session_repository == "postgres":
        return PostgresSessionRepository(database_url=settings.database_url)
    raise ValueError(f"unsupported session repository: {settings.session_repository}")


def _create_agent_run_store(settings: Settings):
    if settings.agent_run_store == "memory":
        return None
    if settings.agent_run_store == "postgres":
        return PostgresAgentRunRepository(database_url=settings.database_url)
    raise ValueError(f"unsupported agent run store: {settings.agent_run_store}")


def _create_document_store(settings: Settings):
    if settings.document_store == "memory":
        return None
    if settings.document_store == "postgres":
        return PostgresDocumentRepository(database_url=settings.database_url)
    raise ValueError(f"unsupported document store: {settings.document_store}")


def _create_knowledge_base_store(settings: Settings):
    if settings.document_store == "memory":
        return InMemoryKnowledgeBaseRepository()
    if settings.document_store == "postgres":
        return PostgresKnowledgeBaseRepository(database_url=settings.database_url)
    raise ValueError(
        f"unsupported knowledge-base store: {settings.document_store}"
    )


def _create_workspace_store(settings: Settings):
    if settings.workspace_store == "memory":
        return InMemoryWorkspaceRepository()
    if settings.workspace_store == "postgres":
        return PostgresWorkspaceRepository(database_url=settings.database_url)
    raise ValueError(
        f"unsupported workspace store: {settings.workspace_store}"
    )


def _create_langgraph_checkpointer(settings: Settings):
    if settings.langgraph_checkpointer == "memory":
        return None, None
    if settings.langgraph_checkpointer == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise RuntimeError(
                "langgraph-checkpoint-postgres and psycopg-pool are required "
                "for LANGGRAPH_CHECKPOINTER=postgres"
            ) from exc

        pool = ConnectionPool(
            conninfo=settings.database_url,
            kwargs={"autocommit": True},
        )
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()
        return checkpointer, pool.close
    raise ValueError(
        f"unsupported LangGraph checkpointer: {settings.langgraph_checkpointer}"
    )


class LazyASGIApp:
    """Creates the real FastAPI app on first ASGI use or attribute access."""

    def __init__(self, factory: Callable[[], FastAPI]) -> None:
        self._factory = factory
        self._app: FastAPI | None = None
        self._lock = Lock()

    def _get_app(self) -> FastAPI:
        if self._app is None:
            with self._lock:
                if self._app is None:
                    self._app = self._factory()
        return self._app

    async def __call__(self, scope, receive, send):
        await self._get_app()(scope, receive, send)

    def __getattr__(self, name: str):
        return getattr(self._get_app(), name)


app = LazyASGIApp(create_app)
