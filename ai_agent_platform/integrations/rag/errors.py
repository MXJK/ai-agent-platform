"""Stable exception hierarchy for RAG boundaries."""


class RAGError(Exception):
    """Base error for retrieval and ingestion failures."""


class RAGValidationError(RAGError):
    """Raised when a document or query is invalid."""


class RAGConfigurationError(RAGError):
    """Raised when a configured provider cannot be initialized."""


class RAGProviderError(RAGError):
    """Raised when an embedding, vector, or reranking provider fails."""


class RAGRerankerUnavailableError(RAGError):
    """Raised when a request requires a reranker that is not configured."""
