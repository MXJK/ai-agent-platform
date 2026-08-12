"""Shared application runtime assembly and lifecycle management."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Literal

from ai_agent_platform.agents import (
    CodingAgentRuntime,
    GameAgentRuntime,
    LLMStructuredAgentPlanner,
    create_coding_tool_registry,
)
from ai_agent_platform.core import (
    CeleryTaskQueue,
    InProcessTaskQueue,
    MetricsRegistry,
    ResolvedConfig,
    Settings,
    configure_logging,
)
from ai_agent_platform.integrations import (
    DirectoryPicker,
    LLMClient,
    MCPToolProvider,
    RAGService,
    SystemDirectoryPicker,
    ToolRegistry,
    PermissionResolver,
    create_mcp_providers_from_config_file,
    create_rag_service,
)
from ai_agent_platform.model_registry import (
    InMemoryModelRegistryRepository,
    InMemorySecretStore,
    KeyringSecretStore,
    ModelRegistryService,
    PostgresModelRegistryRepository,
)
from ai_agent_platform.project_memory.factory import create_project_memory_service
from ai_agent_platform.project_memory.service import ProjectMemoryService
from ai_agent_platform.repositories import (
    InMemoryChangeSetRepository,
    InMemoryKnowledgeBaseRepository,
    InMemorySessionRepository,
    InMemoryWorkspaceRepository,
    PostgresAgentRunRepository,
    PostgresChangeSetRepository,
    PostgresDocumentRepository,
    PostgresKnowledgeBaseRepository,
    PostgresSessionRepository,
    PostgresWorkspaceRepository,
)
from ai_agent_platform.services import (
    AgentRunService,
    ChangeSetService,
    KnowledgeBaseService,
    SessionService,
    UsageLedgerService,
    WorkspaceService,
    ExecutionContextFactory,
    create_conversation_compressor,
)


logger = logging.getLogger(__name__)

RuntimeRole = Literal["api", "worker", "cli"]
_RUNTIME_ROLES = {"api", "worker", "cli"}


@dataclass(frozen=True)
class StartupCheckpoint:
    """A monotonic runtime-assembly milestone."""

    name: str
    elapsed_ms: int


@dataclass(frozen=True)
class RuntimeCloseError:
    """A best-effort cleanup failure captured without skipping later resources."""

    resource: str
    error: str


@dataclass
class RuntimeContainer:
    """Owns one process-local dependency graph and its resource lifecycle."""

    settings: Settings
    role: RuntimeRole
    resolved_config: ResolvedConfig | None = field(default=None, repr=False)
    config_snapshot: dict[str, object] | None = field(default=None, repr=False)
    metrics: MetricsRegistry | None = None
    directory_picker: DirectoryPicker | None = None
    task_queue: Any = None
    session_repository: Any = None
    agent_run_store: Any = None
    change_set_store: Any = None
    document_store: Any = None
    knowledge_base_store: Any = None
    workspace_store: Any = None
    usage_ledger: UsageLedgerService | None = None
    llm_client: LLMClient | None = None
    model_registry: ModelRegistryService | None = None
    game_agent_runtime: GameAgentRuntime | None = None
    workspace_service: WorkspaceService | None = None
    project_memory_service: ProjectMemoryService | None = None
    permission_resolver: PermissionResolver | None = None
    change_set_service: ChangeSetService | None = None
    rag_service: RAGService | None = None
    knowledge_base_service: KnowledgeBaseService | None = None
    mcp_providers: list[MCPToolProvider] = field(default_factory=list)
    tool_registry: ToolRegistry | None = None
    checkpointer: Any = None
    coding_agent_runtime: CodingAgentRuntime | None = None
    session_service: SessionService | None = None
    execution_context_factory: ExecutionContextFactory | None = None
    agent_run_service: AgentRunService | None = None
    startup_timeline: list[StartupCheckpoint] = field(default_factory=list)
    close_errors: list[RuntimeCloseError] = field(default_factory=list)
    _cleanup_callbacks: list[tuple[str, Callable[[], Any]]] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _lifecycle_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _started_at: float = field(default_factory=perf_counter, init=False, repr=False)

    @property
    def closed(self) -> bool:
        with self._lifecycle_lock:
            return self._closed

    def checkpoint(self, name: str) -> None:
        self.startup_timeline.append(
            StartupCheckpoint(
                name=name,
                elapsed_ms=int((perf_counter() - self._started_at) * 1000),
            )
        )

    def register_cleanup(
        self,
        resource: str,
        callback: Callable[[], Any],
    ) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("runtime container is already closed")
            self._cleanup_callbacks.append((resource, callback))

    def close(self) -> list[RuntimeCloseError]:
        """Close owned resources once, in reverse registration order."""

        with self._lifecycle_lock:
            if self._closed:
                return list(self.close_errors)
            self._closed = True
            callbacks = list(reversed(self._cleanup_callbacks))
            self._cleanup_callbacks.clear()
        for resource, callback in callbacks:
            try:
                callback()
            except Exception as exc:  # pragma: no cover - defensive logging path
                failure = RuntimeCloseError(resource=resource, error=str(exc))
                self.close_errors.append(failure)
                logger.exception("failed to close runtime resource %s", resource)
        return list(self.close_errors)


class ApplicationFactory:
    """Creates runtime components behind overridable test seams."""

    def build_runtime(
        self,
        settings: Settings,
        *,
        role: RuntimeRole = "api",
        resolved_config: ResolvedConfig | None = None,
        llm_client: LLMClient | None = None,
        rag_service: RAGService | None = None,
        coding_agent_runtime: CodingAgentRuntime | None = None,
        directory_picker: DirectoryPicker | None = None,
    ) -> RuntimeContainer:
        if role not in _RUNTIME_ROLES:
            raise ValueError(f"unsupported runtime role: {role}")

        container = RuntimeContainer(settings=settings, role=role)
        container.checkpoint("config_loaded")
        try:
            configure_logging(level=settings.log_level, log_format=settings.log_format)
            if role == "worker" and settings.task_queue_backend != "celery":
                raise RuntimeError(
                    "Celery worker requires TASK_QUEUE_BACKEND=celery"
                )

            container.metrics = self.create_metrics_registry()
            container.directory_picker = (
                directory_picker or self.create_directory_picker()
            )
            container.task_queue = self.create_task_queue(
                settings,
                role=role,
                metrics=container.metrics,
            )
            container.register_cleanup("task_queue", container.task_queue.close)

            container.session_repository = self.create_session_repository(settings)
            container.agent_run_store = self.create_agent_run_store(settings)
            container.change_set_store = self.create_change_set_store(settings)
            container.document_store = self.create_document_store(settings)
            container.knowledge_base_store = self.create_knowledge_base_store(
                settings
            )
            container.workspace_store = self.create_workspace_store(settings)

            container.usage_ledger = UsageLedgerService(
                container.session_repository,
                settings,
            )
            container.llm_client = llm_client or self.create_llm_client(settings)
            set_usage_ledger = getattr(
                container.llm_client,
                "set_usage_ledger",
                None,
            )
            if callable(set_usage_ledger):
                set_usage_ledger(container.usage_ledger)
            container.model_registry = self.create_model_registry(
                settings,
                container.llm_client,
            )
            container.game_agent_runtime = self.create_game_agent_runtime()
            container.workspace_service = WorkspaceService(
                store=container.workspace_store,
                allowed_roots=(
                    settings.workspace_allowed_roots
                    or (str(Path.home().resolve()),)
                ),
            )
            container.project_memory_service = self.create_project_memory_service(
                settings,
                workspace_service=container.workspace_service,
                llm_client=container.llm_client,
                metrics=container.metrics,
                usage_ledger=container.usage_ledger,
            )
            container.project_memory_service.set_index_outbox_submitter(
                lambda trigger_id: container.task_queue.submit(
                    "memory_index_outbox",
                    container.project_memory_service.process_index_outbox,
                    trigger_id=trigger_id,
                )
            )
            container.permission_resolver = PermissionResolver()
            container.change_set_service = ChangeSetService(
                repository=container.change_set_store,
                workspace_service=container.workspace_service,
                authorize=container.project_memory_service.authorize,
                live_writes_enabled=settings.live_workspace_writes_enabled,
                apply_mode=settings.change_set_apply_mode,
                auth_mode=settings.auth_mode,
                max_files=settings.change_set_max_files,
                max_patch_chars=settings.change_set_max_patch_chars,
                worktree_parent=settings.change_set_worktree_parent,
                branch_prefix=settings.change_set_branch_prefix,
                command_timeout_seconds=settings.sandbox_command_timeout_seconds,
                permission_resolver=container.permission_resolver,
                role_for=container.project_memory_service.role_for,
            )
            container.rag_service = rag_service or self.create_rag_service(
                settings,
                document_store=container.document_store,
                usage_ledger=container.usage_ledger,
            )
            container.knowledge_base_service = KnowledgeBaseService(
                store=container.knowledge_base_store,
                rag_service=container.rag_service,
            )
            container.checkpoint("stores_ready")

            container.mcp_providers = self.create_mcp_providers(settings)
            for provider in container.mcp_providers:
                container.register_cleanup(
                    f"mcp_provider:{provider.server_name}",
                    provider.close,
                )
            container.checkpoint("mcp_ready")

            container.tool_registry = self.create_tool_registry(
                settings,
                mcp_providers=container.mcp_providers,
            )
            attach_permission_resolver = getattr(
                container.tool_registry,
                "attach_permission_resolver",
                None,
            )
            if callable(attach_permission_resolver):
                attach_permission_resolver(container.permission_resolver)
            container.register_cleanup(
                "tool_registry",
                container.tool_registry.close,
            )
            container.checkpoint("tools_ready")

            if coding_agent_runtime is None:
                (
                    container.checkpointer,
                    close_checkpointer,
                ) = self.create_langgraph_checkpointer(settings)
                if close_checkpointer is not None:
                    container.register_cleanup(
                        "langgraph_checkpointer",
                        close_checkpointer,
                    )
                container.coding_agent_runtime = self.create_coding_agent_runtime(
                    settings,
                    tool_registry=container.tool_registry,
                    run_store=container.agent_run_store,
                    checkpointer=container.checkpointer,
                    llm_client=container.llm_client,
                    knowledge_base_service=container.knowledge_base_service,
                    project_memory_service=container.project_memory_service,
                    change_set_service=container.change_set_service,
                )
            else:
                container.coding_agent_runtime = coding_agent_runtime

            change_set_event_recorder = getattr(
                container.coding_agent_runtime,
                "record_change_set_event",
                None,
            )
            if callable(change_set_event_recorder):
                container.change_set_service.set_audit_callback(
                    change_set_event_recorder
                )
            container.session_service = SessionService(
                repository=container.session_repository,
                agent_runtime=container.game_agent_runtime,
                compressor=create_conversation_compressor(
                    llm_provider=settings.llm_provider,
                    llm_client=container.llm_client,
                ),
                summary_enabled=settings.conversation_summary_enabled,
                summary_trigger_messages=(
                    settings.conversation_summary_trigger_messages
                ),
                summary_keep_recent_messages=(
                    settings.conversation_summary_keep_recent_messages
                ),
                summary_max_chars=settings.conversation_summary_max_chars,
                summary_max_source_chars=(
                    settings.conversation_summary_max_source_chars
                ),
                metrics=container.metrics,
                usage_ledger=container.usage_ledger,
                default_provider=settings.llm_provider,
                default_model=settings.llm_model,
                default_thinking_level=settings.llm_thinking_level,
            )
            container.execution_context_factory = ExecutionContextFactory(
                session_service=container.session_service,
                workspace_service=container.workspace_service,
                workspace_authorizer=container.project_memory_service,
                auth_mode=settings.auth_mode,
                entrypoint_type=role,
                max_context_messages=settings.llm_max_context_messages,
                max_instruction_chars=settings.agent_max_instruction_chars,
                config_snapshot=(
                    resolved_config or ResolvedConfig.from_settings(settings)
                ).safe_snapshot(),
                process_config=(
                    resolved_config or ResolvedConfig.from_settings(settings)
                ),
                tool_registry=container.tool_registry,
            )
            container.agent_run_service = AgentRunService(
                runtime=container.coding_agent_runtime,
                session_service=container.session_service,
                workspace_service=container.workspace_service,
                project_memory_service=container.project_memory_service,
                metrics=container.metrics,
                task_queue=container.task_queue,
                max_context_messages=settings.llm_max_context_messages,
                llm_provider=settings.llm_provider,
                llm_model=settings.llm_model,
                model_registry=container.model_registry,
                execution_context_factory=container.execution_context_factory,
                permission_resolver=container.permission_resolver,
                tool_registry=container.tool_registry,
            )
            container.register_cleanup(
                "agent_run_service",
                container.agent_run_service.close,
            )
            container.checkpoint("agent_ready")
            return container
        except BaseException:
            container.close()
            raise

    def create_metrics_registry(self) -> MetricsRegistry:
        return MetricsRegistry()

    def create_directory_picker(self) -> DirectoryPicker:
        return SystemDirectoryPicker()

    def create_task_queue(
        self,
        settings: Settings,
        *,
        role: RuntimeRole,
        metrics: MetricsRegistry,
    ) -> Any:
        if settings.task_queue_backend == "celery":
            return CeleryTaskQueue(
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
        return InProcessTaskQueue(
            max_workers=settings.background_task_workers,
            max_queue_size=settings.background_task_queue_capacity,
            metrics=metrics,
        )

    def create_session_repository(self, settings: Settings) -> Any:
        if settings.session_repository == "memory":
            return InMemorySessionRepository()
        if settings.session_repository == "postgres":
            return PostgresSessionRepository(database_url=settings.database_url)
        raise ValueError(
            f"unsupported session repository: {settings.session_repository}"
        )

    def create_agent_run_store(self, settings: Settings) -> Any:
        if settings.agent_run_store == "memory":
            return None
        if settings.agent_run_store == "postgres":
            return PostgresAgentRunRepository(database_url=settings.database_url)
        raise ValueError(f"unsupported agent run store: {settings.agent_run_store}")

    def create_change_set_store(self, settings: Settings) -> Any:
        if settings.change_set_store == "memory":
            return InMemoryChangeSetRepository()
        if settings.change_set_store == "postgres":
            return PostgresChangeSetRepository(database_url=settings.database_url)
        raise ValueError(f"unsupported change set store: {settings.change_set_store}")

    def create_document_store(self, settings: Settings) -> Any:
        if settings.document_store == "memory":
            return None
        if settings.document_store == "postgres":
            return PostgresDocumentRepository(database_url=settings.database_url)
        raise ValueError(f"unsupported document store: {settings.document_store}")

    def create_knowledge_base_store(self, settings: Settings) -> Any:
        if settings.document_store == "memory":
            return InMemoryKnowledgeBaseRepository()
        if settings.document_store == "postgres":
            return PostgresKnowledgeBaseRepository(
                database_url=settings.database_url
            )
        raise ValueError(
            f"unsupported knowledge-base store: {settings.document_store}"
        )

    def create_workspace_store(self, settings: Settings) -> Any:
        if settings.workspace_store == "memory":
            return InMemoryWorkspaceRepository()
        if settings.workspace_store == "postgres":
            return PostgresWorkspaceRepository(database_url=settings.database_url)
        raise ValueError(f"unsupported workspace store: {settings.workspace_store}")

    def create_llm_client(self, settings: Settings) -> LLMClient:
        return LLMClient(settings)

    def create_model_registry(
        self,
        settings: Settings,
        llm_client: LLMClient,
    ) -> ModelRegistryService:
        if settings.model_registry_store == "memory":
            repository = InMemoryModelRegistryRepository()
        elif settings.model_registry_store == "postgres":
            repository = PostgresModelRegistryRepository(
                database_url=settings.database_url
            )
        else:
            raise ValueError(
                "unsupported model registry store: "
                f"{settings.model_registry_store}"
            )
        secret_store = (
            InMemorySecretStore()
            if settings.model_secret_backend == "memory"
            else KeyringSecretStore(service_name=settings.app_name)
        )
        runtime_router = getattr(llm_client, "model_router", None)
        initial_models = (
            runtime_router.models
            if runtime_router is not None
            else LLMClient(settings).model_router.models
        )
        registry = ModelRegistryService(
            repository,
            secret_store,
            initial_models=initial_models,
            environment_secret_refs={
                "openai": "env:OPENAI_API_KEY",
                "deepseek": "env:DEEPSEEK_API_KEY",
                "anthropic": "env:ANTHROPIC_API_KEY",
                "google": "env:GOOGLE_API_KEY",
            },
        )
        set_model_registry = getattr(llm_client, "set_model_registry", None)
        if callable(set_model_registry):
            set_model_registry(registry)
        replace_model_catalog = getattr(llm_client, "replace_model_catalog", None)
        test_connection = getattr(llm_client, "test_connection", None)
        if (
            runtime_router is not None
            and callable(replace_model_catalog)
            and callable(test_connection)
        ):
            registry.bind_runtime(
                router=runtime_router,
                catalog_changed=replace_model_catalog,
                test_connection=test_connection,
            )
        return registry

    def create_game_agent_runtime(self) -> GameAgentRuntime:
        return GameAgentRuntime()

    def create_project_memory_service(
        self,
        settings: Settings,
        *,
        workspace_service: WorkspaceService,
        llm_client: LLMClient,
        metrics: MetricsRegistry,
        usage_ledger: UsageLedgerService,
    ) -> ProjectMemoryService:
        return create_project_memory_service(
            settings,
            workspace_service=workspace_service,
            llm_client=llm_client,
            metrics=metrics,
            usage_ledger=usage_ledger,
        )

    def create_rag_service(
        self,
        settings: Settings,
        *,
        document_store: Any,
        usage_ledger: UsageLedgerService,
    ) -> RAGService:
        return create_rag_service(
            settings,
            document_store=document_store,
            usage_ledger=usage_ledger,
        )

    def create_mcp_providers(self, settings: Settings) -> list[MCPToolProvider]:
        if not settings.mcp_enabled:
            return []
        if not settings.mcp_config_path:
            raise ValueError("MCP_CONFIG_PATH is required when MCP_ENABLED=true")
        return create_mcp_providers_from_config_file(
            settings.mcp_config_path,
            request_timeout_seconds=settings.mcp_request_timeout_seconds,
        )

    def create_tool_registry(
        self,
        settings: Settings,
        *,
        mcp_providers: list[MCPToolProvider],
    ) -> ToolRegistry:
        registry = create_coding_tool_registry(
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
        if settings.tool_allowlist is not None:
            registry.restrict_to(settings.tool_allowlist)
        return registry

    def create_langgraph_checkpointer(
        self,
        settings: Settings,
    ) -> tuple[Any, Callable[[], Any] | None]:
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
            try:
                checkpointer = PostgresSaver(pool)
                checkpointer.setup()
            except BaseException:
                pool.close()
                raise
            return checkpointer, pool.close
        raise ValueError(
            "unsupported LangGraph checkpointer: "
            f"{settings.langgraph_checkpointer}"
        )

    def create_coding_agent_runtime(
        self,
        settings: Settings,
        *,
        tool_registry: ToolRegistry,
        run_store: Any,
        checkpointer: Any,
        llm_client: LLMClient,
        knowledge_base_service: KnowledgeBaseService,
        project_memory_service: ProjectMemoryService,
        change_set_service: ChangeSetService,
    ) -> CodingAgentRuntime:
        return CodingAgentRuntime(
            tool_registry=tool_registry,
            run_store=run_store,
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
            change_set_service=change_set_service,
        )


def build_runtime(
    settings: Settings | ResolvedConfig,
    role: RuntimeRole = "api",
    *,
    factory: ApplicationFactory | None = None,
    llm_client: LLMClient | None = None,
    rag_service: RAGService | None = None,
    coding_agent_runtime: CodingAgentRuntime | None = None,
    directory_picker: DirectoryPicker | None = None,
) -> RuntimeContainer:
    """Build a complete API, worker, or future CLI runtime."""
    resolved_config = (
        settings
        if isinstance(settings, ResolvedConfig)
        else ResolvedConfig.from_settings(settings)
    )
    container = (factory or ApplicationFactory()).build_runtime(
        resolved_config.settings,
        role=role,
        resolved_config=resolved_config,
        llm_client=llm_client,
        rag_service=rag_service,
        coding_agent_runtime=coding_agent_runtime,
        directory_picker=directory_picker,
    )
    container.resolved_config = resolved_config
    container.config_snapshot = resolved_config.safe_snapshot()
    return container


__all__ = [
    "ApplicationFactory",
    "RuntimeCloseError",
    "RuntimeContainer",
    "RuntimeRole",
    "StartupCheckpoint",
    "build_runtime",
]
