from __future__ import annotations

import json
import os
import subprocess
import threading
from queue import Empty, Queue
from typing import Any, TextIO

from ai_agent_platform.integrations.mcp.client import MCPTool
from ai_agent_platform.integrations.mcp.config import MCPServerConfig


MCP_PROTOCOL_VERSION = "2025-06-18"


class MCPStdioClientError(Exception):
    pass


class MCPStdioClient:
    def __init__(
        self,
        config: MCPServerConfig,
        *,
        client_name: str = "ai-agent-platform",
        client_version: str = "0.1.0",
        request_timeout_seconds: float = 10.0,
    ) -> None:
        if config.transport != "stdio":
            raise MCPStdioClientError(
                f"MCPStdioClient only supports stdio transport, got {config.transport}"
            )
        if not config.command:
            raise MCPStdioClientError("stdio MCP server command is required")
        self._config = config
        self._client_name = client_name
        self._client_version = client_version
        self._request_timeout_seconds = request_timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._responses: dict[int, dict[str, Any]] = {}
        self._response_queue: Queue[dict[str, Any]] = Queue()
        self._stderr_lines: Queue[str] = Queue()
        self._request_id = 0
        self._lock = threading.Lock()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._initialized = False

    def list_tools(self) -> list[MCPTool]:
        self._ensure_initialized()
        tools: list[MCPTool] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            result = self._request("tools/list", params)
            for raw_tool in result.get("tools", []):
                tools.append(_mcp_tool_from_response(raw_tool))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self._ensure_initialized()
        return self._request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
        )

    def close(self) -> None:
        process = self._process
        if process is None:
            return

        if process.stdin:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass
        self._process = None
        self._initialized = False

    def __enter__(self) -> "MCPStdioClient":
        self._ensure_initialized()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._start_process()
        result = self._request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": self._client_name,
                    "version": self._client_version,
                },
            },
        )
        protocol_version = result.get("protocolVersion")
        if protocol_version != MCP_PROTOCOL_VERSION:
            raise MCPStdioClientError(
                f"unsupported MCP protocol version negotiated: {protocol_version}"
            )
        self._notify("notifications/initialized")
        self._initialized = True

    def _start_process(self) -> None:
        if self._process is not None:
            return
        env = dict(os.environ)
        env.update(self._config.env)
        self._process = subprocess.Popen(
            [self._config.command, *self._config.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
        )
        if self._process.stdout is None or self._process.stdin is None:
            raise MCPStdioClientError("failed to open MCP stdio pipes")
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(self._process.stdout,),
            daemon=True,
        )
        self._stdout_thread.start()
        if self._process.stderr is not None:
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(self._process.stderr,),
                daemon=True,
            )
            self._stderr_thread.start()

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
        self._write_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        response = self._wait_for_response(request_id)
        if "error" in response:
            error = response["error"]
            raise MCPStdioClientError(
                f"MCP request {method} failed: {error.get('message', error)}"
            )
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise MCPStdioClientError(f"MCP request {method} returned invalid result")
        return result

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self._write_message(message)

    def _write_message(self, message: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise MCPStdioClientError("MCP process is not running")
        payload = json.dumps(message, separators=(",", ":"))
        try:
            self._process.stdin.write(payload + "\n")
            self._process.stdin.flush()
        except BrokenPipeError as exc:
            raise MCPStdioClientError("MCP process stdin is closed") from exc

    def _wait_for_response(self, request_id: int) -> dict[str, Any]:
        if request_id in self._responses:
            return self._responses.pop(request_id)

        while True:
            try:
                message = self._response_queue.get(
                    timeout=self._request_timeout_seconds
                )
            except Empty as exc:
                stderr = self._collect_stderr()
                detail = f"; stderr: {stderr}" if stderr else ""
                raise MCPStdioClientError(
                    f"timed out waiting for MCP response id={request_id}{detail}"
                ) from exc

            message_id = message.get("id")
            if message_id == request_id:
                return message
            if isinstance(message_id, int):
                self._responses[message_id] = message

    def _read_stdout(self, stdout: TextIO) -> None:
        for line in stdout:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = json.loads(stripped)
            except json.JSONDecodeError:
                self._stderr_lines.put(f"invalid stdout JSON: {stripped[:200]}")
                continue
            if isinstance(message, dict) and "id" in message:
                self._response_queue.put(message)

    def _read_stderr(self, stderr: TextIO) -> None:
        for line in stderr:
            stripped = line.strip()
            if stripped:
                self._stderr_lines.put(stripped)

    def _collect_stderr(self) -> str:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._stderr_lines.get_nowait())
            except Empty:
                break
        return "\n".join(lines[-10:])


def create_stdio_mcp_client(
    config: MCPServerConfig,
    *,
    request_timeout_seconds: float = 10.0,
) -> MCPStdioClient:
    return MCPStdioClient(
        config,
        request_timeout_seconds=request_timeout_seconds,
    )


def _mcp_tool_from_response(payload: dict[str, Any]) -> MCPTool:
    annotations = payload.get("annotations") or {}
    return MCPTool(
        name=str(payload["name"]),
        description=str(payload.get("description") or payload.get("title") or ""),
        input_schema=payload.get("inputSchema") or {"type": "object"},
        output_schema=payload.get("outputSchema") or {"type": "object"},
        permission_level=_permission_from_annotations(annotations),
        requires_approval=_requires_approval_from_annotations(annotations),
    )


def _permission_from_annotations(annotations: dict[str, Any]) -> str:
    if annotations.get("destructiveHint") or annotations.get("openWorldHint"):
        return "external_side_effect"
    if annotations.get("readOnlyHint") is False:
        return "write_safe"
    return "read_only"


def _requires_approval_from_annotations(annotations: dict[str, Any]) -> bool:
    return bool(
        annotations.get("destructiveHint")
        or annotations.get("openWorldHint")
        or annotations.get("readOnlyHint") is False
    )
