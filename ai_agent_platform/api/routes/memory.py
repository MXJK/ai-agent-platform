from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from ai_agent_platform.core import Settings, request_user_id
from ai_agent_platform.memory import (
    UserMemoryConflictError,
    UserMemoryNotFoundError,
    UserMemoryService,
    UserMemoryValidationError,
)
from ai_agent_platform.schemas.memory import (
    ConversationMemoryHitResponse,
    ConversationMemorySearchResponse,
    UserMemoriesResponse,
    UserMemoryCreateRequest,
    UserMemoryResponse,
    UserMemorySettingsResponse,
    UserMemorySettingsUpdateRequest,
    UserMemorySceneResponse,
    UserMemoryScenesResponse,
    UserMemoryUpdateRequest,
    UserMemoryVersionRequest,
    UserProfileSnapshotResponse,
)
from ai_agent_platform.services import SessionService


def create_memory_router(
    session_service: SessionService,
    user_memory_service: UserMemoryService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/memory/conversations/search",
        response_model=ConversationMemorySearchResponse,
    )
    def search_conversations(
        request: Request,
        q: str = Query(default="", max_length=500),
        workspace_id: str | None = Query(default=None, max_length=128),
        session_id: str | None = Query(default=None, max_length=64),
        limit: int = Query(default=10, ge=1, le=50),
    ) -> ConversationMemorySearchResponse:
        hits = session_service.search_conversations(
            user_id=request_user_id(request, settings),
            query=q,
            workspace_id=workspace_id,
            session_id=session_id,
            limit=limit,
        )
        return ConversationMemorySearchResponse(
            hits=[ConversationMemoryHitResponse.from_domain(item) for item in hits]
        )

    @router.get(
        "/users/me/memory-settings",
        response_model=UserMemorySettingsResponse,
    )
    def get_settings(request: Request) -> UserMemorySettingsResponse:
        return _handle(
            lambda: UserMemorySettingsResponse.from_domain(
                user_memory_service.get_settings(
                    user_id=request_user_id(request, settings)
                )
            )
        )

    @router.patch(
        "/users/me/memory-settings",
        response_model=UserMemorySettingsResponse,
    )
    def update_settings(
        body: UserMemorySettingsUpdateRequest,
        request: Request,
    ) -> UserMemorySettingsResponse:
        return _handle(
            lambda: UserMemorySettingsResponse.from_domain(
                user_memory_service.update_settings(
                    user_id=request_user_id(request, settings),
                    mode=body.mode,
                )
            )
        )

    @router.get("/users/me/memories", response_model=UserMemoriesResponse)
    def list_memories(
        request: Request,
        memory_status: str | None = Query(default=None, alias="status"),
        kind: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> UserMemoriesResponse:
        return _handle(
            lambda: UserMemoriesResponse(
                memories=[
                    UserMemoryResponse.from_domain(item)
                    for item in user_memory_service.list(
                        user_id=request_user_id(request, settings),
                        status=memory_status,
                        kind=kind,
                        limit=limit,
                        offset=offset,
                    )
                ]
            )
        )

    @router.get("/users/me/memory-scenes", response_model=UserMemoryScenesResponse)
    def list_memory_scenes(request: Request) -> UserMemoryScenesResponse:
        return _handle(
            lambda: UserMemoryScenesResponse(
                scenes=[
                    UserMemorySceneResponse.from_domain(item)
                    for item in user_memory_service.list_scenes(
                        user_id=request_user_id(request, settings)
                    )
                ]
            )
        )

    @router.post(
        "/users/me/memories",
        response_model=UserMemoryResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_memory(
        body: UserMemoryCreateRequest,
        request: Request,
    ) -> UserMemoryResponse:
        return _handle(
            lambda: UserMemoryResponse.from_domain(
                user_memory_service.create_manual(
                    user_id=request_user_id(request, settings),
                    kind=body.kind,
                    title=body.title,
                    content=body.content,
                    importance=body.importance,
                )
            )
        )

    @router.get(
        "/users/me/memories/{memory_id}",
        response_model=UserMemoryResponse,
    )
    def get_memory(
        request: Request,
        memory_id: str = _memory_path(),
    ) -> UserMemoryResponse:
        return _handle(
            lambda: UserMemoryResponse.from_domain(
                user_memory_service.get(
                    user_id=request_user_id(request, settings), memory_id=memory_id
                )
            )
        )

    @router.patch(
        "/users/me/memories/{memory_id}",
        response_model=UserMemoryResponse,
    )
    def update_memory(
        body: UserMemoryUpdateRequest,
        request: Request,
        memory_id: str = _memory_path(),
    ) -> UserMemoryResponse:
        return _handle(
            lambda: UserMemoryResponse.from_domain(
                user_memory_service.update(
                    user_id=request_user_id(request, settings),
                    memory_id=memory_id,
                    expected_version=body.version,
                    kind=body.kind,
                    title=body.title,
                    content=body.content,
                    importance=body.importance,
                )
            )
        )

    @router.delete(
        "/users/me/memories/{memory_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def forget_memory(
        request: Request,
        memory_id: str = _memory_path(),
    ) -> None:
        return _handle(
            lambda: user_memory_service.forget(
                user_id=request_user_id(request, settings), memory_id=memory_id
            )
        )

    @router.post(
        "/users/me/memories/{memory_id}/confirm",
        response_model=UserMemoryResponse,
    )
    def confirm_memory(
        body: UserMemoryVersionRequest,
        request: Request,
        memory_id: str = _memory_path(),
    ) -> UserMemoryResponse:
        return _handle(
            lambda: UserMemoryResponse.from_domain(
                user_memory_service.confirm(
                    user_id=request_user_id(request, settings),
                    memory_id=memory_id,
                    expected_version=body.version,
                )
            )
        )

    @router.post(
        "/users/me/memories/{memory_id}/reject",
        response_model=UserMemoryResponse,
    )
    def reject_memory(
        body: UserMemoryVersionRequest,
        request: Request,
        memory_id: str = _memory_path(),
    ) -> UserMemoryResponse:
        return _handle(
            lambda: UserMemoryResponse.from_domain(
                user_memory_service.reject(
                    user_id=request_user_id(request, settings),
                    memory_id=memory_id,
                    expected_version=body.version,
                )
            )
        )

    @router.get("/users/me/profile", response_model=UserProfileSnapshotResponse)
    def get_profile(request: Request) -> UserProfileSnapshotResponse:
        return _handle(
            lambda: UserProfileSnapshotResponse.from_domain(
                user_memory_service.get_profile(
                    user_id=request_user_id(request, settings)
                )
            )
        )

    @router.post(
        "/users/me/profile/rebuild",
        response_model=UserProfileSnapshotResponse,
    )
    def rebuild_profile(request: Request) -> UserProfileSnapshotResponse:
        return _handle(
            lambda: UserProfileSnapshotResponse.from_domain(
                user_memory_service.rebuild_profile(
                    user_id=request_user_id(request, settings)
                )
            )
        )

    return router


def _handle(function):
    try:
        return function()
    except UserMemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="user memory not found") from exc
    except UserMemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UserMemoryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _memory_path():
    return Path(min_length=1, max_length=64, pattern=r"^umem_[a-f0-9]+$")


__all__ = ["create_memory_router"]
