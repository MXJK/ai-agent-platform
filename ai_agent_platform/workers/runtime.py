"""Build process-local service instances used by Celery workers."""

from __future__ import annotations

from threading import Lock

from ai_agent_platform.core import ConfigResolver, ResolvedConfig, Settings
from ai_agent_platform.runtime import (
    ApplicationFactory,
    RuntimeContainer,
    build_runtime,
)


# Preserve the public worker-facing name while using the shared container.
WorkerServices = RuntimeContainer

_services: RuntimeContainer | None = None
_services_lock = Lock()


def get_worker_services() -> RuntimeContainer:
    """Return the single runtime container owned by this worker process."""

    global _services
    if _services is None:
        with _services_lock:
            if _services is None:
                _services = _create_worker_services()
    return _services


def close_worker_services() -> None:
    """Detach and idempotently close the process-local worker runtime."""

    global _services
    with _services_lock:
        services = _services
        _services = None
    if services is not None:
        services.close()


def _create_worker_services(
    *,
    settings: Settings | ResolvedConfig | None = None,
    application_factory: ApplicationFactory | None = None,
) -> RuntimeContainer:
    config = settings or ConfigResolver.from_default_locations().resolve()
    return build_runtime(
        config,
        role="worker",
        factory=application_factory,
    )


__all__ = [
    "WorkerServices",
    "close_worker_services",
    "get_worker_services",
]
