"""Celery application and distributed task handlers."""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import worker_process_shutdown

from ai_agent_platform.core import Settings
from ai_agent_platform.workers.runtime import (
    close_worker_services,
    get_worker_services,
)
from ai_agent_platform.workers.reliability import (
    execute_reliable_task,
    is_broker_redelivery,
)


settings = Settings.from_env()
celery_app = Celery(
    "ai_agent_platform",
    broker=settings.redis_url,
    backend=settings.celery_result_backend_url,
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_store_errors_even_if_ignored=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=settings.celery_task_soft_time_limit_seconds,
    task_time_limit=settings.celery_task_time_limit_seconds,
    result_expires=settings.celery_result_expires_seconds,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=settings.celery_worker_max_tasks_per_child,
    broker_connection_retry_on_startup=True,
    task_publish_retry=True,
    broker_transport_options={
        "visibility_timeout": settings.celery_visibility_timeout_seconds,
    },
    result_backend_transport_options={
        "visibility_timeout": settings.celery_visibility_timeout_seconds,
    },
    visibility_timeout=settings.celery_visibility_timeout_seconds,
)


@celery_app.task(bind=True, name="ai_agent_platform.agent_run")
def execute_agent_run(task, **payload: Any) -> None:
    run_id = str(payload["run_id"])
    execute_reliable_task(
        task=task,
        task_name="agent_run",
        task_reference=run_id,
        settings=settings,
        handler=lambda: get_worker_services().agent_run_service.execute_run_task(
            **payload,
            broker_redelivered=is_broker_redelivery(task),
        ),
        failure_handler=lambda error, attempt, max_attempts: (
            get_worker_services().agent_run_service.fail_run_task(
                run_id=run_id,
                error=error,
                attempt=attempt,
                max_attempts=max_attempts,
            )
        ),
    )


@celery_app.task(bind=True, name="ai_agent_platform.agent_resume")
def execute_agent_resume(task, **payload: Any) -> None:
    run_id = str(payload["run_id"])
    execute_reliable_task(
        task=task,
        task_name="agent_resume",
        task_reference=run_id,
        settings=settings,
        handler=lambda: get_worker_services().agent_run_service.execute_resume_task(
            **payload,
            broker_redelivered=is_broker_redelivery(task),
        ),
        failure_handler=lambda error, attempt, max_attempts: (
            get_worker_services().agent_run_service.fail_run_task(
                run_id=run_id,
                error=error,
                attempt=attempt,
                max_attempts=max_attempts,
            )
        ),
    )

@worker_process_shutdown.connect
def close_worker_runtime(**_: Any) -> None:
    close_worker_services()
