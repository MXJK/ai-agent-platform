from __future__ import annotations

from contextlib import asynccontextmanager
import ipaddress
import os
import socket
from typing import Any, AsyncContextManager, AsyncIterator, Protocol
from urllib.parse import urlsplit

import httpx2
from mcp import Client as SDKClient, types as mcp_types
from mcp.client.caching import CacheConfig
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from ai_agent_platform.integrations.mcp.config import (
    CURRENT_STDIO,
    LEGACY_SSE,
    MCPServerConfig,
    STREAMABLE_HTTP,
)
from ai_agent_platform.model_registry.secrets import SecretStore


class MCPTransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "mcp_transport_error",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class MCPTransport(Protocol):
    """A transport owns the SDK connection context for one configured Server."""

    kind: str
    endpoint_label: str

    def open_client(self) -> AsyncContextManager[SDKClient]: ...


class StdioMCPTransport:
    kind = CURRENT_STDIO

    def __init__(self, config: MCPServerConfig, secret_store: SecretStore) -> None:
        self._config = config
        self._secret_store = secret_store
        self.endpoint_label = config.endpoint_label

    @asynccontextmanager
    async def open_client(self) -> AsyncIterator[SDKClient]:
        env = _resolve_secret_map(
            self._config.env,
            self._config.env_refs,
            self._secret_store,
            server_name=self._config.name,
            kind="environment",
        )
        params = StdioServerParameters(
            command=str(self._config.command),
            args=list(self._config.args),
            env=env,
        )
        # The SDK otherwise forwards arbitrary Server stderr to the platform's
        # stderr. Drain it to a sink so Server-controlled text and injected
        # environment values cannot become application logs.
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            async with SDKClient(
                stdio_client(params, errlog=errlog),
                read_timeout_seconds=self._config.request_timeout_seconds,
                client_info=_client_info(),
                mode="auto",
                cache=CacheConfig(
                    default_ttl_ms=int(self._config.tool_cache_ttl_seconds * 1000)
                ),
            ) as client:
                yield client


class StreamableHTTPMCPTransport:
    kind = STREAMABLE_HTTP

    def __init__(self, config: MCPServerConfig, secret_store: SecretStore) -> None:
        self._config = config
        self._secret_store = secret_store
        self.endpoint_label = config.endpoint_label

    @asynccontextmanager
    async def open_client(self) -> AsyncIterator[SDKClient]:
        _validate_resolved_target(self._config)
        headers = _resolve_secret_map(
            self._config.headers,
            self._config.header_refs,
            self._secret_store,
            server_name=self._config.name,
            kind="header",
        )
        timeout = httpx2.Timeout(
            self._config.request_timeout_seconds,
            connect=self._config.connect_timeout_seconds,
        )
        async with httpx2.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as http_client:
            async with SDKClient(
                streamable_http_client(
                    str(self._config.url),
                    http_client=http_client,
                ),
                read_timeout_seconds=self._config.request_timeout_seconds,
                client_info=_client_info(),
                mode="auto",
                cache=CacheConfig(
                    default_ttl_ms=int(self._config.tool_cache_ttl_seconds * 1000)
                ),
            ) as client:
                yield client


class LegacySSEMCPTransport:
    kind = LEGACY_SSE

    def __init__(self, config: MCPServerConfig, secret_store: SecretStore) -> None:
        self._config = config
        self._secret_store = secret_store
        self.endpoint_label = config.endpoint_label

    @asynccontextmanager
    async def open_client(self) -> AsyncIterator[SDKClient]:
        _validate_resolved_target(self._config)
        headers = _resolve_secret_map(
            self._config.headers,
            self._config.header_refs,
            self._secret_store,
            server_name=self._config.name,
            kind="header",
        )

        def http_client_factory(**kwargs: Any) -> httpx2.AsyncClient:
            supplied = dict(kwargs)
            supplied["follow_redirects"] = False
            supplied["trust_env"] = False
            return httpx2.AsyncClient(**supplied)

        async with SDKClient(
            sse_client(
                str(self._config.url),
                headers=headers,
                timeout=self._config.connect_timeout_seconds,
                sse_read_timeout=self._config.request_timeout_seconds,
                httpx_client_factory=http_client_factory,
            ),
            read_timeout_seconds=self._config.request_timeout_seconds,
            client_info=_client_info(),
            mode="legacy",
            cache=CacheConfig(
                default_ttl_ms=int(self._config.tool_cache_ttl_seconds * 1000)
            ),
        ) as client:
            yield client


def create_mcp_transport(
    config: MCPServerConfig,
    secret_store: SecretStore,
) -> MCPTransport:
    if config.transport == CURRENT_STDIO:
        return StdioMCPTransport(config, secret_store)
    if config.transport == STREAMABLE_HTTP:
        return StreamableHTTPMCPTransport(config, secret_store)
    if config.transport == LEGACY_SSE:
        return LegacySSEMCPTransport(config, secret_store)
    raise ValueError(f"transport {config.transport} does not use the official SDK path")


def _client_info() -> mcp_types.Implementation:
    return mcp_types.Implementation(name="ai-agent-platform", version="0.1.0")


def _resolve_secret_map(
    literals: dict[str, str],
    refs: dict[str, str],
    secret_store: SecretStore,
    *,
    server_name: str,
    kind: str,
) -> dict[str, str]:
    resolved = dict(literals)
    for name, secret_ref in refs.items():
        value = secret_store.get(secret_ref)
        if value is None:
            raise MCPTransportError(
                f"MCP server {server_name} has an unresolved {kind} secret reference",
                code="mcp_secret_unavailable",
                retryable=False,
            )
        if "\x00" in value or "\r" in value or "\n" in value:
            raise MCPTransportError(
                f"MCP server {server_name} resolved an unsafe {kind} secret",
                code="mcp_security_error",
                retryable=False,
            )
        resolved[name] = value
    return resolved


def _validate_resolved_target(config: MCPServerConfig) -> None:
    """Reject DNS answers that cross the configured network boundary."""

    host = urlsplit(str(config.url)).hostname
    if host is None:
        raise MCPTransportError(
            f"MCP server {config.name} has no HTTP host",
            code="mcp_security_error",
            retryable=False,
        )
    try:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise MCPTransportError(
            f"MCP server {config.name} HTTP host could not be resolved",
            code="mcp_dns_error",
            retryable=True,
        ) from exc
    addresses = {item[4][0] for item in answers}
    if not addresses:
        raise MCPTransportError(
            f"MCP server {config.name} HTTP host returned no addresses",
            code="mcp_dns_error",
            retryable=True,
        )
    if not config.allow_private_network and any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise MCPTransportError(
            f"MCP server {config.name} HTTP host resolved outside the public network boundary",
            code="mcp_security_error",
            retryable=False,
        )
