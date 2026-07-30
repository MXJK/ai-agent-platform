from pathlib import Path as FileSystemPath

from fastapi import APIRouter, HTTPException, Path, Query, Request

from ai_agent_platform.core import Settings, request_user_id
from ai_agent_platform.project_memory import (
    MemoryAccessDeniedError,
    ProjectMemoryService,
)
from ai_agent_platform.schemas import (
    WorkspaceDirectoryBrowseResponse,
    WorkspaceDirectoryResponse,
    WorkspaceResponse,
    WorkspacesResponse,
    WorkspaceUpsertRequest,
)
from ai_agent_platform.services import (
    WorkspaceNotFoundError,
    WorkspaceService,
    WorkspaceValidationError,
)


def create_workspaces_router(
    workspace_service: WorkspaceService,
    *,
    memory_service: ProjectMemoryService | None = None,
    settings: Settings | None = None,
) -> APIRouter:
    router = APIRouter()
    settings = settings or Settings()

    @router.get(
        "/workspace-directories",
        response_model=WorkspaceDirectoryBrowseResponse,
    )
    def browse_workspace_directories(
        path: str | None = Query(default=None, min_length=1, max_length=2000),
    ) -> WorkspaceDirectoryBrowseResponse:
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
            try:
                workspace_service.get(workspace_id)
            except WorkspaceNotFoundError:
                pass
            else:
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
        except WorkspaceValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if memory_service is not None:
            memory_service.ensure_workspace_admin(
                workspace_id=workspace.id,
                actor_user_id=request_user_id(http_request, settings),
            )
        return WorkspaceResponse.from_domain(workspace)

    @router.get("/workspaces", response_model=WorkspacesResponse)
    def list_workspaces(http_request: Request) -> WorkspacesResponse:
        workspaces = workspace_service.list()
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
                WorkspaceResponse.from_domain(item)
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
        return WorkspaceResponse.from_domain(workspace)

    return router


def _directory_name(directory: FileSystemPath) -> str:
    return directory.name or str(directory)
