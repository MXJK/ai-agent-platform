from __future__ import annotations

import json
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations import LLMClient, LLMProviderError
from ai_agent_platform.repositories import SessionNotFoundError
from ai_agent_platform.schemas import ChatStreamRequest
from ai_agent_platform.services import SessionService


logger = logging.getLogger(__name__)


def create_chat_router(
    session_service: SessionService,
    llm_client: LLMClient,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

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
        return StreamingResponse(
            chat_stream_events(
                request=request,
                request_id=request_id,
                session_service=session_service,
                llm_client=llm_client,
                settings=settings,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def chat_stream_events(
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
    yield sse("meta", {"request_id": request_id, "provider": provider, "model": model})

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
                yield sse("delta", {"text": event.text})
            elif event.type == "usage" and event.usage is not None:
                if event.usage.input_tokens > 0:
                    latest_input_tokens = event.usage.input_tokens
                if event.usage.output_tokens > 0:
                    latest_output_tokens = event.usage.output_tokens
                yield sse(
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
        yield sse(
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
        yield sse(
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
        yield sse(
            "error",
            {
                "request_id": request_id,
                "code": "internal_error",
                "message": "chat stream failed",
                "retryable": False,
            },
        )


def sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
