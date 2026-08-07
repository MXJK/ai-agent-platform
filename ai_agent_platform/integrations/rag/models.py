"""Data contracts and extension protocols for the RAG subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ParsedDocument:
    id: str
    knowledge_base_id: str
    filename: str
    text: str
    source_uri: str | None = None
    title: str | None = None
    description: str = ""
    tags: list[str] = field(default_factory=list)
    media_type: str | None = None
    byte_size: int | None = None


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    knowledge_base_id: str
    title: str
    filename: str
    description: str
    tags: list[str]
    media_type: str | None
    byte_size: int | None
    content_hash: str
    chunk_count: int
    is_searchable: bool
    last_index_status: str
    last_index_error: str | None
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None
    source_uri: str | None = None


@dataclass(frozen=True)
class DocumentChunk:
    id: str
    knowledge_base_id: str
    document_id: str
    filename: str
    chunk_index: int
    text: str
    start_line: int | None = None
    end_line: int | None = None
    symbols: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RetrievedDocument:
    id: str
    knowledge_base_id: str
    document_id: str
    filename: str
    chunk_index: int
    text: str
    score: float
    start_line: int | None = None
    end_line: int | None = None
    symbols: list[str] = field(default_factory=list)
    recall_score: float | None = None
    lexical_score: float | None = None
    hybrid_score: float | None = None
    rerank_score: float | None = None
    dense_rank: int | None = None
    lexical_rank: int | None = None
    fusion_score: float | None = None


@dataclass(frozen=True)
class IngestedDocument:
    knowledge_base_id: str
    document_id: str
    filename: str
    chunk_count: int
    index_job_id: str | None = None
    index_status: str = "active"
    document: KnowledgeDocument | None = None


@dataclass(frozen=True)
class DocumentVectorSnapshot:
    chunks: list[DocumentChunk]
    embeddings: list[list[float]]


@dataclass(frozen=True)
class IndexJob:
    id: str
    knowledge_base_id: str
    filename: str
    status: str
    document_id: str | None
    chunk_count: int
    error: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


@dataclass(frozen=True)
class RAGAnswer:
    answer: str
    citations: list[RetrievedDocument]
    retrieval: "RetrievalExecution"


@dataclass(frozen=True)
class RerankerCapabilities:
    available: bool
    provider: str | None
    model: str | None
    default_enabled: bool
    status: str


@dataclass(frozen=True)
class RetrievalExecution:
    rerank_requested: bool
    rerank_applied: bool
    provider: str | None
    model: str | None
    candidate_count: int
    result_count: int
    rerank_duration_ms: float | None = None


@dataclass(frozen=True)
class RAGSearchResult:
    results: list[RetrievedDocument]
    retrieval: RetrievalExecution


class EmbeddingProvider(Protocol):
    def embed_texts(
        self,
        texts: list[str],
        *,
        task_type: str = "document",
    ) -> list[list[float]]:
        ...


class VectorStore(Protocol):
    def delete_document(self, *, document_id: str) -> None:
        ...

    def delete_knowledge_base(self, *, knowledge_base_id: str) -> None:
        ...

    def snapshot_document(
        self,
        *,
        document_id: str,
    ) -> DocumentVectorSnapshot:
        ...

    def restore_document(
        self,
        *,
        document_id: str,
        snapshot: DocumentVectorSnapshot,
    ) -> None:
        ...

    def upsert_chunks(
        self,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        ...

    def replace_document(
        self,
        *,
        document_id: str,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        ...

    def search(
        self,
        *,
        knowledge_base_id: str,
        query_embedding: list[float],
        limit: int,
    ) -> list[RetrievedDocument]:
        ...


class DocumentStore(Protocol):
    def save_document(
        self,
        document: ParsedDocument,
        chunks: list[DocumentChunk],
    ) -> KnowledgeDocument:
        ...

    def find_document_by_filename(
        self,
        *,
        knowledge_base_id: str,
        filename: str,
    ) -> KnowledgeDocument | None:
        ...

    def get_document(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
    ) -> KnowledgeDocument | None:
        ...

    def list_documents(
        self,
        *,
        knowledge_base_id: str,
        query: str,
        status: str | None,
        sort: str,
        page: int,
        page_size: int,
    ) -> tuple[list[KnowledgeDocument], int]:
        ...

    def update_document_metadata(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
        title: str,
        description: str,
        tags: list[str],
    ) -> KnowledgeDocument | None:
        ...

    def get_document_chunks(
        self,
        *,
        document_id: str,
    ) -> list[DocumentChunk]:
        ...

    def delete_document(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
    ) -> bool:
        ...

    def search_lexical(
        self,
        *,
        knowledge_base_id: str,
        query: str,
        limit: int,
    ) -> list[RetrievedDocument]:
        ...


class IndexJobStore(Protocol):
    def create_index_job(self, job: IndexJob) -> None:
        ...

    def transition_index_job(
        self,
        *,
        job_id: str,
        expected_status: str,
        status: str,
        document_id: str | None = None,
        chunk_count: int | None = None,
        error: str | None = None,
    ) -> IndexJob:
        ...

    def get_index_job(self, job_id: str) -> IndexJob | None:
        ...

    def list_index_jobs(
        self,
        *,
        knowledge_base_id: str,
        limit: int,
    ) -> list[IndexJob]:
        ...


class Reranker(Protocol):
    def rerank(
        self,
        *,
        query: str,
        candidates: list[RetrievedDocument],
        limit: int,
    ) -> list[RetrievedDocument]:
        ...
