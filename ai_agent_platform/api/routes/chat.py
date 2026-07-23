from __future__ import annotations

import json
import logging
from queue import Empty, Queue
from threading import Thread
from time import perf_counter
from typing import Iterable, Iterator
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ai_agent_platform.core import MetricsRegistry, Settings
from ai_agent_platform.integrations import LLMClient, LLMProviderError, LLMStreamEvent
from ai_agent_platform.repositories import SessionNotFoundError
from ai_agent_platform.schemas import ChatStreamRequest
from ai_agent_platform.services import SessionService


logger = logging.getLogger(__name__)


def create_chat_router(
    session_service: SessionService,
    llm_client: LLMClient,
    settings: Settings,
    metrics: MetricsRegistry,
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
                metrics=metrics,
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
    metrics: MetricsRegistry,
):
    started_at = perf_counter()
    provider = request.provider or settings.llm_provider
    model = request.model or settings.llm_model
    thinking_level = None
    if provider == "google" and model.startswith("gemini-3"):
        thinking_level = request.thinking_level or settings.llm_thinking_level
    answer_parts: list[str] = []
    latest_input_tokens = 0
    latest_output_tokens = 0
    latest_thoughts_tokens = 0
    usage_recorded = False
    metrics.increment("chat_streams_started_total")

    logger.info(
        "chat stream started",
        extra={
            "request_id": request_id,
            "conversation_id": request.conversation_id,
            "provider": provider,
            "model": model,
            "thinking_level": thinking_level,
        },
    )
    yield sse(
        "meta",
        {
            "request_id": request_id,
            "provider": provider,
            "model": model,
            "thinking_level": thinking_level,
        },
    )

    def record_usage() -> None:
        nonlocal usage_recorded
        if usage_recorded or not (
            latest_input_tokens or latest_output_tokens or latest_thoughts_tokens
        ):
            return
        session_service.record_token_usage(
            session_id=request.conversation_id,
            provider=provider,
            model=model,
            input_tokens=latest_input_tokens,
            output_tokens=latest_output_tokens,
        )
        metrics.increment("llm_input_tokens_total", latest_input_tokens)
        metrics.increment("llm_output_tokens_total", latest_output_tokens)
        metrics.increment("llm_thoughts_tokens_total", latest_thoughts_tokens)
        usage_recorded = True

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
        llm_events = llm_client.stream_chat(
            messages,
            provider=request.provider,
            model=request.model,
            thinking_level=request.thinking_level,
        )
        for event in stream_with_heartbeat(
            llm_events,
            heartbeat_seconds=settings.sse_heartbeat_seconds,
        ):
            if event is None:
                metrics.increment("chat_stream_heartbeats_total")
                yield sse_heartbeat()
                continue
            if event.type == "delta":
                answer_parts.append(event.text)
                yield sse("delta", {"text": event.text})
            elif event.type == "usage" and event.usage is not None:
                if event.usage.input_tokens > 0:
                    latest_input_tokens = event.usage.input_tokens
                if event.usage.output_tokens > 0:
                    latest_output_tokens = event.usage.output_tokens
                if event.usage.thoughts_tokens > 0:
                    latest_thoughts_tokens = event.usage.thoughts_tokens
                yield sse(
                    "usage",
                    {
                        "input_tokens": latest_input_tokens,
                        "output_tokens": latest_output_tokens,
                        "thoughts_tokens": latest_thoughts_tokens,
                        "total_tokens": (
                            latest_input_tokens
                            + latest_output_tokens
                            + latest_thoughts_tokens
                        ),
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
        record_usage()

        elapsed_ms = int((perf_counter() - started_at) * 1000)
        metrics.increment("chat_streams_completed_total")
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
                "thoughts_tokens": latest_thoughts_tokens,
            },
        )
        yield sse(
            "done",
            {
                "request_id": request_id,
                "elapsed_ms": elapsed_ms,
                "input_tokens": latest_input_tokens,
                "output_tokens": latest_output_tokens,
                "thoughts_tokens": latest_thoughts_tokens,
                "total_tokens": (
                    latest_input_tokens
                    + latest_output_tokens
                    + latest_thoughts_tokens
                ),
            },
        )
    except LLMProviderError as exc:
        record_usage()
        metrics.increment("chat_streams_failed_total")
        metrics.increment("llm_provider_errors_total")
        logger.warning(
            "chat stream provider error",
            extra={
                "request_id": request_id,
                "conversation_id": request.conversation_id,
                "provider": provider,
                "model": model,
                "retryable": exc.retryable,
                "code": exc.code,
                "finish_reason": exc.finish_reason,
            },
        )
        yield sse(
            "error",
            {
                "request_id": request_id,
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "finish_reason": exc.finish_reason,
                "partial_response": bool(answer_parts),
                "input_tokens": latest_input_tokens,
                "output_tokens": latest_output_tokens,
                "thoughts_tokens": latest_thoughts_tokens,
                "total_tokens": (
                    latest_input_tokens
                    + latest_output_tokens
                    + latest_thoughts_tokens
                ),
            },
        )
    except Exception:
        metrics.increment("chat_streams_failed_total")
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
    finally:
        metrics.observe_ms(
            "chat_stream_duration_ms",
            int((perf_counter() - started_at) * 1000),
        )


def sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_heartbeat() -> str:
    return ": heartbeat\n\n"


def stream_with_heartbeat(
    events: Iterable[LLMStreamEvent],
    *,
    heartbeat_seconds: float,
) -> Iterator[LLMStreamEvent | None]:
    queue: Queue[tuple[str, object]] = Queue()

    def produce() -> None:
        try:
            for event in events:
                queue.put(("event", event))
        except BaseException as exc:
            queue.put(("error", exc))
        finally:
            queue.put(("done", None))

    Thread(target=produce, name="llm-stream", daemon=True).start()
    while True:
        try:
            item_type, payload = queue.get(timeout=heartbeat_seconds)
        except Empty:
            yield None
            continue
        if item_type == "event":
            assert isinstance(payload, LLMStreamEvent)
            yield payload
        elif item_type == "error":
            assert isinstance(payload, BaseException)
            raise payload
        else:
            break
