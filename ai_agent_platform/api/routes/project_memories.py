from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from ai_agent_platform.core import Settings, request_user_id
from ai_agent_platform.project_memory.service import (
    MemoryAccessDeniedError,
    MemoryConflictError,
    MemoryNotFoundError,
    MemoryValidationError,
    ProjectMemoryService,
)
from ai_agent_platform.schemas import (
    MemoryExtractionJobResponse,
    MemoryExtractionJobsResponse,
    MemoryReindexResponse,
    MemorySettingsResponse,
    MemorySettingsUpdateRequest,
    MemoryVersionRequest,
    ProjectMemoriesResponse,
    ProjectMemoryCreateRequest,
    ProjectMemoryResponse,
    ProjectMemoryUpdateRequest,
)
from ai_agent_platform.services import WorkspaceNotFoundError


def create_project_memories_router(
    memory_service: ProjectMemoryService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/workspaces/{workspace_id}/memory-settings",
        response_model=MemorySettingsResponse,
    )
    def get_memory_settings(
        request: Request,
        workspace_id: str = _workspace_path(),
    ) -> MemorySettingsResponse:
        return _handle(
            lambda: MemorySettingsResponse.from_domain(
                memory_service.get_settings(
                    workspace_id=workspace_id,
                    actor_user_id=request_user_id(request, settings),
                )
            )
        )

    @router.patch(
        "/workspaces/{workspace_id}/memory-settings",
        response_model=MemorySettingsResponse,
    )
    def update_memory_settings(
        body: MemorySettingsUpdateRequest,
        request: Request,
        workspace_id: str = _workspace_path(),
    ) -> MemorySettingsResponse:
        return _handle(
            lambda: MemorySettingsResponse.from_domain(
                memory_service.update_settings(
                    workspace_id=workspace_id,
                    actor_user_id=request_user_id(request, settings),
                    mode=body.mode,
                )
            )
        )

    @router.get(
        "/workspaces/{workspace_id}/memories",
        response_model=ProjectMemoriesResponse,
    )
    def list_memories(
        request: Request,
        workspace_id: str = _workspace_path(),
        memory_status: str | None = Query(default=None, alias="status"),
        kind: str | None = Query(default=None),
        include_previous_revisions: bool = Query(default=False),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> ProjectMemoriesResponse:
        return _handle(
            lambda: ProjectMemoriesResponse(
                memories=[
                    ProjectMemoryResponse.from_domain(item)
                    for item in memory_service.list_memories(
                        workspace_id=workspace_id,
                        actor_user_id=request_user_id(request, settings),
                        status=memory_status,
                        kind=kind,
                        include_previous_revisions=include_previous_revisions,
                        limit=limit,
                        offset=offset,
                    )
                ]
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/memories",
        response_model=ProjectMemoryResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_memory(
        body: ProjectMemoryCreateRequest,
        request: Request,
        workspace_id: str = _workspace_path(),
    ) -> ProjectMemoryResponse:
        return _handle(
            lambda: ProjectMemoryResponse.from_domain(
                memory_service.create_manual(
                    workspace_id=workspace_id,
                    actor_user_id=request_user_id(request, settings),
                    kind=body.kind,
                    title=body.title,
                    content=body.content,
                    importance=body.importance,
                    expires_at=body.expires_at,
                )
            )
        )

    @router.get(
        "/workspaces/{workspace_id}/memories/{memory_id}",
        response_model=ProjectMemoryResponse,
    )
    def get_memory(
        request: Request,
        workspace_id: str = _workspace_path(),
        memory_id: str = _memory_path(),
    ) -> ProjectMemoryResponse:
        return _handle(
            lambda: ProjectMemoryResponse.from_domain(
                memory_service.get_memory(
                    workspace_id=workspace_id,
                    memory_id=memory_id,
                    actor_user_id=request_user_id(request, settings),
                )
            )
        )

    @router.patch(
        "/workspaces/{workspace_id}/memories/{memory_id}",
        response_model=ProjectMemoryResponse,
    )
    def update_memory(
        body: ProjectMemoryUpdateRequest,
        request: Request,
        workspace_id: str = _workspace_path(),
        memory_id: str = _memory_path(),
    ) -> ProjectMemoryResponse:
        return _handle(
            lambda: ProjectMemoryResponse.from_domain(
                memory_service.update_memory(
                    workspace_id=workspace_id,
                    memory_id=memory_id,
                    actor_user_id=request_user_id(request, settings),
                    expected_version=body.version,
                    kind=body.kind,
                    title=body.title,
                    content=body.content,
                    importance=body.importance,
                    expires_at=body.expires_at,
                )
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/memories/{memory_id}/confirm",
        response_model=ProjectMemoryResponse,
    )
    def confirm_memory(
        body: MemoryVersionRequest,
        request: Request,
        workspace_id: str = _workspace_path(),
        memory_id: str = _memory_path(),
    ) -> ProjectMemoryResponse:
        return _handle(
            lambda: ProjectMemoryResponse.from_domain(
                memory_service.confirm(
                    workspace_id=workspace_id,
                    memory_id=memory_id,
                    actor_user_id=request_user_id(request, settings),
                    expected_version=body.version,
                )
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/memories/{memory_id}/reject",
        response_model=ProjectMemoryResponse,
    )
    def reject_memory(
        body: MemoryVersionRequest,
        request: Request,
        workspace_id: str = _workspace_path(),
        memory_id: str = _memory_path(),
    ) -> ProjectMemoryResponse:
        return _handle(
            lambda: ProjectMemoryResponse.from_domain(
                memory_service.reject(
                    workspace_id=workspace_id,
                    memory_id=memory_id,
                    actor_user_id=request_user_id(request, settings),
                    expected_version=body.version,
                )
            )
        )

    @router.delete(
        "/workspaces/{workspace_id}/memories/{memory_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def forget_memory(
        request: Request,
        workspace_id: str = _workspace_path(),
        memory_id: str = _memory_path(),
    ) -> None:
        return _handle(
            lambda: memory_service.forget(
                workspace_id=workspace_id,
                memory_id=memory_id,
                actor_user_id=request_user_id(request, settings),
            )
        )

    @router.get(
        "/workspaces/{workspace_id}/memory-jobs",
        response_model=MemoryExtractionJobsResponse,
    )
    def list_memory_jobs(
        request: Request,
        workspace_id: str = _workspace_path(),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> MemoryExtractionJobsResponse:
        return _handle(
            lambda: MemoryExtractionJobsResponse(
                jobs=[
                    MemoryExtractionJobResponse.from_domain(item)
                    for item in memory_service.list_jobs(
                        workspace_id=workspace_id,
                        actor_user_id=request_user_id(request, settings),
                        limit=limit,
                    )
                ]
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/memories/reindex",
        response_model=MemoryReindexResponse,
    )
    def reindex_memories(
        request: Request,
        workspace_id: str = _workspace_path(),
    ) -> MemoryReindexResponse:
        return _handle(
            lambda: MemoryReindexResponse(
                queued_count=memory_service.reindex(
                    workspace_id=workspace_id,
                    actor_user_id=request_user_id(request, settings),
                )
            )
        )

    return router


def _handle(function):
    try:
        return function()
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace not found") from exc
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="memory not found") from exc
    except MemoryAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except MemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MemoryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _workspace_path():
    return Path(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )


def _memory_path():
    return Path(min_length=1, max_length=64, pattern=r"^mem_[a-f0-9]+$")


__all__ = ["create_project_memories_router"]
