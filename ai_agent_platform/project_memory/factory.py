"""Configuration-driven project-memory composition."""

from __future__ import annotations

from typing import Callable

from ai_agent_platform.core import MetricsRegistry, Settings
from ai_agent_platform.integrations import LLMClient
from ai_agent_platform.integrations.rag import (
    GeminiEmbeddingProvider,
    HashingEmbeddingProvider,
    OpenAIEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)
from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.project_memory.extractor import LLMMemoryExtractor
from ai_agent_platform.project_memory.service import ProjectMemoryService
from ai_agent_platform.project_memory.sqlite_vector import SQLiteMemoryVectorStore
from ai_agent_platform.project_memory.vector import (
    InMemoryMemoryVectorStore,
    QdrantMemoryVectorStore,
)
from ai_agent_platform.repositories import (
    InMemoryProjectMemoryRepository,
    PostgresProjectMemoryRepository,
    SQLiteProjectMemoryRepository,
)
from ai_agent_platform.services.workspace_service import WorkspaceService


def create_project_memory_service(
    settings: Settings,
    *,
    workspace_service: WorkspaceService,
    llm_client: LLMClient,
    metrics: MetricsRegistry,
    usage_ledger=None,
    local_state_database: LocalStateDatabase | None = None,
    credential_resolver: Callable[[str], str | None] | None = None,
) -> ProjectMemoryService:
    if settings.project_memory_store == "postgres":
        repository = PostgresProjectMemoryRepository(
            database_url=settings.database_url
        )
    elif settings.project_memory_store == "sqlite":
        if local_state_database is None:
            raise ValueError("PROJECT_MEMORY_STORE=sqlite requires local state")
        repository = SQLiteProjectMemoryRepository(database=local_state_database)
    else:
        repository = InMemoryProjectMemoryRepository()

    if settings.embedding_provider == "openai":
        embedding_provider = OpenAIEmbeddingProvider(
            settings,
            usage_ledger=usage_ledger,
            credential_resolver=credential_resolver,
        )
    elif settings.embedding_provider == "gemini":
        embedding_provider = GeminiEmbeddingProvider(
            settings,
            usage_ledger=usage_ledger,
            credential_resolver=credential_resolver,
        )
    elif settings.embedding_provider == "sentence_transformer":
        embedding_provider = SentenceTransformerEmbeddingProvider(
            model_name=settings.embedding_model,
            device=settings.sentence_transformer_embedding_device,
            usage_ledger=usage_ledger,
        )
    else:
        embedding_provider = HashingEmbeddingProvider(
            dimensions=settings.local_embedding_dimensions,
            model=settings.embedding_model,
            usage_ledger=usage_ledger,
        )

    if settings.project_memory_vector_store == "qdrant":
        vector_store = QdrantMemoryVectorStore(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            collection_name=settings.project_memory_qdrant_collection,
        )
    elif settings.project_memory_vector_store == "sqlite":
        if local_state_database is None:
            raise ValueError("PROJECT_MEMORY_VECTOR_STORE=sqlite requires local state")
        vector_store = SQLiteMemoryVectorStore(
            database=local_state_database,
            model=settings.embedding_model,
        )
    else:
        vector_store = InMemoryMemoryVectorStore()

    return ProjectMemoryService(
        repository=repository,
        workspace_service=workspace_service,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        extractor=LLMMemoryExtractor(llm_client),
        enabled=settings.project_memory_enabled,
        default_mode=settings.project_memory_mode,
        candidate_threshold=settings.project_memory_candidate_threshold,
        auto_threshold=settings.project_memory_auto_threshold,
        recall_limit=settings.project_memory_recall_limit,
        result_limit=settings.project_memory_result_limit,
        max_context_chars=settings.project_memory_max_context_chars,
        relevance_weight=settings.project_memory_relevance_weight,
        recency_weight=settings.project_memory_recency_weight,
        importance_weight=settings.project_memory_importance_weight,
        recency_half_life_days=settings.project_memory_recency_half_life_days,
        metrics=metrics,
    )


__all__ = ["create_project_memory_service"]
