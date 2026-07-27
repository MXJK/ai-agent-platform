from fastapi import APIRouter, HTTPException, Path, Response, status

from ai_agent_platform.integrations import (
    LLMClient,
    LLMProviderError,
    RAGConfigurationError,
    RAGProviderError,
    RAGValidationError,
)
from ai_agent_platform.schemas import (
    DocumentIngestRequest,
    DocumentIngestResponse,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseResponse,
    KnowledgeBasesResponse,
    KnowledgeBaseUpdateRequest,
    RAGAskRequest,
    RAGAskResponse,
    RAGChunkResponse,
    RAGSearchRequest,
    RAGSearchResponse,
)
from ai_agent_platform.services import (
    KnowledgeBaseAlreadyExistsError,
    KnowledgeBaseNotFoundError,
    KnowledgeBaseService,
)


KNOWLEDGE_BASE_ID = Path(
    min_length=1,
    max_length=128,
    pattern=r"^[a-zA-Z0-9_-]+$",
)


def create_knowledge_bases_router(
    knowledge_base_service: KnowledgeBaseService,
    llm_client: LLMClient,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/knowledge-bases",
        response_model=KnowledgeBaseResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_knowledge_base(
        request: KnowledgeBaseCreateRequest,
    ) -> KnowledgeBaseResponse:
        try:
            knowledge_base = knowledge_base_service.create(
                knowledge_base_id=request.id,
                name=request.name,
                description=request.description,
                tags=request.tags,
            )
        except KnowledgeBaseAlreadyExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="knowledge base already exists",
            ) from exc
        return KnowledgeBaseResponse.from_domain(knowledge_base)

    @router.get("/knowledge-bases", response_model=KnowledgeBasesResponse)
    def list_knowledge_bases() -> KnowledgeBasesResponse:
        return KnowledgeBasesResponse(
            knowledge_bases=[
                KnowledgeBaseResponse.from_domain(item)
                for item in knowledge_base_service.list()
            ]
        )

    @router.get(
        "/knowledge-bases/{knowledge_base_id}",
        response_model=KnowledgeBaseResponse,
    )
    def get_knowledge_base(
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> KnowledgeBaseResponse:
        try:
            knowledge_base = knowledge_base_service.get(knowledge_base_id)
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="knowledge base not found",
            ) from exc
        return KnowledgeBaseResponse.from_domain(knowledge_base)

    @router.put(
        "/knowledge-bases/{knowledge_base_id}",
        response_model=KnowledgeBaseResponse,
    )
    def update_knowledge_base(
        request: KnowledgeBaseUpdateRequest,
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> KnowledgeBaseResponse:
        try:
            knowledge_base = knowledge_base_service.update(
                knowledge_base_id=knowledge_base_id,
                name=request.name,
                description=request.description,
                tags=request.tags,
            )
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="knowledge base not found",
            ) from exc
        return KnowledgeBaseResponse.from_domain(knowledge_base)

    @router.delete(
        "/knowledge-bases/{knowledge_base_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_knowledge_base(
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> Response:
        try:
            knowledge_base_service.delete(knowledge_base_id)
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="knowledge base not found",
            ) from exc
        except RAGProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

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
            ingested = knowledge_base_service.ingest_document(
                knowledge_base_id=knowledge_base_id,
                filename=request.filename,
                content=request.content,
                source_uri=request.source_uri,
            )
        except RAGValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="knowledge base not found",
            ) from exc
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
            results = knowledge_base_service.search(
                knowledge_base_id=knowledge_base_id,
                query=request.query,
                limit=request.limit,
                recall_limit=request.recall_limit,
            )
        except RAGValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="knowledge base not found",
            ) from exc
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
            answer = knowledge_base_service.answer_question(
                knowledge_base_id=knowledge_base_id,
                question=request.question,
                llm_client=llm_client,
                provider=request.provider,
                model=request.model,
                thinking_level=request.thinking_level,
                limit=request.limit,
                recall_limit=request.recall_limit,
            )
        except RAGValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="knowledge base not found",
            ) from exc
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
