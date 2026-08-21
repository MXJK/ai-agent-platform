from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from ai_agent_platform.core import Settings, require_local_capability
from ai_agent_platform.schemas.skills import (
    SkillEnabledRequest,
    SkillRegistryResponse,
    SkillResponse,
    SkillUpsertRequest,
)
from ai_agent_platform.skills.management import (
    SkillRegistryError,
    SkillRegistryNotFoundError,
    SkillRegistryService,
)


def create_skill_registry_router(
    registry: SkillRegistryService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/skills", response_model=SkillRegistryResponse)
    def get_registry() -> SkillRegistryResponse:
        return SkillRegistryResponse.model_validate(registry.registry_view())

    @router.put("/skills/{skill_name}", response_model=SkillResponse)
    def upsert_skill(
        skill_name: str,
        payload: SkillUpsertRequest,
        request: Request,
    ) -> SkillResponse:
        _require_local_admin(request, settings)
        try:
            result = registry.upsert(
                skill_name,
                content=payload.content,
                enabled=payload.enabled,
            )
        except (ValueError, SkillRegistryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to persist Skill") from exc
        return SkillResponse.model_validate(result)

    @router.patch("/skills/{skill_name}/enabled", response_model=SkillResponse)
    def set_enabled(
        skill_name: str,
        payload: SkillEnabledRequest,
        request: Request,
    ) -> SkillResponse:
        _require_local_admin(request, settings)
        try:
            result = registry.set_enabled(skill_name, enabled=payload.enabled)
        except SkillRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, SkillRegistryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return SkillResponse.model_validate(result)

    @router.delete("/skills/{skill_name}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_skill(skill_name: str, request: Request) -> Response:
        _require_local_admin(request, settings)
        try:
            registry.delete(skill_name)
        except SkillRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, SkillRegistryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def _require_local_admin(request: Request, settings: Settings) -> None:
    require_local_capability(
        request,
        settings,
        detail="Skill registry writes are available only in local mode",
    )
