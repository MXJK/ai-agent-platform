from collections import defaultdict
from dataclasses import replace

from fastapi import APIRouter, HTTPException, Query, Request, status

from ai_agent_platform.core import Settings, request_user_id
from ai_agent_platform.project_memory import (
    MemoryAccessDeniedError,
    ProjectMemoryService,
)
from ai_agent_platform.repositories import (
    SessionArchivedError,
    SessionNotFoundError,
)
from ai_agent_platform.schemas import (
    AddMessageRequest,
    ContextTokenUsageResponse,
    CreateSessionRequest,
    MessageResponse,
    MessagesResponse,
    SessionResponse,
    SessionPatchRequest,
    SessionsResponse,
    SessionSummaryResponse,
    TokenUsageResponse,
    TokenBudgetStatusResponse,
    TokenUsageOperationResponse,
    TokenUsagesResponse,
    WorkspaceTokenBreakdownResponse,
    UserPreferencesPatchRequest,
    UserPreferencesResponse,
)
from ai_agent_platform.services import (
    SessionService,
    WorkspaceNotFoundError,
    WorkspaceService,
    summarize_token_usage,
)


def create_sessions_router(
    session_service: SessionService,
    settings: Settings | None = None,
    workspace_service: WorkspaceService | None = None,
    memory_service: ProjectMemoryService | None = None,
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
    def list_sessions(
        http_request: Request,
        q: str | None = Query(default=None, max_length=200),
        archived: bool = Query(default=False),
        limit: int = Query(default=30, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
    ) -> SessionsResponse:
        user_id = (
            request_user_id(http_request, settings)
            if settings.auth_mode != "disabled"
            or bool(http_request.headers.get("X-User-ID"))
            else None
        )
        try:
            sessions, next_cursor = session_service.list_sessions_page(
                user_id=user_id,
                query=q,
                archived=archived,
                limit=limit,
                cursor=cursor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return SessionsResponse(
            sessions=[SessionResponse.from_domain(session) for session in sessions],
            next_cursor=next_cursor,
        )

    @router.get("/sessions/{session_id}", response_model=SessionResponse)
    def get_session(session_id: str, http_request: Request) -> SessionResponse:
        session = _owned_session(session_id, http_request)
        return SessionResponse.from_domain(session)

    @router.patch("/sessions/{session_id}", response_model=SessionResponse)
    def update_session(
        session_id: str,
        request: SessionPatchRequest,
        http_request: Request,
    ) -> SessionResponse:
        session = _owned_session(session_id, http_request)
        configuration = request.configuration
        if configuration is not None:
            _validate_configuration(session, configuration)
        kwargs = {
            "session_id": session_id,
            "actor_user_id": session.user_id,
            "save_configuration_as_default": (
                request.save_configuration_as_default
            ),
        }
        if "title" in request.model_fields_set:
            assert request.title is not None
            normalized_title = " ".join(request.title.split()).strip()
            if not normalized_title:
                raise HTTPException(status_code=400, detail="title cannot be blank")
            kwargs["title"] = normalized_title
        if "archived" in request.model_fields_set:
            kwargs["archived"] = request.archived
        if configuration is not None:
            for field in (
                "provider",
                "model",
                "thinking_level",
                "workspace_id",
                "composer_mode",
            ):
                if field in configuration.model_fields_set:
                    kwargs[field] = getattr(configuration, field)
        try:
            updated = session_service.update_session(**kwargs)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return SessionResponse.from_domain(updated)

    @router.get(
        "/users/me/preferences",
        response_model=UserPreferencesResponse,
    )
    def get_preferences(http_request: Request) -> UserPreferencesResponse:
        preferences = session_service.get_user_preferences(
            request_user_id(http_request, settings)
        )
        return UserPreferencesResponse.from_domain(preferences)

    @router.patch(
        "/users/me/preferences",
        response_model=UserPreferencesResponse,
    )
    def update_preferences(
        request: UserPreferencesPatchRequest,
        http_request: Request,
    ) -> UserPreferencesResponse:
        user_id = request_user_id(http_request, settings)
        current = session_service.get_user_preferences(user_id)
        changes = {
            name: getattr(request, name)
            for name in request.model_fields_set
        }
        _validate_preference_changes(current, changes, user_id)
        try:
            updated = session_service.save_user_preferences(
                replace(current, **changes)
            )
        except SessionNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="last active session not found",
            ) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return UserPreferencesResponse.from_domain(updated)

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
        except SessionArchivedError as exc:
            raise HTTPException(
                status_code=409,
                detail="archived conversation must be restored before continuing",
            ) from exc
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
        operation_records = defaultdict(list)
        for record in records:
            workspace_records[record.workspace_id].append(record)
            operation_records[record.operation].append(record)
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
            operations=[
                TokenUsageOperationResponse(
                    operation=operation,
                    **summarize_token_usage(
                        operation_records[operation]
                    ).__dict__,
                )
                for operation in sorted(operation_records)
            ],
            budget=(
                TokenBudgetStatusResponse.from_domain(budget)
                if (
                    budget := session_service.get_token_budget_status(
                        session_id=session_id,
                        workspace_id=None,
                    )
                )
                is not None
                else None
            ),
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

    def _validate_configuration(session, configuration) -> None:
        effective_provider = (
            configuration.provider
            if "provider" in configuration.model_fields_set
            else session.provider
        ) or settings.llm_provider
        effective_model = (
            configuration.model
            if "model" in configuration.model_fields_set
            else session.model
        ) or settings.llm_model
        if not settings.is_model_allowed(effective_provider, effective_model):
            raise HTTPException(
                status_code=400,
                detail=(
                    "configured model is not allowlisted: "
                    f"{effective_provider}:{effective_model}"
                ),
            )
        if (
            "composer_mode" in configuration.model_fields_set
            and configuration.composer_mode is None
        ):
            raise HTTPException(
                status_code=400,
                detail="composer_mode cannot be null",
            )
        if (
            "workspace_id" in configuration.model_fields_set
            and configuration.workspace_id is not None
            and workspace_service is not None
        ):
            try:
                workspace_service.get(configuration.workspace_id)
            except WorkspaceNotFoundError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="workspace not found",
                ) from exc
            _authorize_workspace(configuration.workspace_id, session.user_id)

    def _validate_preference_changes(
        current,
        changes: dict,
        actor_user_id: str,
    ) -> None:
        effective_provider = (
            changes.get("default_provider", current.default_provider)
            or settings.llm_provider
        )
        effective_model = (
            changes.get("default_model", current.default_model)
            or settings.llm_model
        )
        if not settings.is_model_allowed(effective_provider, effective_model):
            raise HTTPException(
                status_code=400,
                detail=(
                    "configured model is not allowlisted: "
                    f"{effective_provider}:{effective_model}"
                ),
            )
        if changes.get("default_composer_mode", "present") is None:
            raise HTTPException(
                status_code=400,
                detail="default_composer_mode cannot be null",
            )
        workspace_id = changes.get(
            "default_workspace_id",
            current.default_workspace_id,
        )
        if workspace_id is not None and workspace_service is not None:
            try:
                workspace_service.get(workspace_id)
            except WorkspaceNotFoundError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="workspace not found",
                ) from exc
            _authorize_workspace(workspace_id, actor_user_id)

    def _authorize_workspace(workspace_id: str, actor_user_id: str) -> None:
        if settings.auth_mode == "disabled" or memory_service is None:
            return
        try:
            memory_service.authorize(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                required_role="viewer",
            )
        except MemoryAccessDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    return router
