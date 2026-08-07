from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from pydantic import BaseModel, Field

from ai_agent_platform.domain import KnowledgeBaseRecord
from ai_agent_platform.integrations.rag import (
    IndexJob,
    IngestedDocument,
    KnowledgeDocument,
    RerankerCapabilities,
    RetrievedDocument,
    RetrievalExecution,
)
from ai_agent_platform.schemas.chat import LLMThinkingLevel


KnowledgeBaseTag = Annotated[str, Field(min_length=1, max_length=64)]


class KnowledgeBaseCreateRequest(BaseModel):
    id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    tags: list[KnowledgeBaseTag] = Field(default_factory=list, max_length=20)


class KnowledgeBaseUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    tags: list[KnowledgeBaseTag] = Field(default_factory=list, max_length=20)


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str]
    document_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(
        cls,
        knowledge_base: KnowledgeBaseRecord,
    ) -> "KnowledgeBaseResponse":
        return cls(**knowledge_base.__dict__)


class KnowledgeBasesResponse(BaseModel):
    knowledge_bases: list[KnowledgeBaseResponse]


class KnowledgeDocumentResponse(BaseModel):
    id: str
    knowledge_base_id: str
    title: str
    filename: str
    description: str
    tags: list[str]
    media_type: Optional[str] = None
    byte_size: Optional[int] = None
    content_hash: str
    chunk_count: int
    is_searchable: bool
    last_index_status: str
    last_index_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    indexed_at: Optional[datetime] = None
    source_uri: Optional[str] = None

    @classmethod
    def from_domain(cls, document: KnowledgeDocument) -> "KnowledgeDocumentResponse":
        return cls(**document.__dict__)


class KnowledgeDocumentsResponse(BaseModel):
    items: list[KnowledgeDocumentResponse]
    total: int
    page: int
    page_size: int


class KnowledgeDocumentUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    tags: list[KnowledgeBaseTag] = Field(default_factory=list, max_length=20)


class KnowledgeDocumentBulkDeleteRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=100)


class KnowledgeDocumentDeleteFailure(BaseModel):
    document_id: str
    code: str
    message: str


class KnowledgeDocumentBulkDeleteResponse(BaseModel):
    deleted_ids: list[str]
    failures: list[KnowledgeDocumentDeleteFailure]


class DocumentIngestResponse(BaseModel):
    knowledge_base_id: str
    document_id: str
    filename: str
    chunk_count: int
    index_job_id: Optional[str] = None
    index_status: str
    document: Optional[KnowledgeDocumentResponse] = None

    @classmethod
    def from_domain(cls, document: IngestedDocument) -> "DocumentIngestResponse":
        return cls(
            knowledge_base_id=document.knowledge_base_id,
            document_id=document.document_id,
            filename=document.filename,
            chunk_count=document.chunk_count,
            index_job_id=document.index_job_id,
            index_status=document.index_status,
            document=(
                KnowledgeDocumentResponse.from_domain(document.document)
                if document.document is not None
                else None
            ),
        )


class IndexJobResponse(BaseModel):
    id: str
    knowledge_base_id: str
    filename: str
    status: str
    document_id: Optional[str] = None
    chunk_count: int
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None

    @classmethod
    def from_domain(cls, job: IndexJob) -> "IndexJobResponse":
        return cls(**job.__dict__)


class IndexJobsResponse(BaseModel):
    index_jobs: list[IndexJobResponse]


class RAGSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=20)
    recall_limit: Optional[int] = Field(default=None, ge=1, le=100)
    rerank_enabled: Optional[bool] = None


class RAGChunkResponse(BaseModel):
    id: str
    knowledge_base_id: str
    document_id: str
    filename: str
    chunk_index: int
    text: str
    score: float
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    symbols: list[str] = Field(default_factory=list)
    recall_score: Optional[float] = None
    lexical_score: Optional[float] = None
    hybrid_score: Optional[float] = None
    rerank_score: Optional[float] = None
    dense_rank: Optional[int] = None
    lexical_rank: Optional[int] = None
    fusion_score: Optional[float] = None

    @classmethod
    def from_domain(cls, chunk: RetrievedDocument) -> "RAGChunkResponse":
        return cls(
            id=chunk.id,
            knowledge_base_id=chunk.knowledge_base_id,
            document_id=chunk.document_id,
            filename=chunk.filename,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            score=chunk.score,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            symbols=chunk.symbols,
            recall_score=chunk.recall_score,
            lexical_score=chunk.lexical_score,
            hybrid_score=chunk.hybrid_score,
            rerank_score=chunk.rerank_score,
            dense_rank=chunk.dense_rank,
            lexical_rank=chunk.lexical_rank,
            fusion_score=chunk.fusion_score,
        )


class RAGAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=20)
    recall_limit: Optional[int] = Field(default=None, ge=1, le=100)
    rerank_enabled: Optional[bool] = None
    provider: Optional[str] = Field(default=None, max_length=50)
    model: Optional[str] = Field(default=None, min_length=1, max_length=128)
    thinking_level: Optional[LLMThinkingLevel] = None
    conversation_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    workspace_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )


class RerankerCapabilitiesResponse(BaseModel):
    available: bool
    provider: Optional[str] = None
    model: Optional[str] = None
    default_enabled: bool
    status: str

    @classmethod
    def from_domain(
        cls,
        capabilities: RerankerCapabilities,
    ) -> "RerankerCapabilitiesResponse":
        return cls(**capabilities.__dict__)


class RAGCapabilitiesResponse(BaseModel):
    reranker: RerankerCapabilitiesResponse


class RetrievalExecutionResponse(BaseModel):
    rerank_requested: bool
    rerank_applied: bool
    provider: Optional[str] = None
    model: Optional[str] = None
    candidate_count: int
    result_count: int
    rerank_duration_ms: Optional[float] = None

    @classmethod
    def from_domain(
        cls,
        execution: RetrievalExecution,
    ) -> "RetrievalExecutionResponse":
        return cls(**execution.__dict__)


class RAGSearchResponse(BaseModel):
    knowledge_base_id: str
    results: list[RAGChunkResponse]
    retrieval: RetrievalExecutionResponse


class RAGAskResponse(BaseModel):
    knowledge_base_id: str
    answer: str
    citations: list[RAGChunkResponse]
    retrieval: RetrievalExecutionResponse
