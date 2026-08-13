from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from threading import RLock
import time
from typing import Any, Iterable

from ai_agent_platform.integrations.mcp.client import MCPClient, MCPClientError, MCPTool
from ai_agent_platform.integrations.mcp.config import (
    LEGACY_STDIO_2025_06_18,
    MCPServerConfig,
)
from ai_agent_platform.integrations.mcp.stdio_client import MCPStdioClient
from ai_agent_platform.integrations.mcp.transports import create_mcp_transport
from ai_agent_platform.integrations.permissions import PermissionResolver
from ai_agent_platform.model_registry.secrets import SecretStore


class MCPServerState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    CIRCUIT_OPEN = "circuit_open"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(frozen=True)
class MCPServerStatus:
    name: str
    state: MCPServerState
    required: bool
    transport: str
    endpoint: str
    protocol_version: str | None = None
    tool_count: int = 0
    consecutive_failures: int = 0
    retry_count: int = 0
    circuit_open_until: float | None = None
    cache_ttl_ms: int = 0
    cache_scope: str = "private"
    cache_hit: bool = False
    last_error_code: str | None = None
    last_changed_at: float = field(default_factory=time.time)

    def to_diagnostic(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "required": self.required,
            "transport": self.transport,
            "endpoint": self.endpoint,
            "protocol_version": self.protocol_version,
            "tool_count": self.tool_count,
            "consecutive_failures": self.consecutive_failures,
            "retry_count": self.retry_count,
            "circuit_open_until": self.circuit_open_until,
            "cache_ttl_ms": self.cache_ttl_ms,
            "cache_scope": self.cache_scope,
            "cache_hit": self.cache_hit,
            "last_error_code": self.last_error_code,
            "last_changed_at": self.last_changed_at,
        }


@dataclass
class _ManagedServer:
    config: MCPServerConfig
    status: MCPServerStatus
    client: Any = None
    tools: tuple[MCPTool, ...] = ()
    circuit_opened_monotonic: float | None = None
    lock: RLock = field(default_factory=RLock)


class MCPConnectionManager:
    """Own independent connection policy and diagnostics for each MCP Server."""

    def __init__(
        self,
        configs: Iterable[MCPServerConfig],
        *,
        secret_store: SecretStore,
        permission_resolver: PermissionResolver,
    ) -> None:
        self._secret_store = secret_store
        self._permission_resolver = permission_resolver
        self._servers: dict[str, _ManagedServer] = {}
        self._manager_lock = RLock()
        self._closed = False
        for config in sorted(configs, key=lambda item: item.name):
            if not config.enabled:
                continue
            if config.name in self._servers:
                raise ValueError(f"duplicate MCP server name: {config.name}")
            self._servers[config.name] = _ManagedServer(
                config=config,
                status=MCPServerStatus(
                    name=config.name,
                    state=MCPServerState.DISCONNECTED,
                    required=config.required,
                    transport=config.transport,
                    endpoint=config.endpoint_label,
                ),
            )

    @property
    def ready(self) -> bool:
        return all(
            not server.config.required or server.status.state == MCPServerState.READY
            for server in self._servers.values()
        )

    @property
    def statuses(self) -> tuple[MCPServerStatus, ...]:
        with self._manager_lock:
            return tuple(self._servers[name].status for name in sorted(self._servers))

    @property
    def configs(self) -> tuple[MCPServerConfig, ...]:
        with self._manager_lock:
            return tuple(self._servers[name].config for name in sorted(self._servers))

    def diagnostics(self) -> list[dict[str, Any]]:
        return [status.to_diagnostic() for status in self.statuses]

    def server_config(self, server_name: str) -> MCPServerConfig:
        return self._server(server_name).config

    def status_for(self, server_name: str) -> MCPServerStatus | None:
        with self._manager_lock:
            server = self._servers.get(server_name)
            return server.status if server is not None else None

    def server_tools(self, server_name: str) -> tuple[MCPTool, ...]:
        with self._manager_lock:
            server = self._servers.get(server_name)
            return server.tools if server is not None else ()

    def start(self) -> None:
        self._ensure_open()
        for name in sorted(self._servers):
            try:
                self._connect_and_list(name, refresh=True, force=True)
            except Exception:
                # Status captures the sanitized error. Optional failures never
                # escape startup; required failures are reflected by readiness.
                continue

    def available_server_names(self) -> tuple[str, ...]:
        return tuple(
            status.name
            for status in self.statuses
            if status.state == MCPServerState.READY
        )

    def list_tools(self, server_name: str, *, refresh: bool = False) -> list[MCPTool]:
        server = self._server(server_name)
        with server.lock:
            self._ensure_attempt_allowed(server, force=refresh)
            if server.client is None or refresh:
                return self._connect_and_list(server_name, refresh=refresh, force=refresh)
            try:
                tools = server.client.list_tools(refresh=refresh)
            except Exception as exc:
                self._record_failure(server, exc)
                raise
            server.tools = tuple(tools)
            self._record_success(server)
            return list(server.tools)

    def refresh(self, server_name: str) -> list[MCPTool]:
        return self._connect_and_list(server_name, refresh=True, force=True)

    def upsert_server(self, config: MCPServerConfig) -> MCPServerStatus | None:
        """Replace one Server lifecycle and attempt immediate discovery."""

        self._ensure_open()
        with self._manager_lock:
            if config.name in self._servers:
                self.close_server(config.name)
                self._servers.pop(config.name, None)
            if not config.enabled:
                return None
            server = _ManagedServer(
                config=config,
                status=MCPServerStatus(
                    name=config.name,
                    state=MCPServerState.DISCONNECTED,
                    required=config.required,
                    transport=config.transport,
                    endpoint=config.endpoint_label,
                ),
            )
            self._servers[config.name] = server
        try:
            self._connect_and_list(config.name, refresh=True, force=True)
        except Exception:
            pass
        return self.status_for(config.name)

    def remove_server(self, server_name: str) -> bool:
        with self._manager_lock:
            if server_name not in self._servers:
                return False
            self.close_server(server_name)
            self._servers.pop(server_name, None)
            return True

    def call_tool(
        self,
        server_name: str,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str,
    ) -> Any:
        server = self._server(server_name)
        tool = next((item for item in server.tools if item.name == name), None)
        idempotent = bool(tool and tool.idempotent)
        max_attempts = 1 + (server.config.max_retries if idempotent else 0)
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            with server.lock:
                self._ensure_attempt_allowed(server)
                if server.client is None:
                    self._connect_and_list(server_name, refresh=True, force=False)
                try:
                    result = server.client.call_tool(
                        name,
                        arguments,
                        call_id=call_id,
                    )
                except Exception as exc:
                    last_error = exc
                    self._record_failure(server, exc, retry_count=attempt - 1)
                    retryable = bool(getattr(exc, "retryable", False))
                    if not retryable or attempt >= max_attempts:
                        raise
                    self._disconnect(server)
                else:
                    self._record_success(server, retry_count=attempt - 1)
                    return result
            time.sleep(server.config.retry_backoff_seconds * (2 ** (attempt - 1)))
        assert last_error is not None
        raise last_error

    def cancel(self, server_name: str, call_id: str) -> bool:
        server = self._server(server_name)
        # Cancellation must not wait behind the lock held by the in-flight
        # synchronous call it is trying to stop.
        client = server.client
        cancel = getattr(client, "cancel", None)
        return bool(cancel and cancel(call_id))

    def close_server(self, server_name: str) -> None:
        server = self._server(server_name)
        with server.lock:
            if server.status.state == MCPServerState.CLOSED:
                return
            self._set_status(server, state=MCPServerState.CLOSING)
            self._disconnect(server)
            self._set_status(server, state=MCPServerState.CLOSED)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._manager_lock:
            names = reversed(sorted(self._servers))
        for name in names:
            self.close_server(name)

    def _connect_and_list(
        self,
        server_name: str,
        *,
        refresh: bool,
        force: bool,
    ) -> list[MCPTool]:
        server = self._server(server_name)
        with server.lock:
            self._ensure_attempt_allowed(server, force=force)
            previous_state = server.status.state
            self._set_status(server, state=MCPServerState.CONNECTING)
            if refresh and previous_state in {
                MCPServerState.DEGRADED,
                MCPServerState.UNAVAILABLE,
                MCPServerState.CIRCUIT_OPEN,
            }:
                self._disconnect(server)
            if server.client is None:
                server.client = self._create_client(server.config)
            attempts = 1 + server.config.max_retries
            for attempt in range(1, attempts + 1):
                try:
                    tools = server.client.list_tools(refresh=refresh)
                except Exception as exc:
                    self._record_failure(server, exc, retry_count=attempt - 1)
                    retryable = bool(getattr(exc, "retryable", True))
                    if not retryable or attempt >= attempts:
                        raise
                    self._disconnect(server)
                    time.sleep(
                        server.config.retry_backoff_seconds * (2 ** (attempt - 1))
                    )
                    server.client = self._create_client(server.config)
                    continue
                server.tools = tuple(tools)
                self._record_success(server, retry_count=attempt - 1)
                return list(server.tools)
        return []  # pragma: no cover

    def _create_client(self, config: MCPServerConfig) -> Any:
        if config.transport == LEGACY_STDIO_2025_06_18:
            return MCPStdioClient(
                config,
                request_timeout_seconds=config.request_timeout_seconds,
                secret_store=self._secret_store,
                permission_resolver=self._permission_resolver,
            )
        return MCPClient(
            config,
            create_mcp_transport(config, self._secret_store),
            self._permission_resolver,
        )

    def _record_success(self, server: _ManagedServer, *, retry_count: int = 0) -> None:
        cache = getattr(server.client, "cache_info", None)
        self._set_status(
            server,
            state=MCPServerState.READY,
            protocol_version=getattr(server.client, "protocol_version", None),
            tool_count=len(server.tools),
            consecutive_failures=0,
            retry_count=retry_count,
            circuit_open_until=None,
            cache_ttl_ms=int(getattr(cache, "ttl_ms", 0)),
            cache_scope=str(getattr(cache, "cache_scope", "private")),
            cache_hit=bool(getattr(cache, "hit", False)),
            last_error_code=None,
        )
        server.circuit_opened_monotonic = None

    def _record_failure(
        self,
        server: _ManagedServer,
        exc: BaseException,
        *,
        retry_count: int = 0,
    ) -> None:
        failures = server.status.consecutive_failures + 1
        state = MCPServerState.DEGRADED if server.client is not None else MCPServerState.UNAVAILABLE
        open_until: float | None = None
        if failures >= server.config.circuit_failure_threshold:
            state = MCPServerState.CIRCUIT_OPEN
            server.circuit_opened_monotonic = time.monotonic()
            open_until = time.time() + server.config.circuit_reset_seconds
        self._set_status(
            server,
            state=state,
            consecutive_failures=failures,
            retry_count=retry_count,
            circuit_open_until=open_until,
            last_error_code=str(getattr(exc, "code", "mcp_transport_error")),
        )

    def _ensure_attempt_allowed(self, server: _ManagedServer, *, force: bool = False) -> None:
        self._ensure_open()
        if server.status.state == MCPServerState.CLOSED:
            raise MCPClientError(
                f"MCP server {server.config.name} is closed",
                code="mcp_closed",
            )
        if server.status.state != MCPServerState.CIRCUIT_OPEN or force:
            return
        opened = server.circuit_opened_monotonic or time.monotonic()
        if time.monotonic() - opened >= server.config.circuit_reset_seconds:
            self._set_status(server, state=MCPServerState.DISCONNECTED)
            return
        raise MCPClientError(
            f"MCP server {server.config.name} circuit is open",
            code="mcp_circuit_open",
            retryable=True,
        )

    def _disconnect(self, server: _ManagedServer) -> None:
        client = server.client
        server.client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _set_status(self, server: _ManagedServer, **changes: Any) -> None:
        server.status = replace(server.status, last_changed_at=time.time(), **changes)

    def _server(self, name: str) -> _ManagedServer:
        with self._manager_lock:
            try:
                return self._servers[name]
            except KeyError as exc:
                raise ValueError(f"unknown MCP server: {name}") from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise MCPClientError("MCP connection manager is closed", code="mcp_closed")


class ManagedMCPClient:
    """Provider-facing facade that routes operations through manager policy."""

    def __init__(self, manager: MCPConnectionManager, server_name: str) -> None:
        self.manager = manager
        self.server_name = server_name
        self.config = manager.server_config(server_name)

    def list_tools(self, *, refresh: bool = False) -> list[MCPTool]:
        return self.manager.list_tools(self.server_name, refresh=refresh)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str | None = None,
    ) -> Any:
        return self.manager.call_tool(
            self.server_name,
            name,
            arguments,
            call_id=call_id or f"mcp_{time.time_ns()}",
        )

    def cancel(self, call_id: str) -> bool:
        return self.manager.cancel(self.server_name, call_id)

    def close(self) -> None:
        self.manager.close_server(self.server_name)


class MCPProviderCollection(list[Any]):
    def __init__(self, values: Iterable[Any], manager: MCPConnectionManager) -> None:
        super().__init__(values)
        self.connection_manager = manager
