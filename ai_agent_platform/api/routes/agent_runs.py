import json
import time

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ai_agent_platform.agents.coding_agent import (
    AgentRunInvalidStateError,
    AgentRunNotFoundError,
)
from ai_agent_platform.core import Settings, TaskQueueError, request_user_id
from ai_agent_platform.repositories import SessionArchivedError, SessionNotFoundError
from ai_agent_platform.schemas import (
    AgentRunEventsResponse,
    AgentRunControlRequest,
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
                cwd=request.cwd,
                additional_workspace_ids=request.additional_workspace_ids,
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
        "/sessions/{conversation_id}/agent/runs/latest",
        response_model=AgentRunStatusResponse,
    )
    def get_latest_agent_run(
        conversation_id: str,
        request: Request,
    ) -> AgentRunStatusResponse:
        try:
            record = agent_run_service.get_latest_run_for_actor(
                conversation_id,
                (
                    request_user_id(request, settings)
                    if settings.auth_mode != "disabled"
                    else None
                ),
            )
        except (AgentRunNotFoundError, SessionNotFoundError) as exc:
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
        after: int = 0,
    ) -> AgentRunEventsResponse:
        try:
            record, events = agent_run_service.list_events_for_actor(
                run_id,
                (
                    request_user_id(request, settings)
                    if settings.auth_mode != "disabled"
                    else None
                ),
                after=max(0, after),
            )
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="agent run not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if events or after > 0:
            return AgentRunEventsResponse.from_events(run_id, events)
        return AgentRunEventsResponse.from_domain(record)

    @router.get("/agent/runs/{run_id}/events/stream")
    def stream_agent_run_events(
        run_id: str,
        request: Request,
        cursor: int = 0,
    ) -> StreamingResponse:
        actor_user_id = (
            request_user_id(request, settings)
            if settings.auth_mode != "disabled"
            else None
        )
        try:
            agent_run_service.get_run_for_actor(run_id, actor_user_id)
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="agent run not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        last_event_id = request.headers.get("last-event-id")
        if last_event_id and last_event_id.isdigit():
            cursor = max(cursor, int(last_event_id))

        def event_stream():
            current = max(0, cursor)
            started_at = time.monotonic()
            last_heartbeat = started_at
            stopped_statuses = {
                "waiting_approval",
                "waiting_input",
                "paused",
                "completed",
                "partial",
                "blocked",
                "cancelled",
                "failed",
            }
            while time.monotonic() - started_at < settings.agent_max_elapsed_seconds + 60:
                record, events = agent_run_service.list_events_for_actor(
                    run_id,
                    actor_user_id,
                    after=current,
                )
                for event in events:
                    current = max(current, event.sequence)
                    payload = {
                        "sequence": event.sequence,
                        "type": event.type,
                        "status": event.status,
                        "node": event.node,
                        "summary": event.summary,
                        "output": event.output,
                    }
                    yield (
                        f"id: {event.sequence}\n"
                        f"event: {event.type}\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
                if record.status in stopped_statuses:
                    break
                if time.monotonic() - last_heartbeat >= 15:
                    yield ": keep-alive\n\n"
                    last_heartbeat = time.monotonic()
                time.sleep(0.25)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    def control_agent_run(
        run_id: str,
        request: AgentRunControlRequest,
        http_request: Request,
        *,
        action: str,
    ) -> AgentRunStatusResponse:
        try:
            record = agent_run_service.control_run(
                run_id=run_id,
                action=action,
                message=request.message,
                actor_user_id=(
                    request_user_id(http_request, settings)
                    if settings.auth_mode != "disabled"
                    else None
                ),
            )
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="agent run not found") from exc
        except AgentRunInvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return AgentRunStatusResponse.from_domain(record)

    @router.post("/agent/runs/{run_id}/pause", response_model=AgentRunStatusResponse)
    def pause_agent_run(
        run_id: str,
        request: AgentRunControlRequest,
        http_request: Request,
    ) -> AgentRunStatusResponse:
        return control_agent_run(run_id, request, http_request, action="pause")

    @router.post("/agent/runs/{run_id}/cancel", response_model=AgentRunStatusResponse)
    def cancel_agent_run(
        run_id: str,
        request: AgentRunControlRequest,
        http_request: Request,
    ) -> AgentRunStatusResponse:
        return control_agent_run(run_id, request, http_request, action="cancel")

    @router.post("/agent/runs/{run_id}/steer", response_model=AgentRunStatusResponse)
    def steer_agent_run(
        run_id: str,
        request: AgentRunControlRequest,
        http_request: Request,
    ) -> AgentRunStatusResponse:
        return control_agent_run(run_id, request, http_request, action="steer")

    @router.post("/agent/runs/{run_id}/continue", response_model=AgentRunStatusResponse)
    def continue_agent_run(
        run_id: str,
        request: AgentRunControlRequest,
        http_request: Request,
    ) -> AgentRunStatusResponse:
        try:
            record = agent_run_service.continue_run(
                run_id=run_id,
                message=request.message,
                actor_user_id=(
                    request_user_id(http_request, settings)
                    if settings.auth_mode != "disabled"
                    else None
                ),
            )
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="agent run not found") from exc
        except AgentRunInvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TaskQueueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return AgentRunStatusResponse.from_domain(record)

    return router
