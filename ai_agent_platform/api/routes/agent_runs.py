import time

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ai_agent_platform.agents.coding_agent import (
    AgentRunInvalidStateError,
    AgentRunNotFoundError,
)
from ai_agent_platform.agents.coding.models import (
    AgentCheckpointNotFoundError,
    AgentCheckpointRestoreError,
)
from ai_agent_platform.core import Settings, TaskQueueError, request_user_id
from ai_agent_platform.domain import QueryCommand, QueryLifecycle, QueryParams
from ai_agent_platform.repositories import SessionArchivedError, SessionNotFoundError
from ai_agent_platform.schemas import (
    AgentCheckpointRestoreRequest,
    AgentCheckpointRestoreResponse,
    AgentCheckpointResponse,
    AgentCheckpointsResponse,
    AgentRunEventsResponse,
    AgentRunEventResponse,
    AgentRunControlRequest,
    AgentRunCompactRequest,
    AgentRunRequest,
    AgentRunResumeRequest,
    AgentRunStatusResponse,
    AgentRunSummaryResponse,
    AgentRunsResponse,
    ComposerCapabilitiesResponse,
)
from ai_agent_platform.services import QueryService, WorkspaceNotFoundError


def create_agent_runs_router(
    query_service: QueryService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/agent/composer-capabilities",
        response_model=ComposerCapabilitiesResponse,
    )
    def composer_capabilities(
        conversation_id: str,
        workspace_id: str,
        http_request: Request,
    ) -> ComposerCapabilitiesResponse:
        try:
            payload = query_service.composer_capabilities(
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                actor_user_id=(
                    request_user_id(http_request, settings)
                    if settings.auth_mode != "disabled"
                    else None
                ),
            )
        except SessionNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="conversation not found"
            ) from exc
        except WorkspaceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="workspace not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ComposerCapabilitiesResponse.model_validate(payload)

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
            record = query_service.start(
                QueryParams(
                    conversation_id=request.conversation_id,
                    message=request.message,
                    workspace_id=request.workspace_id,
                    focus_files=tuple(request.focus_files),
                    provider=request.provider,
                    model=request.model,
                    thinking_level=request.thinking_level,
                    routing_policy=request.routing_policy,
                    cwd=request.cwd,
                    additional_workspace_ids=tuple(
                        request.additional_workspace_ids
                    ),
                    skill_name=request.skill_name,
                    skill_arguments=tuple(request.skill_arguments),
                    preferred_tool_name=request.preferred_tool_name,
                    actor_user_id=(
                        request_user_id(http_request, settings)
                        if settings.auth_mode != "disabled"
                        else None
                    ),
                    entrypoint="api",
                    entrypoint_metadata={
                        "transport": "http",
                        "api_version": "v1",
                        "route": "/agent/runs",
                        **(
                            {"approval_policy": request.approval_policy}
                            if request.approval_policy is not None
                            else {}
                        ),
                    },
                )
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

    @router.get("/agent/runs", response_model=AgentRunsResponse)
    def list_agent_runs(
        http_request: Request,
        limit: int = Query(default=30, ge=1, le=100),
    ) -> AgentRunsResponse:
        records = query_service.list_runs_for_actor(
            (
                request_user_id(http_request, settings)
                if settings.auth_mode != "disabled"
                else None
            ),
            limit=limit,
        )
        return AgentRunsResponse(
            runs=[AgentRunSummaryResponse.from_domain(record) for record in records]
        )

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
            record = query_service.execute(
                QueryCommand.RESUME,
                run_id=run_id,
                approved=request.approved,
                message=request.feedback or "",
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
            record = query_service.get_run_for_actor(
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
        "/agent/runs/{run_id}/checkpoints",
        response_model=AgentCheckpointsResponse,
    )
    def get_agent_run_checkpoints(
        run_id: str,
        request: Request,
        limit: int = Query(default=100, ge=1, le=200),
    ) -> AgentCheckpointsResponse:
        try:
            record, checkpoints = query_service.list_checkpoints_for_actor(
                run_id,
                (
                    request_user_id(request, settings)
                    if settings.auth_mode != "disabled"
                    else None
                ),
                limit=limit,
            )
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="agent run not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return AgentCheckpointsResponse(
            run_id=record.run_id,
            current_checkpoint_id=record.checkpoint_id,
            checkpoints=[
                AgentCheckpointResponse.from_domain(item) for item in checkpoints
            ],
        )

    @router.post(
        "/agent/runs/{run_id}/checkpoints/{checkpoint_id}/restore",
        response_model=AgentCheckpointRestoreResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def restore_agent_run_checkpoint(
        run_id: str,
        checkpoint_id: str,
        restore: AgentCheckpointRestoreRequest,
        request: Request,
    ) -> AgentCheckpointRestoreResponse:
        try:
            record, forked_session = query_service.restore_checkpoint(
                run_id=run_id,
                checkpoint_id=checkpoint_id,
                mode=restore.mode,
                message=restore.message,
                actor_user_id=(
                    request_user_id(request, settings)
                    if settings.auth_mode != "disabled"
                    else None
                ),
            )
        except (AgentRunNotFoundError, AgentCheckpointNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SessionArchivedError as exc:
            raise HTTPException(
                status_code=409,
                detail="archived conversation must be restored before rollback",
            ) from exc
        except AgentCheckpointRestoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except TaskQueueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AgentCheckpointRestoreResponse(
            mode=restore.mode,
            source_run_id=run_id,
            source_checkpoint_id=checkpoint_id,
            conversation_id=record.conversation_id,
            forked_conversation_id=(
                forked_session.id if forked_session is not None else None
            ),
            run=AgentRunStatusResponse.from_domain(record),
        )

    @router.get(
        "/sessions/{conversation_id}/agent/runs/latest",
        response_model=AgentRunStatusResponse,
    )
    def get_latest_agent_run(
        conversation_id: str,
        request: Request,
    ) -> AgentRunStatusResponse:
        try:
            record = query_service.get_latest_run_for_actor(
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
            _record, events = query_service.events_for_actor(
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
        return _events_response(query_service, run_id, events)

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
            query_service.get_run_for_actor(run_id, actor_user_id)
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
            while time.monotonic() - started_at < settings.agent_max_elapsed_seconds + 60:
                record, events = query_service.events_for_actor(
                    run_id,
                    actor_user_id,
                    after=current,
                )
                for event in events:
                    current = max(current, event.sequence)
                    yield query_service.event_encoder.encode_sse(event)
                if record.status in QueryLifecycle.STREAM_STOP_STATUSES:
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
            record = query_service.execute(
                QueryCommand(action),
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

    @router.post(
        "/agent/runs/{run_id}/compact",
        response_model=AgentRunStatusResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def compact_agent_run(
        run_id: str,
        request: AgentRunCompactRequest,
        http_request: Request,
    ) -> AgentRunStatusResponse:
        try:
            record = query_service.execute(
                QueryCommand.COMPACT,
                run_id=run_id,
                message=request.instruction,
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
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return AgentRunStatusResponse.from_domain(record)

    @router.post("/agent/runs/{run_id}/continue", response_model=AgentRunStatusResponse)
    def continue_agent_run(
        run_id: str,
        request: AgentRunControlRequest,
        http_request: Request,
    ) -> AgentRunStatusResponse:
        try:
            record = query_service.execute(
                QueryCommand.CONTINUE,
                run_id=run_id,
                message=request.message,
                answers=[answer.model_dump() for answer in request.answers],
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


def _events_response(
    query_service: QueryService,
    run_id: str,
    events,
) -> AgentRunEventsResponse:
    return AgentRunEventsResponse(
        run_id=run_id,
        events=[
            AgentRunEventResponse(
                **query_service.event_encoder.to_payload(
                    event,
                    include_run_id=False,
                )
            )
            for event in events
        ],
    )
