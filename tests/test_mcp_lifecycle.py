from __future__ import annotations

from contextlib import redirect_stderr
import httpx2
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.mcp import (
    MCPClientError,
    MCPServerConfig,
    MCPServerState,
    create_mcp_connection_manager_from_configs,
    load_mcp_server_configs,
)
from ai_agent_platform.integrations.permissions import PermissionResolver
from ai_agent_platform.main import create_app
from ai_agent_platform.model_registry import InMemorySecretStore


class MCPLifecycleTests(unittest.TestCase):
    def test_current_stdio_paginates_sorts_caches_preserves_call_id_and_closes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_path = _write_current_stdio_server(root)
            counter = root / "list-count.txt"
            closed = root / "closed.txt"
            config = _stdio_config(
                server_path,
                counter=counter,
                closed=closed,
                env_refs={"MCP_SERVER_TOKEN": "mcp/stdio/demo"},
                tool_cache_ttl_seconds=10,
                max_retries=0,
            )
            secret_store = InMemorySecretStore()
            secret_store.set("mcp/stdio/demo", "stdio-super-secret")
            manager = create_mcp_connection_manager_from_configs(
                [config],
                secret_store=secret_store,
            )
            stderr = io.StringIO()
            with patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-leak"}):
                with redirect_stderr(stderr):
                    manager.start()
                self.assertTrue(manager.ready)
                tools = manager.list_tools("current_stdio")
                self.assertEqual(
                    [tool.name for tool in tools],
                    ["alpha", "probe_env", "slow", "zeta"],
                )
                self.assertEqual(counter.read_text(encoding="utf-8"), "2")

                manager.list_tools("current_stdio")
                self.assertEqual(counter.read_text(encoding="utf-8"), "2")
                self.assertTrue(manager.statuses[0].cache_hit)

                result = manager.call_tool(
                    "current_stdio",
                    "alpha",
                    {"text": "hello"},
                    call_id="stable-call-7",
                )
                self.assertEqual(
                    result["structuredContent"],
                    {"echo": "hello", "call_id": "stable-call-7"},
                )
                env_result = manager.call_tool(
                    "current_stdio",
                    "probe_env",
                    {},
                    call_id="env-call",
                )
                self.assertFalse(env_result["structuredContent"]["leaked"])
                self.assertTrue(env_result["structuredContent"]["secret_injected"])
                self.assertNotIn("stdio-super-secret", stderr.getvalue())
                self.assertNotIn(
                    "stdio-super-secret",
                    json.dumps(manager.diagnostics(), sort_keys=True),
                )
                self.assertNotIn("mcp/stdio/demo", repr(config))

                manager.refresh("current_stdio")
                self.assertEqual(counter.read_text(encoding="utf-8"), "4")
            manager.close()
            self.assertTrue(_wait_for_path(closed))
            self.assertEqual(manager.statuses[0].state, MCPServerState.CLOSED)

    def test_stdio_reconnects_after_retryable_catalog_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_path = _write_current_stdio_server(root)
            fail_once = root / "fail-once.txt"
            config = _stdio_config(
                server_path,
                fail_once=fail_once,
                max_retries=1,
                circuit_failure_threshold=3,
            )
            manager = create_mcp_connection_manager_from_configs([config])
            try:
                manager.start()
                self.assertTrue(manager.ready)
                self.assertTrue(fail_once.exists())
                self.assertEqual(manager.statuses[0].retry_count, 1)
                self.assertEqual(manager.statuses[0].state, MCPServerState.READY)
            finally:
                manager.close()

    def test_open_circuit_can_be_explicitly_refreshed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_path = _write_current_stdio_server(root)
            fail_once = root / "fail-once.txt"
            config = _stdio_config(
                server_path,
                fail_once=fail_once,
                max_retries=0,
                circuit_failure_threshold=1,
            )
            manager = create_mcp_connection_manager_from_configs([config])
            try:
                manager.start()
                self.assertEqual(
                    manager.statuses[0].state,
                    MCPServerState.CIRCUIT_OPEN,
                )
                with self.assertRaises(MCPClientError) as raised:
                    manager.list_tools("current_stdio")
                self.assertEqual(raised.exception.code, "mcp_circuit_open")

                tools = manager.refresh("current_stdio")
                self.assertTrue(tools)
                self.assertEqual(manager.statuses[0].state, MCPServerState.READY)
            finally:
                manager.close()

    def test_tool_timeout_and_explicit_cancellation_have_stable_codes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_path = _write_current_stdio_server(root)
            timeout_manager = create_mcp_connection_manager_from_configs(
                [
                    _stdio_config(
                        server_path,
                        request_timeout_seconds=0.05,
                        max_retries=0,
                    )
                ]
            )
            timeout_manager.start()
            try:
                with self.assertRaises(MCPClientError) as timed_out:
                    timeout_manager.call_tool(
                        "current_stdio",
                        "slow",
                        {"delay": 0.3},
                        call_id="timeout-call",
                    )
                self.assertEqual(timed_out.exception.code, "mcp_timeout")
                self.assertEqual(timed_out.exception.call_id, "timeout-call")
            finally:
                timeout_manager.close()

            cancel_manager = create_mcp_connection_manager_from_configs(
                [
                    _stdio_config(
                        server_path,
                        request_timeout_seconds=2.0,
                        max_retries=0,
                    )
                ]
            )
            cancel_manager.start()
            outcome: dict[str, object] = {}

            def invoke() -> None:
                try:
                    cancel_manager.call_tool(
                        "current_stdio",
                        "slow",
                        {"delay": 0.5},
                        call_id="cancel-call",
                    )
                except BaseException as exc:  # captured for the assertion thread
                    outcome["error"] = exc

            thread = Thread(target=invoke)
            thread.start()
            time.sleep(0.08)
            self.assertTrue(cancel_manager.cancel("current_stdio", "cancel-call"))
            thread.join(timeout=2)
            try:
                error = outcome.get("error")
                self.assertIsInstance(error, MCPClientError)
                self.assertEqual(getattr(error, "code", None), "mcp_cancelled")
                self.assertEqual(getattr(error, "call_id", None), "cancel-call")
            finally:
                cancel_manager.close()

    def test_streamable_http_uses_secret_refs_and_current_protocol(self) -> None:
        server = _FakeHTTPMCPServer()
        original_async_client = httpx2.AsyncClient

        def http_client_factory(**kwargs: object) -> httpx2.AsyncClient:
            return original_async_client(
                **kwargs,
                transport=httpx2.MockTransport(server),
            )

        with patch(
            "ai_agent_platform.integrations.mcp.transports.httpx2.AsyncClient",
            side_effect=http_client_factory,
        ):
            secret_store = InMemorySecretStore()
            secret_store.set("mcp/http/demo", "Bearer super-secret-token")
            config = MCPServerConfig(
                name="current_http",
                transport="streamable_http",
                url="http://127.0.0.1:8765/mcp",
                allowed_hosts=("127.0.0.1",),
                allow_insecure_http=True,
                allow_private_network=True,
                header_refs={"Authorization": "mcp/http/demo"},
                max_retries=0,
            )
            manager = create_mcp_connection_manager_from_configs(
                [config],
                secret_store=secret_store,
            )
            manager.start()
            try:
                self.assertTrue(manager.ready)
                self.assertEqual(
                    manager.statuses[0].protocol_version,
                    "2026-07-28",
                )
                self.assertEqual(
                    [tool.name for tool in manager.list_tools("current_http")],
                    ["http_echo", "http_zeta"],
                )
                self.assertEqual(server.list_requests, 2)
                manager.list_tools("current_http")
                self.assertEqual(server.list_requests, 2)
                self.assertTrue(manager.statuses[0].cache_hit)
                result = manager.call_tool(
                    "current_http",
                    "http_echo",
                    {"text": "over HTTP"},
                    call_id="http-call",
                )
                self.assertEqual(result["structuredContent"]["call_id"], "http-call")
                self.assertTrue(server.seen_authorization)
                serialized = json.dumps(manager.diagnostics(), sort_keys=True)
                self.assertNotIn("super-secret-token", serialized)
                self.assertNotIn("mcp/http/demo", serialized)
                self.assertNotIn("super-secret-token", repr(config))
                manager.refresh("current_http")
                self.assertEqual(server.list_requests, 4)
            finally:
                manager.close()

    def test_config_rejects_ssrf_header_injection_and_literal_credentials(self) -> None:
        with self.assertRaisesRegex(ValueError, "private HTTP address"):
            MCPServerConfig(
                name="ssrf",
                transport="streamable_http",
                url="https://127.0.0.1/mcp",
                allowed_hosts=("127.0.0.1",),
            )
        with self.assertRaisesRegex(ValueError, "header value"):
            MCPServerConfig(
                name="header",
                transport="streamable_http",
                url="https://example.com/mcp",
                allowed_hosts=("example.com",),
                headers={"X-Trace": "ok\r\nInjected: yes"},
            )
        with self.assertRaisesRegex(ValueError, "credential headers"):
            MCPServerConfig(
                name="credential",
                transport="streamable_http",
                url="https://example.com/mcp",
                allowed_hosts=("example.com",),
                headers={"Authorization": "Bearer plaintext"},
            )
        with self.assertRaisesRegex(ValueError, "sensitive environment"):
            MCPServerConfig(
                name="stdio_secret",
                command=sys.executable,
                env={"API_TOKEN": "plaintext"},
            )

        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "mcp.json"
            config_path.write_text(
                json.dumps(
                    {
                        "unsafe": {
                            "transport": "streamable_http",
                            "url": "http://127.0.0.1/mcp",
                            "allowed_hosts": ["127.0.0.1"],
                            "allow_insecure_http": "false",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must be a boolean"):
                load_mcp_server_configs(config_path)

    def test_mcp_annotations_are_mapped_by_central_permission_resolver(self) -> None:
        resolver = PermissionResolver()
        read_only = resolver.resolve_mcp_annotations(
            name="read",
            annotations={"readOnlyHint": True},
        )
        unknown = resolver.resolve_mcp_annotations(name="unknown", annotations=None)
        destructive = resolver.resolve_mcp_annotations(
            name="delete",
            annotations={"readOnlyHint": True, "destructiveHint": True},
        )

        self.assertEqual(read_only.permission_level, "read_only")
        self.assertFalse(read_only.requires_approval)
        self.assertEqual(unknown.permission_level, "external_side_effect")
        self.assertTrue(unknown.requires_approval)
        self.assertEqual(destructive.permission_level, "external_side_effect")
        self.assertEqual(destructive.permission_source, "mcp_annotation")

    def test_server_timeout_overrides_process_default_independently(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "mcp.json"
            config_path.write_text(
                json.dumps(
                    {
                        "mcp_servers": {
                            "custom": {
                                "command": sys.executable,
                                "request_timeout_seconds": 0.25,
                            },
                            "defaulted": {"command": sys.executable},
                        }
                    }
                ),
                encoding="utf-8",
            )
            configs = load_mcp_server_configs(
                config_path,
                default_request_timeout_seconds=7.0,
            )

        self.assertEqual(
            {config.name: config.request_timeout_seconds for config in configs},
            {"custom": 0.25, "defaulted": 7.0},
        )

    def test_optional_failure_does_not_block_startup_but_required_affects_readiness(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for required, expected_status, expected_ready in (
                (False, 200, True),
                (True, 503, False),
            ):
                config_path = root / f"mcp-{required}.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "mcp_servers": {
                                "broken": {
                                    "transport": "stdio",
                                    "command": "/definitely/missing/mcp-server",
                                    "required": required,
                                    "max_retries": 0,
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                app = create_app(
                    settings=Settings(
                        llm_provider="fake",
                        embedding_provider="local",
                        model_secret_backend="memory",
                        mcp_enabled=True,
                        mcp_config_path=str(config_path),
                    )
                )
                with TestClient(app) as client:
                    response = client.get("/api/v1/health")
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["ready"], expected_ready)
                self.assertEqual(response.json()["mcp_servers"][0]["name"], "broken")

    def test_frontend_registry_persists_secret_refs_and_hot_registers_tools(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_path = _write_current_stdio_server(root)
            config_path = root / "mcp.json"
            secret_value = "frontend-only-secret-value"
            app = create_app(
                settings=Settings(
                    llm_provider="fake",
                    embedding_provider="local",
                    model_secret_backend="memory",
                    mcp_enabled=True,
                    mcp_config_path=str(config_path),
                    workspace_allowed_roots=(str(root.resolve()),),
                )
            )
            payload = {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(server_path), "-", "-", "-"],
                "env_secrets": {"MCP_SERVER_TOKEN": secret_value},
                "required": False,
                "enabled": True,
                "max_retries": 0,
            }
            with TestClient(app, client=("127.0.0.1", 50000)) as client:
                created = client.put(
                    "/api/v1/mcp/servers/frontend_demo",
                    json=payload,
                )
                registry = client.get("/api/v1/mcp/servers")
                tested = client.post(
                    "/api/v1/mcp/servers/frontend_demo/test"
                )

                self.assertEqual(created.status_code, 200, created.text)
                self.assertEqual(created.json()["state"], "ready")
                self.assertIn(
                    "mcp.frontend_demo.alpha",
                    created.json()["registered_tools"],
                )
                self.assertEqual(registry.status_code, 200)
                self.assertTrue(registry.json()["runtime_enabled"])
                self.assertNotIn(secret_value, registry.text)
                self.assertEqual(tested.status_code, 200, tested.text)

                client.put(
                    "/api/v1/workspaces/mcp-demo",
                    json={"root_path": str(root)},
                ).raise_for_status()
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "tester"},
                ).json()["id"]
                capabilities = client.get(
                    "/api/v1/agent/composer-capabilities",
                    params={
                        "conversation_id": session_id,
                        "workspace_id": "mcp-demo",
                    },
                )
                self.assertEqual(capabilities.status_code, 200, capabilities.text)
                self.assertIn(
                    "mcp.frontend_demo.alpha",
                    [item["name"] for item in capabilities.json()["mcp_tools"]],
                )

                disabled = client.patch(
                    "/api/v1/mcp/servers/frontend_demo/enabled",
                    json={"enabled": False},
                )
                self.assertEqual(disabled.json()["state"], "disabled")
                self.assertIsNone(
                    app.state.tool_registry.get_spec("mcp.frontend_demo.alpha")
                )

                enabled = client.patch(
                    "/api/v1/mcp/servers/frontend_demo/enabled",
                    json={"enabled": True},
                )
                self.assertEqual(enabled.json()["state"], "ready")
                self.assertIsNotNone(
                    app.state.tool_registry.get_spec("mcp.frontend_demo.alpha")
                )
                persisted_before_delete = config_path.read_text(encoding="utf-8")
                self.assertNotIn(secret_value, persisted_before_delete)
                self.assertIn(
                    "keyring:mcp-server:frontend_demo:env:mcp_server_token",
                    persisted_before_delete,
                )

                deleted = client.delete(
                    "/api/v1/mcp/servers/frontend_demo"
                )
                self.assertEqual(deleted.status_code, 204)
                self.assertEqual(
                    client.get("/api/v1/mcp/servers").json()["servers"],
                    [],
                )
                frontend = client.get("/").text
                frontend_js = client.get("/static/app.js").text

            persisted = config_path.read_text(encoding="utf-8")
            self.assertNotIn(secret_value, persisted)
            self.assertNotIn("frontend_demo", persisted)
            self.assertIn('data-view-panel="tools"', frontend)
            self.assertIn('id="skill-form"', frontend)
            self.assertIn('id="mcp-server-form"', frontend)
            self.assertIn("/mcp/servers/${encodeURIComponent(name)}", frontend_js)
            self.assertIn("remove_env_secrets", frontend_js)

    def test_frontend_registry_writes_accept_only_trusted_gateway_local_mode(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                model_secret_backend="memory",
                auth_mode="trusted_header",
                gateway_trust_secret="test-gateway-secret",
                mcp_config_path=str(Path(temp_dir) / "mcp.json"),
            )
            with TestClient(
                create_app(settings=settings),
                client=("192.168.97.1", 50000),
            ) as client:
                oidc_gateway = client.put(
                    "/api/v1/mcp/servers/remote_forbidden",
                    headers={
                        "X-Authenticated-User": "remote-user",
                        "X-Gateway-Auth": "test-gateway-secret",
                    },
                    json={
                        "transport": "stdio",
                        "command": sys.executable,
                        "enabled": False,
                    },
                )
                forged_local = client.put(
                    "/api/v1/mcp/servers/forged_local",
                    headers={
                        "X-Authenticated-User": "attacker",
                        "X-Gateway-Auth": "wrong-secret",
                        "X-Gateway-Mode": "local",
                    },
                    json={
                        "transport": "stdio",
                        "command": sys.executable,
                        "enabled": False,
                    },
                )
                local_gateway = client.put(
                    "/api/v1/mcp/servers/local_allowed",
                    headers={
                        "X-Authenticated-User": "local-user",
                        "X-Gateway-Auth": "test-gateway-secret",
                        "X-Gateway-Mode": "local",
                    },
                    json={
                        "transport": "stdio",
                        "command": sys.executable,
                        "enabled": False,
                    },
                )
            persisted = Path(temp_dir) / "mcp.json"

            self.assertEqual(oidc_gateway.status_code, 403)
            self.assertEqual(forged_local.status_code, 401)
            self.assertEqual(local_gateway.status_code, 200)
            self.assertTrue(persisted.is_file())
            saved = persisted.read_text(encoding="utf-8")
            self.assertNotIn("remote_forbidden", saved)
            self.assertNotIn("forged_local", saved)
            self.assertIn("local_allowed", saved)

    def test_frontend_registry_registers_streamable_http_with_secret_header(self) -> None:
        server = _FakeHTTPMCPServer()
        original_async_client = httpx2.AsyncClient

        def http_client_factory(**kwargs: object) -> httpx2.AsyncClient:
            return original_async_client(
                **kwargs,
                transport=httpx2.MockTransport(server),
            )

        with TemporaryDirectory() as temp_dir, patch(
            "ai_agent_platform.integrations.mcp.transports.httpx2.AsyncClient",
            side_effect=http_client_factory,
        ):
            config_path = Path(temp_dir) / "mcp.json"
            app = create_app(
                settings=Settings(
                    llm_provider="fake",
                    embedding_provider="local",
                    model_secret_backend="memory",
                    mcp_enabled=True,
                    mcp_config_path=str(config_path),
                )
            )
            with TestClient(app, client=("127.0.0.1", 50000)) as client:
                response = client.put(
                    "/api/v1/mcp/servers/frontend_http",
                    json={
                        "transport": "streamable_http",
                        "url": "http://127.0.0.1:8765/mcp",
                        "allowed_hosts": ["127.0.0.1"],
                        "allow_insecure_http": True,
                        "allow_private_network": True,
                        "header_secrets": {
                            "Authorization": "Bearer frontend-http-secret"
                        },
                        "max_retries": 0,
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["state"], "ready")
            self.assertEqual(
                response.json()["registered_tools"],
                ["mcp.frontend_http.http_echo", "mcp.frontend_http.http_zeta"],
            )
            self.assertNotIn("frontend-http-secret", response.text)
            persisted = config_path.read_text(encoding="utf-8")
            self.assertNotIn("frontend-http-secret", persisted)
            self.assertIn(
                "keyring:mcp-server:frontend_http:header:authorization",
                persisted,
            )

    def test_frontend_registry_reports_missing_writable_config_path(self) -> None:
        settings = Settings(
            llm_provider="fake",
            embedding_provider="local",
            model_secret_backend="memory",
            mcp_config_path=None,
        )
        with TestClient(
            create_app(settings=settings),
            client=("127.0.0.1", 50000),
        ) as client:
            registry = client.get("/api/v1/mcp/servers")
            create = client.put(
                "/api/v1/mcp/servers/not_writable",
                json={"transport": "stdio", "command": sys.executable},
            )

        self.assertEqual(registry.status_code, 200)
        self.assertFalse(registry.json()["config_writable"])
        self.assertEqual(create.status_code, 409)

    def test_legacy_stdio_requires_explicit_compatibility_flag(self) -> None:
        with self.assertRaisesRegex(ValueError, "legacy_compatibility"):
            MCPServerConfig(
                name="legacy",
                transport="stdio_2025_06_18",
                command=sys.executable,
            )
        with self.assertRaisesRegex(ValueError, "legacy_compatibility"):
            MCPServerConfig(
                name="legacy_http",
                transport="legacy_sse",
                url="https://example.com/sse",
                allowed_hosts=("example.com",),
            )
        legacy_http = MCPServerConfig(
            name="legacy_http",
            transport="legacy_sse",
            url="https://example.com/sse",
            allowed_hosts=("example.com",),
            legacy_compatibility=True,
        )
        self.assertEqual(legacy_http.transport, "legacy_sse")

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_path = _write_current_stdio_server(root)
            manager = create_mcp_connection_manager_from_configs(
                [
                    MCPServerConfig(
                        name="legacy",
                        transport="stdio_2025_06_18",
                        legacy_compatibility=True,
                        command=sys.executable,
                        args=[str(server_path), "-", "-", "-"],
                        max_retries=0,
                    )
                ]
            )
            manager.start()
            try:
                self.assertTrue(manager.ready)
                self.assertEqual(
                    manager.statuses[0].protocol_version,
                    "2025-06-18",
                )
                result = manager.call_tool(
                    "legacy",
                    "alpha",
                    {"text": "legacy"},
                    call_id="legacy-call",
                )
                self.assertEqual(result["structuredContent"]["call_id"], "legacy-call")
            finally:
                manager.close()


def _stdio_config(
    server_path: Path,
    *,
    counter: Path | None = None,
    closed: Path | None = None,
    fail_once: Path | None = None,
    **overrides: object,
) -> MCPServerConfig:
    values: dict[str, object] = {
        "name": "current_stdio",
        "transport": "stdio",
        "command": sys.executable,
        "args": [
            str(server_path),
            str(counter or "-"),
            str(closed or "-"),
            str(fail_once or "-"),
        ],
        "connect_timeout_seconds": 2.0,
        "request_timeout_seconds": 1.0,
        "tool_cache_ttl_seconds": 5.0,
        "max_retries": 0,
    }
    values.update(overrides)
    return MCPServerConfig(**values)  # type: ignore[arg-type]


def _write_current_stdio_server(root: Path) -> Path:
    path = root / "fake_current_mcp.py"
    path.write_text(CURRENT_STDIO_SERVER, encoding="utf-8")
    return path


def _wait_for_path(path: Path, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return path.exists()


class _FakeHTTPMCPServer:
    def __init__(self) -> None:
        self.seen_authorization: list[str | None] = []
        self.list_requests = 0

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.seen_authorization.append(request.headers.get("Authorization"))
        message = json.loads(request.content)
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "server/discover":
            result = {
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {"listChanged": False}},
                "ttlMs": 5000,
                "cacheScope": "private",
                "resultType": "complete",
            }
        elif method == "tools/list":
            self.list_requests += 1
            cursor = params.get("cursor")
            tool = (
                {
                    "name": "http_zeta",
                    "description": "Last HTTP tool alphabetically.",
                    "inputSchema": {"type": "object"},
                    "annotations": {
                        "readOnlyHint": True,
                        "idempotentHint": True,
                    },
                }
                if cursor is None
                else {
                    "name": "http_echo",
                    "description": "Echo over Streamable HTTP.",
                    "inputSchema": {"type": "object"},
                    "annotations": {
                        "readOnlyHint": True,
                        "idempotentHint": True,
                    },
                }
            )
            result = {
                "tools": [tool],
                "ttlMs": 5000,
                "cacheScope": "private",
                "resultType": "complete",
            }
            if cursor is None:
                result["nextCursor"] = "http-page-2"
        elif method == "tools/call":
            meta = params.get("_meta") or {}
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": params.get("arguments", {}).get("text", ""),
                    }
                ],
                "structuredContent": {
                    "echo": params.get("arguments", {}).get("text", ""),
                    "call_id": meta.get("io.ai-agent-platform/call-id"),
                },
                "isError": False,
                "resultType": "complete",
            }
        else:
            return httpx2.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "unknown"},
                },
            )
        return httpx2.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": request_id, "result": result},
        )


CURRENT_STDIO_SERVER = r'''
import json
import os
from pathlib import Path
import sys
import time

PROTOCOL_VERSION = "2026-07-28"
counter_path, closed_path, fail_once_path = [Path(item) for item in sys.argv[1:4]]
if os.environ.get("MCP_SERVER_TOKEN"):
    sys.stderr.write(os.environ["MCP_SERVER_TOKEN"] + "\n")
    sys.stderr.flush()


def enabled(path):
    return str(path) != "-"


def write(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def count_list():
    if not enabled(counter_path):
        return
    current = int(counter_path.read_text() or "0") if counter_path.exists() else 0
    counter_path.write_text(str(current + 1))


try:
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "server/discover":
            write({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "supportedVersions": [PROTOCOL_VERSION],
                    "capabilities": {"tools": {"listChanged": False}},
                    "ttlMs": 5000,
                    "cacheScope": "private",
                    "resultType": "complete"
                }
            })
        elif method == "initialize":
            write({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "fake-legacy", "version": "1.0"}
                }
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            if enabled(fail_once_path) and not fail_once_path.exists():
                fail_once_path.write_text("failed")
                raise SystemExit(17)
            count_list()
            cursor = params.get("cursor")
            if cursor is None:
                tools = [
                    {
                        "name": "zeta",
                        "description": "Last tool alphabetically.",
                        "inputSchema": {"type": "object"},
                        "annotations": {"readOnlyHint": True, "idempotentHint": True}
                    },
                    {
                        "name": "slow",
                        "description": "Sleep for timeout tests.",
                        "inputSchema": {"type": "object"},
                        "annotations": {"readOnlyHint": True, "idempotentHint": True}
                    }
                ]
                next_cursor = "page-2"
            else:
                tools = [
                    {
                        "name": "alpha",
                        "description": "First tool alphabetically.",
                        "inputSchema": {"type": "object"},
                        "annotations": {"readOnlyHint": True, "idempotentHint": True}
                    },
                    {
                        "name": "probe_env",
                        "description": "Check inherited environment.",
                        "inputSchema": {"type": "object"},
                        "annotations": {"readOnlyHint": True, "idempotentHint": True}
                    }
                ]
                next_cursor = None
            result = {
                "tools": tools,
                "ttlMs": 5000,
                "cacheScope": "private",
                "resultType": "complete"
            }
            if next_cursor:
                result["nextCursor"] = next_cursor
            write({"jsonrpc": "2.0", "id": request_id, "result": result})
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            meta = params.get("_meta") or {}
            if name == "slow":
                time.sleep(float(arguments.get("delay", 0.2)))
                structured = {"slept": True}
            elif name == "probe_env":
                structured = {
                    "leaked": "OPENAI_API_KEY" in os.environ,
                    "secret_injected": "MCP_SERVER_TOKEN" in os.environ,
                }
            else:
                structured = {
                    "echo": arguments.get("text", ""),
                    "call_id": meta.get("io.ai-agent-platform/call-id") or request_id
                }
            write({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": str(structured)}],
                    "structuredContent": structured,
                    "isError": False,
                    "resultType": "complete"
                }
            })
        elif method == "notifications/cancelled":
            continue
        else:
            write({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "unknown method"}
            })
finally:
    if enabled(closed_path):
        closed_path.write_text("closed")
'''


if __name__ == "__main__":
    unittest.main()
