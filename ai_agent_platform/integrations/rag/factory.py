"""Configuration-driven composition of the RAG subsystem."""

from __future__ import annotations

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.rag.errors import RAGConfigurationError
from ai_agent_platform.integrations.rag.models import (
    DocumentStore,
    EmbeddingProvider,
    Reranker,
    VectorStore,
)
from ai_agent_platform.integrations.rag.service import (
    ChromaVectorStore,
    GeminiEmbeddingProvider,
    HashingEmbeddingProvider,
    InMemoryVectorStore,
    NoopReranker,
    OpenAIEmbeddingProvider,
    QdrantVectorStore,
    RAGService,
    RecursiveCharacterChunker,
    SentenceTransformerCrossEncoderReranker,
    TextDocumentParser,
)


def create_rag_service(
    settings: Settings,
    *,
    document_store: DocumentStore | None = None,
) -> RAGService:
    if settings.embedding_provider == "openai":
        embedding_provider: EmbeddingProvider = OpenAIEmbeddingProvider(settings)
    elif settings.embedding_provider == "gemini":
        embedding_provider = GeminiEmbeddingProvider(settings)
    elif settings.embedding_provider == "local":
        embedding_provider = HashingEmbeddingProvider(
            dimensions=settings.local_embedding_dimensions
        )
    else:
        raise RAGConfigurationError(
            f"unsupported embedding provider: {settings.embedding_provider}"
        )

    if settings.rag_vector_store == "chroma":
        vector_store: VectorStore = ChromaVectorStore(
            persist_directory=settings.chroma_persist_directory,
            collection_name=settings.chroma_collection_name,
        )
    elif settings.rag_vector_store == "qdrant":
        vector_store = QdrantVectorStore(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            collection_name=settings.qdrant_collection_name,
        )
    elif settings.rag_vector_store == "memory":
        vector_store = InMemoryVectorStore()
    else:
        raise RAGConfigurationError(
            f"unsupported RAG vector store: {settings.rag_vector_store}"
        )

    if settings.rag_reranker_provider == "sentence_transformer":
        reranker: Reranker = SentenceTransformerCrossEncoderReranker(
            model_name=settings.sentence_transformer_reranker_model
        )
    elif settings.rag_reranker_provider == "none":
        reranker = NoopReranker()
    else:
        raise RAGConfigurationError(
            f"unsupported RAG reranker provider: {settings.rag_reranker_provider}"
        )

    return RAGService(
        parser=TextDocumentParser(),
        chunker=RecursiveCharacterChunker(
            chunk_size=settings.rag_chunk_size,
            chunk_overlap=settings.rag_chunk_overlap,
        ),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        reranker=reranker,
        default_recall_limit=settings.rag_recall_limit,
        max_prompt_chars=settings.rag_max_prompt_chars,
        document_store=document_store,
    )
