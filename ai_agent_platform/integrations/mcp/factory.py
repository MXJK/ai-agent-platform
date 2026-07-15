from __future__ import annotations

from pathlib import Path

from ai_agent_platform.integrations.mcp.config import (
    MCPServerConfig,
    load_mcp_server_configs,
)
from ai_agent_platform.integrations.mcp.provider import MCPToolProvider
from ai_agent_platform.integrations.mcp.stdio_client import MCPStdioClient


def create_mcp_providers_from_configs(
    configs: list[MCPServerConfig],
    *,
    request_timeout_seconds: float = 10.0,
) -> list[MCPToolProvider]:
    providers: list[MCPToolProvider] = []
    for config in configs:
        if not config.enabled:
            continue
        if config.transport != "stdio":
            raise ValueError(f"unsupported MCP transport: {config.transport}")
        providers.append(
            MCPToolProvider(
                server_name=config.name,
                client=MCPStdioClient(
                    config,
                    request_timeout_seconds=request_timeout_seconds,
                ),
            )
        )
    return providers


def create_mcp_providers_from_config_file(
    path: Path | str,
    *,
    request_timeout_seconds: float = 10.0,
) -> list[MCPToolProvider]:
    return create_mcp_providers_from_configs(
        load_mcp_server_configs(path),
        request_timeout_seconds=request_timeout_seconds,
    )
