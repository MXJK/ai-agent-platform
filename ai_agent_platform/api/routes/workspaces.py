from fastapi import APIRouter, HTTPException, Path

from ai_agent_platform.schemas import (
    WorkspaceResponse,
    WorkspacesResponse,
    WorkspaceUpsertRequest,
)
from ai_agent_platform.services import (
    WorkspaceNotFoundError,
    WorkspaceService,
    WorkspaceValidationError,
)


def create_workspaces_router(workspace_service: WorkspaceService) -> APIRouter:
    router = APIRouter()

    @router.put("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def upsert_workspace(
        request: WorkspaceUpsertRequest,
        workspace_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9_-]+$",
        ),
    ) -> WorkspaceResponse:
        try:
            workspace = workspace_service.register(
                workspace_id=workspace_id,
                root_path=request.root_path,
            )
        except WorkspaceValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkspaceResponse.from_domain(workspace)

    @router.get("/workspaces", response_model=WorkspacesResponse)
    def list_workspaces() -> WorkspacesResponse:
        return WorkspacesResponse(
            workspaces=[
                WorkspaceResponse.from_domain(item)
                for item in workspace_service.list()
            ]
        )

    @router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def get_workspace(
        workspace_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9_-]+$",
        ),
    ) -> WorkspaceResponse:
        try:
            workspace = workspace_service.get(workspace_id)
        except WorkspaceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="workspace not found") from exc
        return WorkspaceResponse.from_domain(workspace)

    return router
