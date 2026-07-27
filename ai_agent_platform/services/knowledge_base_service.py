from __future__ import annotations

from typing import Protocol

from ai_agent_platform.domain import KnowledgeBaseRecord
from ai_agent_platform.integrations import LLMClient, RAGService
from ai_agent_platform.integrations.rag import (
    IndexJob,
    IngestedDocument,
    RAGAnswer,
    RetrievedDocument,
)


class KnowledgeBaseStore(Protocol):
    def create(
        self,
        *,
        knowledge_base_id: str,
        name: str,
        description: str,
        tags: list[str],
    ) -> KnowledgeBaseRecord | None:
        ...

    def get(self, knowledge_base_id: str) -> KnowledgeBaseRecord | None:
        ...

    def list(self) -> list[KnowledgeBaseRecord]:
        ...

    def update(
        self,
        *,
        knowledge_base_id: str,
        name: str,
        description: str,
        tags: list[str],
    ) -> KnowledgeBaseRecord | None:
        ...

    def delete(self, knowledge_base_id: str) -> bool:
        ...

    def record_document(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        ...


class KnowledgeBaseNotFoundError(KeyError):
    pass


class KnowledgeBaseAlreadyExistsError(ValueError):
    pass


class IndexJobNotFoundError(KeyError):
    pass


class KnowledgeBaseService:
    def __init__(self, *, store: KnowledgeBaseStore, rag_service: RAGService) -> None:
        self._store = store
        self._rag_service = rag_service

    def create(
        self,
        *,
        knowledge_base_id: str,
        name: str,
        description: str,
        tags: list[str],
    ) -> KnowledgeBaseRecord:
        record = self._store.create(
            knowledge_base_id=knowledge_base_id,
            name=name.strip(),
            description=description.strip(),
            tags=_normalize_tags(tags),
        )
        if record is None:
            raise KnowledgeBaseAlreadyExistsError(knowledge_base_id)
        return record

    def get(self, knowledge_base_id: str) -> KnowledgeBaseRecord:
        record = self._store.get(knowledge_base_id)
        if record is None:
            raise KnowledgeBaseNotFoundError(knowledge_base_id)
        return record

    def list(self) -> list[KnowledgeBaseRecord]:
        return self._store.list()

    def update(
        self,
        *,
        knowledge_base_id: str,
        name: str,
        description: str,
        tags: list[str],
    ) -> KnowledgeBaseRecord:
        record = self._store.update(
            knowledge_base_id=knowledge_base_id,
            name=name.strip(),
            description=description.strip(),
            tags=_normalize_tags(tags),
        )
        if record is None:
            raise KnowledgeBaseNotFoundError(knowledge_base_id)
        return record

    def delete(self, knowledge_base_id: str) -> None:
        self.get(knowledge_base_id)
        self._rag_service.delete_knowledge_base(
            knowledge_base_id=knowledge_base_id
        )
        if not self._store.delete(knowledge_base_id):
            raise KnowledgeBaseNotFoundError(knowledge_base_id)

    def ingest_document(
        self,
        *,
        knowledge_base_id: str,
        filename: str,
        content: str,
        source_uri: str | None,
    ) -> IngestedDocument:
        self.get(knowledge_base_id)
        ingested = self._rag_service.ingest_document(
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            content=content,
            source_uri=source_uri,
        )
        self._store.record_document(
            knowledge_base_id=knowledge_base_id,
            document_id=ingested.document_id,
        )
        return ingested

    def ingest_file(
        self,
        *,
        knowledge_base_id: str,
        filename: str,
        content: bytes,
        source_uri: str | None = None,
    ) -> IngestedDocument:
        self.get(knowledge_base_id)
        ingested = self._rag_service.ingest_file(
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            content=content,
            source_uri=source_uri,
        )
        self._store.record_document(
            knowledge_base_id=knowledge_base_id,
            document_id=ingested.document_id,
        )
        return ingested

    def search(
        self,
        *,
        knowledge_base_id: str,
        query: str,
        limit: int,
        recall_limit: int | None,
    ) -> list[RetrievedDocument]:
        self.get(knowledge_base_id)
        return self._rag_service.search(
            knowledge_base_id=knowledge_base_id,
            query=query,
            limit=limit,
            recall_limit=recall_limit,
        )

    def get_index_job(
        self,
        *,
        knowledge_base_id: str,
        job_id: str,
    ) -> IndexJob:
        self.get(knowledge_base_id)
        job = self._rag_service.get_index_job(job_id=job_id)
        if job is None or job.knowledge_base_id != knowledge_base_id:
            raise IndexJobNotFoundError(job_id)
        return job

    def list_index_jobs(
        self,
        *,
        knowledge_base_id: str,
        limit: int,
    ) -> list[IndexJob]:
        self.get(knowledge_base_id)
        return self._rag_service.list_index_jobs(
            knowledge_base_id=knowledge_base_id,
            limit=limit,
        )

    def answer_question(
        self,
        *,
        knowledge_base_id: str,
        question: str,
        llm_client: LLMClient,
        provider: str | None,
        model: str | None,
        thinking_level: str | None,
        limit: int,
        recall_limit: int | None,
    ) -> RAGAnswer:
        self.get(knowledge_base_id)
        return self._rag_service.answer_question(
            knowledge_base_id=knowledge_base_id,
            question=question,
            llm_client=llm_client,
            provider=provider,
            model=model,
            thinking_level=thinking_level,
            limit=limit,
            recall_limit=recall_limit,
        )


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        value = tag.strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized
