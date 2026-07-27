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
    ) -> None:
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
