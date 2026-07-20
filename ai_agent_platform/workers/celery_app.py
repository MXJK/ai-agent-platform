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


settings = Settings.from_env()
celery_app = Celery("ai_agent_platform", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": settings.celery_visibility_timeout_seconds,
    },
)


@celery_app.task(name="ai_agent_platform.agent_run")
def execute_agent_run(**payload: Any) -> None:
    get_worker_services().agent_run_service.execute_run_task(**payload)


@celery_app.task(name="ai_agent_platform.agent_resume")
def execute_agent_resume(**payload: Any) -> None:
    get_worker_services().agent_run_service.execute_resume_task(**payload)


@celery_app.task(name="ai_agent_platform.repository_index")
def execute_repository_index(**payload: Any) -> None:
    get_worker_services().repository_indexing_service.execute_index_job(**payload)


@worker_process_shutdown.connect
def close_worker_runtime(**_: Any) -> None:
    close_worker_services()
