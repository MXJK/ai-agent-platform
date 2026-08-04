from fastapi import APIRouter, HTTPException, Request, status

from ai_agent_platform.agents.coding_agent import (
    AgentRunInvalidStateError,
    AgentRunNotFoundError,
)
from ai_agent_platform.core import Settings, TaskQueueError, request_user_id
from ai_agent_platform.repositories import SessionArchivedError, SessionNotFoundError
from ai_agent_platform.schemas import (
    AgentRunEventsResponse,
    AgentRunRequest,
    AgentRunResumeRequest,
    AgentRunStatusResponse,
)
from ai_agent_platform.services import AgentRunService, WorkspaceNotFoundError


def create_agent_runs_router(
    agent_run_service: AgentRunService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/agent/runs",
        response_model=AgentRunStatusResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_agent(
        request: AgentRunRequest,
        http_request: Request,
    ) -> AgentRunStatusResponse:
        if len(request.message) > settings.llm_max_input_chars:
            raise HTTPException(
                status_code=413,
                detail="message exceeds configured context limit",
            )
        try:
            record = agent_run_service.submit_run(
                conversation_id=request.conversation_id,
                message=request.message,
                workspace_id=request.workspace_id,
                focus_files=request.focus_files,
                provider=request.provider,
                model=request.model,
                thinking_level=request.thinking_level,
                routing_policy=request.routing_policy,
                actor_user_id=(
                    request_user_id(http_request, settings)
                    if settings.auth_mode != "disabled"
                    else None
                ),
            )
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="conversation not found") from exc
        except WorkspaceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="workspace not found") from exc
        except SessionArchivedError as exc:
            raise HTTPException(
                status_code=409,
                detail="archived conversation must be restored before continuing",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except TaskQueueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        return AgentRunStatusResponse.from_domain(record)

    @router.post(
        "/agent/runs/{run_id}/resume",
        response_model=AgentRunStatusResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def resume_agent_run(
        run_id: str,
        request: AgentRunResumeRequest,
        http_request: Request,
    ) -> AgentRunStatusResponse:
        try:
            record = agent_run_service.resume_run(
                run_id=run_id,
                approved=request.approved,
                feedback=request.feedback,
                actor_user_id=(
                    request_user_id(http_request, settings)
                    if settings.auth_mode != "disabled"
                    else None
                ),
            )
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="agent run not found") from exc
        except AgentRunInvalidStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except SessionArchivedError as exc:
            raise HTTPException(
                status_code=409,
                detail="archived conversation must be restored before continuing",
            ) from exc
        except TaskQueueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return AgentRunStatusResponse.from_domain(record)

    @router.get("/agent/runs/{run_id}", response_model=AgentRunStatusResponse)
    def get_agent_run(
        run_id: str,
        request: Request,
    ) -> AgentRunStatusResponse:
        try:
            record = agent_run_service.get_run_for_actor(
                run_id,
                (
                    request_user_id(request, settings)
                    if settings.auth_mode != "disabled"
                    else None
                ),
            )
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="agent run not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return AgentRunStatusResponse.from_domain(record)

    @router.get(
        "/agent/runs/{run_id}/events",
        response_model=AgentRunEventsResponse,
    )
    def get_agent_run_events(
        run_id: str,
        request: Request,
    ) -> AgentRunEventsResponse:
        try:
            record = agent_run_service.get_run_for_actor(
                run_id,
                (
                    request_user_id(request, settings)
                    if settings.auth_mode != "disabled"
                    else None
                ),
            )
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="agent run not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return AgentRunEventsResponse.from_domain(record)

    return router
