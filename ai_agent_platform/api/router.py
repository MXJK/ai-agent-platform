from __future__ import annotations

import json
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Path, status
from fastapi.responses import StreamingResponse

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations import (
    LLMClient,
    LLMProviderError,
    RAGConfigurationError,
    RAGProviderError,
    RAGService,
    RAGValidationError,
)
from ai_agent_platform.repositories import SessionNotFoundError
from ai_agent_platform.schemas import (
    AddMessageRequest,
    ChatStreamRequest,
    CreateSessionRequest,
    DocumentIngestRequest,
    DocumentIngestResponse,
    HealthResponse,
    MessageResponse,
    MessagesResponse,
    RAGAskRequest,
    RAGAskResponse,
    RAGChunkResponse,
    RAGSearchRequest,
    RAGSearchResponse,
    SessionResponse,
    SessionsResponse,
    SessionSummaryResponse,
)
from ai_agent_platform.services import SessionService


logger = logging.getLogger(__name__)


def create_api_router(
    session_service: SessionService,
    llm_client: LLMClient,
    rag_service: RAGService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="ai-agent-platform")

    @router.post(
        "/sessions",
        response_model=SessionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_session(request: CreateSessionRequest) -> SessionResponse:
        session = session_service.create_session(user_id=request.user_id)
        return SessionResponse.from_domain(session)

    @router.get("/sessions", response_model=SessionsResponse)
    def list_sessions() -> SessionsResponse:
        sessions = session_service.list_sessions()
        return SessionsResponse(
            sessions=[SessionResponse.from_domain(session) for session in sessions]
        )

    @router.get("/sessions/{session_id}", response_model=SessionResponse)
    def get_session(session_id: str) -> SessionResponse:
        try:
            session = session_service.get_session(session_id=session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        return SessionResponse.from_domain(session)

    @router.get("/sessions/{session_id}/summary", response_model=SessionSummaryResponse)
    def get_session_summary(session_id: str) -> SessionSummaryResponse:
        try:
            summary = session_service.get_session_summary(session_id=session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        return SessionSummaryResponse.from_domain(summary)

    @router.post(
        "/sessions/{session_id}/messages",
        response_model=MessagesResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def add_message(session_id: str, request: AddMessageRequest) -> MessagesResponse:
        try:
            messages = session_service.add_message(
                session_id=session_id,
                role=request.role,
                content=request.content,
                run_agent=request.run_agent,
            )
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc

        return MessagesResponse(
            messages=[MessageResponse.from_domain(message) for message in messages]
        )

    @router.get("/sessions/{session_id}/messages", response_model=MessagesResponse)
    def list_messages(session_id: str) -> MessagesResponse:
        try:
            messages = session_service.list_messages(session_id=session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc

        return MessagesResponse(
            messages=[MessageResponse.from_domain(message) for message in messages]
        )

    @router.post("/chat/stream")
    def chat_stream(request: ChatStreamRequest) -> StreamingResponse:
        if len(request.message) > settings.llm_max_input_chars:
            raise HTTPException(
                status_code=413,
                detail="message exceeds configured context limit",
            )

        try:
            session_service.get_session(session_id=request.conversation_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="conversation not found") from exc

        request_id = f"chat_{uuid4().hex[:12]}"
        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(
            _chat_stream_events(
                request=request,
                request_id=request_id,
                session_service=session_service,
                llm_client=llm_client,
                settings=settings,
            ),
            media_type="text/event-stream",
            headers=headers,
        )

    @router.post(
        "/knowledge-bases/{knowledge_base_id}/documents",
        response_model=DocumentIngestResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def ingest_document(
        request: DocumentIngestRequest,
        knowledge_base_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9_-]+$",
        ),
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
        knowledge_base_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9_-]+$",
        ),
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
        knowledge_base_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9_-]+$",
        ),
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


def _chat_stream_events(
    *,
    request: ChatStreamRequest,
    request_id: str,
    session_service: SessionService,
    llm_client: LLMClient,
    settings: Settings,
):
    started_at = perf_counter()
    provider = request.provider or settings.llm_provider
    model = request.model or settings.llm_model
    answer_parts: list[str] = []
    latest_input_tokens = 0
    latest_output_tokens = 0

    logger.info(
        "chat stream started",
        extra={
            "request_id": request_id,
            "conversation_id": request.conversation_id,
            "provider": provider,
            "model": model,
        },
    )
    yield _sse("meta", {"request_id": request_id, "provider": provider, "model": model})

    try:
        messages = session_service.build_chat_context(
            session_id=request.conversation_id,
            user_message=request.message,
            max_context_messages=settings.llm_max_context_messages,
        )
        session_service.add_message(
            session_id=request.conversation_id,
            role="user",
            content=request.message,
        )

        for event in llm_client.stream_chat(
            messages,
            provider=request.provider,
            model=request.model,
        ):
            if event.type == "delta":
                answer_parts.append(event.text)
                yield _sse("delta", {"text": event.text})
            elif event.type == "usage" and event.usage is not None:
                if event.usage.input_tokens > 0:
                    latest_input_tokens = event.usage.input_tokens
                if event.usage.output_tokens > 0:
                    latest_output_tokens = event.usage.output_tokens
                yield _sse(
                    "usage",
                    {
                        "input_tokens": latest_input_tokens,
                        "output_tokens": latest_output_tokens,
                        "total_tokens": event.usage.total_tokens,
                    },
                )
            elif event.type == "done":
                break

        answer = "".join(answer_parts)
        if answer:
            session_service.add_message(
                session_id=request.conversation_id,
                role="assistant",
                content=answer,
            )
        if latest_input_tokens or latest_output_tokens:
            session_service.record_token_usage(
                session_id=request.conversation_id,
                provider=provider,
                model=model,
                input_tokens=latest_input_tokens,
                output_tokens=latest_output_tokens,
            )

        elapsed_ms = int((perf_counter() - started_at) * 1000)
        logger.info(
            "chat stream completed",
            extra={
                "request_id": request_id,
                "conversation_id": request.conversation_id,
                "provider": provider,
                "model": model,
                "elapsed_ms": elapsed_ms,
                "input_tokens": latest_input_tokens,
                "output_tokens": latest_output_tokens,
            },
        )
        yield _sse(
            "done",
            {
                "request_id": request_id,
                "elapsed_ms": elapsed_ms,
                "input_tokens": latest_input_tokens,
                "output_tokens": latest_output_tokens,
            },
        )
    except LLMProviderError as exc:
        logger.warning(
            "chat stream provider error",
            extra={
                "request_id": request_id,
                "conversation_id": request.conversation_id,
                "provider": provider,
                "model": model,
                "retryable": exc.retryable,
            },
        )
        yield _sse(
            "error",
            {
                "request_id": request_id,
                "code": "llm_provider_error",
                "message": str(exc),
                "retryable": exc.retryable,
            },
        )
    except Exception:
        logger.exception(
            "chat stream unexpected error",
            extra={
                "request_id": request_id,
                "conversation_id": request.conversation_id,
            },
        )
        yield _sse(
            "error",
            {
                "request_id": request_id,
                "code": "internal_error",
                "message": "chat stream failed",
                "retryable": False,
            },
        )


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
