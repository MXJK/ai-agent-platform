from __future__ import annotations

import re
from typing import Any

from ai_agent_platform.integrations.mcp.client import MCPClient, MCPTool
from ai_agent_platform.integrations.tools import ToolExecutionContext, ToolRegistry


class MCPProviderError(Exception):
    pass


class MCPToolProvider:
    def __init__(
        self,
        *,
        server_name: str,
        client: MCPClient,
        namespace: str = "mcp",
    ) -> None:
        self.server_name = _safe_name(server_name)
        self._client = client
        self._namespace = _safe_name(namespace)

    def list_tools(self) -> list[MCPTool]:
        return self._client.list_tools()

    def registered_name(self, tool_name: str) -> str:
        return f"{self._namespace}.{self.server_name}.{_safe_name(tool_name)}"

    def register(self, registry: ToolRegistry) -> None:
        for tool in self.list_tools():
            registry.register(
                self.registered_name(tool.name),
                self._callable_for(tool.name),
                description=tool.description or tool.name,
                input_schema=tool.input_schema,
                output_schema=tool.output_schema,
                provider=f"mcp:{self.server_name}",
                permission_level=tool.permission_level,
                requires_approval=tool.requires_approval,
                risk_summary=(
                    f"MCP tool {self.server_name}.{tool.name} requests "
                    f"{tool.permission_level} permission."
                ),
            )

    def _callable_for(self, tool_name: str):
        def call_mcp_tool(
            context: ToolExecutionContext | None = None, **arguments: Any
        ) -> Any:
            return self._client.call_tool(tool_name, arguments)

        return call_mcp_tool

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


def register_mcp_tools(registry: ToolRegistry, providers: list[MCPToolProvider]) -> None:
    for provider in providers:
        provider.register(registry)


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    normalized = normalized.strip("._-")
    if not normalized:
        raise MCPProviderError("MCP server and tool names must not be empty")
    return normalized
