from fastapi import APIRouter

from ai_agent_platform.schemas import HealthResponse


def create_health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="ai-agent-platform")

    return router
