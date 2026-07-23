"""Background task execution boundary for local and test deployments."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import logging
import re
from threading import BoundedSemaphore, Lock
from time import perf_counter
from typing import Any, Callable, Protocol
from uuid import NAMESPACE_URL, uuid5

from ai_agent_platform.core.metrics import MetricsRegistry


logger = logging.getLogger(__name__)


class TaskQueueError(Exception):
    pass


class TaskQueueFullError(TaskQueueError):
    pass


class TaskQueueClosedError(TaskQueueError):
    pass


class TaskQueue(Protocol):
    def submit(
        self,
        task_name: str,
        function: Callable[..., None],
        **kwargs: Any,
    ) -> Any:
        ...

    def close(self) -> None:
        ...


class InProcessTaskQueue:
    """Bounded thread-backed queue behind a replaceable application protocol.

    Business services depend on ``TaskQueue`` rather than directly on threads,
    allowing a Redis/Celery or cloud queue adapter to be introduced later.
    """

    def __init__(
        self,
        *,
        max_workers: int = 4,
        max_queue_size: int = 100,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        if max_queue_size < 0:
            raise ValueError("max_queue_size must be non-negative")
        self._metrics = metrics or MetricsRegistry()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="background-task",
        )
        self._capacity = BoundedSemaphore(max_workers + max_queue_size)
        self._state_lock = Lock()
        self._closed = False

    def submit(
        self,
        task_name: str,
        function: Callable[..., None],
        **kwargs: Any,
    ) -> Future[None]:
        metric_name = _metric_task_name(task_name)
        with self._state_lock:
            if self._closed:
                raise TaskQueueClosedError("background task queue is closed")
            if not self._capacity.acquire(blocking=False):
                self._metrics.increment("background_tasks_rejected_total")
                self._metrics.increment(
                    f"background_task_{metric_name}_rejected_total"
                )
                raise TaskQueueFullError("background task queue capacity exceeded")
            try:
                submitted_at = perf_counter()
                future = self._executor.submit(
                    self._execute,
                    task_name,
                    metric_name,
                    submitted_at,
                    function,
                    kwargs,
                )
            except Exception:
                self._capacity.release()
                raise
        self._metrics.increment("background_tasks_submitted_total")
        self._metrics.increment(f"background_task_{metric_name}_submitted_total")
        return future

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _execute(
        self,
        task_name: str,
        metric_name: str,
        submitted_at: float,
        function: Callable[..., None],
        kwargs: dict[str, Any],
    ) -> None:
        started_at = perf_counter()
        queue_wait_ms = int((started_at - submitted_at) * 1000)
        self._metrics.increment("background_tasks_started_total")
        self._metrics.increment(f"background_task_{metric_name}_started_total")
        self._metrics.observe_ms("background_task_queue_wait_ms", queue_wait_ms)
        self._metrics.observe_ms(
            f"background_task_{metric_name}_queue_wait_ms",
            queue_wait_ms,
        )
        try:
            function(**kwargs)
        except Exception:
            self._metrics.increment("background_tasks_failed_total")
            self._metrics.increment(f"background_task_{metric_name}_failed_total")
            logger.exception(
                "background task failed",
                extra={"task_name": task_name},
            )
            raise
        else:
            self._metrics.increment("background_tasks_completed_total")
            self._metrics.increment(
                f"background_task_{metric_name}_completed_total"
            )
        finally:
            duration_ms = int((perf_counter() - started_at) * 1000)
            self._metrics.observe_ms("background_task_duration_ms", duration_ms)
            self._metrics.observe_ms(
                f"background_task_{metric_name}_duration_ms",
                duration_ms,
            )
            self._capacity.release()


def _metric_task_name(task_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", task_name.lower()).strip("_")
    return normalized or "unknown"


class CeleryTaskQueue:
    """Publishes named JSON tasks to Celery through a Redis broker."""

    TASK_NAMES = {
        "agent_run": "ai_agent_platform.agent_run",
        "agent_resume": "ai_agent_platform.agent_resume",
    }

    def __init__(
        self,
        *,
        broker_url: str,
        result_backend_url: str | None = None,
        visibility_timeout_seconds: int = 3600,
        publish_max_retries: int = 3,
        publish_retry_backoff_seconds: int = 2,
        publish_retry_backoff_max_seconds: int = 60,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        try:
            from celery import Celery
        except ImportError as exc:
            raise TaskQueueError(
                "celery redis dependencies are not installed; "
                "run pip install -r requirements.txt"
            ) from exc
        self._metrics = metrics or MetricsRegistry()
        self._app = Celery(
            "ai_agent_platform_publisher",
            broker=broker_url,
            backend=result_backend_url,
        )
        self._publish_retry_policy = {
            "max_retries": publish_max_retries,
            "interval_start": 0,
            "interval_step": publish_retry_backoff_seconds,
            "interval_max": publish_retry_backoff_max_seconds,
        }
        self._app.conf.update(
            task_serializer="json",
            accept_content=["json"],
            result_serializer="json",
            broker_connection_retry_on_startup=True,
            broker_transport_options={
                "visibility_timeout": visibility_timeout_seconds,
            },
        )
        self._closed = False
        self._lock = Lock()

    def submit(
        self,
        task_name: str,
        function: Callable[..., None],
        **kwargs: Any,
    ) -> Any:
        del function
        celery_task_name = self.TASK_NAMES.get(task_name)
        if celery_task_name is None:
            raise TaskQueueError(f"unsupported distributed task: {task_name}")
        with self._lock:
            if self._closed:
                raise TaskQueueClosedError("background task queue is closed")
        started_at = perf_counter()
        idempotency_key = _task_idempotency_key(task_name, kwargs)
        task_id = str(uuid5(NAMESPACE_URL, idempotency_key))
        try:
            result = self._app.send_task(
                celery_task_name,
                kwargs=kwargs,
                task_id=task_id,
                headers={"idempotency_key": idempotency_key},
                retry=True,
                retry_policy=self._publish_retry_policy,
            )
        except Exception as exc:
            self._metrics.increment("background_tasks_rejected_total")
            self._metrics.increment(
                f"background_task_{_metric_task_name(task_name)}_rejected_total"
            )
            raise TaskQueueError(f"failed to publish {task_name}: {exc}") from exc
        publish_duration_ms = int((perf_counter() - started_at) * 1000)
        self._metrics.increment("background_tasks_submitted_total")
        self._metrics.increment(
            f"background_task_{_metric_task_name(task_name)}_submitted_total"
        )
        self._metrics.observe_ms(
            "background_task_publish_duration_ms",
            publish_duration_ms,
        )
        return result

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._app.close()


def _task_idempotency_key(task_name: str, payload: dict[str, Any]) -> str:
    canonical_payload = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    payload_digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    return f"ai-agent-platform:{task_name}:{payload_digest}"
