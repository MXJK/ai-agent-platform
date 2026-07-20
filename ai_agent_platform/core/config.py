from __future__ import annotations

from dataclasses import dataclass
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
    repository_index_store: str = "memory"
    langgraph_checkpointer: str = "memory"
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    llm_max_input_chars: int = 8000
    llm_max_context_messages: int = 12
    llm_max_output_tokens: int = 1024
    rag_vector_store: str = "memory"
    chroma_persist_directory: str = ".chroma"
    chroma_collection_name: str = "rag_chunks"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection_name: str = "repo_chunks"
    embedding_provider: str = "local"
    embedding_model: str = "gemini-embedding-001"
    local_embedding_dimensions: int = 128
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 120
    rag_recall_limit: int = 20
    rag_lexical_weight: float = 0.35
    rag_reranker_provider: str = "none"
    sentence_transformer_reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rag_max_prompt_chars: int = 6000
    background_task_workers: int = 4
    background_task_queue_capacity: int = 100
    mcp_enabled: bool = False
    mcp_config_path: str | None = None
    mcp_request_timeout_seconds: float = 10.0
    sandbox_mode: str = "local"
    sandbox_docker_image: str = "python:3.11-slim"
    sandbox_command_timeout_seconds: float = 30.0
    sandbox_workspace_parent: str | None = None

    def __post_init__(self) -> None:
        if not self.api_prefix.startswith("/"):
            raise ValueError("api_prefix must start with '/'")
        _require_choice(
            "log_level",
            self.log_level.upper(),
            {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
        )
        _require_choice("log_format", self.log_format, {"json", "text"})
        for name, value in (
            ("session_repository", self.session_repository),
            ("agent_run_store", self.agent_run_store),
            ("document_store", self.document_store),
            ("repository_index_store", self.repository_index_store),
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
        _require_choice("sandbox_mode", self.sandbox_mode, {"local", "docker"})
        for name, value in (
            ("llm_timeout_seconds", self.llm_timeout_seconds),
            ("llm_max_input_chars", self.llm_max_input_chars),
            ("llm_max_context_messages", self.llm_max_context_messages),
            ("llm_max_output_tokens", self.llm_max_output_tokens),
            ("local_embedding_dimensions", self.local_embedding_dimensions),
            ("rag_chunk_size", self.rag_chunk_size),
            ("rag_recall_limit", self.rag_recall_limit),
            ("rag_max_prompt_chars", self.rag_max_prompt_chars),
            ("background_task_workers", self.background_task_workers),
            ("mcp_request_timeout_seconds", self.mcp_request_timeout_seconds),
            (
                "sandbox_command_timeout_seconds",
                self.sandbox_command_timeout_seconds,
            ),
        ):
            _require_positive(name, value)
        if self.llm_max_retries < 0:
            raise ValueError("llm_max_retries must be greater than or equal to 0")
        if self.rag_chunk_overlap < 0:
            raise ValueError("rag_chunk_overlap must be greater than or equal to 0")
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("rag_chunk_overlap must be smaller than rag_chunk_size")
        if not 0.0 <= self.rag_lexical_weight <= 1.0:
            raise ValueError("rag_lexical_weight must be between 0 and 1")
        if self.background_task_queue_capacity < 0:
            raise ValueError(
                "background_task_queue_capacity must be greater than or equal to 0"
            )
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
            repository_index_store=_env(
                "REPOSITORY_INDEX_STORE", cls.repository_index_store, dotenv
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
            rag_reranker_provider=_env(
                "RAG_RERANKER_PROVIDER", cls.rag_reranker_provider, dotenv
            ),
            sentence_transformer_reranker_model=_env(
                "SENTENCE_TRANSFORMER_RERANKER_MODEL",
                cls.sentence_transformer_reranker_model,
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
