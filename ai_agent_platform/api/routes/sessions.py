from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request, status

from ai_agent_platform.core import Settings, request_user_id
from ai_agent_platform.repositories import SessionNotFoundError
from ai_agent_platform.schemas import (
    AddMessageRequest,
    ContextTokenUsageResponse,
    CreateSessionRequest,
    MessageResponse,
    MessagesResponse,
    SessionResponse,
    SessionsResponse,
    SessionSummaryResponse,
    TokenUsageResponse,
    TokenUsagesResponse,
    WorkspaceTokenBreakdownResponse,
)
from ai_agent_platform.services import SessionService, summarize_token_usage


def create_sessions_router(
    session_service: SessionService,
    settings: Settings | None = None,
) -> APIRouter:
    router = APIRouter()
    settings = settings or Settings()

    @router.post(
        "/sessions",
        response_model=SessionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_session(
        request: CreateSessionRequest,
        http_request: Request,
    ) -> SessionResponse:
        session = session_service.create_session(
            user_id=request_user_id(
                http_request,
                settings,
                claimed_user_id=request.user_id,
            )
        )
        return SessionResponse.from_domain(session)

    @router.get("/sessions", response_model=SessionsResponse)
    def list_sessions(http_request: Request) -> SessionsResponse:
        sessions = session_service.list_sessions()
        if settings.auth_mode != "disabled":
            actor_user_id = request_user_id(http_request, settings)
            sessions = [
                session
                for session in sessions
                if session.user_id == actor_user_id
            ]
        return SessionsResponse(
            sessions=[SessionResponse.from_domain(session) for session in sessions]
        )

    @router.get("/sessions/{session_id}", response_model=SessionResponse)
    def get_session(session_id: str, http_request: Request) -> SessionResponse:
        session = _owned_session(session_id, http_request)
        return SessionResponse.from_domain(session)

    @router.get(
        "/sessions/{session_id}/summary",
        response_model=SessionSummaryResponse,
    )
    def get_session_summary(
        session_id: str,
        http_request: Request,
    ) -> SessionSummaryResponse:
        _owned_session(session_id, http_request)
        summary = session_service.get_session_summary(session_id=session_id)
        return SessionSummaryResponse.from_domain(summary)

    @router.post(
        "/sessions/{session_id}/messages",
        response_model=MessagesResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def add_message(
        session_id: str,
        request: AddMessageRequest,
        http_request: Request,
    ) -> MessagesResponse:
        _owned_session(session_id, http_request)
        try:
            messages = session_service.add_message(
                session_id=session_id,
                role=request.role,
                content=request.content,
                run_agent=request.run_agent,
            )
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        return MessagesResponse(
            messages=[MessageResponse.from_domain(message) for message in messages]
        )

    @router.get("/sessions/{session_id}/messages", response_model=MessagesResponse)
    def list_messages(
        session_id: str,
        http_request: Request,
    ) -> MessagesResponse:
        _owned_session(session_id, http_request)
        try:
            messages = session_service.list_messages(session_id=session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        return MessagesResponse(
            messages=[MessageResponse.from_domain(message) for message in messages]
        )

    @router.get(
        "/sessions/{session_id}/token-usage",
        response_model=TokenUsagesResponse,
    )
    def list_token_usage(
        session_id: str,
        http_request: Request,
    ) -> TokenUsagesResponse:
        _owned_session(session_id, http_request)
        try:
            records = session_service.list_token_usage(session_id=session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        totals = summarize_token_usage(records)
        workspace_records = defaultdict(list)
        for record in records:
            workspace_records[record.workspace_id].append(record)
        context = session_service.get_context_token_usage(
            session_id=session_id,
            max_context_messages=settings.llm_max_context_messages,
        )
        return TokenUsagesResponse(
            session_id=session_id,
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            thoughts_tokens=totals.thoughts_tokens,
            total_tokens=totals.total_tokens,
            record_count=totals.record_count,
            context=ContextTokenUsageResponse.from_domain(context),
            workspaces=[
                WorkspaceTokenBreakdownResponse(
                    workspace_id=workspace_id,
                    **summarize_token_usage(
                        workspace_records[workspace_id]
                    ).__dict__,
                )
                for workspace_id in sorted(
                    workspace_records,
                    key=lambda item: (item is None, item or ""),
                )
            ],
            records=[
                TokenUsageResponse.from_domain(record)
                for record in records
            ],
        )

    def _owned_session(session_id: str, http_request: Request):
        try:
            session = session_service.get_session(session_id=session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        if (
            settings.auth_mode != "disabled"
            and session.user_id != request_user_id(http_request, settings)
        ):
            raise HTTPException(status_code=403, detail="conversation access denied")
        return session

    return router
