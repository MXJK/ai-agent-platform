from __future__ import annotations

import asyncio
from concurrent.futures import (
    CancelledError as FutureCancelledError,
    Future,
    TimeoutError as FutureTimeoutError,
)
from dataclasses import dataclass, field
from threading import Event, Lock, Thread
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

from mcp import Client as SDKClient

from ai_agent_platform.integrations.mcp.config import MCPServerConfig
from ai_agent_platform.integrations.mcp.transports import MCPTransport
from ai_agent_platform.integrations.permissions import PermissionResolver


@dataclass(frozen=True)
class MCPTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    permission_level: str = "external_side_effect"
    requires_approval: bool = True
    idempotent: bool = False


@dataclass(frozen=True)
class MCPToolCacheInfo:
    hit: bool = False
    ttl_ms: int = 0
    cache_scope: str = "private"
    refreshed_at: float | None = None


class MCPClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "mcp_client_error",
        retryable: bool = False,
        call_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.call_id = call_id


class MCPClient:
    """Synchronous platform facade over one official-SDK connection.

    Every instance owns a dedicated event-loop thread, so a slow or wedged
    Server cannot consume another Server's connection or cancellation state.
    """

    def __init__(
        self,
        config: MCPServerConfig,
        transport: MCPTransport,
        permission_resolver: PermissionResolver,
    ) -> None:
        self.config = config
        self.transport = transport
        self._permission_resolver = permission_resolver
        self._runner = _AsyncRunner(name=f"mcp-{config.name}")
        self._protocol_version: str | None = None
        self._catalog: tuple[MCPTool, ...] | None = None
        self._catalog_expires_at = 0.0
        self._cache_info = MCPToolCacheInfo()
        self._calls: dict[str, Future[Any]] = {}
        self._calls_lock = Lock()
        self._lifecycle_lock = Lock()
        self._connection_future: Future[None] | None = None
        self._lifecycle_future: Future[Any] | None = None
        self._command_queue: asyncio.Queue[_Command | None] | None = None
        self._closed = False

    @property
    def protocol_version(self) -> str | None:
        return self._protocol_version

    @property
    def cache_info(self) -> MCPToolCacheInfo:
        return self._cache_info

    @property
    def closed(self) -> bool:
        return self._closed

    def connect(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise MCPClientError(
                    f"MCP server {self.config.name} client is closed",
                    code="mcp_closed",
                )
            if self._connection_future is not None and self._connection_future.done():
                try:
                    self._connection_future.result()
                except Exception as exc:
                    raise _client_error(
                        exc,
                        server_name=self.config.name,
                        default_code="mcp_connect_error",
                    ) from exc
                return
            if self._connection_future is None:
                self._connection_future = Future()
                self._lifecycle_future = self._runner.submit(
                    self._lifecycle_async(self._connection_future)
                )
            future = self._connection_future
            try:
                future.result(timeout=self.config.connect_timeout_seconds)
            except FutureTimeoutError as exc:
                future.cancel()
                raise MCPClientError(
                    f"MCP server {self.config.name} connection timed out",
                    code="mcp_connect_timeout",
                    retryable=True,
                ) from exc
            except Exception as exc:
                raise _client_error(
                    exc,
                    server_name=self.config.name,
                    default_code="mcp_connect_error",
                ) from exc

    def list_tools(self, *, refresh: bool = False) -> list[MCPTool]:
        self.connect()
        now = time.monotonic()
        if not refresh and self._catalog is not None and now < self._catalog_expires_at:
            self._cache_info = MCPToolCacheInfo(
                hit=True,
                ttl_ms=max(0, int((self._catalog_expires_at - now) * 1000)),
                cache_scope=self._cache_info.cache_scope,
                refreshed_at=self._cache_info.refreshed_at,
            )
            return list(self._catalog)
        future = self._dispatch(
            lambda client: self._list_tools_async(client, refresh=refresh)
        )
        try:
            tools, ttl_ms, cache_scope = future.result(
                timeout=self.config.request_timeout_seconds
            )
        except FutureTimeoutError as exc:
            future.cancel()
            raise MCPClientError(
                f"MCP server {self.config.name} tools/list timed out",
                code="mcp_timeout",
                retryable=True,
            ) from exc
        except Exception as exc:
            raise _client_error(
                exc,
                server_name=self.config.name,
                default_code="mcp_list_tools_error",
            ) from exc
        effective_ttl_ms = (
            ttl_ms
            if ttl_ms > 0
            else int(self.config.tool_cache_ttl_seconds * 1000)
        )
        self._catalog = tuple(tools)
        self._catalog_expires_at = time.monotonic() + (effective_ttl_ms / 1000)
        self._cache_info = MCPToolCacheInfo(
            hit=False,
            ttl_ms=effective_ttl_ms,
            cache_scope=cache_scope,
            refreshed_at=time.time(),
        )
        return list(self._catalog)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str | None = None,
    ) -> Any:
        self.connect()
        stable_call_id = call_id or f"mcp_{uuid4().hex[:16]}"
        future = self._dispatch(
            lambda client: self._call_tool_async(
                client,
                name,
                arguments,
                call_id=stable_call_id,
            )
        )
        with self._calls_lock:
            if stable_call_id in self._calls:
                future.cancel()
                raise MCPClientError(
                    "MCP call ID is already active",
                    code="mcp_call_id_conflict",
                    call_id=stable_call_id,
                )
            self._calls[stable_call_id] = future
        try:
            return future.result(timeout=self.config.request_timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise MCPClientError(
                f"MCP server {self.config.name} tool call timed out",
                code="mcp_timeout",
                retryable=True,
                call_id=stable_call_id,
            ) from exc
        except FutureCancelledError as exc:
            raise MCPClientError(
                f"MCP server {self.config.name} tool call was cancelled",
                code="mcp_cancelled",
                retryable=False,
                call_id=stable_call_id,
            ) from exc
        except Exception as exc:
            raise _client_error(
                exc,
                server_name=self.config.name,
                default_code="mcp_protocol_error",
                call_id=stable_call_id,
            ) from exc
        finally:
            with self._calls_lock:
                self._calls.pop(stable_call_id, None)

    def cancel(self, call_id: str) -> bool:
        with self._calls_lock:
            future = self._calls.get(call_id)
        return bool(future and future.cancel())

    def invalidate_tools(self) -> None:
        self._catalog = None
        self._catalog_expires_at = 0.0

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            with self._calls_lock:
                futures = list(self._calls.values())
            for future in futures:
                future.cancel()
            lifecycle = self._lifecycle_future
            queue = self._command_queue
            if lifecycle is not None and not lifecycle.done():
                if queue is not None:
                    self._runner.call_soon(queue.put_nowait, None)
                else:
                    lifecycle.cancel()
                try:
                    lifecycle.result(
                        timeout=max(2.0, self.config.connect_timeout_seconds)
                    )
                except Exception:
                    lifecycle.cancel()
            self._runner.close()

    async def _lifecycle_async(self, connected: Future[None]) -> None:
        try:
            async with self.transport.open_client() as client:
                self._protocol_version = str(client.protocol_version)
                self._command_queue = asyncio.Queue()
                if not connected.cancelled():
                    connected.set_result(None)
                while True:
                    command = await self._command_queue.get()
                    if command is None:
                        break
                    if command.future.cancelled():
                        continue
                    task = asyncio.create_task(command.operation(client))

                    def cancel_task(done: Future[Any]) -> None:
                        if done.cancelled():
                            self._runner.call_soon(task.cancel)

                    command.future.add_done_callback(cancel_task)
                    try:
                        result = await task
                    except asyncio.CancelledError:
                        command.future.cancel()
                    except BaseException as exc:
                        if not command.future.done():
                            command.future.set_exception(exc)
                    else:
                        if not command.future.done():
                            command.future.set_result(result)
        except BaseException as exc:
            if not connected.done():
                connected.set_exception(exc)
            raise
        finally:
            self._command_queue = None
            self._protocol_version = None

    def _dispatch(
        self,
        operation: Callable[[SDKClient], Awaitable[Any]],
    ) -> Future[Any]:
        queue = self._command_queue
        if queue is None:
            raise MCPClientError(
                f"MCP server {self.config.name} is not connected",
                code="mcp_disconnected",
                retryable=True,
            )
        future: Future[Any] = Future()
        self._runner.call_soon(queue.put_nowait, _Command(operation, future))
        return future

    async def _list_tools_async(
        self,
        client: SDKClient,
        *,
        refresh: bool,
    ) -> tuple[list[MCPTool], int, str]:
        tools: list[MCPTool] = []
        cursors: set[str] = set()
        cursor: str | None = None
        ttl_values: list[int] = []
        cache_scopes: list[str] = []
        while True:
            page = await client.list_tools(
                cursor=cursor,
                cache_mode="refresh" if refresh else "use",
            )
            ttl_values.append(int(page.ttl_ms))
            cache_scopes.append(str(page.cache_scope))
            tools.extend(self._tool_from_sdk(item) for item in page.tools)
            cursor = page.next_cursor
            if cursor is None:
                break
            if cursor in cursors:
                raise MCPClientError(
                    f"MCP server {self.config.name} returned a repeated tools/list cursor",
                    code="mcp_invalid_pagination",
                )
            cursors.add(cursor)
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise MCPClientError(
                f"MCP server {self.config.name} returned duplicate tool names",
                code="mcp_invalid_tool_catalog",
            )
        tools.sort(key=lambda item: (item.name, item.description))
        ttl_ms = min(ttl_values) if ttl_values else 0
        cache_scope = "private" if "private" in cache_scopes else "public"
        return tools, ttl_ms, cache_scope

    async def _call_tool_async(
        self,
        client: SDKClient,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str,
    ) -> dict[str, Any]:
        result = await client.call_tool(
            name,
            arguments,
            read_timeout_seconds=self.config.request_timeout_seconds,
            meta={"io.ai-agent-platform/call-id": call_id},
        )
        return result.model_dump(by_alias=True, exclude_none=True)

    def _tool_from_sdk(self, tool: Any) -> MCPTool:
        annotations = (
            tool.annotations.model_dump(by_alias=True, exclude_none=True)
            if tool.annotations is not None
            else {}
        )
        permission = self._permission_resolver.resolve_mcp_annotations(
            name=str(tool.name),
            annotations=annotations,
        )
        return MCPTool(
            name=str(tool.name),
            description=str(tool.description or tool.title or ""),
            input_schema=dict(tool.input_schema or {"type": "object"}),
            output_schema=dict(tool.output_schema or {"type": "object"}),
            permission_level=permission.permission_level,
            requires_approval=permission.requires_approval,
            idempotent=bool(
                annotations.get("idempotentHint") is True
                or permission.permission_level == "read_only"
            ),
        )



@dataclass(frozen=True)
class _Command:
    operation: Callable[[SDKClient], Awaitable[Any]]
    future: Future[Any]


class _AsyncRunner:
    def __init__(self, *, name: str) -> None:
        self._ready = Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = Thread(target=self._run, name=name, daemon=True)
        self._thread.start()
        self._ready.wait()

    def submit(self, coroutine: Any) -> Future[Any]:
        if self._loop is None:
            raise RuntimeError("MCP event loop is closed")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def close(self) -> None:
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=5)
        self._loop = None

    def call_soon(self, callback: Callable[..., Any], *args: Any) -> None:
        if self._loop is None:
            raise RuntimeError("MCP event loop is closed")
        self._loop.call_soon_threadsafe(callback, *args)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()


def _client_error(
    exc: BaseException,
    *,
    server_name: str,
    default_code: str,
    call_id: str | None = None,
) -> MCPClientError:
    if isinstance(exc, MCPClientError):
        return exc
    raw_code = str(getattr(exc, "code", default_code))
    is_connection_closed = raw_code == "-32000"
    is_timeout = raw_code == "-32001" or "timeout" in type(exc).__name__.lower()
    is_internal_code = raw_code.startswith("mcp_")
    is_protocol_code = raw_code != default_code and not is_internal_code
    code = (
        "mcp_timeout"
        if is_timeout
        else "mcp_connection_closed"
        if is_connection_closed
        else raw_code
        if is_internal_code
        else default_code
    )
    retryable_hint = getattr(exc, "retryable", None)
    retryable = (
        bool(retryable_hint)
        if retryable_hint is not None
        else not is_protocol_code
    ) or is_timeout or is_connection_closed
    # Never copy transport exception bodies into this boundary: SDK/HTTP
    # messages may contain endpoint details or server-controlled text.
    return MCPClientError(
        f"MCP server {server_name} request failed ({type(exc).__name__})",
        code=code,
        retryable=retryable,
        call_id=call_id,
    )
