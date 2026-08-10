from pathlib import Path as FileSystemPath
from collections import defaultdict
from ipaddress import ip_address

from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from ai_agent_platform.core import Settings, request_user_id
from ai_agent_platform.integrations import (
    DirectoryPicker,
    DirectoryPickerBusyError,
    DirectoryPickerError,
    DirectoryPickerUnavailableError,
)
from ai_agent_platform.project_memory import (
    MemoryAccessDeniedError,
    ProjectMemoryService,
)
from ai_agent_platform.schemas import (
    WorkspaceDirectoryBrowseResponse,
    WorkspaceDirectoryPickRequest,
    WorkspaceDirectoryPickResponse,
    WorkspaceDirectoryResponse,
    WorkspaceResponse,
    WorkspaceTokenUsageResponse,
    TokenBudgetStatusResponse,
    TokenUsageOperationResponse,
    WorkspacesResponse,
    WorkspaceUpsertRequest,
)
from ai_agent_platform.services import (
    SessionService,
    WorkspaceNotFoundError,
    WorkspaceRootConflictError,
    WorkspaceService,
    WorkspaceValidationError,
    summarize_token_usage,
)


def create_workspaces_router(
    workspace_service: WorkspaceService,
    *,
    memory_service: ProjectMemoryService | None = None,
    session_service: SessionService | None = None,
    settings: Settings | None = None,
    directory_picker: DirectoryPicker | None = None,
) -> APIRouter:
    router = APIRouter()
    settings = settings or Settings()

    @router.post(
        "/workspace-directory-picker",
        response_model=WorkspaceDirectoryPickResponse,
    )
    def pick_workspace_directory(
        request: WorkspaceDirectoryPickRequest,
        http_request: Request,
    ) -> WorkspaceDirectoryPickResponse:
        if settings.auth_mode != "disabled" or not _is_loopback_request(http_request):
            raise HTTPException(
                status_code=403,
                detail="native directory picker is only available for local mode",
            )
        if directory_picker is None:
            raise HTTPException(
                status_code=501,
                detail="native directory picker is unavailable",
            )
        initial_path = workspace_service.directory_picker_initial_path(
            request.initial_path
        )
        try:
            selected_path = directory_picker.pick_directory(
                initial_path=initial_path
            )
        except DirectoryPickerUnavailableError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except DirectoryPickerBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except DirectoryPickerError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if selected_path is None:
            return WorkspaceDirectoryPickResponse(path=None, cancelled=True)
        try:
            selected_path = workspace_service.validate_directory(selected_path)
        except WorkspaceValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkspaceDirectoryPickResponse(
            path=selected_path,
            cancelled=False,
        )

    @router.get(
        "/workspace-directories",
        response_model=WorkspaceDirectoryBrowseResponse,
    )
    def browse_workspace_directories(
        http_request: Request,
        path: str | None = Query(default=None, min_length=1, max_length=2000),
    ) -> WorkspaceDirectoryBrowseResponse:
        if settings.auth_mode != "disabled":
            request_user_id(http_request, settings)
        try:
            current_path, parent_path, directories = (
                workspace_service.browse_directories(path)
            )
        except WorkspaceValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkspaceDirectoryBrowseResponse(
            current_path=current_path,
            parent_path=parent_path,
            directories=[
                WorkspaceDirectoryResponse(
                    name=_directory_name(directory),
                    path=str(directory),
                )
                for directory in directories
            ],
        )

    @router.put("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def upsert_workspace(
        request: WorkspaceUpsertRequest,
        http_request: Request,
        workspace_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
        ),
    ) -> WorkspaceResponse:
        if settings.auth_mode != "disabled" and memory_service is not None:
            if workspace_service.get_including_removed(workspace_id) is not None:
                try:
                    memory_service.authorize(
                        workspace_id=workspace_id,
                        actor_user_id=request_user_id(http_request, settings),
                        required_role="admin",
                    )
                except MemoryAccessDeniedError as exc:
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
        try:
            workspace = workspace_service.register(
                workspace_id=workspace_id,
                root_path=request.root_path,
            )
        except WorkspaceRootConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkspaceValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if memory_service is not None:
            memory_service.ensure_workspace_admin(
                workspace_id=workspace.id,
                actor_user_id=request_user_id(http_request, settings),
            )
        return _workspace_response(
            workspace,
            actor_user_id=(
                request_user_id(http_request, settings)
                if settings.auth_mode != "disabled"
                else None
            ),
        )

    @router.delete(
        "/workspaces/{workspace_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def remove_workspace(
        http_request: Request,
        workspace_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
        ),
    ) -> None:
        try:
            workspace_service.get(workspace_id)
        except WorkspaceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="workspace not found") from exc
        if settings.auth_mode != "disabled" and memory_service is not None:
            try:
                memory_service.authorize(
                    workspace_id=workspace_id,
                    actor_user_id=request_user_id(http_request, settings),
                    required_role="admin",
                )
            except MemoryAccessDeniedError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
        try:
            workspace_service.remove(workspace_id)
        except WorkspaceNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="workspace not found",
            ) from exc

    @router.get("/workspaces", response_model=WorkspacesResponse)
    def list_workspaces(http_request: Request) -> WorkspacesResponse:
        workspaces = workspace_service.list()
        actor_user_id: str | None = None
        if settings.auth_mode != "disabled" and memory_service is not None:
            actor_user_id = request_user_id(http_request, settings)
            visible = []
            for workspace in workspaces:
                try:
                    memory_service.authorize(
                        workspace_id=workspace.id,
                        actor_user_id=actor_user_id,
                    )
                except MemoryAccessDeniedError:
                    continue
                visible.append(workspace)
            workspaces = visible
        return WorkspacesResponse(
            workspaces=[
                _workspace_response(item, actor_user_id=actor_user_id)
                for item in workspaces
            ]
        )

    @router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def get_workspace(
        http_request: Request,
        workspace_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
        ),
    ) -> WorkspaceResponse:
        try:
            workspace = workspace_service.get(workspace_id)
        except WorkspaceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="workspace not found") from exc
        if settings.auth_mode != "disabled" and memory_service is not None:
            try:
                memory_service.authorize(
                    workspace_id=workspace_id,
                    actor_user_id=request_user_id(http_request, settings),
                )
            except MemoryAccessDeniedError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
        return _workspace_response(
            workspace,
            actor_user_id=(
                request_user_id(http_request, settings)
                if settings.auth_mode != "disabled"
                else None
            ),
        )

    @router.get(
        "/workspaces/{workspace_id}/token-usage",
        response_model=WorkspaceTokenUsageResponse,
    )
    def get_workspace_token_usage(
        http_request: Request,
        workspace_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
        ),
    ) -> WorkspaceTokenUsageResponse:
        try:
            workspace_service.get(workspace_id)
        except WorkspaceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="workspace not found") from exc
        if settings.auth_mode != "disabled" and memory_service is not None:
            try:
                memory_service.authorize(
                    workspace_id=workspace_id,
                    actor_user_id=request_user_id(http_request, settings),
                )
            except MemoryAccessDeniedError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
        records = (
            session_service.list_workspace_token_usage(workspace_id)
            if session_service is not None
            else []
        )
        totals = summarize_token_usage(records)
        operation_records = defaultdict(list)
        for record in records:
            operation_records[record.operation].append(record)
        budget = (
            session_service.get_token_budget_status(
                session_id=None,
                workspace_id=workspace_id,
            )
            if session_service is not None
            else None
        )
        return WorkspaceTokenUsageResponse(
            workspace_id=workspace_id,
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            thoughts_tokens=totals.thoughts_tokens,
            total_tokens=totals.total_tokens,
            record_count=totals.record_count,
            conversation_count=len(
                {
                    record.session_id
                    for record in records
                    if record.session_id is not None
                }
            ),
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
                if budget is not None
                else None
            ),
        )

    def _workspace_response(
        workspace,
        *,
        actor_user_id: str | None,
    ) -> WorkspaceResponse:
        role = "admin"
        if memory_service is not None and actor_user_id is not None:
            role = memory_service.role_for(
                workspace_id=workspace.id,
                actor_user_id=actor_user_id,
            )
        status_value = workspace_service.status(workspace.id)
        return WorkspaceResponse.from_domain(
            workspace,
            status=status_value,
            role=role,
            can_update=role == "admin",
            available=status_value == "ready",
        )

    return router


def _directory_name(directory: FileSystemPath) -> str:
    return directory.name or str(directory)


def _is_loopback_request(request: Request) -> bool:
    if request.client is None:
        return False
    try:
        return ip_address(request.client.host).is_loopback
    except ValueError:
        return request.client.host == "localhost"
