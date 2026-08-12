from __future__ import annotations

import re
from typing import Any

from ai_agent_platform.integrations.mcp.client import MCPClient, MCPTool
from ai_agent_platform.integrations.tools import ToolExecutionContext, ToolRegistry


class MCPProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "mcp_provider_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


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
                max_retries=1 if tool.permission_level == "read_only" else 0,
                idempotent=tool.permission_level == "read_only",
                risk_summary=(
                    f"MCP tool {self.server_name}.{tool.name} requests "
                    f"{tool.permission_level} permission."
                ),
                permission_source="mcp_annotation",
            )

    def _callable_for(self, tool_name: str):
        def call_mcp_tool(
            context: ToolExecutionContext | None = None, **arguments: Any
        ) -> Any:
            result = self._client.call_tool(tool_name, arguments)
            return normalize_mcp_tool_result(result)

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


def normalize_mcp_tool_result(result: Any) -> Any:
    """Map MCP tools/call payloads to the registry's success/error contract."""

    if not isinstance(result, dict):
        return result
    if bool(result.get("isError")):
        raise MCPProviderError(
            _mcp_error_text(result.get("content")),
            code="mcp_tool_error",
            retryable=False,
        )
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    if "content" not in result:
        return result
    content = result.get("content")
    if not isinstance(content, list):
        return {"content": content}
    text_parts = [
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    non_text_blocks = [
        block
        for block in content
        if not (isinstance(block, dict) and block.get("type") == "text")
    ]
    normalized: dict[str, Any] = {"content": "\n".join(text_parts)}
    if non_text_blocks:
        normalized["content_blocks"] = non_text_blocks
    return normalized


def _mcp_error_text(content: Any) -> str:
    if isinstance(content, list):
        text = "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
        if text:
            return text
    if isinstance(content, str) and content:
        return content
    return "MCP tool returned isError=true"
