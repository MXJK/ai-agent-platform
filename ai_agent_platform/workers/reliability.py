"""Retry, timeout, and redelivery policies shared by Celery tasks."""

from __future__ import annotations

import logging
import random
from typing import Any, Callable

import httpx
from billiard.exceptions import SoftTimeLimitExceeded
from psycopg import OperationalError as PostgresOperationalError
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
    TimeoutError as RedisTimeoutError,
)

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations import RAGProviderError
from ai_agent_platform.integrations.llm import LLMProviderError


logger = logging.getLogger(__name__)


def execute_reliable_task(
    *,
    task: Any,
    task_name: str,
    task_reference: str,
    settings: Settings,
    handler: Callable[[], None],
    failure_handler: Callable[[str, int, int], None],
) -> None:
    """Run one task with bounded retries and best-effort failure persistence."""

    try:
        handler()
    except Exception as exc:
        retries = int(getattr(task.request, "retries", 0))
        attempt = retries + 1
        max_attempts = settings.celery_task_max_retries + 1
        if _is_retryable_error(exc) and retries < settings.celery_task_max_retries:
            countdown = retry_delay_seconds(
                retry_number=retries,
                base_seconds=settings.celery_task_retry_backoff_seconds,
                max_seconds=settings.celery_task_retry_backoff_max_seconds,
            )
            logger.warning(
                "distributed task scheduled for retry",
                extra={
                    "task_name": task_name,
                    "task_reference": task_reference,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "countdown_seconds": countdown,
                    "error": str(exc),
                },
            )
            raise task.retry(
                exc=exc,
                countdown=countdown,
                max_retries=settings.celery_task_max_retries,
            )

        error = _terminal_error_message(
            exc,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        try:
            failure_handler(error, attempt, max_attempts)
        except Exception:
            logger.exception(
                "distributed task failure state could not be persisted",
                extra={
                    "task_name": task_name,
                    "task_reference": task_reference,
                },
            )
        raise


def is_broker_redelivery(task: Any) -> bool:
    request = task.request
    delivery_info = getattr(request, "delivery_info", None) or {}
    return bool(delivery_info.get("redelivered"))


def is_retry_or_redelivery(task: Any) -> bool:
    request = task.request
    return is_broker_redelivery(task) or int(
        getattr(request, "retries", 0)
    ) > 0


def retry_delay_seconds(
    *,
    retry_number: int,
    base_seconds: int,
    max_seconds: int,
) -> int:
    """Return capped exponential backoff with full jitter."""

    capped_delay = min(base_seconds * (2**retry_number), max_seconds)
    return random.randint(0, capped_delay)


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, SoftTimeLimitExceeded):
        return False
    if isinstance(exc, LLMProviderError):
        return exc.retryable
    return isinstance(
        exc,
        (
            ConnectionError,
            TimeoutError,
            httpx.TransportError,
            PostgresOperationalError,
            RAGProviderError,
            RedisConnectionError,
            RedisTimeoutError,
        ),
    )


def _terminal_error_message(
    exc: Exception,
    *,
    attempt: int,
    max_attempts: int,
) -> str:
    if isinstance(exc, SoftTimeLimitExceeded):
        return f"task exceeded its soft time limit on attempt {attempt}"
    return f"task failed on attempt {attempt}/{max_attempts}: {exc}"
