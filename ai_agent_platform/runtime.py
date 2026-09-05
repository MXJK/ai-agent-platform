"""Shared application runtime assembly and lifecycle management."""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
import logging
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Literal

from ai_agent_platform.agents.game_agent import GameAgentRuntime
from ai_agent_platform.agents.coding.tools import create_coding_tool_registry
from ai_agent_platform.agents.coding import InMemoryAgentRunStore
from ai_agent_platform.cogent import AgentRuntime
from ai_agent_platform.cogent.runtime import CogentRuntime
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
    MCPConnectionManager,
    MCPRegistryService,
    MCPToolProvider,
    RAGService,
    SystemDirectoryPicker,
    ToolRegistry,
    ToolPoolBuilder,
    PermissionResolver,
    create_mcp_providers_from_config_file,
    create_rag_service,
)
from ai_agent_platform.evaluation import (
    FaultInjectingToolRegistry,
    ToolFaultController,
)
from ai_agent_platform.evaluation.service import EvalService
from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.memory import UserMemoryService
from ai_agent_platform.memory.repository import SQLiteUserMemoryRepository
from ai_agent_platform.model_registry import (
    EncryptedFileSecretStore,
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
    InMemoryEvalRepository,
    InMemoryKnowledgeBaseRepository,
    InMemorySessionRepository,
    InMemoryWorkspaceRepository,
    PostgresAgentRunRepository,
    PostgresChangeSetRepository,
    PostgresEvalRepository,
    PostgresDocumentRepository,
    PostgresKnowledgeBaseRepository,
    PostgresSessionRepository,
    PostgresWorkspaceRepository,
    SQLiteAgentRunRepository,
    SQLiteSessionRepository,
    SQLiteWorkspaceRepository,
    create_query_unit_of_work,
)
from ai_agent_platform.services import (
    AgentRunService,
    ChangeSetService,
    KnowledgeBaseService,
    QueryService,
    SessionService,
    UsageLedgerService,
    WorkspaceService,
    ExecutionContextFactory,
    ExecutionWorkspaceRuntime,
    create_conversation_compressor,
)
from ai_agent_platform.skills import (
    CommandRegistry,
    SkillCatalog,
    SkillDiscovery,
    SkillService,
    SkillRegistryService,
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
    local_state_database: LocalStateDatabase | None = field(default=None, repr=False)
    session_repository: Any = None
    agent_run_store: Any = None
    change_set_store: Any = None
    document_store: Any = None
    eval_store: Any = None
    tool_fault_controller: Any = field(default=None, repr=False)
    knowledge_base_store: Any = None
    workspace_store: Any = None
    usage_ledger: UsageLedgerService | None = None
    llm_client: LLMClient | None = None
    model_registry: ModelRegistryService | None = None
    secret_store: Any = field(default=None, repr=False)
    game_agent_runtime: GameAgentRuntime | None = None
    workspace_service: WorkspaceService | None = None
    project_memory_service: ProjectMemoryService | None = None
    user_memory_service: UserMemoryService | None = None
    permission_resolver: PermissionResolver | None = None
    change_set_service: ChangeSetService | None = None
    rag_service: RAGService | None = None
    knowledge_base_service: KnowledgeBaseService | None = None
    mcp_providers: list[MCPToolProvider] = field(default_factory=list)
    mcp_connection_manager: MCPConnectionManager | None = None
    mcp_registry: MCPRegistryService | None = None
    tool_registry: ToolRegistry | None = None
    tool_pool_builder: ToolPoolBuilder | None = None
    skill_service: SkillService | None = None
    skill_registry: SkillRegistryService | None = None
    skill_catalog: SkillCatalog | None = None
    command_registry: CommandRegistry | None = None
    coding_agent_runtime: AgentRuntime | None = None
    cogent_runtime: CogentRuntime | None = None
    session_service: SessionService | None = None
    execution_context_factory: ExecutionContextFactory | None = None
    execution_workspace_runtime: ExecutionWorkspaceRuntime | None = None
    query_uow: Any = None
    query_service: QueryService | None = None
    agent_run_service: AgentRunService | None = None
    eval_service: Any = None
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
        coding_agent_runtime: AgentRuntime | None = None,
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

            if _uses_local_state(settings):
                container.local_state_database = self.create_local_state_database(
                    settings
                )
            session_kwargs: dict[str, Any] = {}
            if "local_state_database" in inspect.signature(
                self.create_session_repository
            ).parameters:
                session_kwargs["local_state_database"] = (
                    container.local_state_database
                )
            container.session_repository = self.create_session_repository(
                settings, **session_kwargs
            )
            run_store_kwargs: dict[str, Any] = {}
            if "local_state_database" in inspect.signature(
                self.create_agent_run_store
            ).parameters:
                run_store_kwargs["local_state_database"] = (
                    container.local_state_database
                )
            container.agent_run_store = self.create_agent_run_store(
                settings, **run_store_kwargs
            )
            container.change_set_store = self.create_change_set_store(settings)
            container.document_store = self.create_document_store(settings)
            container.eval_store = self.create_eval_store(settings)
            container.knowledge_base_store = self.create_knowledge_base_store(
                settings
            )
            workspace_kwargs: dict[str, Any] = {}
            if "local_state_database" in inspect.signature(
                self.create_workspace_store
            ).parameters:
                workspace_kwargs["local_state_database"] = (
                    container.local_state_database
                )
            container.workspace_store = self.create_workspace_store(
                settings, **workspace_kwargs
            )

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
            container.secret_store = self.create_secret_store(settings)
            container.model_registry = self.create_model_registry(
                settings,
                container.llm_client,
                secret_store=container.secret_store,
            )
            if role == "api" and settings.model_probe_interval_seconds > 0:
                container.model_registry.start_periodic_probes(
                    interval_seconds=settings.model_probe_interval_seconds
                )
                container.register_cleanup(
                    "model_registry_probes",
                    container.model_registry.close,
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
                local_state_database=container.local_state_database,
                credential_resolver=(
                    container.model_registry.credential_for_provider
                ),
            )
            recovered_workspace_count = _recover_single_user_workspace_ownership(
                settings,
                workspace_service=container.workspace_service,
                project_memory_service=container.project_memory_service,
            )
            if recovered_workspace_count:
                logger.info(
                    "ensured fixed single-user ownership for persisted workspaces",
                    extra={"workspace_count": recovered_workspace_count},
                )
            container.project_memory_service.set_index_outbox_submitter(
                lambda trigger_id: container.task_queue.submit(
                    "memory_index_outbox",
                    container.project_memory_service.process_index_outbox,
                    trigger_id=trigger_id,
                )
            )
            container.project_memory_service.resume_index_outbox()
            container.user_memory_service = UserMemoryService(
                repository=(
                    SQLiteUserMemoryRepository(
                        database=container.local_state_database
                    )
                    if container.local_state_database is not None
                    else None
                ),
                enabled=settings.user_memory_enabled,
                default_mode=settings.user_memory_mode,
                max_context_chars=settings.user_profile_max_context_chars,
            )

            def refresh_layered_memory(
                *, workspace_id: str, actor_user_id: str
            ) -> None:
                workspace = container.workspace_service.get(workspace_id)
                memories = []
                while True:
                    page = container.project_memory_service.list_memories(
                        workspace_id=workspace_id,
                        actor_user_id=actor_user_id,
                        status="active",
                        limit=200,
                        offset=len(memories),
                    )
                    memories.extend(page)
                    if len(page) < 200:
                        break
                container.user_memory_service.refresh_project_scene(
                    user_id=actor_user_id,
                    workspace_id=workspace_id,
                    workspace_title=Path(workspace.root_path).name or workspace.id,
                    memories=memories,
                )

            container.project_memory_service.set_layered_memory_submitter(
                lambda workspace_id, actor_user_id: container.task_queue.submit(
                    "layered_memory_refresh",
                    refresh_layered_memory,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                )
            )
            if settings.auth_mode == "single_user":
                for workspace in container.workspace_service.list():
                    if settings.project_memory_mode == "auto":
                        container.project_memory_service.update_settings(
                            workspace_id=workspace.id,
                            actor_user_id=settings.single_user_id.strip(),
                            mode="auto",
                        )
                    if container.user_memory_service.enabled:
                        container.task_queue.submit(
                            "layered_memory_startup_refresh",
                            refresh_layered_memory,
                            workspace_id=workspace.id,
                            actor_user_id=settings.single_user_id.strip(),
                        )
            container.permission_resolver = PermissionResolver()
            container.execution_workspace_runtime = ExecutionWorkspaceRuntime(
                runtime_parent=settings.sandbox_workspace_parent,
                worktree_parent=settings.change_set_worktree_parent,
                branch_prefix=settings.change_set_branch_prefix,
                command_timeout_seconds=settings.sandbox_command_timeout_seconds,
            )
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
                credential_resolver=(
                    container.model_registry.credential_for_provider
                ),
            )
            container.knowledge_base_service = KnowledgeBaseService(
                store=container.knowledge_base_store,
                rag_service=container.rag_service,
            )
            container.checkpoint("stores_ready")

            mcp_factory_parameters = inspect.signature(
                self.create_mcp_providers
            ).parameters
            mcp_factory_kwargs: dict[str, Any] = {}
            if "secret_store" in mcp_factory_parameters:
                mcp_factory_kwargs["secret_store"] = container.secret_store
            if "permission_resolver" in mcp_factory_parameters:
                mcp_factory_kwargs["permission_resolver"] = (
                    container.permission_resolver
                )
            container.mcp_providers = self.create_mcp_providers(
                settings,
                **mcp_factory_kwargs,
            )
            container.mcp_connection_manager = getattr(
                container.mcp_providers,
                "connection_manager",
                None,
            )
            if container.mcp_connection_manager is not None:
                container.register_cleanup(
                    "mcp_connection_manager",
                    container.mcp_connection_manager.close,
                )
            else:
                for provider in container.mcp_providers:
                    container.register_cleanup(
                        f"mcp_provider:{provider.server_name}",
                        provider.close,
                    )
            container.checkpoint("mcp_ready")

            tool_factory_kwargs: dict[str, Any] = {
                "mcp_providers": container.mcp_providers,
            }
            if "execution_workspace_runtime" in inspect.signature(
                self.create_tool_registry
            ).parameters:
                tool_factory_kwargs["execution_workspace_runtime"] = (
                    container.execution_workspace_runtime
                )
            if "session_repository" in inspect.signature(
                self.create_tool_registry
            ).parameters:
                tool_factory_kwargs["session_repository"] = (
                    container.session_repository
                )
            container.tool_registry = self.create_tool_registry(
                settings,
                **tool_factory_kwargs,
            )
            if settings.eval_fault_injection_enabled:
                # Evaluation affordance, off by default: the failure-recovery
                # metric needs a genuinely failed ToolResult on the real path.
                container.tool_fault_controller = ToolFaultController()
                container.tool_registry = FaultInjectingToolRegistry(
                    container.tool_registry,
                    container.tool_fault_controller,
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
            container.tool_pool_builder = ToolPoolBuilder(
                container.tool_registry
            )
            container.mcp_registry = self.create_mcp_registry(
                settings,
                secret_store=container.secret_store,
                tool_registry=container.tool_registry,
                connection_manager=container.mcp_connection_manager,
            )
            container.checkpoint("tools_ready")

            container.skill_service = self.create_skill_service(
                settings,
                tool_registry=container.tool_registry,
            )
            skill_loader = getattr(container.tool_registry, "skill_loader", None)
            if skill_loader is not None:
                skill_loader.bind(container.skill_service)
            container.skill_registry = self.create_skill_registry(
                settings,
                container.skill_service,
            )
            container.skill_catalog = container.skill_service.discover()
            container.command_registry = CommandRegistry(
                container.skill_catalog.commands
            )
            container.checkpoint("skills_ready")

            if coding_agent_runtime is None:
                container.cogent_runtime = self.create_cogent_runtime(
                    settings,
                    tool_registry=container.tool_registry,
                    run_store=container.agent_run_store,
                    llm_client=container.llm_client,
                    change_set_service=container.change_set_service,
                    tool_pool_builder=container.tool_pool_builder,
                    execution_workspace_runtime=(
                        container.execution_workspace_runtime
                    ),
                    metrics=container.metrics,
                )
                container.coding_agent_runtime = container.cogent_runtime
            else:
                container.coding_agent_runtime = coding_agent_runtime
                if isinstance(coding_agent_runtime, CogentRuntime):
                    container.cogent_runtime = coding_agent_runtime

            change_set_event_recorder = getattr(
                container.coding_agent_runtime,
                "record_change_set_event",
                None,
            )
            if callable(change_set_event_recorder):
                container.change_set_service.set_audit_callback(
                    change_set_event_recorder
                )
            if container.cogent_runtime is not None:
                container.change_set_service.set_run_write_guard(
                    container.cogent_runtime.require_change_set_writable
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
                summary_sync_on_overflow=(
                    settings.conversation_summary_sync_on_overflow
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
                max_context_messages_ceiling=(
                    settings.llm_max_context_messages_ceiling
                ),
                llm_client=container.llm_client,
                max_instruction_chars=settings.agent_max_instruction_chars,
                config_snapshot=(
                    resolved_config or ResolvedConfig.from_settings(settings)
                ).safe_snapshot(),
                skill_service=container.skill_service,
                process_config=(
                    resolved_config or ResolvedConfig.from_settings(settings)
                ),
                tool_registry=container.tool_registry,
                tool_pool_builder=container.tool_pool_builder,
                model_registry=container.model_registry,
                execution_workspace_runtime=container.execution_workspace_runtime,
            )
            container.query_uow = create_query_unit_of_work(
                session_service=container.session_service,
                session_repository=container.session_repository,
                run_store=getattr(
                    container.coding_agent_runtime,
                    "_run_store",
                    None,
                ),
            )
            if container.query_uow is None and coding_agent_runtime is None:
                raise ValueError(
                    "QueryService requires session and Run stores on the same "
                    "supported backend for atomic start"
                )
            container.agent_run_service = AgentRunService(
                runtime=container.coding_agent_runtime,
                session_service=container.session_service,
                workspace_service=container.workspace_service,
                workspace_authorizer=container.project_memory_service,
                metrics=container.metrics,
                task_queue=container.task_queue,
                max_context_messages=settings.llm_max_context_messages,
                llm_provider=settings.llm_provider,
                llm_model=settings.llm_model,
                model_registry=container.model_registry,
                execution_context_factory=container.execution_context_factory,
                query_uow=container.query_uow,
                permission_resolver=container.permission_resolver,
                tool_registry=container.tool_registry,
                tool_pool_builder=container.tool_pool_builder,
            )
            container.query_service = container.agent_run_service
            container.register_cleanup(
                "agent_run_service",
                container.agent_run_service.close,
            )
            container.eval_service = self.create_eval_service(
                settings,
                repository=container.eval_store,
                query_service=container.query_service,
                session_service=container.session_service,
                workspace_service=container.workspace_service,
                memory_service=container.project_memory_service,
                model_registry=container.model_registry,
                fault_controller=container.tool_fault_controller,
            )
            container.checkpoint("agent_ready")
            if role in {'api', 'cli'} and settings.agent_run_store in {'sqlite', 'postgres'}:
                container.query_service.recover_incomplete_runs()
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

    def create_local_state_database(self, settings: Settings) -> LocalStateDatabase:
        return LocalStateDatabase(settings.local_state_path)

    def create_session_repository(
        self,
        settings: Settings,
        *,
        local_state_database: LocalStateDatabase | None = None,
    ) -> Any:
        if settings.session_repository == "memory":
            return InMemorySessionRepository()
        if settings.session_repository == "postgres":
            return PostgresSessionRepository(database_url=settings.database_url)
        if settings.session_repository == "sqlite":
            if local_state_database is None:
                raise ValueError("SESSION_REPOSITORY=sqlite requires local state")
            return SQLiteSessionRepository(database=local_state_database)
        raise ValueError(
            f"unsupported session repository: {settings.session_repository}"
        )

    def create_agent_run_store(
        self,
        settings: Settings,
        *,
        local_state_database: LocalStateDatabase | None = None,
    ) -> Any:
        if settings.agent_run_store == "memory":
            return InMemoryAgentRunStore()
        if settings.agent_run_store == "postgres":
            return PostgresAgentRunRepository(database_url=settings.database_url)
        if settings.agent_run_store == "sqlite":
            if local_state_database is None:
                raise ValueError("AGENT_RUN_STORE=sqlite requires local state")
            return SQLiteAgentRunRepository(database=local_state_database)
        raise ValueError(f"unsupported agent run store: {settings.agent_run_store}")

    def create_change_set_store(self, settings: Settings) -> Any:
        if settings.change_set_store == "memory":
            return InMemoryChangeSetRepository()
        if settings.change_set_store == "postgres":
            return PostgresChangeSetRepository(database_url=settings.database_url)
        raise ValueError(f"unsupported change set store: {settings.change_set_store}")

    def create_eval_service(
        self,
        settings: Settings,
        *,
        repository: Any,
        query_service: Any,
        session_service: Any,
        workspace_service: Any,
        memory_service: Any = None,
        model_registry: Any = None,
        fault_controller: Any = None,
    ) -> Any:
        return EvalService(
            repository=repository,
            query_service=query_service,
            session_service=session_service,
            workspace_service=workspace_service,
            memory_service=memory_service,
            model_registry=model_registry,
            workspace_root=_eval_workspace_root(settings),
            actor_user_id=(
                settings.single_user_id.strip()
                if settings.auth_mode == "single_user"
                else ""
            ),
            fault_controller=fault_controller,
        )

    def create_eval_store(self, settings: Settings) -> Any:
        if settings.eval_store == "memory":
            return InMemoryEvalRepository()
        if settings.eval_store == "postgres":
            return PostgresEvalRepository(database_url=settings.database_url)
        raise ValueError(f"unsupported eval store: {settings.eval_store}")

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

    def create_workspace_store(
        self,
        settings: Settings,
        *,
        local_state_database: LocalStateDatabase | None = None,
    ) -> Any:
        if settings.workspace_store == "memory":
            return InMemoryWorkspaceRepository()
        if settings.workspace_store == "postgres":
            return PostgresWorkspaceRepository(database_url=settings.database_url)
        if settings.workspace_store == "sqlite":
            if local_state_database is None:
                raise ValueError("WORKSPACE_STORE=sqlite requires local state")
            return SQLiteWorkspaceRepository(database=local_state_database)
        raise ValueError(f"unsupported workspace store: {settings.workspace_store}")

    def create_llm_client(self, settings: Settings) -> LLMClient:
        return LLMClient(settings)

    def create_model_registry(
        self,
        settings: Settings,
        llm_client: LLMClient,
        *,
        secret_store: Any | None = None,
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
        secret_store = secret_store or self.create_secret_store(settings)
        runtime_router = getattr(llm_client, "model_router", None)
        # PostgreSQL is the product registry: an empty catalog must stay empty
        # until the local owner registers providers and models through Model
        # Management. Static/default catalogs remain useful only for the
        # explicitly ephemeral memory runtime used by tests and local smoke runs.
        initial_models = (
            (
                runtime_router.models
                if runtime_router is not None
                else LLMClient(settings).model_router.models
            )
            if settings.model_registry_store == "memory"
            else ()
        )
        registry = ModelRegistryService(
            repository,
            secret_store,
            initial_models=initial_models,
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

    def create_secret_store(self, settings: Settings) -> Any:
        if settings.model_secret_backend == "memory":
            return InMemorySecretStore()
        if settings.model_secret_backend == "encrypted_file":
            state_path = Path(settings.local_state_path).expanduser()
            return EncryptedFileSecretStore(
                state_path.with_name("provider-secrets.enc")
            )
        return KeyringSecretStore(service_name=settings.app_name)

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
        local_state_database: LocalStateDatabase | None = None,
        credential_resolver: Callable[[str], str | None] | None = None,
    ) -> ProjectMemoryService:
        return create_project_memory_service(
            settings,
            workspace_service=workspace_service,
            llm_client=llm_client,
            metrics=metrics,
            usage_ledger=usage_ledger,
            local_state_database=local_state_database,
            credential_resolver=credential_resolver,
        )

    def create_rag_service(
        self,
        settings: Settings,
        *,
        document_store: Any,
        usage_ledger: UsageLedgerService,
        credential_resolver: Callable[[str], str | None] | None = None,
    ) -> RAGService:
        return create_rag_service(
            settings,
            document_store=document_store,
            usage_ledger=usage_ledger,
            credential_resolver=credential_resolver,
        )

    def create_mcp_providers(
        self,
        settings: Settings,
        *,
        secret_store: Any | None = None,
        permission_resolver: PermissionResolver | None = None,
    ) -> list[MCPToolProvider]:
        if not settings.mcp_enabled:
            return []
        if not settings.mcp_config_path:
            raise ValueError("MCP_CONFIG_PATH is required when MCP_ENABLED=true")
        return create_mcp_providers_from_config_file(
            settings.mcp_config_path,
            request_timeout_seconds=settings.mcp_request_timeout_seconds,
            secret_store=secret_store,
            permission_resolver=permission_resolver,
        )

    def create_tool_registry(
        self,
        settings: Settings,
        *,
        mcp_providers: list[MCPToolProvider],
        execution_workspace_runtime: ExecutionWorkspaceRuntime | None = None,
        session_repository: Any | None = None,
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
            execution_workspace_runtime=execution_workspace_runtime,
        )
        if settings.tool_allowlist is not None:
            registry.restrict_to(settings.tool_allowlist)
        return registry

    def create_mcp_registry(
        self,
        settings: Settings,
        *,
        secret_store: Any,
        tool_registry: ToolRegistry,
        connection_manager: MCPConnectionManager | None,
    ) -> MCPRegistryService:
        return MCPRegistryService(
            config_path=settings.mcp_config_path,
            secret_store=secret_store,
            tool_registry=tool_registry,
            connection_manager=connection_manager,
            tool_allowlist=settings.tool_allowlist,
        )

    def create_skill_service(
        self,
        settings: Settings,
        *,
        tool_registry: ToolRegistry,
    ) -> SkillService:
        package_root = Path(__file__).resolve().parent
        user_root = Path(settings.skills_directory_path).expanduser()
        discovery = SkillDiscovery(
            bundled_root=package_root / "bundled_skills",
            user_root=user_root,
            legacy_user_root=(
                Path.home() / ".ai-agent-platform" / "skills"
                if user_root == Path.home() / ".cogent" / "skills" else None
            ),
        )
        effective_selection = (
            settings.enabled_skills
            if settings.enabled_skills is not None
            else settings.skill_allowlist
        )
        list_specs = getattr(tool_registry, "list_specs", None)
        available_tools = (
            tuple(spec.name for spec in list_specs())
            if settings.skills_enabled and callable(list_specs)
            else ()
        )
        return SkillService(
            discovery,
            enabled=settings.skills_allowed and settings.skills_enabled,
            enabled_skills=effective_selection,
            available_tools=available_tools,
        )

    def create_skill_registry(
        self,
        settings: Settings,
        skill_service: SkillService,
    ) -> SkillRegistryService:
        return SkillRegistryService(
            user_root=Path(settings.skills_directory_path).expanduser(),
            skill_service=skill_service,
        )

    def create_cogent_runtime(
        self,
        settings: Settings,
        *,
        tool_registry: ToolRegistry,
        run_store: Any,
        llm_client: LLMClient,
        change_set_service: ChangeSetService,
        tool_pool_builder: ToolPoolBuilder,
        execution_workspace_runtime: ExecutionWorkspaceRuntime | None = None,
        metrics: MetricsRegistry | None = None,
    ) -> CogentRuntime:
        from ai_agent_platform.cogent.client import RegistryClient
        from ai_agent_platform.cogent.memory.service import MemoryService
        del metrics
        cogent_client = RegistryClient(llm_client)
        return CogentRuntime(
            tool_registry=tool_registry,
            run_store=run_store,
            llm_client=cogent_client,
            tool_pool_builder=tool_pool_builder,
            approval_policy=settings.agent_approval_policy,
            change_set_service=change_set_service,
            execution_workspace_runtime=execution_workspace_runtime,
            max_parallel_reads=settings.agent_max_parallel_tools_per_step,
            tool_result_max_chars=50_000,
            memory_service=MemoryService(client=cogent_client, run_store=run_store),
        )


def _eval_workspace_root(settings: Settings) -> str:
    """Where eval fixture workspaces are written.

    It has to sit inside an allowed workspace root, otherwise the run would be
    rejected by the same policy that protects a user's real directories. Evals
    get no exemption from that policy.
    """

    configured = settings.eval_workspace_root.strip()
    if configured:
        return configured
    roots = settings.workspace_allowed_roots
    base = Path(roots[0]) if roots else Path.home()
    return str(base / ".agent-evals")


def build_runtime(
    settings: Settings | ResolvedConfig,
    role: RuntimeRole = "api",
    *,
    factory: ApplicationFactory | None = None,
    llm_client: LLMClient | None = None,
    rag_service: RAGService | None = None,
    coding_agent_runtime: AgentRuntime | None = None,
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


def _uses_local_state(settings: Settings) -> bool:
    return settings.user_memory_enabled or any(
        value == "sqlite"
        for value in (
            settings.session_repository,
            settings.agent_run_store,
            settings.workspace_store,
            settings.project_memory_store,
            settings.project_memory_vector_store,
        )
    )


def _recover_single_user_workspace_ownership(
    settings: Settings,
    *,
    workspace_service: WorkspaceService,
    project_memory_service: ProjectMemoryService,
) -> int:
    """Make the fixed local owner authoritative over persisted workspaces."""
    if settings.auth_mode != "single_user":
        return 0
    workspaces = workspace_service.list_including_removed()
    for workspace in workspaces:
        project_memory_service.ensure_workspace_admin(
            workspace_id=workspace.id,
            actor_user_id=settings.single_user_id.strip(),
        )
    return len(workspaces)


__all__ = [
    "ApplicationFactory",
    "RuntimeCloseError",
    "RuntimeContainer",
    "RuntimeRole",
    "StartupCheckpoint",
    "build_runtime",
]
