from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from pydantic import BaseModel, Field

from ai_agent_platform.domain import KnowledgeBaseRecord
from ai_agent_platform.integrations.rag import IngestedDocument, RetrievedDocument
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


class DocumentIngestRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    source_uri: Optional[str] = Field(default=None, max_length=1000)


class DocumentIngestResponse(BaseModel):
    knowledge_base_id: str
    document_id: str
    filename: str
    chunk_count: int

    @classmethod
    def from_domain(cls, document: IngestedDocument) -> "DocumentIngestResponse":
        return cls(
            knowledge_base_id=document.knowledge_base_id,
            document_id=document.document_id,
            filename=document.filename,
            chunk_count=document.chunk_count,
        )


class RAGSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=20)
    recall_limit: Optional[int] = Field(default=None, ge=1, le=100)


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
        )


class RAGSearchResponse(BaseModel):
    knowledge_base_id: str
    results: list[RAGChunkResponse]


class RAGAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=20)
    recall_limit: Optional[int] = Field(default=None, ge=1, le=100)
    provider: Optional[str] = Field(default=None, max_length=50)
    model: Optional[str] = Field(default=None, min_length=1, max_length=128)
    thinking_level: Optional[LLMThinkingLevel] = None


class RAGAskResponse(BaseModel):
    knowledge_base_id: str
    answer: str
    citations: list[RAGChunkResponse]
