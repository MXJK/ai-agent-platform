"""RAG parsing, retrieval, storage, and service composition.

The package keeps the historical ``ai_agent_platform.integrations.rag`` import
surface stable while implementations live in focused modules.
"""

from ai_agent_platform.integrations.rag.errors import (
    RAGConfigurationError,
    RAGError,
    RAGProviderError,
    RAGValidationError,
)
from ai_agent_platform.integrations.rag.evaluation import (
    RetrievalMetrics,
    evaluate_retrieval,
)
from ai_agent_platform.integrations.rag.models import (
    DocumentChunk,
    DocumentStore,
    EmbeddingProvider,
    IngestedDocument,
    ParsedDocument,
    RAGAnswer,
    Reranker,
    RetrievedDocument,
    VectorStore,
)
from ai_agent_platform.integrations.rag.factory import create_rag_service
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
    SUPPORTED_DOCUMENT_EXTENSIONS,
    SUPPORTED_TEXT_EXTENSIONS,
    TextDocumentParser,
)

__all__ = [
    "ChromaVectorStore",
    "DocumentChunk",
    "DocumentStore",
    "EmbeddingProvider",
    "GeminiEmbeddingProvider",
    "HashingEmbeddingProvider",
    "InMemoryVectorStore",
    "IngestedDocument",
    "NoopReranker",
    "OpenAIEmbeddingProvider",
    "ParsedDocument",
    "QdrantVectorStore",
    "RAGAnswer",
    "RAGConfigurationError",
    "RAGError",
    "RAGProviderError",
    "RAGService",
    "RAGValidationError",
    "RecursiveCharacterChunker",
    "Reranker",
    "RetrievedDocument",
    "RetrievalMetrics",
    "SentenceTransformerCrossEncoderReranker",
    "SUPPORTED_DOCUMENT_EXTENSIONS",
    "SUPPORTED_TEXT_EXTENSIONS",
    "TextDocumentParser",
    "VectorStore",
    "create_rag_service",
    "evaluate_retrieval",
]
