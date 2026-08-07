from __future__ import annotations

from typing import Protocol

from ai_agent_platform.domain import KnowledgeBaseRecord
from ai_agent_platform.integrations import LLMClient, RAGService
from ai_agent_platform.integrations.rag import (
    IndexJob,
    IngestedDocument,
    KnowledgeDocument,
    RAGAnswer,
    RAGSearchResult,
    RerankerCapabilities,
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

    def forget_document(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        ...

    def touch(self, knowledge_base_id: str) -> None:
        ...


class KnowledgeBaseNotFoundError(KeyError):
    pass


class KnowledgeBaseAlreadyExistsError(ValueError):
    pass


class IndexJobNotFoundError(KeyError):
    pass


class DocumentNotFoundError(KeyError):
    pass


class DocumentFilenameConflictError(ValueError):
    def __init__(self, *, filename: str, existing_document_id: str) -> None:
        super().__init__(filename)
        self.filename = filename
        self.existing_document_id = existing_document_id


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
        title: str | None = None,
        description: str = "",
        tags: list[str] | None = None,
        media_type: str | None = None,
    ) -> IngestedDocument:
        self.get(knowledge_base_id)
        self._ensure_filename_available(
            knowledge_base_id=knowledge_base_id,
            filename=filename,
        )
        ingested = self._rag_service.ingest_document(
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            content=content,
            source_uri=source_uri,
            title=title,
            description=description.strip(),
            tags=_normalize_tags(tags or []),
            media_type=media_type,
            byte_size=len(content.encode("utf-8")),
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
        title: str | None = None,
        description: str = "",
        tags: list[str] | None = None,
        media_type: str | None = None,
    ) -> IngestedDocument:
        self.get(knowledge_base_id)
        self._ensure_filename_available(
            knowledge_base_id=knowledge_base_id,
            filename=filename,
        )
        ingested = self._rag_service.ingest_file(
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            content=content,
            source_uri=source_uri,
            title=title,
            description=description.strip(),
            tags=_normalize_tags(tags or []),
            media_type=media_type,
        )
        self._store.record_document(
            knowledge_base_id=knowledge_base_id,
            document_id=ingested.document_id,
        )
        return ingested

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
        self.get(knowledge_base_id)
        return self._rag_service.list_documents(
            knowledge_base_id=knowledge_base_id,
            query=query.strip(),
            status=status,
            sort=sort,
            page=page,
            page_size=page_size,
        )

    def get_document(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
    ) -> KnowledgeDocument:
        self.get(knowledge_base_id)
        document = self._rag_service.get_document(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        if document is None:
            raise DocumentNotFoundError(document_id)
        return document

    def update_document(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
        title: str,
        description: str,
        tags: list[str],
    ) -> KnowledgeDocument:
        self.get_document(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        updated = self._rag_service.update_document_metadata(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            title=title.strip(),
            description=description.strip(),
            tags=_normalize_tags(tags),
        )
        if updated is None:
            raise DocumentNotFoundError(document_id)
        self._store.touch(knowledge_base_id)
        return updated

    def replace_document_file(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
        filename: str,
        content: bytes,
        media_type: str | None,
    ) -> IngestedDocument:
        document = self.get_document(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        self._ensure_filename_available(
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            replacing_document_id=document_id,
        )
        ingested = self._rag_service.replace_file(
            document=document,
            filename=filename,
            content=content,
            media_type=media_type,
        )
        self._store.record_document(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        return ingested

    def delete_document(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        self.get_document(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        if not self._rag_service.delete_document(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        ):
            raise DocumentNotFoundError(document_id)
        self._store.forget_document(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )

    def _ensure_filename_available(
        self,
        *,
        knowledge_base_id: str,
        filename: str,
        replacing_document_id: str | None = None,
    ) -> None:
        existing = self._rag_service.find_document_by_filename(
            knowledge_base_id=knowledge_base_id,
            filename=filename,
        )
        if existing is not None and existing.id != replacing_document_id:
            raise DocumentFilenameConflictError(
                filename=filename,
                existing_document_id=existing.id,
            )

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

    def search_with_metadata(
        self,
        *,
        knowledge_base_id: str,
        query: str,
        limit: int,
        recall_limit: int | None,
        rerank_enabled: bool | None,
    ) -> RAGSearchResult:
        self.get(knowledge_base_id)
        return self._rag_service.search_with_metadata(
            knowledge_base_id=knowledge_base_id,
            query=query,
            limit=limit,
            recall_limit=recall_limit,
            rerank_enabled=rerank_enabled,
        )

    def reranker_capabilities(self) -> RerankerCapabilities:
        return self._rag_service.reranker_capabilities()

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
        rerank_enabled: bool | None,
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
            rerank_enabled=rerank_enabled,
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
