from fastapi import APIRouter

from ai_agent_platform.core import MetricsRegistry
from ai_agent_platform.schemas import HealthResponse, MetricsResponse


def create_health_router(
    metrics: MetricsRegistry,
    *,
    service_name: str,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service=service_name)

    @router.get("/metrics", response_model=MetricsResponse)
    def get_metrics() -> MetricsResponse:
        snapshot = metrics.snapshot()
        return MetricsResponse(service=service_name, **snapshot)

    return router
