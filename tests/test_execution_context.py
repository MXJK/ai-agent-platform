from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_agent_platform.agents import CodingAgentRuntime
from ai_agent_platform.agents.coding import AgentRunRecord, InMemoryAgentRunStore
from ai_agent_platform.agents.coding.context import InstructionSecurityError
from ai_agent_platform.core import ConfigResolver, ConfigSecurityError
from ai_agent_platform.domain import Session, RunContextSnapshot
from ai_agent_platform.integrations.tools import ToolRegistry
from ai_agent_platform.model_registry import ModelSelection
from ai_agent_platform.repositories import (
    InMemoryWorkspaceRepository,
    PostgresAgentRunRepository,
)
from ai_agent_platform.schemas import AgentRunRequest
from ai_agent_platform.services import (
    AgentRunService,
    ExecutionContextFactory,
    WorkspaceNotFoundError,
    WorkspaceService,
)


class _SessionService:
    def __init__(self, *, user_id: str = "alice") -> None:
        self.session = Session(
            id="session_1",
            user_id=user_id,
            created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        self.history = [{"role": "user", "content": "frozen history"}]
        self.summary = None

    def get_session(self, *, session_id: str):
        if session_id != self.session.id:
            raise KeyError(session_id)
        return self.session

    def resolve_execution_config(self, **values: object):
        return {
            "provider": values.get("provider"),
            "model": values.get("model"),
            "thinking_level": values.get("thinking_level"),
            "workspace_id": values.get("workspace_id"),
        }

    def build_agent_context(self, **_: object):
        return [dict(item) for item in self.history]

    def get_conversation_summary(self, _: str):
        return self.summary

    def add_message(self, **_: object):
        return []

    def list_messages(self, **_: object):
        return []


class _Authorizer:
    def __init__(self, roles: dict[tuple[str, str], str]) -> None:
        self.roles = roles

    def authorize(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        required_role: str,
    ) -> None:
        del required_role
        if (workspace_id, actor_user_id) not in self.roles:
            raise PermissionError("workspace access denied")

    def role_for(self, *, workspace_id: str, actor_user_id: str) -> str | None:
        return self.roles.get((workspace_id, actor_user_id))


class ExecutionContextFactoryTests(unittest.TestCase):
    def test_api_accepts_additional_workspace_ids_not_raw_paths(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "registered Workspace IDs",
        ):
            AgentRunRequest(
                conversation_id="session_1",
                message="inspect",
                workspace_id="main",
                additional_workspace_ids=["/tmp/unregistered"],
            )

    def test_snapshot_is_json_round_trippable_immutable_and_redacted(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "AGENTS.md").write_text("root rules", encoding="utf-8")
            child = root / "src"
            child.mkdir()
            (child / "CLAUDE.md").write_text("compat rules", encoding="utf-8")
            (child / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            service = _workspace_service(root, ("main", root))
            sessions = _SessionService()
            sessions.summary = SimpleNamespace(
                content="frozen summary",
                summarized_message_count=4,
                through_message_id="msg_4",
                version=2,
                source_chars=512,
                updated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
            )
            factory = ExecutionContextFactory(
                session_service=sessions,
                workspace_service=service,
                workspace_authorizer=_Authorizer({("main", "alice"): "editor"}),
                auth_mode="trusted_header",
                entrypoint_type="api",
                config_snapshot={
                    "openai_api_key": "sk-secret-value",
                    "database_url": "postgresql://alice:password@db/app",
                    "runtime": {"agent_max_context_chars": 1000},
                },
            )
            snapshot = factory.create(
                conversation_id="session_1",
                user_message="inspect src/app.py",
                workspace_id="main",
                actor_user_id="alice",
                focus_files=["src/app.py"],
                cwd="src",
                model_selection=ModelSelection(
                    mode="manual",
                    preferred_provider="openai",
                    preferred_model="gpt-test",
                ),
                run_id="run_fixed",
            )

            payload = snapshot.to_dict()
            encoded = json.dumps(payload)
            self.assertNotIn("sk-secret-value", encoded)
            self.assertNotIn("alice:password", encoded)
            self.assertEqual(
                [item.path for item in snapshot.instructions.sources],
                ["AGENTS.md", "src/CLAUDE.md"],
            )
            self.assertEqual(snapshot.identity.workspace_role, "editor")
            self.assertEqual(snapshot.session.summary.content, "frozen summary")
            self.assertEqual(snapshot.project.cwd, str(child.resolve()))
            self.assertEqual(RunContextSnapshot.from_dict(payload), snapshot)
            with self.assertRaises(FrozenInstanceError):
                snapshot.identity.actor_user_id = "mallory"  # type: ignore[misc]
            payload["identity"]["actor_user_id"] = "mallory"  # type: ignore[index]
            self.assertEqual(snapshot.identity.actor_user_id, "alice")
            config_copy = snapshot.project.project_config
            config_copy["new"] = "value"
            self.assertNotIn("new", snapshot.project.project_config)

    def test_agents_priority_remains_above_claude_compatibility(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "AGENTS.md").write_text("agents", encoding="utf-8")
            (root / "CLAUDE.md").write_text("claude", encoding="utf-8")
            child = root / "src"
            child.mkdir()
            (child / "AGENTS.md").write_text("regular", encoding="utf-8")
            (child / "AGENTS.override.md").write_text("override", encoding="utf-8")
            (child / "CLAUDE.md").write_text("child claude", encoding="utf-8")
            (child / "app.py").touch()
            factory = _factory(root)
            snapshot = factory.create(
                conversation_id="session_1",
                user_message="inspect",
                workspace_id="main",
                focus_files=["src/app.py"],
                model_selection=ModelSelection(),
            )
            self.assertEqual(
                [item.path for item in snapshot.instructions.sources],
                ["AGENTS.md", "src/AGENTS.override.md"],
            )

    def test_two_workspaces_freeze_isolated_config_instructions_and_tools(self) -> None:
        with TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            alpha = parent / "alpha"
            beta = parent / "beta"
            for root, tool, instruction, elapsed in (
                (alpha, "tool.alpha", "alpha config", 101),
                (beta, "tool.beta", "beta config", 202),
            ):
                (root / ".ai-agent-platform").mkdir(parents=True)
                (root / "AGENTS.md").write_text(
                    f"{root.name} agents",
                    encoding="utf-8",
                )
                (root / ".ai-agent-platform" / "config.json").write_text(
                    json.dumps(
                        {
                            "runtime": {"agent_max_elapsed_seconds": elapsed},
                            "project_session": {
                                "enabled_tools": [tool],
                                "project_instructions": [instruction],
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            registry = _tool_registry("tool.alpha", "tool.beta")
            process = ConfigResolver(
                user_config={
                    "process_security": {
                        "tool_allowlist": ["tool.alpha", "tool.beta"]
                    }
                },
                env={},
            ).resolve_process()
            factory = ExecutionContextFactory(
                session_service=_SessionService(),
                workspace_service=_workspace_service(
                    parent,
                    ("alpha", alpha),
                    ("beta", beta),
                ),
                auth_mode="disabled",
                process_config=process,
                tool_registry=registry,
            )

            alpha_snapshot = factory.create(
                conversation_id="session_1",
                user_message="inspect alpha",
                workspace_id="alpha",
                model_selection=ModelSelection(),
            )
            beta_snapshot = factory.create(
                conversation_id="session_1",
                user_message="inspect beta",
                workspace_id="beta",
                model_selection=ModelSelection(),
            )

            self.assertEqual(alpha_snapshot.tools.enabled_tools, ("tool.alpha",))
            self.assertEqual(beta_snapshot.tools.enabled_tools, ("tool.beta",))
            self.assertEqual(
                [spec.name for spec in registry.list_specs()],
                ["tool.alpha", "tool.beta"],
            )
            self.assertEqual(
                [item.text for item in alpha_snapshot.instructions.sources],
                ["alpha agents", "alpha config"],
            )
            self.assertEqual(
                [item.text for item in beta_snapshot.instructions.sources],
                ["beta agents", "beta config"],
            )
            self.assertGreater(
                alpha_snapshot.instructions.sources[0].priority,
                alpha_snapshot.instructions.sources[1].priority,
            )
            self.assertEqual(
                alpha_snapshot.project.project_config["config"]["runtime"][
                    "agent_max_elapsed_seconds"
                ]["value"],
                101,
            )
            self.assertEqual(
                beta_snapshot.project.project_config["config"]["runtime"][
                    "agent_max_elapsed_seconds"
                ]["value"],
                202,
            )
            self.assertNotEqual(
                alpha_snapshot.metadata.config_version,
                beta_snapshot.metadata.config_version,
            )

    def test_config_instruction_shares_budget_and_keeps_provenance(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".ai-agent-platform").mkdir()
            (root / "AGENTS.override.md").write_text("OVERRIDE", encoding="utf-8")
            (root / ".ai-agent-platform" / "config.json").write_text(
                json.dumps(
                    {
                        "project_session": {
                            "project_instructions": ["CONFIGURATION"]
                        }
                    }
                ),
                encoding="utf-8",
            )
            factory = ExecutionContextFactory(
                session_service=_SessionService(),
                workspace_service=_workspace_service(root, ("main", root)),
                process_config=ConfigResolver(
                    env={},
                    explicit_overrides={"agent_max_instruction_chars": 10},
                ).resolve_process(),
                tool_registry=_tool_registry("tool.a"),
                max_instruction_chars=10,
            )
            snapshot = factory.create(
                conversation_id="session_1",
                user_message="inspect",
                workspace_id="main",
                model_selection=ModelSelection(),
            )

            file_source, config_source = snapshot.instructions.sources
            self.assertEqual(file_source.text, "OVERRIDE")
            self.assertEqual(config_source.text, "CO")
            self.assertTrue(config_source.truncated)
            self.assertEqual(config_source.kind, "config_instruction")
            self.assertTrue(config_source.path.startswith("config://"))
            self.assertIn("workspace:.ai-agent-platform/config.json", config_source.reason)
            self.assertEqual(
                sum(len(item.text) for item in snapshot.instructions.sources),
                10,
            )

    def test_instruction_symlinks_nonregular_files_and_replacement_fail_closed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            root = parent / "workspace"
            outside = parent / "outside"
            root.mkdir()
            outside.mkdir()
            secret = outside / "secret.md"
            secret.write_text("outside secret content", encoding="utf-8")
            (root / "AGENTS.md").symlink_to(secret)
            factory = _factory(root)
            values = {
                "conversation_id": "session_1",
                "user_message": "inspect",
                "workspace_id": "main",
                "model_selection": ModelSelection(),
            }
            with self.assertRaises(InstructionSecurityError) as symlink_error:
                factory.create(**values)
            self.assertNotIn("outside secret content", str(symlink_error.exception))

            (root / "AGENTS.md").unlink()
            (root / "escaped").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "focus file escapes"):
                factory.create(**values, focus_files=["escaped/secret.md"])

            (root / "escaped").unlink()
            fifo = root / "AGENTS.md"
            os.mkfifo(fifo)
            try:
                with self.assertRaisesRegex(InstructionSecurityError, "regular file"):
                    factory.create(**values)
            finally:
                fifo.unlink()

            (root / "AGENTS.md").write_text("stable", encoding="utf-8")
            with patch(
                "ai_agent_platform.agents.coding.context._same_stable_file",
                return_value=False,
            ), self.assertRaisesRegex(InstructionSecurityError, "changed while"):
                factory.create(**values)

    def test_project_tool_selection_cannot_widen_cap_or_name_unknown_tool(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".ai-agent-platform").mkdir()
            config_path = root / ".ai-agent-platform" / "config.json"
            registry = _tool_registry("tool.a", "tool.b")
            process = ConfigResolver(
                user_config={
                    "process_security": {"tool_allowlist": ["tool.a"]}
                },
                env={},
            ).resolve_process()
            factory = ExecutionContextFactory(
                session_service=_SessionService(),
                workspace_service=_workspace_service(root, ("main", root)),
                process_config=process,
                tool_registry=registry,
            )
            config_path.write_text(
                json.dumps(
                    {"project_session": {"enabled_tools": ["tool.b"]}}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigSecurityError, "process allowlist"):
                factory.create(
                    conversation_id="session_1",
                    user_message="inspect",
                    workspace_id="main",
                    model_selection=ModelSelection(),
                )

            unrestricted = ConfigResolver(env={}).resolve_process()
            unknown_factory = ExecutionContextFactory(
                session_service=_SessionService(),
                workspace_service=_workspace_service(root, ("main", root)),
                process_config=unrestricted,
                tool_registry=registry,
            )
            config_path.write_text(
                json.dumps(
                    {"project_session": {"enabled_tools": ["missing.tool"]}}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown tools"):
                unknown_factory.create(
                    conversation_id="session_1",
                    user_message="inspect",
                    workspace_id="main",
                    model_selection=ModelSelection(),
                )

    def test_rejects_cross_user_additional_workspace_and_path_escapes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            root = parent / "main"
            extra = parent / "extra"
            outside = parent / "outside"
            for item in (root, extra, outside):
                item.mkdir()
            (root / "escape").symlink_to(outside, target_is_directory=True)
            service = _workspace_service(
                parent,
                ("main", root),
                ("extra", extra),
            )
            authorizer = _Authorizer(
                {
                    ("main", "alice"): "admin",
                    ("extra", "bob"): "viewer",
                }
            )
            factory = ExecutionContextFactory(
                session_service=_SessionService(),
                workspace_service=service,
                workspace_authorizer=authorizer,
                auth_mode="trusted_header",
            )
            base = {
                "conversation_id": "session_1",
                "user_message": "inspect",
                "workspace_id": "main",
                "model_selection": ModelSelection(),
            }
            with self.assertRaisesRegex(PermissionError, "conversation"):
                factory.create(**base, actor_user_id="bob")
            with self.assertRaisesRegex(PermissionError, "workspace"):
                factory.create(
                    **base,
                    actor_user_id="alice",
                    additional_workspace_ids=["extra"],
                )
            authorizer.roles[("extra", "alice")] = "viewer"
            authorized = factory.create(
                **base,
                actor_user_id="alice",
                additional_workspace_ids=["extra"],
            )
            self.assertEqual(
                [item.workspace_id for item in authorized.additional_directories],
                ["extra"],
            )
            self.assertEqual(
                authorized.additional_directories[0].workspace_role,
                "viewer",
            )
            with self.assertRaisesRegex(ValueError, "cwd escapes"):
                factory.create(**base, actor_user_id="alice", cwd="../outside")
            with self.assertRaisesRegex(ValueError, "cwd escapes"):
                factory.create(**base, actor_user_id="alice", cwd="escape")
            with self.assertRaisesRegex(ValueError, "focus file escapes"):
                factory.create(
                    **base,
                    actor_user_id="alice",
                    focus_files=["escape/secret.txt"],
                )
            with self.assertRaises(WorkspaceNotFoundError):
                factory.create(
                    **base,
                    actor_user_id="alice",
                    additional_workspace_ids=[str(outside)],
                )

    def test_git_unavailable_and_non_repository_are_diagnostics(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            factory = _factory(root)
            base = {
                "conversation_id": "session_1",
                "user_message": "inspect",
                "workspace_id": "main",
                "model_selection": ModelSelection(),
            }
            non_repo = factory.create(**base)
            self.assertTrue(non_repo.project.git.available)
            self.assertFalse(non_repo.project.git.is_repository)
            self.assertIn("not_a_git_repository", non_repo.project.git.diagnostics)
            with patch(
                "ai_agent_platform.services.execution_context._git",
                side_effect=FileNotFoundError,
            ):
                unavailable = factory.create(**base)
            self.assertFalse(unavailable.project.git.available)
            self.assertIn("git_unavailable", unavailable.project.git.diagnostics)

    @unittest.skipUnless(shutil.which("git"), "git executable is unavailable")
    def test_git_head_branch_and_dirty_summary_are_captured(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "config", "user.name", "Test User"],
                check=True,
            )
            tracked = root / "tracked.txt"
            tracked.write_text("first\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "commit", "-qm", "initial"],
                check=True,
            )
            tracked.write_text("second\n", encoding="utf-8")
            (root / "untracked.txt").write_text("new\n", encoding="utf-8")

            snapshot = _factory(root).create(
                conversation_id="session_1",
                user_message="inspect",
                workspace_id="main",
                model_selection=ModelSelection(),
            )
            git = snapshot.project.git
            self.assertTrue(git.available)
            self.assertTrue(git.is_repository)
            self.assertEqual(len(git.head or ""), 40)
            self.assertTrue(git.branch)
            self.assertTrue(git.dirty.is_dirty)
            self.assertEqual(git.dirty.changed_count, 2)
            self.assertEqual(git.dirty.unstaged_count, 1)
            self.assertEqual(git.dirty.untracked_count, 1)


class WorkerContextRecoveryTests(unittest.TestCase):
    def test_new_worker_reconstructs_execution_only_from_persisted_run_id(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            instruction = root / "AGENTS.md"
            instruction.write_text("frozen instruction", encoding="utf-8")
            (root / ".ai-agent-platform").mkdir()
            project_config = root / ".ai-agent-platform" / "config.json"
            project_config.write_text(
                json.dumps(
                    {
                        "project_session": {
                            "enabled_tools": ["tool.a"],
                            "project_instructions": ["frozen config instruction"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            sessions = _SessionService()
            workspaces = _workspace_service(root, ("main", root))
            factory = ExecutionContextFactory(
                session_service=sessions,
                workspace_service=workspaces,
                auth_mode="disabled",
                process_config=ConfigResolver(env={}).resolve_process(),
                tool_registry=_tool_registry("tool.a", "tool.b"),
            )
            store = InMemoryAgentRunStore()
            queued_runtime = _QueuedRuntime(store)
            queue = _CaptureQueue()
            submitter = AgentRunService(
                runtime=queued_runtime,
                session_service=sessions,
                workspace_service=workspaces,
                task_queue=queue,
                execution_context_factory=factory,
            )
            record = submitter.submit_run(
                conversation_id="session_1",
                message="original request",
                workspace_id="main",
            )
            self.assertEqual(queue.payloads, [{"run_id": record.run_id}])

            sessions.history[0]["content"] = "mutated history"
            instruction.write_text("mutated instruction", encoding="utf-8")
            project_config.write_text(
                json.dumps(
                    {
                        "project_session": {
                            "enabled_tools": ["tool.b"],
                            "project_instructions": ["mutated config instruction"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            worker_runtime = _WorkerRuntime(store)
            worker = AgentRunService(
                runtime=worker_runtime,
                session_service=sessions,
                workspace_service=workspaces,
                task_queue=_CaptureQueue(),
            )
            different_cwd = root / "worker-cwd"
            different_cwd.mkdir()
            previous_cwd = Path.cwd()
            os.chdir(different_cwd)
            try:
                worker.execute_run_task(run_id=record.run_id)
            finally:
                os.chdir(previous_cwd)

            call = worker_runtime.calls[0]
            self.assertEqual(call["user_input"], "original request")
            self.assertEqual(call["history"][0]["content"], "frozen history")
            context = call["run_context"]
            self.assertEqual(context.metadata.run_id, record.run_id)
            self.assertEqual(
                context.instructions.sources[0].text,
                "frozen instruction",
            )
            self.assertEqual(
                context.instructions.sources[1].text,
                "frozen config instruction",
            )
            self.assertEqual(context.tools.enabled_tools, ("tool.a",))
            self.assertEqual(call["actor_user_id"], "alice")

    def test_worker_runtime_rejects_illegal_persisted_tool_selection(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = _tool_registry("tool.a")
            snapshot = ExecutionContextFactory(
                session_service=_SessionService(),
                workspace_service=_workspace_service(root, ("main", root)),
                process_config=ConfigResolver(env={}).resolve_process(),
                tool_registry=registry,
            ).create(
                conversation_id="session_1",
                user_message="inspect",
                workspace_id="main",
                model_selection=ModelSelection(),
                run_id="run_tampered",
            )
            payload = snapshot.to_dict()
            payload["tools"]["enabled_tools"] = ["missing.tool"]  # type: ignore[index]
            payload["tools"]["version"] = (  # type: ignore[index]
                "sha256:"
                + hashlib.sha256(b'["missing.tool"]').hexdigest()[:16]
            )
            restored = RunContextSnapshot.from_dict(payload)
            runtime = CodingAgentRuntime(tool_registry=registry)

            with self.assertRaisesRegex(ValueError, "unknown tools"):
                runtime.run(
                    conversation_id="ignored",
                    user_input="ignored",
                    history=[],
                    workspace_id="ignored",
                    workspace_root=str(root),
                    run_context=restored,
                )

    def test_nullable_legacy_schema_one_snapshot_remains_loadable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot = _factory(root).create(
                conversation_id="session_1",
                user_message="legacy",
                workspace_id="main",
                model_selection=ModelSelection(),
            )
            payload = snapshot.to_dict()
            payload["metadata"]["schema_version"] = 1  # type: ignore[index]
            payload.pop("tools")
            for source in payload["instructions"]["sources"]:  # type: ignore[index]
                source.pop("priority")

            restored = RunContextSnapshot.from_dict(payload)

            self.assertIsNone(restored.tools.enabled_tools)
            self.assertEqual(restored.metadata.schema_version, 1)

    def test_postgres_store_round_trips_the_context_json(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot = _factory(root).create(
                conversation_id="session_1",
                user_message="persist me",
                workspace_id="main",
                model_selection=ModelSelection(),
                run_id="run_pg",
            )
            record = AgentRunRecord(
                run_id="run_pg",
                thread_id="run_pg",
                conversation_id="session_1",
                workspace_id="main",
                workspace_root=str(root.resolve()),
                status="queued",
                checkpoint_id=None,
                latest_node=None,
                next_nodes=["setup_workspace"],
                trace=[],
                context_snapshot=snapshot,
            )
            save_connection = _FakeConnection([])
            with patch(
                "ai_agent_platform.repositories.postgres._require_psycopg",
                return_value=object(),
            ), patch(
                "ai_agent_platform.repositories.postgres._require_jsonb",
                return_value=lambda value: value,
            ):
                repository = PostgresAgentRunRepository(
                    database_url="postgresql://test"
                )
                repository._connect = lambda: save_connection
                repository.save(record)
            save_sql, save_params = save_connection.calls[0]
            self.assertIn("run_context_snapshot", save_sql)
            self.assertIn(snapshot.to_dict(), save_params)

            row = (
                "run_pg",
                "run_pg",
                "session_1",
                "main",
                str(root.resolve()),
                "queued",
                None,
                None,
                ["setup_workspace"],
                [],
                None,
                None,
                None,
                [],
                None,
                [],
                snapshot.to_dict(),
            )
            load_connection = _FakeConnection([row])
            with patch(
                "ai_agent_platform.repositories.postgres._require_psycopg",
                return_value=object(),
            ):
                repository = PostgresAgentRunRepository(
                    database_url="postgresql://test"
                )
                repository._connect = lambda: load_connection
                restored = repository.get("run_pg")
            self.assertEqual(restored.context_snapshot, snapshot)


class _QueuedRuntime:
    def __init__(self, store: InMemoryAgentRunStore) -> None:
        self.store = store

    def create_queued_run(self, **values: object) -> AgentRunRecord:
        record = AgentRunRecord(
            run_id=str(values["run_id"]),
            thread_id=str(values["run_id"]),
            conversation_id=str(values["conversation_id"]),
            workspace_id=str(values["workspace_id"]),
            workspace_root=str(values["workspace_root"]),
            status="queued",
            checkpoint_id=None,
            latest_node=None,
            next_nodes=["setup_workspace"],
            trace=[],
            context_snapshot=values["context_snapshot"],  # type: ignore[arg-type]
        )
        self.store.save(record)
        return record

    def get_run(self, run_id: str) -> AgentRunRecord:
        return self.store.get(run_id)


class _WorkerRuntime:
    def __init__(self, store: InMemoryAgentRunStore) -> None:
        self.store = store
        self.calls: list[dict[str, object]] = []

    def get_run(self, run_id: str) -> AgentRunRecord:
        return self.store.get(run_id)

    def run(self, **values: object):
        self.calls.append(values)
        return SimpleNamespace(status="completed", answer="")


class _CaptureQueue:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def submit(self, _name: str, _handler, **values: object) -> None:
        self.payloads.append(values)

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.current: object = None

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(self, sql: str, params: tuple[object, ...] = ()):
        self.calls.append((sql, params))
        self.current = self.results.pop(0) if self.results else None
        return self

    def fetchone(self):
        return self.current


def _workspace_service(
    allowed_root: Path,
    *workspaces: tuple[str, Path],
) -> WorkspaceService:
    service = WorkspaceService(
        store=InMemoryWorkspaceRepository(),
        allowed_roots=(str(allowed_root),),
    )
    for workspace_id, root in workspaces:
        service.register(workspace_id=workspace_id, root_path=str(root))
    return service


def _tool_registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(name, lambda: {})
    return registry


def _factory(root: Path) -> ExecutionContextFactory:
    return ExecutionContextFactory(
        session_service=_SessionService(),
        workspace_service=_workspace_service(root, ("main", root)),
        auth_mode="disabled",
    )


if __name__ == "__main__":
    unittest.main()
