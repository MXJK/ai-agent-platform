from fastapi import APIRouter, HTTPException, Request, status

from ai_agent_platform.core import Settings, request_user_id
from ai_agent_platform.schemas.change_set import (
    ChangeSetApplyRequest,
    ChangeSetRejectRequest,
    ChangeSetRevertRequest,
    ChangeSetResponse,
)
from ai_agent_platform.services import (
    ChangeSetConflictError,
    ChangeSetInvalidStateError,
    ChangeSetNotFoundError,
    ChangeSetPermissionError,
    ChangeSetService,
    ChangeSetValidationError,
)


def create_change_sets_router(
    service: ChangeSetService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/agent/runs/{run_id}/changes",
        response_model=ChangeSetResponse,
    )
    def get_run_change_set(run_id: str, request: Request) -> ChangeSetResponse:
        try:
            record = service.get_for_run(
                run_id,
                actor_user_id=_actor(request, settings),
            )
        except ChangeSetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="change set not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return ChangeSetResponse.from_domain(record)

    @router.post(
        "/agent/runs/{run_id}/changes/apply",
        response_model=ChangeSetResponse,
        status_code=status.HTTP_200_OK,
    )
    def apply_run_change_set(
        run_id: str,
        body: ChangeSetApplyRequest,
        request: Request,
    ) -> ChangeSetResponse:
        try:
            current = service.get_for_run(
                run_id,
                actor_user_id=_actor(request, settings),
            )
            if current.id != body.change_set_id:
                raise ChangeSetConflictError("change set does not belong to this run")
            record = service.apply(
                body.change_set_id,
                expected_patch_sha256=body.patch_sha256,
                actor_user_id=_actor(request, settings),
            )
        except ChangeSetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="change set not found") from exc
        except ChangeSetConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ChangeSetInvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ChangeSetPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ChangeSetValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return ChangeSetResponse.from_domain(record)

    @router.post(
        "/agent/runs/{run_id}/changes/reject",
        response_model=ChangeSetResponse,
        status_code=status.HTTP_200_OK,
    )
    def reject_run_change_set(
        run_id: str,
        body: ChangeSetRejectRequest,
        request: Request,
    ) -> ChangeSetResponse:
        try:
            current = service.get_for_run(
                run_id,
                actor_user_id=_actor(request, settings),
            )
            if current.id != body.change_set_id:
                raise ChangeSetConflictError("change set does not belong to this run")
            record = service.reject(
                body.change_set_id,
                actor_user_id=_actor(request, settings),
            )
        except ChangeSetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="change set not found") from exc
        except (ChangeSetConflictError, ChangeSetInvalidStateError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return ChangeSetResponse.from_domain(record)

    @router.post(
        "/agent/runs/{run_id}/changes/revert",
        response_model=ChangeSetResponse,
        status_code=status.HTTP_200_OK,
    )
    def revert_run_change_set(
        run_id: str,
        body: ChangeSetRevertRequest,
        request: Request,
    ) -> ChangeSetResponse:
        try:
            current = service.get_for_run(
                run_id,
                actor_user_id=_actor(request, settings),
            )
            if current.id != body.change_set_id:
                raise ChangeSetConflictError("change set does not belong to this run")
            record = service.revert(
                body.change_set_id,
                expected_patch_sha256=body.patch_sha256,
                actor_user_id=_actor(request, settings),
            )
        except ChangeSetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="change set not found") from exc
        except (ChangeSetConflictError, ChangeSetInvalidStateError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ChangeSetPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ChangeSetValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return ChangeSetResponse.from_domain(record)

    return router


def _actor(request: Request, settings: Settings) -> str | None:
    return (
        request_user_id(request, settings)
        if settings.auth_mode != "disabled"
        else None
    )
