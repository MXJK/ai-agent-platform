import asyncio
import json
from pathlib import Path

import httpx

from ai_agent_platform.cogent.http_client import CogentAPIError, CogentHTTPClient
from ai_agent_platform.domain import QueryCommand, QueryParams


def test_http_client_uses_server_registry_session_workspace_and_sse():
    requests: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        requests.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/api/v1/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/api/v1/model-registry":
            return httpx.Response(
                200,
                json={
                    "connections": [
                        {
                            "provider": "openai",
                            "display_name": "OpenAI",
                            "credential_configured": True,
                            "status": "healthy",
                        }
                    ],
                    "models": [
                        {
                            "id": "model-1",
                            "provider": "openai",
                            "model": "gpt-test",
                            "enabled": True,
                        }
                    ],
                },
            )
        if path == "/api/v1/users/me/preferences":
            return httpx.Response(
                200,
                json={"default_workspace_id": "project"},
            )
        if path == "/api/v1/workspaces":
            return httpx.Response(
                200,
                json={
                    "workspaces": [
                        {
                            "id": "project",
                            "root_path": "/workspaces/project",
                            "available": True,
                        }
                    ]
                },
            )
        if path == "/api/v1/sessions" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "session-1",
                    "workspace_id": None,
                    "composer_mode": "chat",
                    "provider": None,
                    "model": None,
                },
            )
        if path == "/api/v1/sessions/session-1" and request.method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "id": "session-1",
                    "workspace_id": "project",
                    "composer_mode": "agent",
                    "provider": None,
                    "model": None,
                },
            )
        if path == "/api/v1/agent/composer-capabilities":
            return httpx.Response(
                200,
                json={
                    "commands": [{"name": "help", "description": "Help"}],
                    "skill_commands": [],
                    "mcp_tools": [],
                },
            )
        if path == "/api/v1/agent/runs" and request.method == "POST":
            return httpx.Response(202, json={"run_id": "run-1"})
        if path == "/api/v1/agent/runs/run-1/events/stream":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=(
                    'id: 1\nevent: answer_delta\ndata: {"sequence":1,"type":"answer_delta",'
                    '"status":"running","node":"model","summary":"answer",'
                    '"output":{"text":"shared reply"}}\n\n'
                    'id: 2\nevent: run_completed\ndata: {"sequence":2,'
                    '"type":"run_completed","status":"completed","node":null,'
                    '"summary":"done","output":{}}\n\n'
                ),
            )
        if path == "/api/v1/agent/runs/run-1":
            return httpx.Response(
                200,
                json={
                    "run_id": "run-1",
                    "status": "completed",
                    "checkpoint_id": None,
                    "pending_approval": None,
                    "error": None,
                    "result": {"answer": "shared reply"},
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    async def scenario():
        client = CogentHTTPClient(
            "http://cogent.test",
            user_id="user",
            transport=httpx.MockTransport(handler),
        )
        try:
            context = await client.prepare(cwd=Path("/tmp/project"))
            assert context.workspace_id == "project"
            assert context.workspace_root == "/workspaces/project"
            assert context.model_summary == "auto · 1 models"
            assert context.credential_summary == "1/1 credentials"
            assert "credential configured" in client.registry_text()
            capabilities = await client.composer_capabilities()
            assert capabilities["commands"][0]["name"] == "help"
            events = [
                event
                async for event in client.query(
                    QueryParams(
                        conversation_id=context.session_id,
                        workspace_id=context.workspace_id,
                        message="hello",
                        permission_mode="acceptEdits",
                    )
                )
            ]
            assert [event.type for event in events] == [
                "answer_delta",
                "run_completed",
            ]
            assert (await client.result("run-1")).output_dict()["answer"] == "shared reply"
        finally:
            await client.close()

    asyncio.run(scenario())
    run_request = next(item for item in requests if item[:2] == ("POST", "/api/v1/agent/runs"))
    assert run_request[2]["workspace_id"] == "project"
    assert run_request[2]["permission_mode"] == "acceptEdits"
    assert "api_key" not in json.dumps(requests)


def test_http_client_does_not_fall_back_when_server_has_no_workspace():
    def handler(request: httpx.Request) -> httpx.Response:
        payloads = {
            "/api/v1/health": {"status": "ok"},
            "/api/v1/model-registry": {"connections": [], "models": []},
            "/api/v1/users/me/preferences": {"default_workspace_id": None},
            "/api/v1/workspaces": {"workspaces": []},
        }
        return httpx.Response(200, json=payloads[request.url.path])

    async def scenario():
        client = CogentHTTPClient(transport=httpx.MockTransport(handler))
        try:
            try:
                await client.prepare(cwd=Path("/tmp/project"))
            except CogentAPIError as exc:
                assert "Add one in the web Workspace settings" in str(exc)
            else:
                raise AssertionError("missing Workspace must fail closed")
        finally:
            await client.close()

    asyncio.run(scenario())


def test_http_client_accepts_async_resume_and_compact_controls():
    requests: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        requests.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/api/v1/agent/runs/run-1":
            status = "waiting_approval" if len(requests) == 1 else "paused"
            return httpx.Response(
                200,
                json={
                    "run_id": "run-1",
                    "status": status,
                    "checkpoint_id": None,
                    "pending_approval": None,
                    "error": None,
                    "result": None,
                },
            )
        if path == "/api/v1/agent/runs/run-1/resume":
            return httpx.Response(202, json={"run_id": "run-1", "status": "running"})
        if path == "/api/v1/agent/runs/run-1/events/stream":
            return httpx.Response(200, text="")
        if path == "/api/v1/agent/runs/run-1/compact":
            return httpx.Response(202, json={"run_id": "run-1", "status": "paused"})
        raise AssertionError(f"unexpected request: {request.method} {path}")

    async def scenario():
        client = CogentHTTPClient(
            "http://cogent.test",
            transport=httpx.MockTransport(handler),
        )
        try:
            assert [event async for event in client.resume("run-1")] == []
            result = await client.control(
                "run-1",
                QueryCommand.COMPACT,
                message="retain recent tool pairs",
            )
            assert result.status == "paused"
        finally:
            await client.close()

    asyncio.run(scenario())
    assert (
        "POST",
        "/api/v1/agent/runs/run-1/resume",
        {"approved": True, "feedback": None},
    ) in requests
    assert (
        "POST",
        "/api/v1/agent/runs/run-1/compact",
        {"instruction": "retain recent tool pairs"},
    ) in requests
