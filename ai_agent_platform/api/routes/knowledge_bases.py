from fastapi import APIRouter, HTTPException, Path, status

from ai_agent_platform.integrations import (
    LLMClient,
    LLMProviderError,
    RAGConfigurationError,
    RAGProviderError,
    RAGService,
    RAGValidationError,
)
from ai_agent_platform.schemas import (
    DocumentIngestRequest,
    DocumentIngestResponse,
    RAGAskRequest,
    RAGAskResponse,
    RAGChunkResponse,
    RAGSearchRequest,
    RAGSearchResponse,
)


KNOWLEDGE_BASE_ID = Path(
    min_length=1,
    max_length=128,
    pattern=r"^[a-zA-Z0-9_-]+$",
)


def create_knowledge_bases_router(
    rag_service: RAGService,
    llm_client: LLMClient,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/knowledge-bases/{knowledge_base_id}/documents",
        response_model=DocumentIngestResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def ingest_document(
        request: DocumentIngestRequest,
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> DocumentIngestResponse:
        try:
            ingested = rag_service.ingest_document(
                knowledge_base_id=knowledge_base_id,
                filename=request.filename,
                content=request.content,
                source_uri=request.source_uri,
            )
        except RAGValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return DocumentIngestResponse.from_domain(ingested)

    @router.post(
        "/knowledge-bases/{knowledge_base_id}/search",
        response_model=RAGSearchResponse,
    )
    def search_knowledge_base(
        request: RAGSearchRequest,
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> RAGSearchResponse:
        try:
            results = rag_service.search(
                knowledge_base_id=knowledge_base_id,
                query=request.query,
                limit=request.limit,
                recall_limit=request.recall_limit,
            )
        except RAGValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RAGSearchResponse(
            knowledge_base_id=knowledge_base_id,
            results=[RAGChunkResponse.from_domain(result) for result in results],
        )

    @router.post(
        "/knowledge-bases/{knowledge_base_id}/ask",
        response_model=RAGAskResponse,
    )
    def ask_knowledge_base(
        request: RAGAskRequest,
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> RAGAskResponse:
        try:
            answer = rag_service.answer_question(
                knowledge_base_id=knowledge_base_id,
                question=request.question,
                llm_client=llm_client,
                provider=request.provider,
                model=request.model,
                limit=request.limit,
                recall_limit=request.recall_limit,
            )
        except RAGValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RAGProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (RAGConfigurationError, LLMProviderError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return RAGAskResponse(
            knowledge_base_id=knowledge_base_id,
            answer=answer.answer,
            citations=[
                RAGChunkResponse.from_domain(citation)
                for citation in answer.citations
            ],
        )

    return router
