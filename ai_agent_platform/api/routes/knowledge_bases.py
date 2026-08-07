from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    Response,
    UploadFile,
    status,
)
import mimetypes
from typing import Literal
from uuid import uuid4

from ai_agent_platform.integrations import (
    LLMClient,
    LLMProviderError,
    RAGConfigurationError,
    RAGProviderError,
    RAGRerankerUnavailableError,
    RAGValidationError,
)
from ai_agent_platform.schemas import (
    DocumentIngestResponse,
    IndexJobResponse,
    IndexJobsResponse,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseResponse,
    KnowledgeBasesResponse,
    KnowledgeBaseUpdateRequest,
    KnowledgeDocumentBulkDeleteRequest,
    KnowledgeDocumentBulkDeleteResponse,
    KnowledgeDocumentDeleteFailure,
    KnowledgeDocumentResponse,
    KnowledgeDocumentsResponse,
    KnowledgeDocumentUpdateRequest,
    RAGCapabilitiesResponse,
    RAGAskRequest,
    RAGAskResponse,
    RAGChunkResponse,
    RAGSearchRequest,
    RAGSearchResponse,
    RerankerCapabilitiesResponse,
    RetrievalExecutionResponse,
)
from ai_agent_platform.services import (
    DocumentFilenameConflictError,
    DocumentNotFoundError,
    IndexJobNotFoundError,
    KnowledgeBaseAlreadyExistsError,
    KnowledgeBaseNotFoundError,
    KnowledgeBaseService,
    SessionService,
    WorkspaceNotFoundError,
    WorkspaceService,
)
from ai_agent_platform.repositories import SessionNotFoundError
from ai_agent_platform.usage_ledger import model_usage_scope
from ai_agent_platform.model_registry import ModelRegistryService, model_selection_scope


KNOWLEDGE_BASE_ID = Path(
    min_length=1,
    max_length=128,
    pattern=r"^[a-zA-Z0-9_-]+$",
)
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
DOCUMENT_ID = Path(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")


def create_knowledge_bases_router(
    knowledge_base_service: KnowledgeBaseService,
    llm_client: LLMClient,
    *,
    session_service: SessionService | None = None,
    workspace_service: WorkspaceService | None = None,
    model_registry: ModelRegistryService | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/rag/capabilities", response_model=RAGCapabilitiesResponse)
    def get_rag_capabilities() -> RAGCapabilitiesResponse:
        return RAGCapabilitiesResponse(
            reranker=RerankerCapabilitiesResponse.from_domain(
                knowledge_base_service.reranker_capabilities()
            )
        )

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
    async def ingest_document(
        file: UploadFile = File(...),
        title: str | None = Form(default=None, max_length=255),
        description: str = Form(default="", max_length=2000),
        tags: str = Form(default="", max_length=1000),
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> DocumentIngestResponse:
        filename = _upload_filename(file.filename)
        if not filename or len(filename) > 255:
            raise HTTPException(status_code=400, detail="invalid upload filename")
        try:
            content = await file.read(MAX_DOCUMENT_BYTES + 1)
        finally:
            await file.close()
        if len(content) > MAX_DOCUMENT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="uploaded file exceeds the 20 MiB limit",
            )
        try:
            ingested = knowledge_base_service.ingest_file(
                knowledge_base_id=knowledge_base_id,
                filename=filename,
                content=content,
                title=title.strip() if title and title.strip() else None,
                description=description,
                tags=_form_tags(tags),
                media_type=_upload_media_type(filename, file.content_type),
            )
        except DocumentFilenameConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "document_filename_conflict",
                    "message": "a document with this filename already exists",
                    "existing_document_id": exc.existing_document_id,
                    "filename": exc.filename,
                },
            ) from exc
        except RAGValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="knowledge base not found",
            ) from exc
        except RAGProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return DocumentIngestResponse.from_domain(ingested)

    @router.get(
        "/knowledge-bases/{knowledge_base_id}/documents",
        response_model=KnowledgeDocumentsResponse,
    )
    def list_documents(
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
        query: str = Query(default="", max_length=255),
        status_filter: Literal[
            "pending",
            "parsing",
            "embedding",
            "vector_written",
            "active",
            "failed",
        ]
        | None = Query(default=None, alias="status"),
        sort: Literal[
            "updated_at_desc",
            "created_at_desc",
            "title_asc",
        ] = Query(default="updated_at_desc"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> KnowledgeDocumentsResponse:
        try:
            documents, total = knowledge_base_service.list_documents(
                knowledge_base_id=knowledge_base_id,
                query=query,
                status=status_filter,
                sort=sort,
                page=page,
                page_size=page_size,
            )
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(status_code=404, detail="knowledge base not found") from exc
        return KnowledgeDocumentsResponse(
            items=[KnowledgeDocumentResponse.from_domain(item) for item in documents],
            total=total,
            page=page,
            page_size=page_size,
        )

    @router.post(
        "/knowledge-bases/{knowledge_base_id}/documents/bulk-delete",
        response_model=KnowledgeDocumentBulkDeleteResponse,
    )
    def bulk_delete_documents(
        request: KnowledgeDocumentBulkDeleteRequest,
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> KnowledgeDocumentBulkDeleteResponse:
        deleted_ids: list[str] = []
        failures: list[KnowledgeDocumentDeleteFailure] = []
        for document_id in dict.fromkeys(request.document_ids):
            try:
                knowledge_base_service.delete_document(
                    knowledge_base_id=knowledge_base_id,
                    document_id=document_id,
                )
                deleted_ids.append(document_id)
            except (KnowledgeBaseNotFoundError, DocumentNotFoundError):
                failures.append(
                    KnowledgeDocumentDeleteFailure(
                        document_id=document_id,
                        code="document_not_found",
                        message="document not found in this knowledge base",
                    )
                )
            except RAGProviderError as exc:
                failures.append(
                    KnowledgeDocumentDeleteFailure(
                        document_id=document_id,
                        code="document_delete_failed",
                        message=str(exc),
                    )
                )
        return KnowledgeDocumentBulkDeleteResponse(
            deleted_ids=deleted_ids,
            failures=failures,
        )

    @router.get(
        "/knowledge-bases/{knowledge_base_id}/documents/{document_id}",
        response_model=KnowledgeDocumentResponse,
    )
    def get_document(
        document_id: str = DOCUMENT_ID,
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> KnowledgeDocumentResponse:
        try:
            document = knowledge_base_service.get_document(
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
            )
        except (KnowledgeBaseNotFoundError, DocumentNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="document not found") from exc
        return KnowledgeDocumentResponse.from_domain(document)

    @router.patch(
        "/knowledge-bases/{knowledge_base_id}/documents/{document_id}",
        response_model=KnowledgeDocumentResponse,
    )
    def update_document(
        request: KnowledgeDocumentUpdateRequest,
        document_id: str = DOCUMENT_ID,
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> KnowledgeDocumentResponse:
        try:
            document = knowledge_base_service.update_document(
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                title=request.title,
                description=request.description,
                tags=request.tags,
            )
        except (KnowledgeBaseNotFoundError, DocumentNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="document not found") from exc
        return KnowledgeDocumentResponse.from_domain(document)

    @router.put(
        "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/content",
        response_model=DocumentIngestResponse,
    )
    async def replace_document_content(
        file: UploadFile = File(...),
        document_id: str = DOCUMENT_ID,
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> DocumentIngestResponse:
        filename = _upload_filename(file.filename)
        if not filename or len(filename) > 255:
            raise HTTPException(status_code=400, detail="invalid upload filename")
        media_type = _upload_media_type(filename, file.content_type)
        try:
            content = await file.read(MAX_DOCUMENT_BYTES + 1)
        finally:
            await file.close()
        if len(content) > MAX_DOCUMENT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="uploaded file exceeds the 20 MiB limit",
            )
        try:
            ingested = knowledge_base_service.replace_document_file(
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                filename=filename,
                content=content,
                media_type=media_type,
            )
        except DocumentFilenameConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "document_filename_conflict",
                    "message": "a document with this filename already exists",
                    "existing_document_id": exc.existing_document_id,
                    "filename": exc.filename,
                },
            ) from exc
        except (KnowledgeBaseNotFoundError, DocumentNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="document not found") from exc
        except RAGValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RAGProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return DocumentIngestResponse.from_domain(ingested)

    @router.delete(
        "/knowledge-bases/{knowledge_base_id}/documents/{document_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_document(
        document_id: str = DOCUMENT_ID,
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> Response:
        try:
            knowledge_base_service.delete_document(
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
            )
        except (KnowledgeBaseNotFoundError, DocumentNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="document not found") from exc
        except RAGProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get(
        "/knowledge-bases/{knowledge_base_id}/index-jobs",
        response_model=IndexJobsResponse,
    )
    def list_index_jobs(
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> IndexJobsResponse:
        try:
            jobs = knowledge_base_service.list_index_jobs(
                knowledge_base_id=knowledge_base_id,
                limit=limit,
            )
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="knowledge base not found",
            ) from exc
        return IndexJobsResponse(
            index_jobs=[IndexJobResponse.from_domain(job) for job in jobs]
        )

    @router.get(
        "/knowledge-bases/{knowledge_base_id}/index-jobs/{job_id}",
        response_model=IndexJobResponse,
    )
    def get_index_job(
        job_id: str = Path(min_length=1, max_length=128),
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> IndexJobResponse:
        try:
            job = knowledge_base_service.get_index_job(
                knowledge_base_id=knowledge_base_id,
                job_id=job_id,
            )
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="knowledge base not found",
            ) from exc
        except IndexJobNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="index job not found",
            ) from exc
        return IndexJobResponse.from_domain(job)

    @router.post(
        "/knowledge-bases/{knowledge_base_id}/search",
        response_model=RAGSearchResponse,
    )
    def search_knowledge_base(
        request: RAGSearchRequest,
        knowledge_base_id: str = KNOWLEDGE_BASE_ID,
    ) -> RAGSearchResponse:
        try:
            search_result = knowledge_base_service.search_with_metadata(
                knowledge_base_id=knowledge_base_id,
                query=request.query,
                limit=request.limit,
                recall_limit=request.recall_limit,
                rerank_enabled=request.rerank_enabled,
            )
        except RAGRerankerUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except RAGValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (RAGConfigurationError, RAGProviderError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="knowledge base not found",
            ) from exc
        return RAGSearchResponse(
            knowledge_base_id=knowledge_base_id,
            results=[
                RAGChunkResponse.from_domain(result)
                for result in search_result.results
            ],
            retrieval=RetrievalExecutionResponse.from_domain(
                search_result.retrieval
            ),
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
            if request.conversation_id and session_service is not None:
                session_service.get_session(request.conversation_id)
            if request.workspace_id and workspace_service is not None:
                workspace_service.get(request.workspace_id)
            request_id = f"rag_ask_{uuid4().hex[:12]}"
            selection = (
                model_registry.selection_for_session(request.conversation_id)
                if model_registry is not None and request.conversation_id
                else None
            )
            with model_usage_scope(
                session_id=request.conversation_id,
                workspace_id=request.workspace_id,
                operation="rag_ask",
                resource_id=request_id,
            ), model_selection_scope(selection):
                answer = knowledge_base_service.answer_question(
                    knowledge_base_id=knowledge_base_id,
                    question=request.question,
                    llm_client=llm_client,
                    provider=request.provider,
                    model=request.model,
                    thinking_level=request.thinking_level,
                    limit=request.limit,
                    recall_limit=request.recall_limit,
                    rerank_enabled=request.rerank_enabled,
                )
        except RAGRerankerUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except RAGValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KnowledgeBaseNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="knowledge base not found",
            ) from exc
        except SessionNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="conversation not found",
            ) from exc
        except WorkspaceNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="workspace not found",
            ) from exc
        except (RAGConfigurationError, RAGProviderError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except LLMProviderError as exc:
            status_code = (
                429
                if exc.code == "token_budget_exceeded"
                else 400
                if exc.code
                in {"llm_model_not_allowed", "llm_provider_not_allowed"}
                else 502
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        return RAGAskResponse(
            knowledge_base_id=knowledge_base_id,
            answer=answer.answer,
            citations=[
                RAGChunkResponse.from_domain(citation)
                for citation in answer.citations
            ],
            retrieval=RetrievalExecutionResponse.from_domain(answer.retrieval),
        )

    return router


def _upload_filename(filename: str | None) -> str:
    normalized = (filename or "").replace("\\", "/").strip()
    if normalized.lower().startswith("c:/fakepath/"):
        return normalized.rsplit("/", 1)[-1]
    parts = normalized.split("/")
    if normalized.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        return parts[-1] if parts else ""
    return normalized


def _upload_media_type(filename: str, reported: str | None) -> str | None:
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    value = (reported or "").strip().lower()
    return value if value and value != "application/octet-stream" else None


def _form_tags(value: str) -> list[str]:
    return [
        item.strip()
        for item in value.replace("，", ",").split(",")
        if item.strip()
    ]
