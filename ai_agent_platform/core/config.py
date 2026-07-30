from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "ai-agent-platform"
    api_prefix: str = "/api/v1"
    log_level: str = "WARNING"
    log_format: str = "json"
    llm_provider: str = "fake"
    llm_model: str = "demo-stream-model"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    database_url: str = (
        "postgresql://ai_agent:ai_agent_password@localhost:5432/ai_agent_platform"
    )
    session_repository: str = "memory"
    agent_run_store: str = "memory"
    document_store: str = "memory"
    workspace_store: str = "memory"
    workspace_allowed_roots: tuple[str, ...] = field(
        default_factory=lambda: (str(Path.cwd().resolve()),)
    )
    langgraph_checkpointer: str = "memory"
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    llm_max_input_chars: int = 8000
    llm_max_context_messages: int = 12
    llm_max_output_tokens: int = 4096
    llm_thinking_level: str = "low"
    conversation_summary_enabled: bool = True
    conversation_summary_trigger_messages: int = 12
    conversation_summary_keep_recent_messages: int = 6
    conversation_summary_max_chars: int = 2000
    conversation_summary_max_source_chars: int = 12000
    sse_heartbeat_seconds: float = 10.0
    rag_vector_store: str = "memory"
    chroma_persist_directory: str = ".chroma"
    chroma_collection_name: str = "rag_chunks"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection_name: str = "knowledge_chunks"
    project_memory_enabled: bool = False
    project_memory_mode: str = "off"
    project_memory_candidate_threshold: float = 0.60
    project_memory_auto_threshold: float = 0.85
    project_memory_recall_limit: int = 20
    project_memory_result_limit: int = 6
    project_memory_max_context_chars: int = 3000
    project_memory_qdrant_collection: str = "project_memories"
    project_memory_relevance_weight: float = 0.65
    project_memory_recency_weight: float = 0.20
    project_memory_importance_weight: float = 0.15
    project_memory_recency_half_life_days: float = 180.0
    embedding_provider: str = "local"
    embedding_model: str = "gemini-embedding-001"
    local_embedding_dimensions: int = 128
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 120
    rag_recall_limit: int = 20
    rag_lexical_weight: float = 0.35
    rag_rrf_k: int = 60
    rag_reranker_provider: str = "sentence_transformer"
    sentence_transformer_reranker_model: str = "BAAI/bge-reranker-base"
    sentence_transformer_reranker_device: str = "cpu"
    rag_rerank_default_enabled: bool = False
    rag_max_prompt_chars: int = 6000
    background_task_workers: int = 4
    background_task_queue_capacity: int = 100
    task_queue_backend: str = "in_process"
    redis_url: str = "redis://localhost:6379/0"
    celery_result_backend_url: str = "redis://localhost:6379/1"
    celery_visibility_timeout_seconds: int = 3600
    celery_task_max_retries: int = 3
    celery_task_retry_backoff_seconds: int = 2
    celery_task_retry_backoff_max_seconds: int = 60
    celery_task_soft_time_limit_seconds: int = 900
    celery_task_time_limit_seconds: int = 960
    celery_result_expires_seconds: int = 86400
    celery_worker_max_tasks_per_child: int = 100
    mcp_enabled: bool = False
    mcp_config_path: str | None = None
    mcp_request_timeout_seconds: float = 10.0
    sandbox_mode: str = "local"
    sandbox_docker_image: str = "python:3.11-slim"
    sandbox_command_timeout_seconds: float = 30.0
    sandbox_workspace_parent: str | None = None
    agent_max_exploration_rounds: int = 4
    agent_max_read_tools_per_round: int = 6
    agent_max_context_files: int = 12
    agent_max_context_chars: int = 32000
    agent_max_instruction_chars: int = 16000
    agent_max_tool_rounds: int = 4
    agent_max_tool_calls: int = 12
    auth_mode: str = "disabled"
    gateway_trust_secret: str | None = None

    def __post_init__(self) -> None:
        if not self.api_prefix.startswith("/"):
            raise ValueError("api_prefix must start with '/'")
        _require_choice(
            "log_level",
            self.log_level.upper(),
            {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
        )
        _require_choice("log_format", self.log_format, {"json", "text"})
        _require_choice(
            "llm_thinking_level",
            self.llm_thinking_level,
            {"minimal", "low", "medium", "high"},
        )
        for name, value in (
            ("session_repository", self.session_repository),
            ("agent_run_store", self.agent_run_store),
            ("document_store", self.document_store),
            ("workspace_store", self.workspace_store),
        ):
            _require_choice(name, value, {"memory", "postgres"})
        _require_choice(
            "langgraph_checkpointer",
            self.langgraph_checkpointer,
            {"memory", "postgres"},
        )
        _require_choice(
            "rag_vector_store",
            self.rag_vector_store,
            {"memory", "chroma", "qdrant"},
        )
        _require_choice(
            "rag_reranker_provider",
            self.rag_reranker_provider,
            {"none", "sentence_transformer"},
        )
        if self.rag_rerank_default_enabled and self.rag_reranker_provider == "none":
            raise ValueError(
                "rag_rerank_default_enabled requires a configured reranker provider"
            )
        if not self.sentence_transformer_reranker_device.strip():
            raise ValueError(
                "sentence_transformer_reranker_device must not be empty"
            )
        _require_choice(
            "project_memory_mode",
            self.project_memory_mode,
            {"off", "shadow", "review", "auto"},
        )
        _require_choice(
            "auth_mode",
            self.auth_mode,
            {"disabled", "trusted_header"},
        )
        _require_choice("sandbox_mode", self.sandbox_mode, {"local", "docker"})
        _require_choice(
            "task_queue_backend",
            self.task_queue_backend,
            {"celery", "in_process"},
        )
        for name, value in (
            ("llm_timeout_seconds", self.llm_timeout_seconds),
            ("sse_heartbeat_seconds", self.sse_heartbeat_seconds),
            ("llm_max_input_chars", self.llm_max_input_chars),
            ("llm_max_context_messages", self.llm_max_context_messages),
            ("llm_max_output_tokens", self.llm_max_output_tokens),
            (
                "conversation_summary_trigger_messages",
                self.conversation_summary_trigger_messages,
            ),
            (
                "conversation_summary_keep_recent_messages",
                self.conversation_summary_keep_recent_messages,
            ),
            (
                "conversation_summary_max_chars",
                self.conversation_summary_max_chars,
            ),
            (
                "conversation_summary_max_source_chars",
                self.conversation_summary_max_source_chars,
            ),
            ("local_embedding_dimensions", self.local_embedding_dimensions),
            ("rag_chunk_size", self.rag_chunk_size),
            ("rag_recall_limit", self.rag_recall_limit),
            ("rag_rrf_k", self.rag_rrf_k),
            ("rag_max_prompt_chars", self.rag_max_prompt_chars),
            ("project_memory_recall_limit", self.project_memory_recall_limit),
            ("project_memory_result_limit", self.project_memory_result_limit),
            (
                "project_memory_max_context_chars",
                self.project_memory_max_context_chars,
            ),
            (
                "project_memory_recency_half_life_days",
                self.project_memory_recency_half_life_days,
            ),
            ("background_task_workers", self.background_task_workers),
            (
                "celery_visibility_timeout_seconds",
                self.celery_visibility_timeout_seconds,
            ),
            (
                "celery_task_retry_backoff_seconds",
                self.celery_task_retry_backoff_seconds,
            ),
            (
                "celery_task_retry_backoff_max_seconds",
                self.celery_task_retry_backoff_max_seconds,
            ),
            (
                "celery_task_soft_time_limit_seconds",
                self.celery_task_soft_time_limit_seconds,
            ),
            (
                "celery_task_time_limit_seconds",
                self.celery_task_time_limit_seconds,
            ),
            ("celery_result_expires_seconds", self.celery_result_expires_seconds),
            (
                "celery_worker_max_tasks_per_child",
                self.celery_worker_max_tasks_per_child,
            ),
            ("mcp_request_timeout_seconds", self.mcp_request_timeout_seconds),
            (
                "sandbox_command_timeout_seconds",
                self.sandbox_command_timeout_seconds,
            ),
            ("agent_max_exploration_rounds", self.agent_max_exploration_rounds),
            (
                "agent_max_read_tools_per_round",
                self.agent_max_read_tools_per_round,
            ),
            ("agent_max_context_files", self.agent_max_context_files),
            ("agent_max_context_chars", self.agent_max_context_chars),
            ("agent_max_instruction_chars", self.agent_max_instruction_chars),
            ("agent_max_tool_rounds", self.agent_max_tool_rounds),
            ("agent_max_tool_calls", self.agent_max_tool_calls),
        ):
            _require_positive(name, value)
        if self.llm_max_retries < 0:
            raise ValueError("llm_max_retries must be greater than or equal to 0")
        if self.rag_chunk_overlap < 0:
            raise ValueError("rag_chunk_overlap must be greater than or equal to 0")
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("rag_chunk_overlap must be smaller than rag_chunk_size")
        if (
            self.conversation_summary_keep_recent_messages
            >= self.conversation_summary_trigger_messages
        ):
            raise ValueError(
                "conversation_summary_keep_recent_messages must be smaller than "
                "conversation_summary_trigger_messages"
            )
        if not 0.0 <= self.rag_lexical_weight <= 1.0:
            raise ValueError("rag_lexical_weight must be between 0 and 1")
        if not 0.0 <= self.project_memory_candidate_threshold <= 1.0:
            raise ValueError(
                "project_memory_candidate_threshold must be between 0 and 1"
            )
        if not 0.0 <= self.project_memory_auto_threshold <= 1.0:
            raise ValueError(
                "project_memory_auto_threshold must be between 0 and 1"
            )
        if (
            self.project_memory_candidate_threshold
            > self.project_memory_auto_threshold
        ):
            raise ValueError(
                "project_memory_candidate_threshold must not exceed "
                "project_memory_auto_threshold"
            )
        if self.project_memory_result_limit > self.project_memory_recall_limit:
            raise ValueError(
                "project_memory_result_limit must not exceed "
                "project_memory_recall_limit"
            )
        memory_weights = (
            self.project_memory_relevance_weight,
            self.project_memory_recency_weight,
            self.project_memory_importance_weight,
        )
        if any(weight < 0.0 or weight > 1.0 for weight in memory_weights):
            raise ValueError("project memory ranking weights must be between 0 and 1")
        if not math.isclose(sum(memory_weights), 1.0, abs_tol=1e-9):
            raise ValueError("project memory ranking weights must sum to 1")
        if self.auth_mode == "trusted_header" and not self.gateway_trust_secret:
            raise ValueError(
                "gateway_trust_secret is required when auth_mode=trusted_header"
            )
        if self.background_task_queue_capacity < 0:
            raise ValueError(
                "background_task_queue_capacity must be greater than or equal to 0"
            )
        if self.celery_task_max_retries < 0:
            raise ValueError(
                "celery_task_max_retries must be greater than or equal to 0"
            )
        if (
            self.celery_task_retry_backoff_max_seconds
            < self.celery_task_retry_backoff_seconds
        ):
            raise ValueError(
                "celery_task_retry_backoff_max_seconds must be greater than or "
                "equal to celery_task_retry_backoff_seconds"
            )
        if (
            self.celery_task_time_limit_seconds
            <= self.celery_task_soft_time_limit_seconds
        ):
            raise ValueError(
                "celery_task_time_limit_seconds must be greater than "
                "celery_task_soft_time_limit_seconds"
            )
        if (
            self.celery_visibility_timeout_seconds
            <= self.celery_task_time_limit_seconds
        ):
            raise ValueError(
                "celery_visibility_timeout_seconds must be greater than "
                "celery_task_time_limit_seconds"
            )
        if self.task_queue_backend == "celery":
            if not self.redis_url.startswith(("redis://", "rediss://")):
                raise ValueError(
                    "redis_url must start with redis:// or rediss:// for Celery"
                )
            if not self.celery_result_backend_url.startswith(
                ("redis://", "rediss://")
            ):
                raise ValueError(
                    "celery_result_backend_url must start with redis:// or rediss://"
                )
            self._validate_distributed_task_storage()
        if self.mcp_enabled and not self.mcp_config_path:
            raise ValueError("mcp_config_path is required when mcp_enabled is true")

    @classmethod
    def from_env(cls) -> "Settings":
        dotenv = _load_dotenv()
        return cls(
            app_name=_env("APP_NAME", cls.app_name, dotenv),
            api_prefix=_env("API_PREFIX", cls.api_prefix, dotenv),
            log_level=_env("LOG_LEVEL", cls.log_level, dotenv),
            log_format=_env("LOG_FORMAT", cls.log_format, dotenv),
            llm_provider=_env("LLM_PROVIDER", cls.llm_provider, dotenv),
            llm_model=_env("LLM_MODEL", cls.llm_model, dotenv),
            openai_api_key=_env("OPENAI_API_KEY", None, dotenv),
            anthropic_api_key=_env("ANTHROPIC_API_KEY", None, dotenv),
            google_api_key=_env(
                "GOOGLE_API_KEY",
                _env("GEMINI_API_KEY", None, dotenv),
                dotenv,
            ),
            database_url=_env("DATABASE_URL", cls.database_url, dotenv),
            session_repository=_env(
                "SESSION_REPOSITORY", cls.session_repository, dotenv
            ),
            agent_run_store=_env("AGENT_RUN_STORE", cls.agent_run_store, dotenv),
            document_store=_env("DOCUMENT_STORE", cls.document_store, dotenv),
            workspace_store=_env(
                "WORKSPACE_STORE", cls.workspace_store, dotenv
            ),
            workspace_allowed_roots=_paths_env(
                "WORKSPACE_ALLOWED_ROOTS",
                (str(Path.cwd().resolve()),),
                dotenv,
            ),
            langgraph_checkpointer=_env(
                "LANGGRAPH_CHECKPOINTER", cls.langgraph_checkpointer, dotenv
            ),
            llm_timeout_seconds=_float_env(
                "LLM_TIMEOUT_SECONDS", cls.llm_timeout_seconds, dotenv
            ),
            llm_max_retries=_int_env("LLM_MAX_RETRIES", cls.llm_max_retries, dotenv),
            llm_max_input_chars=_int_env(
                "LLM_MAX_INPUT_CHARS", cls.llm_max_input_chars, dotenv
            ),
            llm_max_context_messages=_int_env(
                "LLM_MAX_CONTEXT_MESSAGES", cls.llm_max_context_messages, dotenv
            ),
            llm_max_output_tokens=_int_env(
                "LLM_MAX_OUTPUT_TOKENS", cls.llm_max_output_tokens, dotenv
            ),
            llm_thinking_level=_env(
                "LLM_THINKING_LEVEL", cls.llm_thinking_level, dotenv
            ),
            conversation_summary_enabled=_bool_env(
                "CONVERSATION_SUMMARY_ENABLED",
                cls.conversation_summary_enabled,
                dotenv,
            ),
            conversation_summary_trigger_messages=_int_env(
                "CONVERSATION_SUMMARY_TRIGGER_MESSAGES",
                cls.conversation_summary_trigger_messages,
                dotenv,
            ),
            conversation_summary_keep_recent_messages=_int_env(
                "CONVERSATION_SUMMARY_KEEP_RECENT_MESSAGES",
                cls.conversation_summary_keep_recent_messages,
                dotenv,
            ),
            conversation_summary_max_chars=_int_env(
                "CONVERSATION_SUMMARY_MAX_CHARS",
                cls.conversation_summary_max_chars,
                dotenv,
            ),
            conversation_summary_max_source_chars=_int_env(
                "CONVERSATION_SUMMARY_MAX_SOURCE_CHARS",
                cls.conversation_summary_max_source_chars,
                dotenv,
            ),
            sse_heartbeat_seconds=_float_env(
                "SSE_HEARTBEAT_SECONDS", cls.sse_heartbeat_seconds, dotenv
            ),
            rag_vector_store=_env("RAG_VECTOR_STORE", cls.rag_vector_store, dotenv),
            chroma_persist_directory=_env(
                "CHROMA_PERSIST_DIRECTORY", cls.chroma_persist_directory, dotenv
            ),
            chroma_collection_name=_env(
                "CHROMA_COLLECTION_NAME", cls.chroma_collection_name, dotenv
            ),
            qdrant_url=_env("QDRANT_URL", cls.qdrant_url, dotenv),
            qdrant_api_key=_env("QDRANT_API_KEY", None, dotenv),
            qdrant_collection_name=_env(
                "QDRANT_COLLECTION_NAME", cls.qdrant_collection_name, dotenv
            ),
            project_memory_enabled=_bool_env(
                "PROJECT_MEMORY_ENABLED",
                cls.project_memory_enabled,
                dotenv,
            ),
            project_memory_mode=_env(
                "PROJECT_MEMORY_MODE",
                cls.project_memory_mode,
                dotenv,
            ),
            project_memory_candidate_threshold=_float_env(
                "PROJECT_MEMORY_CANDIDATE_THRESHOLD",
                cls.project_memory_candidate_threshold,
                dotenv,
            ),
            project_memory_auto_threshold=_float_env(
                "PROJECT_MEMORY_AUTO_THRESHOLD",
                cls.project_memory_auto_threshold,
                dotenv,
            ),
            project_memory_recall_limit=_int_env(
                "PROJECT_MEMORY_RECALL_LIMIT",
                cls.project_memory_recall_limit,
                dotenv,
            ),
            project_memory_result_limit=_int_env(
                "PROJECT_MEMORY_RESULT_LIMIT",
                cls.project_memory_result_limit,
                dotenv,
            ),
            project_memory_max_context_chars=_int_env(
                "PROJECT_MEMORY_MAX_CONTEXT_CHARS",
                cls.project_memory_max_context_chars,
                dotenv,
            ),
            project_memory_qdrant_collection=_env(
                "PROJECT_MEMORY_QDRANT_COLLECTION",
                cls.project_memory_qdrant_collection,
                dotenv,
            ),
            project_memory_relevance_weight=_float_env(
                "PROJECT_MEMORY_RELEVANCE_WEIGHT",
                cls.project_memory_relevance_weight,
                dotenv,
            ),
            project_memory_recency_weight=_float_env(
                "PROJECT_MEMORY_RECENCY_WEIGHT",
                cls.project_memory_recency_weight,
                dotenv,
            ),
            project_memory_importance_weight=_float_env(
                "PROJECT_MEMORY_IMPORTANCE_WEIGHT",
                cls.project_memory_importance_weight,
                dotenv,
            ),
            project_memory_recency_half_life_days=_float_env(
                "PROJECT_MEMORY_RECENCY_HALF_LIFE_DAYS",
                cls.project_memory_recency_half_life_days,
                dotenv,
            ),
            embedding_provider=_env(
                "EMBEDDING_PROVIDER", cls.embedding_provider, dotenv
            ),
            embedding_model=_env("EMBEDDING_MODEL", cls.embedding_model, dotenv),
            local_embedding_dimensions=_int_env(
                "LOCAL_EMBEDDING_DIMENSIONS", cls.local_embedding_dimensions, dotenv
            ),
            rag_chunk_size=_int_env("RAG_CHUNK_SIZE", cls.rag_chunk_size, dotenv),
            rag_chunk_overlap=_int_env(
                "RAG_CHUNK_OVERLAP", cls.rag_chunk_overlap, dotenv
            ),
            rag_recall_limit=_int_env("RAG_RECALL_LIMIT", cls.rag_recall_limit, dotenv),
            rag_lexical_weight=_float_env(
                "RAG_LEXICAL_WEIGHT", cls.rag_lexical_weight, dotenv
            ),
            rag_rrf_k=_int_env("RAG_RRF_K", cls.rag_rrf_k, dotenv),
            rag_reranker_provider=_env(
                "RAG_RERANKER_PROVIDER", cls.rag_reranker_provider, dotenv
            ),
            sentence_transformer_reranker_model=_env(
                "SENTENCE_TRANSFORMER_RERANKER_MODEL",
                cls.sentence_transformer_reranker_model,
                dotenv,
            ),
            sentence_transformer_reranker_device=_env(
                "SENTENCE_TRANSFORMER_RERANKER_DEVICE",
                cls.sentence_transformer_reranker_device,
                dotenv,
            ),
            rag_rerank_default_enabled=_bool_env(
                "RAG_RERANK_DEFAULT_ENABLED",
                cls.rag_rerank_default_enabled,
                dotenv,
            ),
            rag_max_prompt_chars=_int_env(
                "RAG_MAX_PROMPT_CHARS", cls.rag_max_prompt_chars, dotenv
            ),
            background_task_workers=_int_env(
                "BACKGROUND_TASK_WORKERS", cls.background_task_workers, dotenv
            ),
            background_task_queue_capacity=_int_env(
                "BACKGROUND_TASK_QUEUE_CAPACITY",
                cls.background_task_queue_capacity,
                dotenv,
            ),
            task_queue_backend=_env(
                "TASK_QUEUE_BACKEND", cls.task_queue_backend, dotenv
            ),
            redis_url=_env("REDIS_URL", cls.redis_url, dotenv),
            celery_result_backend_url=_env(
                "CELERY_RESULT_BACKEND_URL",
                cls.celery_result_backend_url,
                dotenv,
            ),
            celery_visibility_timeout_seconds=_int_env(
                "CELERY_VISIBILITY_TIMEOUT_SECONDS",
                cls.celery_visibility_timeout_seconds,
                dotenv,
            ),
            celery_task_max_retries=_int_env(
                "CELERY_TASK_MAX_RETRIES",
                cls.celery_task_max_retries,
                dotenv,
            ),
            celery_task_retry_backoff_seconds=_int_env(
                "CELERY_TASK_RETRY_BACKOFF_SECONDS",
                cls.celery_task_retry_backoff_seconds,
                dotenv,
            ),
            celery_task_retry_backoff_max_seconds=_int_env(
                "CELERY_TASK_RETRY_BACKOFF_MAX_SECONDS",
                cls.celery_task_retry_backoff_max_seconds,
                dotenv,
            ),
            celery_task_soft_time_limit_seconds=_int_env(
                "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS",
                cls.celery_task_soft_time_limit_seconds,
                dotenv,
            ),
            celery_task_time_limit_seconds=_int_env(
                "CELERY_TASK_TIME_LIMIT_SECONDS",
                cls.celery_task_time_limit_seconds,
                dotenv,
            ),
            celery_result_expires_seconds=_int_env(
                "CELERY_RESULT_EXPIRES_SECONDS",
                cls.celery_result_expires_seconds,
                dotenv,
            ),
            celery_worker_max_tasks_per_child=_int_env(
                "CELERY_WORKER_MAX_TASKS_PER_CHILD",
                cls.celery_worker_max_tasks_per_child,
                dotenv,
            ),
            mcp_enabled=_bool_env("MCP_ENABLED", cls.mcp_enabled, dotenv),
            mcp_config_path=_env("MCP_CONFIG_PATH", None, dotenv),
            mcp_request_timeout_seconds=_float_env(
                "MCP_REQUEST_TIMEOUT_SECONDS",
                cls.mcp_request_timeout_seconds,
                dotenv,
            ),
            sandbox_mode=_env("SANDBOX_MODE", cls.sandbox_mode, dotenv),
            sandbox_docker_image=_env(
                "SANDBOX_DOCKER_IMAGE",
                cls.sandbox_docker_image,
                dotenv,
            ),
            sandbox_command_timeout_seconds=_float_env(
                "SANDBOX_COMMAND_TIMEOUT_SECONDS",
                cls.sandbox_command_timeout_seconds,
                dotenv,
            ),
            sandbox_workspace_parent=_env("SANDBOX_WORKSPACE_PARENT", None, dotenv),
            agent_max_exploration_rounds=_int_env(
                "AGENT_MAX_EXPLORATION_ROUNDS",
                cls.agent_max_exploration_rounds,
                dotenv,
            ),
            agent_max_read_tools_per_round=_int_env(
                "AGENT_MAX_READ_TOOLS_PER_ROUND",
                cls.agent_max_read_tools_per_round,
                dotenv,
            ),
            agent_max_context_files=_int_env(
                "AGENT_MAX_CONTEXT_FILES",
                cls.agent_max_context_files,
                dotenv,
            ),
            agent_max_context_chars=_int_env(
                "AGENT_MAX_CONTEXT_CHARS",
                cls.agent_max_context_chars,
                dotenv,
            ),
            agent_max_instruction_chars=_int_env(
                "AGENT_MAX_INSTRUCTION_CHARS",
                cls.agent_max_instruction_chars,
                dotenv,
            ),
            agent_max_tool_rounds=_int_env(
                "AGENT_MAX_TOOL_ROUNDS",
                cls.agent_max_tool_rounds,
                dotenv,
            ),
            agent_max_tool_calls=_int_env(
                "AGENT_MAX_TOOL_CALLS",
                cls.agent_max_tool_calls,
                dotenv,
            ),
            auth_mode=_env("AUTH_MODE", cls.auth_mode, dotenv),
            gateway_trust_secret=_env("GATEWAY_TRUST_SECRET", None, dotenv),
        )

    def _validate_distributed_task_storage(self) -> None:
        required_values = {
            "session_repository": (self.session_repository, "postgres"),
            "agent_run_store": (self.agent_run_store, "postgres"),
            "document_store": (self.document_store, "postgres"),
            "workspace_store": (
                self.workspace_store,
                "postgres",
            ),
            "langgraph_checkpointer": (
                self.langgraph_checkpointer,
                "postgres",
            ),
            "rag_vector_store": (self.rag_vector_store, "qdrant"),
        }
        invalid = [
            f"{name}={actual} (expected {expected})"
            for name, (actual, expected) in required_values.items()
            if actual != expected
        ]
        if invalid:
            raise ValueError(
                "celery task queue requires shared storage: " + ", ".join(invalid)
            )


def _require_choice(name: str, value: str, allowed: set[str]) -> None:
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {choices}")


def _require_positive(name: str, value: float | int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")


def _env(name: str, default: str | None, dotenv: dict[str, str]) -> str | None:
    return os.getenv(name, dotenv.get(name, default))


def _int_env(name: str, default: int, dotenv: dict[str, str]) -> int:
    value = _env(name, None, dotenv)
    return int(value) if value is not None else default


def _float_env(name: str, default: float, dotenv: dict[str, str]) -> float:
    value = _env(name, None, dotenv)
    return float(value) if value is not None else default


def _bool_env(name: str, default: bool, dotenv: dict[str, str]) -> bool:
    value = _env(name, None, dotenv)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _paths_env(
    name: str,
    default: tuple[str, ...],
    dotenv: dict[str, str],
) -> tuple[str, ...]:
    value = _env(name, None, dotenv)
    if value is None:
        return default
    parsed = tuple(item.strip() for item in value.split(os.pathsep) if item.strip())
    return parsed or default


def _load_dotenv(path: str = ".env") -> dict[str, str]:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip("\"'")
        if name:
            values[name] = value
    return values
