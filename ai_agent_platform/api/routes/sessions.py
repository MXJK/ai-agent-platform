from fastapi import APIRouter, HTTPException, status

from ai_agent_platform.repositories import SessionNotFoundError
from ai_agent_platform.schemas import (
    AddMessageRequest,
    CreateSessionRequest,
    MessageResponse,
    MessagesResponse,
    SessionResponse,
    SessionsResponse,
    SessionSummaryResponse,
)
from ai_agent_platform.services import SessionService


def create_sessions_router(session_service: SessionService) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/sessions",
        response_model=SessionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_session(request: CreateSessionRequest) -> SessionResponse:
        session = session_service.create_session(user_id=request.user_id)
        return SessionResponse.from_domain(session)

    @router.get("/sessions", response_model=SessionsResponse)
    def list_sessions() -> SessionsResponse:
        sessions = session_service.list_sessions()
        return SessionsResponse(
            sessions=[SessionResponse.from_domain(session) for session in sessions]
        )

    @router.get("/sessions/{session_id}", response_model=SessionResponse)
    def get_session(session_id: str) -> SessionResponse:
        try:
            session = session_service.get_session(session_id=session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        return SessionResponse.from_domain(session)

    @router.get(
        "/sessions/{session_id}/summary",
        response_model=SessionSummaryResponse,
    )
    def get_session_summary(session_id: str) -> SessionSummaryResponse:
        try:
            summary = session_service.get_session_summary(session_id=session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        return SessionSummaryResponse.from_domain(summary)

    @router.post(
        "/sessions/{session_id}/messages",
        response_model=MessagesResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def add_message(session_id: str, request: AddMessageRequest) -> MessagesResponse:
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
    def list_messages(session_id: str) -> MessagesResponse:
        try:
            messages = session_service.list_messages(session_id=session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        return MessagesResponse(
            messages=[MessageResponse.from_domain(message) for message in messages]
        )

    return router
