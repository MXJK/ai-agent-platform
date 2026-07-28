from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest

from fastapi.testclient import TestClient

from ai_agent_platform.agents.coding_agent import create_coding_tool_registry
from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.mcp import (
    MCPServerConfig,
    MCPTool,
    MCPToolProvider,
    create_mcp_providers_from_configs,
    load_mcp_server_configs,
    normalize_mcp_tool_result,
)
from ai_agent_platform.integrations.tools import ToolCall, ToolExecutionContext
from ai_agent_platform.main import create_app


def wait_for_agent_run(
    client: TestClient,
    run_id: str,
    terminal_statuses: tuple[str, ...] = ("completed", "failed", "waiting_approval"),
) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/agent/runs/{run_id}")
        if response.status_code == 200:
            body = response.json()
            if body["status"] in terminal_statuses:
                return body
        time.sleep(0.02)
    raise AssertionError(f"agent run {run_id} did not reach {terminal_statuses}")


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                name="echo",
                description="Echo a payload for MCP integration tests.",
                input_schema={
                    "type": "object",
                    "required": ["text"],
                    "properties": {"text": {"type": "string"}},
                },
                output_schema={
                    "type": "object",
                    "properties": {"echo": {"type": "string"}},
                },
                permission_level="read_only",
            ),
            MCPTool(
                name="create_issue",
                description="Create a remote issue.",
                input_schema={
                    "type": "object",
                    "required": ["title"],
                    "properties": {"title": {"type": "string"}},
                },
                permission_level="external_side_effect",
                requires_approval=True,
            ),
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.calls.append((name, arguments))
        if name == "echo":
            return {"echo": arguments["text"]}
        if name == "create_issue":
            return {"issue_id": "ISSUE-1", "title": arguments["title"]}
        raise ValueError(f"unknown fake MCP tool: {name}")


class MCPProviderTests(unittest.TestCase):
    def test_normalizes_structured_content_and_mcp_tool_errors(self) -> None:
        self.assertEqual(
            normalize_mcp_tool_result(
                {
                    "structuredContent": {"issue_id": "ISSUE-1"},
                    "content": [{"type": "text", "text": "created"}],
                    "isError": False,
                }
            ),
            {"issue_id": "ISSUE-1"},
        )

        with self.assertRaisesRegex(Exception, "permission denied") as raised:
            normalize_mcp_tool_result(
                {
                    "content": [{"type": "text", "text": "permission denied"}],
                    "isError": True,
                }
            )
        self.assertEqual(raised.exception.code, "mcp_tool_error")

    def test_registers_and_executes_mcp_tools_through_tool_registry(self) -> None:
        client = FakeMCPClient()
        provider = MCPToolProvider(server_name="demo_server", client=client)
        registry = create_coding_tool_registry(mcp_providers=[provider])

        specs = {spec.name: spec for spec in registry.list_specs()}
        self.assertIn("mcp.demo_server.echo", specs)
        self.assertEqual(specs["mcp.demo_server.echo"].provider, "mcp:demo_server")
        self.assertEqual(
            specs["mcp.demo_server.echo"].permission_level,
            "read_only",
        )
        self.assertIn("mcp.demo_server.create_issue", specs)
        self.assertEqual(
            specs["mcp.demo_server.create_issue"].permission_level,
            "external_side_effect",
        )
        self.assertTrue(specs["mcp.demo_server.create_issue"].requires_approval)

        result = registry.execute(
            ToolCall(
                name="mcp.demo_server.echo",
                arguments={"text": "hello MCP"},
            ),
            context=ToolExecutionContext(
                conversation_id="sess_1",
                workspace_id="workspace_main",
                workspace_root=".",
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.result, {"echo": "hello MCP"})
        self.assertEqual(result.provider, "mcp:demo_server")
        self.assertEqual(result.permission_level, "read_only")
        self.assertIn("MCP tool demo_server.echo", result.risk_summary)
        self.assertEqual(result.arguments_summary, {"text": "hello MCP"})
        self.assertEqual(client.calls, [("echo", {"text": "hello MCP"})])

    def test_loads_mcp_server_configs_from_json(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "mcp.json"
            config_path.write_text(
                """
{
  "mcp_servers": {
    "github": {
      "transport": "stdio",
      "command": "github-mcp-server",
      "args": ["--read-only"],
      "env": {"GITHUB_TOKEN": "test-token"}
    },
    "disabled": {
      "enabled": false,
      "command": "skip-me"
    }
  }
}
""".strip(),
                encoding="utf-8",
            )

            configs = load_mcp_server_configs(config_path)

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].name, "github")
        self.assertEqual(configs[0].transport, "stdio")
        self.assertEqual(configs[0].command, "github-mcp-server")
        self.assertEqual(configs[0].args, ["--read-only"])
        self.assertEqual(configs[0].env, {"GITHUB_TOKEN": "test-token"})

    def test_stdio_mcp_client_discovers_and_calls_subprocess_tools(self) -> None:
        with TemporaryDirectory() as temp_dir:
            server_path = Path(temp_dir) / "fake_mcp_server.py"
            server_path.write_text(
                FAKE_MCP_SERVER,
                encoding="utf-8",
            )
            providers = create_mcp_providers_from_configs(
                [
                    MCPServerConfig(
                        name="stdio_demo",
                        transport="stdio",
                        command=sys.executable,
                        args=[str(server_path)],
                    )
                ],
                request_timeout_seconds=2.0,
            )
            try:
                registry = create_coding_tool_registry(mcp_providers=providers)

                specs = {spec.name: spec for spec in registry.list_specs()}
                self.assertIn("mcp.stdio_demo.echo", specs)
                self.assertEqual(
                    specs["mcp.stdio_demo.echo"].provider,
                    "mcp:stdio_demo",
                )

                result = registry.execute(
                    ToolCall(
                        name="mcp.stdio_demo.echo",
                        arguments={"text": "from subprocess"},
                    ),
                    context=ToolExecutionContext(
                        conversation_id="sess_1",
                        workspace_id="workspace_main",
                        workspace_root=".",
                    ),
                )

                self.assertTrue(result.ok)
                self.assertEqual(
                    result.result,
                    {"content": "from subprocess"},
                )
                self.assertEqual(result.provider, "mcp:stdio_demo")
            finally:
                for provider in providers:
                    provider.close()

    def test_create_app_loads_enabled_mcp_config_into_tool_registry(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_path = root / "fake_mcp_server.py"
            server_path.write_text(FAKE_MCP_SERVER, encoding="utf-8")
            config_path = root / "mcp.json"
            config_path.write_text(
                f"""
{{
  "mcp_servers": {{
    "startup_demo": {{
      "transport": "stdio",
      "command": "{sys.executable}",
      "args": ["{server_path}"]
    }}
  }}
}}
""".strip(),
                encoding="utf-8",
            )
            app = create_app(
                settings=Settings(
                    llm_provider="fake",
                    embedding_provider="local",
                    mcp_enabled=True,
                    mcp_config_path=str(config_path),
                    mcp_request_timeout_seconds=2.0,
                )
            )

            with TestClient(app) as client:
                response = client.get("/api/v1/health")
                specs = {spec.name: spec for spec in app.state.tool_registry.list_specs()}

            self.assertEqual(response.status_code, 200)
            self.assertIn("mcp.startup_demo.echo", specs)
            self.assertEqual(
                specs["mcp.startup_demo.echo"].provider,
                "mcp:startup_demo",
            )

    def test_agent_dynamically_executes_read_only_mcp_tool(self) -> None:
        with TemporaryDirectory() as temp_dir:
            app = _create_app_with_fake_mcp(temp_dir, server_name="dynamic_demo")

            with TestClient(app) as client:
                create_response = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "user_1"},
                )
                session_id = create_response.json()["id"]
                client.put(
                    "/api/v1/workspaces/workspace_main",
                    json={"root_path": "."},
                )
                run_response = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "message": "please echo hello MCP using the echo tool",
                        "workspace_id": "workspace_main",
                    },
                )
                run_status_body = wait_for_agent_run(
                    client,
                    run_response.json()["run_id"],
                    terminal_statuses=("completed", "failed"),
                )

            self.assertEqual(run_response.status_code, 202)
            body = run_status_body["result"]
            self.assertEqual(body["status"], "completed")
            tool_names = [tool_call["name"] for tool_call in body["tool_calls"]]
            self.assertIn("mcp.dynamic_demo.echo", tool_names)
            result_by_name = {item["name"]: item for item in body["tool_results"]}
            self.assertTrue(result_by_name["mcp.dynamic_demo.echo"]["ok"])
            self.assertEqual(
                result_by_name["mcp.dynamic_demo.echo"]["provider"],
                "mcp:dynamic_demo",
            )
            self.assertEqual(
                result_by_name["mcp.dynamic_demo.echo"]["permission_level"],
                "read_only",
            )

    def test_agent_routes_non_read_only_mcp_tool_to_approval(self) -> None:
        with TemporaryDirectory() as temp_dir:
            app = _create_app_with_fake_mcp(temp_dir, server_name="approval_demo")

            with TestClient(app) as client:
                create_response = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "user_1"},
                )
                session_id = create_response.json()["id"]
                client.put(
                    "/api/v1/workspaces/workspace_main",
                    json={"root_path": "."},
                )
                run_response = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "message": "please create issue for production outage",
                        "workspace_id": "workspace_main",
                    },
                )
                run_status_body = wait_for_agent_run(
                    client,
                    run_response.json()["run_id"],
                )
                run_body = run_status_body["result"]
                resume_response = client.post(
                    f"/api/v1/agent/runs/{run_body['run_id']}/resume",
                    json={"approved": True, "feedback": "approved for test"},
                )
                resume_status_body = wait_for_agent_run(
                    client,
                    run_body["run_id"],
                    terminal_statuses=("completed", "failed"),
                )

            self.assertEqual(run_response.status_code, 202)
            body = run_status_body
            self.assertEqual(body["status"], "waiting_approval")
            self.assertIn(
                "mcp.approval_demo.create_issue",
                body["pending_approval"]["planned_tools"],
            )
            approval_names = [
                item["name"]
                for item in body["pending_approval"]["approval_required_tools"]
            ]
            self.assertIn("mcp.approval_demo.create_issue", approval_names)
            approval_item = next(
                item
                for item in body["pending_approval"]["approval_required_tools"]
                if item["name"] == "mcp.approval_demo.create_issue"
            )
            self.assertEqual(
                approval_item["permission_level"],
                "external_side_effect",
            )
            self.assertIn("MCP tool approval_demo.create_issue", approval_item["risk_summary"])
            self.assertIn("title", approval_item["arguments_summary"])
            self.assertEqual(resume_response.status_code, 202)
            resume_body = resume_status_body["result"]
            self.assertEqual(resume_body["status"], "completed")
            result_by_name = {item["name"]: item for item in resume_body["tool_results"]}
            self.assertTrue(result_by_name["mcp.approval_demo.create_issue"]["ok"])
            self.assertEqual(
                result_by_name["mcp.approval_demo.create_issue"]["provider"],
                "mcp:approval_demo",
            )

FAKE_MCP_SERVER = r'''
import json
import sys

PROTOCOL_VERSION = "2025-06-18"


def write(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "fake-mcp", "version": "0.1.0"},
                },
            }
        )
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo text.",
                            "inputSchema": {
                                "type": "object",
                                "required": ["text"],
                                "properties": {"text": {"type": "string"}},
                            },
                            "annotations": {"readOnlyHint": True},
                        },
                        {
                            "name": "create_issue",
                            "description": "Create issue tickets in an external tracker.",
                            "inputSchema": {
                                "type": "object",
                                "required": ["title"],
                                "properties": {"title": {"type": "string"}},
                            },
                            "annotations": {"destructiveHint": True},
                        }
                    ]
                },
            }
        )
    elif method == "tools/call":
        params = message.get("params", {})
        name = params.get("name")
        if name == "echo":
            text = params.get("arguments", {}).get("text", "")
            write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": False,
                    },
                }
            )
        elif name == "create_issue":
            title = params.get("arguments", {}).get("title", "")
            write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "structuredContent": {"issue_id": "ISSUE-1", "title": title},
                        "content": [{"type": "text", "text": title}],
                        "isError": False,
                    },
                }
            )
        else:
            write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"unknown tool: {name}"},
                }
            )
    else:
        write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"unknown method: {method}"},
            }
        )
'''


def _create_app_with_fake_mcp(temp_dir: str, *, server_name: str):
    root = Path(temp_dir)
    server_path = root / "fake_mcp_server.py"
    server_path.write_text(FAKE_MCP_SERVER, encoding="utf-8")
    config_path = root / "mcp.json"
    config_path.write_text(
        f"""
{{
  "mcp_servers": {{
    "{server_name}": {{
      "transport": "stdio",
      "command": "{sys.executable}",
      "args": ["{server_path}"]
    }}
  }}
}}
""".strip(),
        encoding="utf-8",
    )
    return create_app(
        settings=Settings(
            llm_provider="fake",
            embedding_provider="local",
            mcp_enabled=True,
            mcp_config_path=str(config_path),
            mcp_request_timeout_seconds=2.0,
        )
    )


if __name__ == "__main__":
    unittest.main()
