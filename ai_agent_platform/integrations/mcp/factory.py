from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ai_agent_platform.integrations.mcp.config import (
    MCPServerConfig,
    load_mcp_server_configs,
)
from ai_agent_platform.integrations.mcp.manager import (
    MCPConnectionManager,
    ManagedMCPClient,
    MCPProviderCollection,
)
from ai_agent_platform.integrations.mcp.provider import MCPToolProvider
from ai_agent_platform.integrations.permissions import PermissionResolver
from ai_agent_platform.model_registry.secrets import (
    InMemorySecretStore,
    SecretStore,
)


def create_mcp_connection_manager_from_configs(
    configs: list[MCPServerConfig],
    *,
    secret_store: SecretStore | None = None,
    permission_resolver: PermissionResolver | None = None,
    request_timeout_seconds: float | None = None,
) -> MCPConnectionManager:
    resolved_configs = [
        replace(
            config,
            request_timeout_seconds=request_timeout_seconds,
        )
        if request_timeout_seconds is not None
        else config
        for config in configs
    ]
    return MCPConnectionManager(
        resolved_configs,
        secret_store=secret_store or InMemorySecretStore(),
        permission_resolver=permission_resolver or PermissionResolver(),
    )


def create_mcp_providers_from_configs(
    configs: list[MCPServerConfig],
    *,
    request_timeout_seconds: float | None = None,
    secret_store: SecretStore | None = None,
    permission_resolver: PermissionResolver | None = None,
) -> list[MCPToolProvider]:
    manager = create_mcp_connection_manager_from_configs(
        configs,
        secret_store=secret_store,
        permission_resolver=permission_resolver,
        request_timeout_seconds=request_timeout_seconds,
    )
    manager.start()
    providers = [
        MCPToolProvider(
            server_name=name,
            client=ManagedMCPClient(manager, name),
        )
        for name in manager.available_server_names()
    ]
    return MCPProviderCollection(providers, manager)


def create_mcp_providers_from_config_file(
    path: Path | str,
    *,
    request_timeout_seconds: float | None = None,
    secret_store: SecretStore | None = None,
    permission_resolver: PermissionResolver | None = None,
) -> list[MCPToolProvider]:
    config_path = Path(path)
    return create_mcp_providers_from_configs(
        (
            load_mcp_server_configs(
                config_path,
                default_request_timeout_seconds=request_timeout_seconds,
            )
            if config_path.exists()
            else []
        ),
        secret_store=secret_store,
        permission_resolver=permission_resolver,
    )
