from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "ai-agent-platform"
    api_prefix: str = "/api/v1"
    llm_provider: str = "fake"
    llm_model: str = "demo-stream-model"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    llm_max_input_chars: int = 8000
    llm_max_context_messages: int = 12
    llm_max_output_tokens: int = 1024
    rag_vector_store: str = "memory"
    chroma_persist_directory: str = ".chroma"
    chroma_collection_name: str = "rag_chunks"
    embedding_provider: str = "gemini"
    embedding_model: str = "gemini-embedding-001"
    local_embedding_dimensions: int = 128
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 120
    rag_recall_limit: int = 20
    rag_reranker_provider: str = "none"
    sentence_transformer_reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rag_max_prompt_chars: int = 6000

    @classmethod
    def from_env(cls) -> "Settings":
        dotenv = _load_dotenv()
        return cls(
            app_name=_env("APP_NAME", cls.app_name, dotenv),
            api_prefix=_env("API_PREFIX", cls.api_prefix, dotenv),
            llm_provider=_env("LLM_PROVIDER", cls.llm_provider, dotenv),
            llm_model=_env("LLM_MODEL", cls.llm_model, dotenv),
            openai_api_key=_env("OPENAI_API_KEY", None, dotenv),
            anthropic_api_key=_env("ANTHROPIC_API_KEY", None, dotenv),
            google_api_key=_env(
                "GOOGLE_API_KEY",
                _env("GEMINI_API_KEY", None, dotenv),
                dotenv,
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
        )


def _env(name: str, default: str | None, dotenv: dict[str, str]) -> str | None:
    return os.getenv(name, dotenv.get(name, default))


def _int_env(name: str, default: int, dotenv: dict[str, str]) -> int:
    value = _env(name, None, dotenv)
    return int(value) if value is not None else default


def _float_env(name: str, default: float, dotenv: dict[str, str]) -> float:
    value = _env(name, None, dotenv)
    return float(value) if value is not None else default


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
