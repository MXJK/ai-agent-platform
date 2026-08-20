from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from ai_agent_platform.core import Settings, require_local_capability
from ai_agent_platform.model_registry import (
    ModelConnectionTestError,
    ModelDiscoveryError,
    ModelRegistryConflictError,
    ModelRegistryNotFoundError,
    ModelRegistryService,
    SecretStoreError,
)
from ai_agent_platform.repositories import SessionNotFoundError
from ai_agent_platform.schemas.model_registry import (
    ModelDiscoveryResponse,
    ModelConnectionTestResponse,
    ModelRegistryResponse,
    ProviderConnectionResponse,
    ProviderConnectionUpsertRequest,
    RegisteredModelResponse,
    RegisteredModelCreateRequest,
    RegisteredModelUpdateRequest,
    SessionModelPreferenceRequest,
    SessionModelPreferenceResponse,
)
from ai_agent_platform.services import SessionService


def create_model_registry_router(
    model_registry: ModelRegistryService,
    session_service: SessionService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/model-registry", response_model=ModelRegistryResponse)
    def get_registry() -> ModelRegistryResponse:
        return ModelRegistryResponse.model_validate(model_registry.registry_view())

    @router.put(
        "/model-registry/connections/{provider}",
        response_model=ProviderConnectionResponse,
    )
    def upsert_connection(
        provider: str,
        request: ProviderConnectionUpsertRequest,
        http_request: Request,
    ) -> ProviderConnectionResponse:
        _require_local_admin(http_request, settings)
        try:
            value = model_registry.upsert_connection(
                provider=provider,
                display_name=request.display_name,
                api_key=request.api_key.get_secret_value() if request.api_key else None,
                enabled=request.enabled,
            )
        except (ValueError, SecretStoreError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ProviderConnectionResponse.model_validate(value)

    @router.delete(
        "/model-registry/connections/{provider}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_connection(provider: str, request: Request) -> Response:
        _require_local_admin(request, settings)
        try:
            model_registry.delete_connection(provider)
        except ModelRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="provider connection not found") from exc
        except SecretStoreError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/model-registry/connections/{provider}/test",
        response_model=ModelConnectionTestResponse,
    )
    def test_connection(
        provider: str,
        request: Request,
    ) -> ModelConnectionTestResponse:
        _require_local_admin(request, settings)
        try:
            value = model_registry.test_provider_connection(provider)
        except ModelRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="provider connection not found") from exc
        except ModelConnectionTestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return ModelConnectionTestResponse.model_validate(value)

    @router.get(
        "/model-registry/connections/{provider}/available-models",
        response_model=ModelDiscoveryResponse,
    )
    def discover_models(provider: str, request: Request) -> ModelDiscoveryResponse:
        _require_local_admin(request, settings)
        try:
            value = model_registry.discover_models(provider)
        except ModelRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="provider connection not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ModelDiscoveryError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return ModelDiscoveryResponse.model_validate(value)

    @router.post(
        "/model-registry/models",
        response_model=RegisteredModelResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_model(
        request: RegisteredModelCreateRequest,
        http_request: Request,
    ) -> RegisteredModelResponse:
        _require_local_admin(http_request, settings)
        try:
            value = model_registry.register_model(
                **request.model_dump(exclude_none=True)
            )
        except ModelRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="provider connection not found") from exc
        except ModelRegistryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RegisteredModelResponse.model_validate(value)

    @router.put(
        "/model-registry/models/{model_id}",
        response_model=RegisteredModelResponse,
    )
    def update_model(
        model_id: str,
        request: RegisteredModelUpdateRequest,
        http_request: Request,
    ) -> RegisteredModelResponse:
        _require_local_admin(http_request, settings)
        try:
            value = model_registry.update_model(
                model_id,
                **request.model_dump(exclude_none=True),
            )
        except ModelRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="model or provider not found") from exc
        except ModelRegistryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RegisteredModelResponse.model_validate(value)

    @router.delete(
        "/model-registry/models/{model_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_model(model_id: str, request: Request) -> Response:
        _require_local_admin(request, settings)
        try:
            model_registry.delete_model(model_id)
        except ModelRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="model not found") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get(
        "/sessions/{session_id}/model-preference",
        response_model=SessionModelPreferenceResponse,
    )
    def get_session_preference(session_id: str) -> SessionModelPreferenceResponse:
        _require_session(session_service, session_id)
        return SessionModelPreferenceResponse.model_validate(
            model_registry.get_preference(session_id),
            from_attributes=True,
        )

    @router.put(
        "/sessions/{session_id}/model-preference",
        response_model=SessionModelPreferenceResponse,
    )
    def update_session_preference(
        session_id: str,
        request: SessionModelPreferenceRequest,
    ) -> SessionModelPreferenceResponse:
        session = _require_session(session_service, session_id)
        try:
            preference = model_registry.set_preference(
                session_id=session_id,
                **request.model_dump(),
            )
        except ModelRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="preferred model not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        selection = model_registry.resolve_preference(preference)
        session_service.update_session(
            session_id=session_id,
            actor_user_id=session.user_id,
            provider=(
                selection.preferred_provider
                if selection.mode == "manual"
                else None
            ),
            model=(
                selection.preferred_model
                if selection.mode == "manual"
                else None
            ),
        )
        return SessionModelPreferenceResponse.model_validate(
            preference,
            from_attributes=True,
        )

    return router


def _require_local_admin(request: Request, settings: Settings) -> None:
    require_local_capability(
        request,
        settings,
        detail="model registry writes are available only in local mode",
    )


def _require_session(session_service: SessionService, session_id: str):
    try:
        return session_service.get_session(session_id=session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
