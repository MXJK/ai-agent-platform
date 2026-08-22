from __future__ import annotations

import argparse
import asyncio
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from ai_agent_platform.api import entrypoint as api_entrypoint
from ai_agent_platform.cli import (
    CliApplication,
    CliInterruptController,
    _run_mode,
    build_parser,
    main as cli_main,
    validate_cli_environment,
)
from ai_agent_platform.core import ResolvedConfig, Settings
from ai_agent_platform.domain import AgentEvent, QueryCommand, QueryParams, QueryResult
from ai_agent_platform.runtime import RuntimeContainer, build_runtime
from ai_agent_platform.sdk import AgentSDK
from ai_agent_platform.services import AgentEventEncoder


_EVENT_SCHEMA = {
    "sequence",
    "run_id",
    "status",
    "type",
    "summary",
    "output",
    "node",
}


class ShellAdapterContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_query_resume_and_control_keep_domain_contracts(self) -> None:
        query_events = _events("run_query", "completed")
        resumed_events = _events("run_resume", "completed")

        class StubQueryService:
            event_encoder = AgentEventEncoder()

            def __init__(self) -> None:
                self.commands: list[QueryCommand] = []

            def query(self, _params, *, cursor=0):
                self.query_cursor = cursor
                return _iterator(query_events)

            def get_result(self, run_id, *, actor_user_id=None):
                if run_id == "run_resume" and not self.commands:
                    return QueryResult(
                        run_id=run_id,
                        status="waiting_input",
                        cursor=7,
                        resumable=True,
                    )
                status = "cancelled" if QueryCommand.CANCEL in self.commands else "completed"
                return QueryResult(
                    run_id=run_id,
                    status=status,
                    cursor=9,
                )

            def execute(self, command, **_kwargs):
                resolved = QueryCommand(command)
                self.commands.append(resolved)
                return SimpleNamespace(run_id=_kwargs["run_id"])

            def iter_events(self, run_id, *, actor_user_id=None, cursor=0):
                self.resume_cursor = cursor
                return _iterator(resumed_events)

        service = StubQueryService()
        runtime = RuntimeContainer(settings=Settings(), role="cli")
        runtime.query_service = service  # type: ignore[assignment]
        sdk = AgentSDK(runtime)

        queried = [
            event
            async for event in sdk.query(
                QueryParams(
                    conversation_id="session",
                    message="hello",
                    workspace_id="workspace",
                )
            )
        ]
        resumed = [event async for event in sdk.resume("run_resume")]
        controlled = sdk.control("run_resume", QueryCommand.CANCEL)

        self.assertTrue(all(isinstance(item, AgentEvent) for item in queried))
        self.assertTrue(all(isinstance(item, AgentEvent) for item in resumed))
        self.assertIsInstance(controlled, QueryResult)
        self.assertEqual(service.resume_cursor, 7)
        self.assertEqual(
            service.commands,
            [QueryCommand.CONTINUE, QueryCommand.CANCEL],
        )

    async def test_ctrl_c_cancels_only_the_active_run(self) -> None:
        cancelled = asyncio.Event()

        class StubQueryService:
            event_encoder = AgentEventEncoder()

            def execute(self, command, **kwargs):
                self.command = QueryCommand(command)
                cancelled.set()
                return SimpleNamespace(run_id=kwargs["run_id"])

            def get_result(self, run_id, *, actor_user_id=None):
                return QueryResult(run_id=run_id, status="cancelled", cursor=2)

        service = StubQueryService()
        runtime = RuntimeContainer(settings=Settings(), role="cli")
        runtime.query_service = service  # type: ignore[assignment]
        output = io.StringIO()
        errors = io.StringIO()
        controller = CliInterruptController(asyncio.get_running_loop())

        async def slow_events():
            yield _event(1, "run_interrupt", "queued", "run_queued")
            await cancelled.wait()
            yield _event(2, "run_interrupt", "cancelled", "run_cancelled")

        with TemporaryDirectory() as temp_dir:
            application = CliApplication(
                runtime,
                workspace_root=temp_dir,
                workspace_id="workspace",
                output_stream=output,
                error_stream=errors,
                interrupt=controller,
            )
            streaming = asyncio.create_task(application._stream(slow_events()))
            while controller.active_run_id is None:
                await asyncio.sleep(0)
            controller.request_interrupt()
            result, interrupted = await streaming

        self.assertTrue(interrupted)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(service.command, QueryCommand.CANCEL)
        self.assertIn("cancelling active Run", errors.getvalue())

    async def test_repl_exposes_required_commands_and_explicit_exit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            runtime = build_runtime(_settings(root), role="cli")
            output = io.StringIO()
            try:
                application = CliApplication(
                    runtime,
                    workspace_root=root,
                    workspace_id="workspace",
                    input_stream=io.StringIO(
                        "/skills\n/tools\n/mcp\n/permissions\n/resume\n/exit\n"
                    ),
                    output_stream=output,
                    error_stream=io.StringIO(),
                )
                self.assertEqual(await application.run_repl(), 0)
            finally:
                runtime.close()

        kinds = {
            payload["kind"]
            for payload in _json_objects(output.getvalue())
            if "kind" in payload
        }
        self.assertEqual(
            kinds,
            {"skills", "tools", "mcp", "permissions", "error"},
        )
        diagnostics = {
            item["kind"]: item
            for item in _json_objects(output.getvalue())
            if "kind" in item
        }
        self.assertEqual(diagnostics["skills"]["workspace_id"], "workspace")
        self.assertEqual(diagnostics["skills"]["agent"], "coding")
        self.assertEqual(diagnostics["tools"]["workspace_id"], "workspace")
        self.assertIn("effective_pool_tools", diagnostics["mcp"])
        self.assertEqual(
            diagnostics["permissions"]["workspace_role"],
            "admin",
        )
        self.assertIn("effective_denies", diagnostics["permissions"])

    async def test_global_skill_command_submits_query_and_freezes_invocation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            _write_project_skill(
                root,
                name="review",
                command="review",
                required_tools=("repo.read_file",),
            )
            _write_project_config(
                root,
                enabled_skills=("user:review",),
            )
            runtime = build_runtime(_settings(root), role="cli")
            output = io.StringIO()
            try:
                application = CliApplication(
                    runtime,
                    workspace_root=root,
                    workspace_id="workspace",
                    input_stream=io.StringIO("/review app.py\n/exit\n"),
                    output_stream=output,
                    error_stream=io.StringIO(),
                )
                self.assertEqual(await application.run_repl(), 0)
                record = runtime.coding_agent_runtime.get_run(
                    application.last_run_id
                )
            finally:
                runtime.close()

        self.assertEqual(record.status, "completed")
        invocation = record.context_snapshot.metadata.entrypoint_metadata[
            "skill_invocation"
        ]
        self.assertEqual(invocation["skill_name"], "user:review")
        self.assertEqual(invocation["arguments"], ["app.py"])
        self.assertIn(
            "skill_instruction",
            {item.kind for item in record.context_snapshot.instructions.sources},
        )
        self.assertNotIn(
            "review",
            record.context_snapshot.tools.enabled_tools,
        )

    async def test_compact_command_forces_one_native_reduction_with_instruction(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            runtime = build_runtime(_settings(root), role="cli")
            errors = io.StringIO()
            try:
                application = CliApplication(
                    runtime,
                    workspace_root=root,
                    workspace_id="workspace",
                    input_stream=io.StringIO(
                        "/compact preserve deployment decisions\n/exit\n"
                    ),
                    output_stream=io.StringIO(),
                    error_stream=errors,
                )
                self.assertEqual(await application.run_repl(), 0)
                record = runtime.coding_agent_runtime.get_run(
                    application.last_run_id
                )
            finally:
                runtime.close()

        self.assertIn("/compact", errors.getvalue())
        self.assertTrue(
            record.context_snapshot.metadata.entrypoint_metadata[
                "force_context_compaction"
            ]
        )
        self.assertIn(
            "preserve deployment decisions",
            record.context_snapshot.session.user_message,
        )
        self.assertEqual(record.status, "completed")

    async def test_disabled_and_missing_tool_skill_commands_are_rejected_stably(self) -> None:
        for enabled_skills, required_tools, expected_code in (
            ((), (), "skill_disabled"),
            (
                ("user:review",),
                ("missing.tool",),
                "skill_required_tools_unavailable",
            ),
        ):
            with self.subTest(expected_code=expected_code), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
                _write_project_skill(
                    root,
                    name="review",
                    command="review",
                    required_tools=required_tools,
                )
                _write_project_config(root, enabled_skills=enabled_skills)
                runtime = build_runtime(
                    _settings(root, enabled_skills=enabled_skills),
                    role="cli",
                )
                output = io.StringIO()
                try:
                    application = CliApplication(
                        runtime,
                        workspace_root=root,
                        workspace_id="workspace",
                        input_stream=io.StringIO("/review app.py\n/exit\n"),
                        output_stream=output,
                        error_stream=io.StringIO(),
                    )
                    self.assertEqual(await application.run_repl(), 0)
                finally:
                    runtime.close()
                diagnostics = [
                    item
                    for item in _json_objects(output.getvalue())
                    if item.get("kind") == "error"
                ]
                self.assertEqual(diagnostics[-1]["code"], expected_code)
                self.assertIsNone(application.last_run_id)


class ShellAdapterE2ETests(unittest.TestCase):
    def test_web_print_repl_and_sdk_share_fake_run_status_and_event_schema(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            message = "Explain app.py"

            web_status, web_events = _run_web(root, message)
            print_status, print_events = _run_print(root, message)
            repl_status, repl_events = _run_repl(root, message)
            sdk_status, sdk_events = _run_sdk(root, message)

        self.assertEqual(
            {web_status, print_status, repl_status, sdk_status},
            {"completed"},
        )
        for events in (web_events, print_events, repl_events, sdk_events):
            self.assertTrue(events)
            self.assertTrue(all(set(item) == _EVENT_SCHEMA for item in events))
            self.assertEqual(events[-1]["type"], "run_completed")
            self.assertEqual(events[-1]["status"], "completed")
        self.assertEqual(
            [item["type"] for item in web_events],
            [item["type"] for item in print_events],
        )
        self.assertEqual(
            [item["type"] for item in web_events],
            [item["type"] for item in repl_events],
        )
        self.assertEqual(
            [item["type"] for item in web_events],
            [item["type"] for item in sdk_events],
        )

    def test_console_parser_and_api_entrypoint_own_process_concerns(self) -> None:
        args = build_parser().parse_args(
            ["--workspace", ".", "print", "hello", "world"]
        )
        self.assertEqual(args.mode, "print")
        self.assertEqual(args.message, ["hello", "world"])
        pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn('ai-agent = "ai_agent_platform.cli:main"', pyproject)
        self.assertIn(
            'ai-agent-platform = "ai_agent_platform.cli:main"',
            pyproject,
        )
        self.assertIn(
            'ai-agent-api = "ai_agent_platform.api.entrypoint:main"',
            pyproject,
        )

        resolved = ResolvedConfig.from_settings(
            Settings(auth_mode="disabled", model_secret_backend="memory")
        )
        with (
            patch.object(
                api_entrypoint.ConfigResolver,
                "from_default_locations",
            ) as resolver,
            patch.object(api_entrypoint.uvicorn, "run") as run,
        ):
            resolver.return_value.resolve_process.return_value = resolved
            with self.assertRaisesRegex(ValueError, "loopback"):
                api_entrypoint.main(["--host", "0.0.0.0"])
        run.assert_not_called()

    def test_cli_main_validates_environment_checkpoints_and_closes_runtime(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            resolved = ResolvedConfig.from_settings(
                Settings(
                    workspace_allowed_roots=(str(root),),
                    model_secret_backend="memory",
                    auth_mode="trusted_header",
                    gateway_trust_secret="test-only-secret",
                    live_workspace_writes_enabled=True,
                )
            )
            runtime = RuntimeContainer(settings=resolved.settings, role="cli")
            stderr = io.StringIO()
            with (
                patch("ai_agent_platform.cli.ConfigResolver") as resolver,
                patch("ai_agent_platform.cli.build_runtime", return_value=runtime) as build,
                patch(
                    "ai_agent_platform.cli._run_mode",
                    new=AsyncMock(return_value=0),
                ) as run_mode,
                patch("sys.stderr", stderr),
            ):
                resolver.from_default_locations.return_value.resolve_process.return_value = (
                    resolved
                )
                exit_code = cli_main(
                    ["--workspace", str(root), "print", "hello"]
                )

        self.assertEqual(exit_code, 0)
        build.assert_called_once_with(resolved, role="cli")
        self.assertTrue(runtime.closed)
        self.assertEqual(
            [item.name for item in runtime.startup_timeline],
            ["cli_ready"],
        )
        self.assertTrue(run_mode.await_args.kwargs["install_sigint"])
        self.assertIn("live workspace writes are enabled", stderr.getvalue())

    def test_cli_safe_environment_rejects_paths_outside_process_allowlist(self) -> None:
        with TemporaryDirectory() as allowed_dir, TemporaryDirectory() as other_dir:
            resolved = ResolvedConfig.from_settings(
                Settings(workspace_allowed_roots=(allowed_dir,))
            )
            with self.assertRaisesRegex(ValueError, "WORKSPACE_ALLOWED_ROOTS"):
                validate_cli_environment(
                    resolved,
                    workspace=other_dir,
                    workspace_id="workspace",
                )

    def test_api_entrypoint_closes_runtime_even_if_uvicorn_returns_early(self) -> None:
        resolved = ResolvedConfig.from_settings(
            Settings(auth_mode="disabled", model_secret_backend="memory")
        )
        runtime = SimpleNamespace(close=Mock())
        application = SimpleNamespace(state=SimpleNamespace(runtime=runtime))
        with (
            patch.object(
                api_entrypoint.ConfigResolver,
                "from_default_locations",
            ) as resolver,
            patch.object(
                api_entrypoint,
                "create_app",
                return_value=application,
            ),
            patch.object(api_entrypoint.uvicorn, "run") as run,
        ):
            resolver.return_value.resolve_process.return_value = resolved
            self.assertEqual(
                api_entrypoint.main(["--host", "127.0.0.1", "--port", "9000"]),
                0,
            )

        run.assert_called_once_with(
            application,
            host="127.0.0.1",
            port=9000,
        )
        runtime.close.assert_called_once_with()


def _settings(
    root: Path,
    *,
    enabled_skills: tuple[str, ...] | None = None,
) -> Settings:
    return Settings(
        llm_provider="fake",
        model_secret_backend="memory",
        rag_reranker_provider="none",
        workspace_allowed_roots=(str(root.resolve()),),
        background_task_workers=1,
        conversation_summary_enabled=False,
        skills_directory_path=str(root / ".global-skills"),
        skills_enabled=True,
        enabled_skills=enabled_skills,
    )


def _write_project_config(
    root: Path,
    *,
    enabled_skills: tuple[str, ...],
) -> None:
    path = root / ".ai-agent-platform" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "project_session": {
                    "skills_enabled": True,
                    "enabled_skills": list(enabled_skills),
                }
            }
        ),
        encoding="utf-8",
    )


def _write_project_skill(
    root: Path,
    *,
    name: str,
    command: str,
    required_tools: tuple[str, ...],
) -> None:
    path = root / ".global-skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "---\n"
            f"name: {name}\n"
            f"description: {name} description\n"
            "agents: [coding]\n"
            "modes: [default]\n"
            "context_budget: 1000\n"
            f"tools: [{', '.join(required_tools)}]\n"
            "command:\n"
            f"  name: {command}\n"
            "---\n"
            "Review the requested files using only the effective tool pool.\n"
        ),
        encoding="utf-8",
    )


def _run_web(root: Path, message: str) -> tuple[str, list[dict[str, object]]]:
    with TestClient(api_entrypoint.create_app(settings=_settings(root))) as client:
        workspace = client.put(
            "/api/v1/workspaces/workspace",
            json={"root_path": str(root)},
        )
        assert workspace.status_code == 200, workspace.text
        session = client.post(
            "/api/v1/sessions",
            json={"user_id": "user"},
        )
        assert session.status_code == 201, session.text
        started = client.post(
            "/api/v1/agent/runs",
            json={
                "conversation_id": session.json()["id"],
                "message": message,
                "workspace_id": "workspace",
            },
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["run_id"]
        record = started.json()
        deadline = time.monotonic() + 10
        while record["status"] not in {"completed", "partial", "blocked", "failed"}:
            if time.monotonic() >= deadline:
                raise AssertionError("Web fake-provider Run did not finish")
            time.sleep(0.01)
            record = client.get(f"/api/v1/agent/runs/{run_id}").json()
        response = client.get(f"/api/v1/agent/runs/{run_id}/events")
        assert response.status_code == 200, response.text
        events = [
            {**item, "run_id": run_id}
            for item in response.json()["events"]
        ]
        return record["status"], events


def _run_print(root: Path, message: str) -> tuple[str, list[dict[str, object]]]:
    runtime = build_runtime(_settings(root), role="cli")
    output = io.StringIO()
    try:
        args = argparse.Namespace(
            mode="print",
            message=[message],
            workspace_id="workspace",
            user="user",
            session_id=None,
        )
        exit_code = asyncio.run(
            _run_mode(
                args,
                runtime,
                workspace_root=root,
                output_stream=output,
                error_stream=io.StringIO(),
            )
        )
        assert exit_code == 0
        events = _json_objects(output.getvalue())
        return str(events[-1]["status"]), events
    finally:
        runtime.close()


def _run_repl(root: Path, message: str) -> tuple[str, list[dict[str, object]]]:
    runtime = build_runtime(_settings(root), role="cli")
    output = io.StringIO()
    try:
        args = argparse.Namespace(
            mode="repl",
            workspace_id="workspace",
            user="user",
            session_id=None,
        )
        exit_code = asyncio.run(
            _run_mode(
                args,
                runtime,
                workspace_root=root,
                input_stream=io.StringIO(f"{message}\n/exit\n"),
                output_stream=output,
                error_stream=io.StringIO(),
            )
        )
        assert exit_code == 0
        events = [
            item
            for item in _json_objects(output.getvalue())
            if "sequence" in item
        ]
        return str(events[-1]["status"]), events
    finally:
        runtime.close()


def _run_sdk(root: Path, message: str) -> tuple[str, list[dict[str, object]]]:
    runtime = build_runtime(_settings(root), role="cli")
    try:
        runtime.workspace_service.register(  # type: ignore[union-attr]
            workspace_id="workspace",
            root_path=str(root),
        )
        runtime.project_memory_service.ensure_workspace_admin(  # type: ignore[union-attr]
            workspace_id="workspace",
            actor_user_id="user",
        )
        session = runtime.session_service.create_session("user")  # type: ignore[union-attr]
        sdk = AgentSDK(runtime)
        events = asyncio.run(
            _collect(
                sdk.query(
                    QueryParams(
                        conversation_id=session.id,
                        message=message,
                        workspace_id="workspace",
                        cwd=str(root),
                        entrypoint="sdk",
                        entrypoint_metadata={"client": "e2e"},
                    )
                )
            )
        )
        result = sdk.result(events[-1].run_id)
        encoder = runtime.query_service.event_encoder  # type: ignore[union-attr]
        return result.status, [encoder.to_payload(item) for item in events]
    finally:
        runtime.close()


async def _collect(events):
    return [event async for event in events]


def _json_objects(value: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in value.splitlines():
        start = line.find("{")
        if start < 0:
            continue
        payloads.append(json.loads(line[start:]))
    return payloads


def _event(sequence: int, run_id: str, status: str, event_type: str) -> AgentEvent:
    return AgentEvent(
        sequence=sequence,
        run_id=run_id,
        status=status,
        type=event_type,
        summary=event_type,
    )


def _events(run_id: str, status: str) -> list[AgentEvent]:
    return [
        _event(1, run_id, "queued", "run_queued"),
        _event(2, run_id, status, f"run_{status}"),
    ]


async def _iterator(events: list[AgentEvent]):
    for event in events:
        yield event


if __name__ == "__main__":
    unittest.main()
