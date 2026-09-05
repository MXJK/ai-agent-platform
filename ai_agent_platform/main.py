from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ai_agent_platform.cogent import AgentRuntime
from ai_agent_platform.api import create_api_router
from ai_agent_platform.core import (
    ConfigResolver,
    RequestObservabilityMiddleware,
    ResolvedConfig,
    Settings,
)
from ai_agent_platform.integrations import (
    DirectoryPicker,
    LLMClient,
    RAGService,
)
from ai_agent_platform.runtime import ApplicationFactory, build_runtime


def create_app(
    settings: Settings | ResolvedConfig | None = None,
    llm_client: LLMClient | None = None,
    rag_service: RAGService | None = None,
    coding_agent_runtime: AgentRuntime | None = None,
    directory_picker: DirectoryPicker | None = None,
    application_factory: ApplicationFactory | None = None,
) -> FastAPI:
    if settings is None:
        resolved_config = ConfigResolver.from_default_locations().resolve_process()
        settings = resolved_config.settings
    elif isinstance(settings, ResolvedConfig):
        resolved_config = settings
        settings = resolved_config.settings
    else:
        resolved_config = ResolvedConfig.from_settings(settings)
    runtime = build_runtime(
        resolved_config,
        role="api",
        factory=application_factory,
        llm_client=llm_client,
        rag_service=rag_service,
        coding_agent_runtime=coding_agent_runtime,
        directory_picker=directory_picker,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            app.state.runtime.close()

    try:
        app = FastAPI(title=settings.app_name, lifespan=lifespan)
        app.add_middleware(
            RequestObservabilityMiddleware,
            metrics=runtime.metrics,
        )
        app.state.runtime = runtime
        app.state.resolved_config = resolved_config
        app.state.config_snapshot = resolved_config.safe_snapshot()
        app.state.startup_timeline = runtime.startup_timeline
        app.state.metrics = runtime.metrics
        app.state.mcp_providers = runtime.mcp_providers
        app.state.mcp_connection_manager = runtime.mcp_connection_manager
        app.state.mcp_registry = runtime.mcp_registry
        app.state.skill_registry = runtime.skill_registry
        app.state.tool_registry = runtime.tool_registry
        app.state.agent_run_service = runtime.agent_run_service
        app.state.query_service = runtime.query_service
        app.state.change_set_service = runtime.change_set_service
        app.state.session_service = runtime.session_service
        app.state.usage_ledger = runtime.usage_ledger
        app.state.workspace_service = runtime.workspace_service
        app.state.knowledge_base_service = runtime.knowledge_base_service
        app.state.project_memory_service = runtime.project_memory_service
        app.state.user_memory_service = runtime.user_memory_service
        app.state.task_queue = runtime.task_queue
        app.state.model_registry = runtime.model_registry
        static_dir = Path(__file__).parent / "static"

        app.include_router(
            create_api_router(
                session_service=runtime.session_service,
                llm_client=runtime.llm_client,
                knowledge_base_service=runtime.knowledge_base_service,
                query_service=runtime.query_service,
                change_set_service=runtime.change_set_service,
                workspace_service=runtime.workspace_service,
                project_memory_service=runtime.project_memory_service,
                user_memory_service=runtime.user_memory_service,
                settings=settings,
                metrics=runtime.metrics,
                task_queue=runtime.task_queue,
                model_registry=runtime.model_registry,
                directory_picker=runtime.directory_picker,
                mcp_registry=runtime.mcp_registry,
                skill_registry=runtime.skill_registry,
                mcp_connection_manager=runtime.mcp_connection_manager,
                eval_service=runtime.eval_service,
            ),
            prefix=settings.api_prefix,
        )
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/", include_in_schema=False)
        def frontend() -> FileResponse:
            return FileResponse(static_dir / "index.html")

        return app
    except BaseException:
        runtime.close()
        raise


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
