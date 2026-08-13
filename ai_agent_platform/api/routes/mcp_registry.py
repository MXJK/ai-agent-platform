from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.mcp import (
    MCPClientError,
    MCPStdioClientError,
    MCPTransportError,
)
from ai_agent_platform.integrations.mcp.registry import (
    MCPRegistryError,
    MCPRegistryNotFoundError,
    MCPRegistryService,
    MCPRegistryUnavailableError,
)
from ai_agent_platform.model_registry import SecretStoreError
from ai_agent_platform.schemas.mcp import (
    MCPRegistryResponse,
    MCPServerEnabledRequest,
    MCPServerResponse,
    MCPServerUpsertRequest,
)


def create_mcp_registry_router(
    registry: MCPRegistryService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/mcp/servers", response_model=MCPRegistryResponse)
    def get_registry() -> MCPRegistryResponse:
        return MCPRegistryResponse.model_validate(registry.registry_view())

    @router.put("/mcp/servers/{server_name}", response_model=MCPServerResponse)
    def upsert_server(
        server_name: str,
        request: MCPServerUpsertRequest,
    ) -> MCPServerResponse:
        _require_local_admin(settings)
        values = request.model_dump(exclude={"env_secrets", "header_secrets"})
        values["env_secret_values"] = {
            key: secret.get_secret_value()
            for key, secret in request.env_secrets.items()
        }
        values["header_secret_values"] = {
            key: secret.get_secret_value()
            for key, secret in request.header_secrets.items()
        }
        try:
            result = registry.upsert_server(name=server_name, **values)
        except MCPRegistryUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, MCPRegistryError, SecretStoreError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="failed to persist MCP server configuration",
            ) from exc
        return MCPServerResponse.model_validate(result)

    @router.patch(
        "/mcp/servers/{server_name}/enabled",
        response_model=MCPServerResponse,
    )
    def set_enabled(
        server_name: str,
        request: MCPServerEnabledRequest,
    ) -> MCPServerResponse:
        _require_local_admin(settings)
        try:
            result = registry.set_enabled(server_name, enabled=request.enabled)
        except MCPRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except MCPRegistryUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, MCPRegistryError, SecretStoreError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return MCPServerResponse.model_validate(result)

    @router.post(
        "/mcp/servers/{server_name}/test",
        response_model=MCPServerResponse,
    )
    def test_server(server_name: str) -> MCPServerResponse:
        _require_local_admin(settings)
        try:
            result = registry.refresh_server(server_name)
        except MCPRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except MCPRegistryUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (MCPClientError, MCPStdioClientError, MCPTransportError) as exc:
            error_code = getattr(exc, "code", "mcp_connection_failed")
            raise HTTPException(
                status_code=502,
                detail=f"MCP connection failed ({error_code})",
            ) from exc
        return MCPServerResponse.model_validate(result)

    @router.delete(
        "/mcp/servers/{server_name}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_server(server_name: str) -> Response:
        _require_local_admin(settings)
        try:
            registry.delete_server(server_name)
        except MCPRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (MCPRegistryError, SecretStoreError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def _require_local_admin(settings: Settings) -> None:
    if settings.auth_mode != "disabled":
        raise HTTPException(
            status_code=403,
            detail="MCP registry writes are available only in loopback local mode",
        )
