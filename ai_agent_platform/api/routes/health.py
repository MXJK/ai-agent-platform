from fastapi import APIRouter, Response, status as http_status

from ai_agent_platform.core import MetricsRegistry
from ai_agent_platform.integrations import MCPConnectionManager
from ai_agent_platform.schemas import HealthResponse, MetricsResponse


def create_health_router(
    metrics: MetricsRegistry,
    *,
    service_name: str,
    session_storage: str = "memory",
    mcp_connection_manager: MCPConnectionManager | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    def health(response: Response) -> HealthResponse:
        mcp_servers = (
            mcp_connection_manager.diagnostics()
            if mcp_connection_manager is not None
            else []
        )
        ready = bool(
            mcp_connection_manager is None or mcp_connection_manager.ready
        )
        degraded = any(item["state"] != "ready" for item in mcp_servers)
        if not ready:
            response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="ok" if ready and not degraded else ("degraded" if ready else "not_ready"),
            service=service_name,
            session_storage=session_storage,
            persistent_sessions=session_storage in {"postgres", "sqlite"},
            ready=ready,
            mcp_servers=mcp_servers,
        )

    @router.get("/metrics", response_model=MetricsResponse)
    def get_metrics() -> MetricsResponse:
        snapshot = metrics.snapshot()
        return MetricsResponse(service=service_name, **snapshot)

    return router
