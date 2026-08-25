from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.llm import (
    LLMProviderError,
    LLMRequestPlan,
    LLMStreamEvent,
    LLMUsage,
    _google_usage,
)
from ai_agent_platform.main import create_app
from ai_agent_platform.repositories import InMemoryWorkspaceRepository
from ai_agent_platform.runtime import ApplicationFactory
from ai_agent_platform.schemas.chat import ChatStreamRequest
from ai_agent_platform.usage_ledger import current_model_usage_context


def wait_for_run(client: TestClient, run_id: str) -> dict:
    for _ in range(200):
        body = client.get(f"/api/v1/agent/runs/{run_id}").json()
        if body["status"] in {
            "completed",
            "partial",
            "blocked",
            "cancelled",
            "failed",
            "waiting_input",
            "waiting_approval",
            "paused",
        }:
            return body
        time.sleep(0.01)
    raise AssertionError("agent run did not finish")


def upload_document(
    client: TestClient,
    knowledge_base_id: str,
    filename: str,
    content: str | bytes,
):
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": (filename, payload)},
    )


class ApiTests(unittest.TestCase):
    def test_single_user_sessions_always_belong_to_fixed_owner(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with self._client(
                Path(temp_dir),
                auth_mode="single_user",
                single_user_id="owner",
                native_directory_picker_mode="disabled",
            ) as client:
                created = client.post(
                    "/api/v1/sessions",
                    headers={"X-User-ID": "header-attacker"},
                    json={"user_id": "body-attacker"},
                )
                fetched = client.get(
                    f"/api/v1/sessions/{created.json()['id']}",
                    headers={"X-User-ID": "another-attacker"},
                )

            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["user_id"], "owner")
            self.assertEqual(fetched.status_code, 200)
            self.assertEqual(fetched.json()["id"], created.json()["id"])
            self.assertEqual(fetched.json()["user_id"], "owner")

    def test_single_user_recovers_legacy_workspace_ownership(self) -> None:
        class LegacyWorkspaceFactory(ApplicationFactory):
            def __init__(self, store, workspace_ids):
                self.store = store
                self.workspace_ids = workspace_ids

            def create_workspace_store(self, settings):
                return self.store

            def create_project_memory_service(self, settings, **kwargs):
                service = super().create_project_memory_service(settings, **kwargs)
                for workspace_id in self.workspace_ids:
                    service.ensure_workspace_admin(
                        workspace_id=workspace_id,
                        actor_user_id="legacy-user",
                    )
                return service

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active_root = root / "active"
            restored_root = root / "restored"
            active_root.mkdir()
            restored_root.mkdir()
            store = InMemoryWorkspaceRepository()
            store.upsert(
                workspace_id="active",
                root_path=str(root / "legacy-active-mount"),
            )
            store.upsert(
                workspace_id="removed",
                root_path=str(root / "legacy-removed-mount"),
            )
            store.remove("removed")
            factory = LegacyWorkspaceFactory(store, ("active", "removed"))
            settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                workspace_allowed_roots=(str(root.resolve()),),
                background_task_workers=2,
                auth_mode="single_user",
                single_user_id="owner",
                native_directory_picker_mode="disabled",
            )
            app = create_app(settings=settings, application_factory=factory)

            with TestClient(app) as client:
                listed = client.get("/api/v1/workspaces")
                updated = client.put(
                    "/api/v1/workspaces/active",
                    json={"root_path": str(active_root)},
                )
                restored = client.put(
                    "/api/v1/workspaces/removed",
                    json={"root_path": str(restored_root)},
                )
                memory_service = app.state.project_memory_service

                self.assertEqual(listed.status_code, 200)
                self.assertEqual(
                    [item["id"] for item in listed.json()["workspaces"]],
                    ["active"],
                )
                self.assertEqual(listed.json()["workspaces"][0]["role"], "admin")
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(
                    updated.json()["root_path"],
                    str(active_root.resolve()),
                )
                self.assertEqual(restored.status_code, 200)
                self.assertEqual(
                    restored.json()["root_path"],
                    str(restored_root.resolve()),
                )
                for workspace_id in ("active", "removed"):
                    self.assertEqual(
                        memory_service.role_for(
                            workspace_id=workspace_id,
                            actor_user_id="owner",
                        ),
                        "admin",
                    )
                    self.assertEqual(
                        memory_service.role_for(
                            workspace_id=workspace_id,
                            actor_user_id="legacy-user",
                        ),
                        "admin",
                    )

            disabled_store = InMemoryWorkspaceRepository()
            disabled_store.upsert(
                workspace_id="active",
                root_path=str(active_root),
            )
            disabled_factory = LegacyWorkspaceFactory(disabled_store, ("active",))
            disabled_app = create_app(
                settings=Settings(
                    llm_provider="fake",
                    embedding_provider="local",
                    workspace_allowed_roots=(str(root.resolve()),),
                    background_task_workers=2,
                    auth_mode="disabled",
                ),
                application_factory=disabled_factory,
            )
            try:
                self.assertIsNone(
                    disabled_app.state.project_memory_service.role_for(
                        workspace_id="active",
                        actor_user_id="owner",
                    )
                )
            finally:
                disabled_app.state.runtime.close()

    def test_chat_rolls_old_turns_into_summary_visible_from_api(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                workspace_allowed_roots=(str(Path(temp_dir).resolve()),),
                background_task_workers=2,
                conversation_summary_trigger_messages=4,
                conversation_summary_keep_recent_messages=2,
            )
            with TestClient(create_app(settings=settings)) as client:
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "user_1"},
                ).json()["id"]
                for message in ("first durable choice", "second question"):
                    response = client.post(
                        "/api/v1/chat/stream",
                        json={
                            "conversation_id": session_id,
                            "message": message,
                        },
                    )
                    self.assertEqual(response.status_code, 200)

                summary = None
                for _ in range(100):
                    summary = client.get(
                        f"/api/v1/sessions/{session_id}/summary"
                    ).json()
                    if summary["summary_version"]:
                        break
                    time.sleep(0.01)

                assert summary is not None
                self.assertEqual(summary["message_count"], 4)
                self.assertEqual(summary["summarized_message_count"], 2)
                self.assertEqual(summary["summary_version"], 1)
                self.assertIn("first durable choice", summary["compressed_summary"])

    def test_workspace_registration_listing_and_lookup(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            with self._client(root) as client:
                created = client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(workspace)},
                )
                self.assertEqual(created.status_code, 200)
                self.assertEqual(created.json()["root_path"], str(workspace.resolve()))
                self.assertEqual(created.json()["status"], "ready")
                self.assertEqual(created.json()["role"], "admin")
                self.assertTrue(created.json()["can_update"])
                repeated = client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(workspace)},
                )
                self.assertEqual(repeated.status_code, 200)
                self.assertEqual(
                    repeated.json()["revision"],
                    created.json()["revision"],
                )
                conflict = client.put(
                    "/api/v1/workspaces/project-copy",
                    json={"root_path": str(workspace)},
                )
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(
                    conflict.json()["detail"],
                    "workspace root is already registered",
                )
                self.assertEqual(
                    client.get("/api/v1/workspaces/project").json()["id"],
                    "project",
                )
                listed = client.get("/api/v1/workspaces").json()["workspaces"]
                self.assertEqual([item["id"] for item in listed], ["project"])
                self.assertTrue(listed[0]["available"])

    def test_workspace_remove_preserves_registration_for_restore(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            with self._client(root) as client:
                created = client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(workspace)},
                ).json()

                removed = client.delete("/api/v1/workspaces/project")

                self.assertEqual(removed.status_code, 204)
                self.assertEqual(
                    client.get("/api/v1/workspaces").json()["workspaces"],
                    [],
                )
                self.assertEqual(
                    client.get("/api/v1/workspaces/project").status_code,
                    404,
                )
                self.assertEqual(
                    client.delete("/api/v1/workspaces/project").status_code,
                    404,
                )

                restored = client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(workspace)},
                ).json()
                self.assertEqual(restored["revision"], created["revision"])
                self.assertTrue(restored["available"])

    def test_workspace_listing_marks_moved_folder_unavailable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            with self._client(root) as client:
                client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(workspace)},
                )
                workspace.rmdir()

                listed = client.get("/api/v1/workspaces").json()["workspaces"]

                self.assertEqual(len(listed), 1)
                self.assertFalse(listed[0]["available"])

    def test_workspace_rejects_missing_outside_and_symlink_escape(self) -> None:
        with TemporaryDirectory() as allowed_dir, TemporaryDirectory() as outside_dir:
            allowed = Path(allowed_dir)
            outside = Path(outside_dir)
            link = allowed / "escape"
            link.symlink_to(outside, target_is_directory=True)
            with self._client(allowed) as client:
                missing = client.put(
                    "/api/v1/workspaces/missing",
                    json={"root_path": str(allowed / "missing")},
                )
                direct = client.put(
                    "/api/v1/workspaces/outside",
                    json={"root_path": str(outside)},
                )
                linked = client.put(
                    "/api/v1/workspaces/link",
                    json={"root_path": str(link)},
                )
            self.assertEqual(missing.status_code, 400)
            self.assertEqual(direct.status_code, 400)
            self.assertEqual(linked.status_code, 400)

    def test_workspace_directory_browser_stays_within_allowed_roots(self) -> None:
        with TemporaryDirectory() as allowed_dir, TemporaryDirectory() as outside_dir:
            allowed = Path(allowed_dir)
            alpha = allowed / "alpha"
            nested = alpha / "nested"
            beta = allowed / "Beta"
            alpha.mkdir()
            nested.mkdir()
            beta.mkdir()
            (allowed / ".hidden").mkdir()
            (allowed / "notes.txt").write_text("not a directory", encoding="utf-8")
            (allowed / "escape").symlink_to(
                Path(outside_dir),
                target_is_directory=True,
            )

            with self._client(allowed) as client:
                roots = client.get("/api/v1/workspace-directories")
                listing = client.get(
                    "/api/v1/workspace-directories",
                    params={"path": str(allowed)},
                )
                nested_listing = client.get(
                    "/api/v1/workspace-directories",
                    params={"path": str(alpha)},
                )
                outside = client.get(
                    "/api/v1/workspace-directories",
                    params={"path": outside_dir},
                )

            self.assertEqual(roots.status_code, 200)
            self.assertIsNone(roots.json()["current_path"])
            self.assertEqual(
                [item["path"] for item in roots.json()["directories"]],
                [str(allowed.resolve())],
            )
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.json()["current_path"], str(allowed.resolve()))
            self.assertIsNone(listing.json()["parent_path"])
            self.assertEqual(
                [item["name"] for item in listing.json()["directories"]],
                ["alpha", "Beta"],
            )
            self.assertEqual(
                nested_listing.json()["parent_path"],
                str(allowed.resolve()),
            )
            self.assertEqual(
                [item["name"] for item in nested_listing.json()["directories"]],
                ["nested"],
            )
            self.assertEqual(outside.status_code, 400)
            self.assertEqual(
                outside.json()["detail"],
                "workspace root is outside WORKSPACE_ALLOWED_ROOTS",
            )

    def test_native_workspace_directory_picker_is_local_and_validated(self) -> None:
        class StubDirectoryPicker:
            def __init__(self, selections):
                self.selections = list(selections)
                self.initial_paths = []

            def pick_directory(self, *, initial_path=None):
                self.initial_paths.append(initial_path)
                return self.selections.pop(0)

        with TemporaryDirectory() as allowed_dir, TemporaryDirectory() as outside_dir:
            allowed = Path(allowed_dir).resolve()
            project = allowed / "project"
            project.mkdir()
            picker = StubDirectoryPicker([str(project), None, outside_dir])
            settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                workspace_allowed_roots=(str(allowed),),
                background_task_workers=2,
            )
            app = create_app(settings=settings, directory_picker=picker)
            with TestClient(app, client=("127.0.0.1", 50000)) as client:
                selected = client.post(
                    "/api/v1/workspace-directory-picker",
                    json={"initial_path": str(allowed)},
                )
                cancelled = client.post(
                    "/api/v1/workspace-directory-picker",
                    json={"initial_path": None},
                )
                outside = client.post(
                    "/api/v1/workspace-directory-picker",
                    json={"initial_path": str(allowed)},
                )

            self.assertEqual(selected.status_code, 200)
            self.assertEqual(
                selected.json(),
                {"path": str(project), "cancelled": False},
            )
            self.assertEqual(
                cancelled.json(),
                {"path": None, "cancelled": True},
            )
            self.assertEqual(outside.status_code, 400)
            self.assertEqual(
                outside.json()["detail"],
                "workspace root is outside WORKSPACE_ALLOWED_ROOTS",
            )
            self.assertEqual(picker.initial_paths, [str(allowed)] * 3)

    def test_native_workspace_directory_picker_rejects_remote_and_auth_modes(self) -> None:
        class FailingDirectoryPicker:
            called = False

            def pick_directory(self, *, initial_path=None):
                self.called = True
                raise AssertionError("remote request must not open a system dialog")

        with TemporaryDirectory() as allowed_dir:
            allowed = Path(allowed_dir).resolve()
            picker = FailingDirectoryPicker()
            settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                workspace_allowed_roots=(str(allowed),),
                background_task_workers=2,
            )
            app = create_app(settings=settings, directory_picker=picker)
            with TestClient(app, client=("203.0.113.10", 50000)) as client:
                remote_response = client.post(
                    "/api/v1/workspace-directory-picker",
                    json={"initial_path": None},
                )

            trusted_settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                workspace_allowed_roots=(str(allowed),),
                background_task_workers=2,
                auth_mode="trusted_header",
                gateway_trust_secret="test-secret",
            )
            trusted_app = create_app(
                settings=trusted_settings,
                directory_picker=picker,
            )
            with TestClient(
                trusted_app,
                client=("127.0.0.1", 50000),
            ) as client:
                trusted_response = client.post(
                    "/api/v1/workspace-directory-picker",
                    json={"initial_path": None},
                )

            self.assertEqual(remote_response.status_code, 403)
            self.assertEqual(trusted_response.status_code, 403)
            self.assertFalse(picker.called)

    def test_native_workspace_directory_picker_accepts_trusted_local_gateway(
        self,
    ) -> None:
        class StubDirectoryPicker:
            def __init__(self, selection: str):
                self.selection = selection
                self.initial_paths = []

            def pick_directory(self, *, initial_path=None):
                self.initial_paths.append(initial_path)
                return self.selection

        with TemporaryDirectory() as allowed_dir:
            allowed = Path(allowed_dir).resolve()
            project = allowed / "project"
            project.mkdir()
            picker = StubDirectoryPicker(str(project))
            settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                workspace_allowed_roots=(str(allowed),),
                background_task_workers=2,
                auth_mode="trusted_header",
                gateway_trust_secret="test-secret",
                native_directory_picker_mode="trusted_local_gateway",
            )
            app = create_app(settings=settings, directory_picker=picker)
            with TestClient(app, client=("192.168.97.1", 50000)) as client:
                missing_identity = client.post(
                    "/api/v1/workspace-directory-picker",
                    json={"initial_path": None},
                )
                selected = client.post(
                    "/api/v1/workspace-directory-picker",
                    headers={
                        "X-Authenticated-User": "local-user",
                        "X-Gateway-Auth": "test-secret",
                        "X-Gateway-Mode": "local",
                    },
                    json={"initial_path": str(allowed)},
                )

                oidc_gateway = client.post(
                    "/api/v1/workspace-directory-picker",
                    headers={
                        "X-Authenticated-User": "remote-user",
                        "X-Gateway-Auth": "test-secret",
                    },
                    json={"initial_path": str(allowed)},
                )

            self.assertEqual(missing_identity.status_code, 401)
            self.assertEqual(selected.status_code, 200)
            self.assertEqual(oidc_gateway.status_code, 403)
            self.assertEqual(
                selected.json(),
                {"path": str(project), "cancelled": False},
            )
            self.assertEqual(picker.initial_paths, [str(allowed)])

    def test_agent_uses_workspace_contract_and_live_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            source = workspace / "app.py"
            source.write_text("VALUE = 'first'\n", encoding="utf-8")
            with self._client(root) as client:
                session_id = client.post(
                    "/api/v1/sessions", json={"user_id": "tester"}
                ).json()["id"]
                client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(workspace)},
                )
                rejected = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "message": "read app.py",
                        "repository_id": "project",
                    },
                )
                self.assertEqual(rejected.status_code, 422)

                first = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "message": "app.py 中的 VALUE 是什么？",
                        "workspace_id": "project",
                        "focus_files": ["app.py"],
                    },
                )
                result = wait_for_run(client, first.json()["run_id"])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["workspace_id"], "project")
                usage = result["result"]["metrics"]
                self.assertGreater(usage["input_tokens"], 0)
                self.assertGreater(usage["output_tokens"], 0)
                self.assertEqual(
                    usage["total_tokens"],
                    usage["input_tokens"]
                    + usage["output_tokens"]
                    + usage["thoughts_tokens"],
                )
                sources = result["result"]["context_sources"]
                self.assertTrue(
                    any(
                        item["path"] == "app.py" and "first" in item["text"]
                        for item in sources
                    )
                )
                self.assertNotIn("rag_context", result["result"])
                events = client.get(
                    f"/api/v1/agent/runs/{first.json()['run_id']}/events"
                ).json()["events"]
                self.assertEqual(events[0]["type"], "run_queued")
                self.assertEqual(events[-1]["type"], "run_completed")
                cursor_events = client.get(
                    f"/api/v1/agent/runs/{first.json()['run_id']}/events",
                    params={"after": events[-2]["sequence"]},
                ).json()["events"]
                self.assertEqual(cursor_events[-1]["type"], "run_completed")
                with client.stream(
                    "GET",
                    f"/api/v1/agent/runs/{first.json()['run_id']}/events/stream",
                ) as event_stream:
                    stream_text = "".join(event_stream.iter_text())
                self.assertIn("event: run_completed", stream_text)
                completed_control = client.post(
                    f"/api/v1/agent/runs/{first.json()['run_id']}/cancel",
                    json={"message": "too late"},
                )
                self.assertEqual(completed_control.status_code, 409)

                source.write_text("VALUE = 'second'\n", encoding="utf-8")
                second = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "message": "再次读取 app.py",
                        "workspace_id": "project",
                        "focus_files": ["app.py"],
                    },
                )
                second_result = wait_for_run(client, second.json()["run_id"])
                self.assertTrue(
                    any(
                        item["path"] == "app.py" and "second" in item["text"]
                        for item in second_result["result"]["context_sources"]
                    )
                )
                recent_runs = client.get(
                    "/api/v1/agent/runs",
                    params={"limit": 1},
                )
                self.assertEqual(recent_runs.status_code, 200)
                self.assertEqual(
                    recent_runs.json()["runs"][0]["run_id"],
                    second.json()["run_id"],
                )
                self.assertEqual(
                    recent_runs.json()["runs"][0]["status"],
                    second_result["status"],
                )
                latest_run = client.get(
                    f"/api/v1/sessions/{session_id}/agent/runs/latest"
                )
                self.assertEqual(latest_run.status_code, 200)
                self.assertEqual(latest_run.json()["run_id"], second.json()["run_id"])
                conversation_usage = client.get(
                    f"/api/v1/sessions/{session_id}/token-usage"
                ).json()
                workspace_usage = client.get(
                    "/api/v1/workspaces/project/token-usage"
                ).json()
                self.assertGreaterEqual(conversation_usage["record_count"], 2)
                self.assertEqual(
                    sum(
                        item["record_count"]
                        for item in conversation_usage["operations"]
                        if item["operation"] == "agent"
                    ),
                    conversation_usage["record_count"],
                )
                self.assertGreater(
                    conversation_usage["context"]["estimated_tokens"],
                    0,
                )
                self.assertEqual(
                    conversation_usage["workspaces"][0]["workspace_id"],
                    "project",
                )
                self.assertEqual(
                    workspace_usage["total_tokens"],
                    conversation_usage["total_tokens"],
                )
                self.assertEqual(workspace_usage["conversation_count"], 1)

    def test_completed_patch_only_run_can_fork_from_historical_checkpoint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            (workspace / "app.py").write_text("VALUE = 'current'\n", encoding="utf-8")
            with self._client(root) as client:
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "tester"},
                ).json()["id"]
                client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(workspace)},
                ).raise_for_status()
                started = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "message": "read app.py",
                        "workspace_id": "project",
                        "focus_files": ["app.py"],
                    },
                )
                self.assertEqual(started.status_code, 202, started.text)
                source_run_id = started.json()["run_id"]
                source_execution_root = Path(started.json()["execution_root"])
                source_result = wait_for_run(client, source_run_id)
                self.assertEqual(source_result["status"], "completed")
                self.assertFalse(source_execution_root.exists())

                history = client.get(
                    f"/api/v1/agent/runs/{source_run_id}/checkpoints",
                    params={"limit": 200},
                )
                self.assertEqual(history.status_code, 200, history.text)
                selected = next(
                    checkpoint
                    for checkpoint in history.json()["checkpoints"]
                    if checkpoint["can_restore"]
                )
                restored = client.post(
                    f"/api/v1/agent/runs/{source_run_id}/checkpoints/"
                    f"{selected['checkpoint_id']}/restore",
                    json={"mode": "fork", "message": "take another path"},
                )
                self.assertEqual(restored.status_code, 202, restored.text)
                restored_body = restored.json()
                branch = restored_body["run"]
                self.assertNotEqual(branch["run_id"], source_run_id)
                self.assertNotEqual(
                    restored_body["forked_conversation_id"],
                    session_id,
                )
                self.assertNotEqual(branch["execution_root"], str(source_execution_root))
                branch_result = wait_for_run(client, branch["run_id"])

                self.assertEqual(branch_result["status"], "completed")
                self.assertEqual(
                    client.get(f"/api/v1/agent/runs/{source_run_id}").json()["status"],
                    "completed",
                )

    def test_removed_repository_index_endpoints_return_404(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            response = client.post(
                "/api/v1/repositories/repo_main/index",
                json={"root_path": temp_dir},
            )
            self.assertEqual(response.status_code, 404)

    def test_session_without_agent_run_has_no_latest_run(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            session_id = client.post(
                "/api/v1/sessions",
                json={"user_id": "tester"},
            ).json()["id"]
            response = client.get(
                f"/api/v1/sessions/{session_id}/agent/runs/latest"
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "agent run not found")

    def test_serves_unified_chat_and_workspace_agent_frontend(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            response = client.get("/")
            script_response = client.get("/static/app.js")
            stylesheet_response = client.get("/static/styles.css")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn(
            '/static/styles.css?v=20260825-chat-message-ui-v2',
            response.text,
        )
        self.assertIn(
            '/static/app.js?v=20260825-chat-message-ui-v2',
            response.text,
        )
        self.assertIn('id="composer-mode-input"', response.text)
        self.assertNotIn('id="agent-workspace-mode-select"', response.text)
        self.assertNotIn("执行位置", response.text)
        self.assertIn('id="slash-command-menu"', response.text)
        self.assertIn('data-memory-tab="project"', response.text)
        self.assertIn('data-memory-tab="profile"', response.text)
        self.assertIn('data-memory-tab="conversations"', response.text)
        self.assertIn('class="memory-layer-rail"', response.text)
        self.assertIn("项目记忆", response.text)
        self.assertIn("个人记忆", response.text)
        self.assertIn("对话记录", response.text)
        self.assertNotIn('class="memory-tab-code"', response.text)
        self.assertIn('id="new-project-memory-btn"', response.text)
        self.assertNotIn('id="memory-mode-input"', response.text)
        self.assertNotIn('id="save-memory-mode-btn"', response.text)
        self.assertNotIn('id="reindex-memory-btn"', response.text)
        self.assertNotIn('id="refresh-memory-btn"', response.text)
        self.assertNotIn("CURRENT WORKSPACE", response.text)
        self.assertIn('id="new-user-memory-btn"', response.text)
        self.assertIn('id="conversation-memory-detail"', response.text)
        self.assertNotIn('id="user-memory-scenes"', response.text)
        self.assertNotIn('fetchJson("/users/me/memory-scenes")', script_response.text)
        self.assertIn('id="slash-command-options"', response.text)
        self.assertIn('id="jump-to-latest-btn"', response.text)
        self.assertIn('aria-autocomplete="list"', response.text)
        self.assertNotIn('data-view="agent"', response.text)
        self.assertNotIn('id="agent-view"', response.text)
        self.assertNotIn('id="session-token-usage"', response.text)
        self.assertNotIn('id="composer-attachment-btn"', response.text)
        self.assertNotIn('id="composer-provider-input"', response.text)
        self.assertIn('id="thinking-level-input"', response.text)
        self.assertNotIn('id="workspace-draft-id-input"', response.text)
        self.assertIn('id="composer-scope-strip"', response.text)
        self.assertIn('id="composer-workspace-btn"', response.text)
        self.assertIn('id="composer-context-budget"', response.text)
        self.assertIn('id="composer-context-kicker"', response.text)
        self.assertIn("累计 Token", response.text)
        self.assertIn("function formatTokenPercentage(value)", script_response.text)
        self.assertIn('return "<0.01%"', script_response.text)
        self.assertIn("maximumFractionDigits: 2", script_response.text)
        self.assertIn(
            "上下文上限 ${formatTokenCount(budget)} · ${percentage}",
            script_response.text,
        )
        self.assertIn("Math.min(1, ratio)", script_response.text)
        self.assertIn("上下文上限未知", script_response.text)
        self.assertNotIn("历史尚未形成", script_response.text)
        self.assertNotIn('id="composer-workspace-select"', response.text)
        self.assertNotIn('id="workspace-catalog-list"', response.text)
        self.assertIn('id="workspace-manager-list"', response.text)
        self.assertIn('id="workspace-default-toggle"', response.text)
        self.assertIn('id="open-workspace-picker-btn"', response.text)
        self.assertIn('id="workspace-picker-dialog"', response.text)
        self.assertIn('id="workspace-token-list"', response.text)
        self.assertIn('id="recent-sessions-list"', response.text)
        self.assertIn('class="inspector-recent"', response.text)
        self.assertIn('aria-label="最近会话侧栏"', response.text)
        self.assertIn('id="toggle-sidebar-btn"', response.text)
        self.assertIn('id="sidebar-resizer"', response.text)
        self.assertIn('id="inspector-resizer"', response.text)
        self.assertNotIn('id="active-session-inspector-btn"', response.text)
        self.assertNotIn('class="inspector-detail"', response.text)
        self.assertNotIn('id="trace-tab"', response.text)
        self.assertNotIn('id="raw-tab"', response.text)
        self.assertLess(
            response.text.index('id="inspector-panel"'),
            response.text.index('id="recent-sessions-list"'),
        )
        self.assertNotIn('class="recent-sessions"', response.text)
        self.assertIn('id="session-search-input"', response.text)
        self.assertIn('id="archived-session-notice"', response.text)
        self.assertIn('id="active-session-header"', response.text)
        self.assertNotIn('id="active-run-control"', response.text)
        self.assertNotIn('id="active-run-checkpoints-btn"', response.text)
        self.assertIn('id="checkpoint-history-dialog"', response.text)
        self.assertIn('id="checkpoint-history-list"', response.text)
        self.assertIn('id="checkpoint-fork-btn"', response.text)
        self.assertIn('id="checkpoint-rollback-btn"', response.text)
        self.assertIn('id="rename-current-session-btn"', response.text)
        self.assertIn('id="mobile-more-btn"', response.text)
        self.assertIn('id="mobile-more-menu"', response.text)
        self.assertIn('data-view="trace-audit"', response.text)
        self.assertIn('data-view="evals"><svg', response.text)
        self.assertIn('id="trace-audit-view"', response.text)
        self.assertIn('id="trace-audit-timeline"', response.text)
        self.assertIn('data-audit-filter="tool"', response.text)
        self.assertIn('id="composer-config" class="composer-config"', response.text)
        self.assertNotIn('class="conversation" aria-live=', response.text)
        self.assertIn('class="welcome-signal"', response.text)
        self.assertIn("<b>VERIFY</b>", response.text)
        self.assertIn('id="knowledge-base-list"', response.text)
        self.assertIn('id="document-files-input"', response.text)
        self.assertIn('id="knowledge-documents-panel"', response.text)
        self.assertIn('id="knowledge-ask-panel"', response.text)
        self.assertIn('id="knowledge-settings-panel"', response.text)
        self.assertIn('id="knowledge-document-rows"', response.text)
        self.assertIn('id="document-drawer"', response.text)
        self.assertIn('class="button-row document-actions"', response.text)
        self.assertIn("最终结果数", response.text)
        self.assertIn('id="rag-rerank-toggle"', response.text)
        self.assertIn('id="rag-strategy-summary"', response.text)
        self.assertIn('aria-pressed="false"', response.text)
        self.assertEqual(
            script_response.text.count("rerank_enabled: rerankEnabled"),
            2,
        )
        self.assertIn("new AbortController()", script_response.text)
        self.assertIn('fetchJson("/users/me/preferences"', script_response.text)
        self.assertIn("restoreInitialSession", script_response.text)
        self.assertIn("switchView(initialView, true)", script_response.text)
        self.assertIn("restoreLatestAgentRun", script_response.text)
        self.assertIn("inline-agent-checkpoint", script_response.text)
        self.assertIn("data-inline-agent-action", script_response.text)
        self.assertIn("inline-agent-controls", script_response.text)
        self.assertIn("data-inline-run-action", script_response.text)
        self.assertIn("renderInlineRunFooter", script_response.text)
        self.assertIn("data-inline-checkpoint-history", script_response.text)
        self.assertIn("取消 Run", script_response.text)
        self.assertIn("bubble.appendChild(footer)", script_response.text)
        self.assertIn("bubble.insertBefore(card, footer)", script_response.text)
        self.assertNotIn("renderActiveRunControl", script_response.text)
        self.assertIn("openCheckpointHistory", script_response.text)
        self.assertIn("restoreSelectedCheckpoint", script_response.text)
        self.assertIn("/checkpoints?limit=200", script_response.text)
        self.assertIn('restoreSelectedCheckpoint("fork")', script_response.text)
        self.assertIn('restoreSelectedCheckpoint("rollback")', script_response.text)
        self.assertIn("新的执行路径已创建，但界面切换失败", script_response.text)
        self.assertIn("inline-change-review", script_response.text)
        self.assertIn("data-inline-change-action", script_response.text)
        self.assertIn("已在执行时写入", script_response.text)
        self.assertIn("本次运行只修改临时副本", script_response.text)
        self.assertIn("Agent 写入的变更已安全回滚", script_response.text)
        self.assertIn('viewName === "agent"', script_response.text)
        self.assertIn('viewName === "mcp" ? "tools"', script_response.text)
        self.assertIn('data-view-panel="tools"', response.text)
        self.assertIn('id="skill-form"', response.text)
        self.assertNotIn("代码 Agent 页面", script_response.text)
        self.assertIn("已提交补充信息", script_response.text)
        self.assertIn("已继续运行", script_response.text)
        self.assertIn("await reader.cancel()", script_response.text)
        self.assertIn("空会话不参与启动恢复", script_response.text)
        self.assertIn("session.message_count > 0", script_response.text)
        self.assertIn("隐藏最近会话", script_response.text)
        self.assertIn("setSidebarVisible", script_response.text)
        self.assertIn("bindPanelResizer", script_response.text)
        self.assertIn("sidebarWidth", script_response.text)
        self.assertIn('data-response-action="show-trace"', script_response.text)
        self.assertIn("setRagRequestBusy", script_response.text)
        self.assertIn("listKnowledgeDocuments", script_response.text)
        self.assertIn("bulkDeleteKnowledgeDocuments", script_response.text)
        self.assertIn("document_filename_conflict", script_response.text)
        self.assertIn("citation_content_accuracy", script_response.text)
        self.assertIn("data-provider=", script_response.text)
        self.assertIn("renderEvalCallLifecycle", script_response.text)
        self.assertIn("renderEvalReadEvidence", script_response.text)
        self.assertIn('fetchJson("/agent/runs?limit=50")', script_response.text)
        self.assertIn("buildAuditEvents", script_response.text)
        self.assertIn("approval_decided", script_response.text)
        self.assertIn('"evals", "trace-audit"', script_response.text)
        self.assertLess(
            script_response.text.index('type.includes("error")'),
            script_response.text.index('type.startsWith("tool_")'),
        )
        self.assertIn("查看精确参数", script_response.text)
        self.assertIn("强制基线会记录 forced=true", script_response.text)
        self.assertIn(".knowledge-workbench", stylesheet_response.text)
        self.assertIn(".document-actions", stylesheet_response.text)
        self.assertIn(".inline-agent-checkpoint", stylesheet_response.text)
        self.assertIn(".inline-agent-controls", stylesheet_response.text)
        self.assertIn(".inline-run-footer", stylesheet_response.text)
        self.assertNotIn(".active-run-control", stylesheet_response.text)
        self.assertIn(".checkpoint-history-dialog", stylesheet_response.text)
        self.assertIn(".checkpoint-card", stylesheet_response.text)
        self.assertIn(".inline-change-review", stylesheet_response.text)
        self.assertIn(".change-file-row", stylesheet_response.text)
        self.assertIn("scroll-margin-block: 96px 340px", stylesheet_response.text)
        self.assertIn("flex: 1 1 440px", stylesheet_response.text)
        self.assertIn(".document-name-cell::before", stylesheet_response.text)
        self.assertIn("isCurrentRagRequest", script_response.text)
        self.assertIn('signal: request.controller.signal', script_response.text)
        self.assertNotIn('id="document-content-input"', response.text)
        self.assertNotIn('id="document-filename-input"', response.text)
        self.assertNotIn('id="repository-id-input"', response.text)
        self.assertEqual(script_response.status_code, 200)
        self.assertIn("thinking_level", script_response.text)
        self.assertIn("submitComposerMessage", script_response.text)
        self.assertIn("runAgentFromComposer", script_response.text)
        self.assertIn("loadSlashCapabilities", script_response.text)
        self.assertIn("parseComposerSlashInvocation", script_response.text)
        self.assertIn("preferred_tool_name", script_response.text)
        self.assertIn("saveComposerDraft", script_response.text)
        self.assertIn("conversationIsNearBottom", script_response.text)
        self.assertIn("setChatWorkbenchActive", script_response.text)
        self.assertIn("setMobileMoreOpen", script_response.text)
        self.assertIn("syncInspectorPresentation", script_response.text)
        self.assertIn("failureRecoveryMarkup", script_response.text)
        self.assertIn('data-message-action="copy"', script_response.text)
        self.assertIn('data-response-action="retry"', script_response.text)
        self.assertIn("output.scrollTo", script_response.text)
        self.assertIn("event.isComposing", script_response.text)
        self.assertIn(".inspector-recent", stylesheet_response.text)
        self.assertIn("body.sidebar-hidden .app-shell", stylesheet_response.text)
        self.assertIn(".panel-resizer", stylesheet_response.text)
        self.assertIn("--topbar-height: 58px", stylesheet_response.text)
        self.assertIn("min-height: 44px", stylesheet_response.text)
        self.assertIn(
            "height: calc(100vh - var(--topbar-height) - 70px",
            stylesheet_response.text,
        )
        self.assertNotIn('switchView("agent");', script_response.text)
        self.assertIn("setMessageDeliveryState", script_response.text)
        self.assertIn('onReady: () => {', script_response.text)
        self.assertIn("onSubmitted", script_response.text)
        self.assertIn("onSubmissionError", script_response.text)
        run_agent_source = script_response.text.split(
            "async function runAgent({", 1
        )[1].split("function activeRunPresentation", 1)[0]
        self.assertLess(
            run_agent_source.index("onReady(conversationId)"),
            run_agent_source.index('fetchJson("/agent/runs"'),
        )
        composer_source = script_response.text.split(
            "async function runAgentFromComposer()", 1
        )[1].split("async function streamChat()", 1)[0]
        self.assertLess(
            composer_source.index('appendChatMessage("user", submission.message)'),
            composer_source.index("onSubmitted: (body)"),
        )
        self.assertIn('class="message-delivery-state"', script_response.text)
        self.assertIn(".chat-message.user .message-label", stylesheet_response.text)
        self.assertIn(".chat-message.assistant .message-content", stylesheet_response.text)
        self.assertIn(".chat-message.is-submit-failed", stylesheet_response.text)
        self.assertIn("max-width: min(100%, 68ch)", stylesheet_response.text)
        self.assertIn("overflow-wrap: anywhere", stylesheet_response.text)
        self.assertIn("padding-left: 30px", stylesheet_response.text)
        self.assertIn("renderExecutionProcess", script_response.text)
        self.assertIn("traceToolNames", script_response.text)
        self.assertIn("executionProcessPresentation", script_response.text)
        self.assertIn("formatWorkDuration", script_response.text)
        self.assertIn("details.dataset.elapsedMs", script_response.text)
        self.assertIn("Agent 运行已停止。", script_response.text)
        self.assertIn('aria-current="step"', script_response.text)
        self.assertIn('aria-live="polite" aria-atomic="true"', script_response.text)
        self.assertIn("renderResponseMetrics", script_response.text)
        self.assertIn('class="welcome-signal"', script_response.text)
        self.assertIn("--signal: #62d6c2", stylesheet_response.text)
        self.assertIn("@keyframes signal-arrive", stylesheet_response.text)
        self.assertIn("--z-overlay: 80", stylesheet_response.text)
        self.assertIn(".chat-workbench.has-conversation", stylesheet_response.text)
        self.assertIn(".composer-scope-strip", stylesheet_response.text)
        self.assertIn(
            "minmax(0, 0.82fr) minmax(0, 0.88fr) minmax(0, 1.4fr)",
            stylesheet_response.text,
        )
        self.assertIn(".response-error-card", stylesheet_response.text)
        self.assertIn(".inspector-backdrop:not([hidden])", stylesheet_response.text)
        self.assertIn("body.mobile-more-open", stylesheet_response.text)
        self.assertIn(".primary-nav > .mobile-overflow-nav", stylesheet_response.text)
        self.assertIn("repeat(5, minmax(0, 1fr))", stylesheet_response.text)
        self.assertIn("loadSessionTokenUsage", script_response.text)
        self.assertIn("loadWorkspaceTokenUsage", script_response.text)
        self.assertIn("累计实际消耗", script_response.text)
        self.assertIn(
            "const ratio = budget > 0 ? total / budget : 0;",
            script_response.text,
        )
        self.assertIn('contextNode.classList.remove("warning", "error")', script_response.text)
        self.assertIn("超过 100% 不代表当前请求超出上下文窗口", script_response.text)
        self.assertIn("await loadSessionTokenUsage([conversationId]);", script_response.text)
        self.assertIn("createAgentProgressPresenter", script_response.text)
        self.assertIn("await onProgress", script_response.text)
        self.assertIn("agentProgressBodyFromEvents", script_response.text)
        self.assertIn("renderStreamedAgentProgress", script_response.text)
        self.assertIn("executionActivityEvents", script_response.text)
        self.assertIn('event.type === "answer_delta"', script_response.text)
        self.assertIn("streamed_answer", script_response.text)
        self.assertIn("publishProgress(progressBody)", script_response.text)
        self.assertIn("return polledBody || latestBody", script_response.text)
        self.assertNotIn("const latestBody = await refreshRun();", script_response.text)
        self.assertNotIn('id="trace-list"', response.text)
        self.assertIn("workspace_id", script_response.text)
        self.assertNotIn("workspace_mode: workspaceMode", script_response.text)
        self.assertNotIn('$("agent-workspace-mode-select")', script_response.text)
        self.assertIn('data-inline-change-action="${recordedLive && applied ? "revert" : "apply"}"', script_response.text)
        self.assertIn("browseWorkspaceDirectories", script_response.text)
        self.assertIn("/workspace-directories", script_response.text)
        self.assertIn("body.directories?.length === 1", script_response.text)
        self.assertIn("系统窗口不可用时", response.text)
        self.assertIn("/workspace-directory-picker", script_response.text)
        self.assertIn("NATIVE_PICKER_LOCAL_ONLY_DETAIL", script_response.text)
        self.assertIn("policyFallback", script_response.text)
        self.assertIn("系统文件夹窗口不可用，已打开备用选择器", script_response.text)
        self.assertIn("createKnowledgeBase", script_response.text)
        self.assertNotIn("repository_id", script_response.text)
        self.assertIn("prefers-reduced-motion", stylesheet_response.text)
        self.assertIn(".execution-process", stylesheet_response.text)
        self.assertIn(".execution-step-marker", stylesheet_response.text)
        self.assertIn(".execution-process.complete:not([open])", stylesheet_response.text)
        self.assertIn("@keyframes execution-spin", stylesheet_response.text)
        self.assertIn(".response-metrics", stylesheet_response.text)
        self.assertIn(".slash-command-menu", stylesheet_response.text)
        self.assertIn(".jump-to-latest", stylesheet_response.text)
        self.assertIn("width: fit-content", stylesheet_response.text)
        self.assertIn(".trace-audit-layout", stylesheet_response.text)
        self.assertIn(".trace-audit-timeline", stylesheet_response.text)
        self.assertIn(".trace-audit-payload", stylesheet_response.text)

    def test_composer_capabilities_expose_effective_skill_and_freeze_invocation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            skill_root = root / "global-skills"
            skill = skill_root / "review" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                """---
name: review
description: Review requested code
agents: [coding]
modes: [default]
context_budget: 1200
tools: []
command:
  name: review
  description: Review requested code
  usage: \"[path]\"
  aliases: [rv]
---
Inspect the requested code before reporting findings.
""",
                encoding="utf-8",
            )
            app = create_app(
                settings=Settings(
                    llm_provider="fake",
                    embedding_provider="local",
                    workspace_allowed_roots=(str(root.resolve()),),
                    background_task_workers=1,
                    skills_enabled=True,
                    skills_directory_path=str(skill_root),
                )
            )
            with TestClient(app) as client:
                client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(workspace)},
                ).raise_for_status()
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "tester"},
                ).json()["id"]

                capabilities = client.get(
                    "/api/v1/agent/composer-capabilities",
                    params={
                        "conversation_id": session_id,
                        "workspace_id": "project",
                    },
                )

                self.assertEqual(capabilities.status_code, 200, capabilities.text)
                self.assertEqual(
                    capabilities.json()["skill_commands"],
                    [{
                        "name": "review",
                        "description": "Review requested code",
                        "usage": "[path]",
                        "aliases": ["rv"],
                        "skill_name": "review",
                        "skill_qualified_name": "user:review",
                        "source": "user",
                    }],
                )
                self.assertEqual(capabilities.json()["mcp_tools"], [])
                self.assertNotIn(
                    "allowed_workspace_modes",
                    capabilities.json(),
                )
                self.assertNotIn(
                    "default_workspace_mode",
                    capabilities.json(),
                )
                self.assertNotIn(
                    "workspace_mode_unavailable_reasons",
                    capabilities.json(),
                )

                started = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "workspace_id": "project",
                        "message": "/review src/app.py",
                        "skill_name": "user:review",
                        "skill_arguments": ["src/app.py"],
                    },
                )
                self.assertEqual(started.status_code, 202, started.text)
                record = app.state.query_service.get_run_for_actor(
                    started.json()["run_id"],
                    None,
                )
                assert record.context_snapshot is not None
                self.assertEqual(
                    record.context_snapshot.metadata.entrypoint_metadata[
                        "skill_invocation"
                    ],
                    {
                        "skill_name": "user:review",
                        "arguments": ["src/app.py"],
                    },
                )
                self.assertIn(
                    "skill://user:review",
                    [
                        item.path
                        for item in record.context_snapshot.instructions.sources
                    ],
                )

    def test_agent_run_uses_server_direct_mode_and_rejects_per_run_override(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
            settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                workspace_allowed_roots=(str(root.resolve()),),
                background_task_workers=1,
                auth_mode="trusted_header",
                gateway_trust_secret="test-gateway-secret",
                live_workspace_writes_enabled=True,
                agent_workspace_default_mode="direct",
                agent_workspace_allowed_modes=("direct",),
            )
            headers = {
                "X-Authenticated-User": "editor-1",
                "X-Gateway-Auth": "test-gateway-secret",
            }
            with TestClient(create_app(settings=settings)) as client:
                client.put(
                    "/api/v1/workspaces/project",
                    headers=headers,
                    json={"root_path": str(workspace)},
                ).raise_for_status()
                session_id = client.post(
                    "/api/v1/sessions",
                    headers=headers,
                    json={"user_id": "ignored"},
                ).json()["id"]
                capabilities = client.get(
                    "/api/v1/agent/composer-capabilities",
                    headers=headers,
                    params={
                        "conversation_id": session_id,
                        "workspace_id": "project",
                    },
                )
                rejected_override = client.post(
                    "/api/v1/agent/runs",
                    headers=headers,
                    json={
                        "conversation_id": session_id,
                        "workspace_id": "project",
                        "workspace_mode": "patch_only",
                        "message": "inspect app.py",
                    },
                )
                started = client.post(
                    "/api/v1/agent/runs",
                    headers=headers,
                    json={
                        "conversation_id": session_id,
                        "workspace_id": "project",
                        "message": "inspect app.py",
                    },
                )

                self.assertEqual(capabilities.status_code, 200, capabilities.text)
                self.assertNotIn("default_workspace_mode", capabilities.json())
                self.assertEqual(
                    rejected_override.status_code,
                    422,
                    rejected_override.text,
                )
                self.assertEqual(started.status_code, 202, started.text)
                self.assertEqual(started.json()["workspace_mode"], "direct")
                self.assertEqual(
                    started.json()["execution_root"], str(workspace.resolve())
                )

    def test_chat_request_accepts_google_provider(self) -> None:
        request = ChatStreamRequest(
            conversation_id="sess_google",
            message="你好",
            provider="google",
            model="gemini-test-model",
            thinking_level="medium",
        )

        self.assertEqual(request.provider, "google")
        self.assertEqual(request.thinking_level, "medium")

    def test_streams_chat_response_and_records_messages(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            session_id = client.post(
                "/api/v1/sessions",
                json={"user_id": "user_1"},
            ).json()["id"]
            client.put(
                "/api/v1/workspaces/workspace_main",
                json={"root_path": temp_dir},
            )
            stream_response = client.post(
                "/api/v1/chat/stream",
                json={
                    "conversation_id": session_id,
                    "message": "解释一下SSE",
                    "workspace_id": "workspace_main",
                },
            )

            self.assertEqual(stream_response.status_code, 200)
            self.assertEqual(
                stream_response.headers["content-type"].split(";")[0],
                "text/event-stream",
            )
            self.assertIn("event: meta", stream_response.text)
            self.assertIn("event: delta", stream_response.text)
            self.assertIn("event: usage", stream_response.text)
            self.assertIn("event: done", stream_response.text)

            messages = client.get(
                f"/api/v1/sessions/{session_id}/messages"
            ).json()["messages"]
            self.assertEqual(
                [message["role"] for message in messages],
                ["user", "assistant"],
            )
            self.assertIn("fake model reply to", messages[1]["content"])
            metrics = client.get("/api/v1/metrics").json()["counters"]
            self.assertEqual(metrics["chat_streams_completed_total"], 1)
            self.assertGreater(metrics["llm_input_tokens_total"], 0)
            self.assertGreater(metrics["llm_output_tokens_total"], 0)
            usage = client.get(
                f"/api/v1/sessions/{session_id}/token-usage"
            ).json()
            self.assertEqual(usage["session_id"], session_id)
            self.assertGreater(usage["input_tokens"], 0)
            self.assertGreater(usage["output_tokens"], 0)
            self.assertEqual(usage["thoughts_tokens"], 0)
            self.assertEqual(
                usage["total_tokens"],
                usage["input_tokens"] + usage["output_tokens"],
            )
            self.assertEqual(len(usage["records"]), 1)
            self.assertGreater(usage["context"]["estimated_tokens"], 0)
            self.assertEqual(usage["context"]["message_count"], 2)
            self.assertEqual(
                usage["workspaces"][0]["workspace_id"],
                "workspace_main",
            )
            workspace_usage = client.get(
                "/api/v1/workspaces/workspace_main/token-usage"
            ).json()
            self.assertEqual(
                workspace_usage["total_tokens"],
                usage["total_tokens"],
            )
            self.assertEqual(workspace_usage["conversation_count"], 1)

    def test_chat_rejects_before_persisting_when_session_budget_is_exhausted(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                llm_provider="fake",
                llm_model="fake-primary",
                session_token_budget=8,
                token_budget_action="reject",
                workspace_allowed_roots=(str(Path(temp_dir).resolve()),),
            )
            with TestClient(create_app(settings=settings)) as client:
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "user_1"},
                ).json()["id"]

                response = client.post(
                    "/api/v1/chat/stream",
                    json={
                        "conversation_id": session_id,
                        "message": "hello",
                    },
                )

                self.assertEqual(response.status_code, 429)
                self.assertEqual(
                    response.json()["detail"]["code"],
                    "token_budget_exceeded",
                )
                messages = client.get(
                    f"/api/v1/sessions/{session_id}/messages"
                ).json()["messages"]
                self.assertEqual(messages, [])

    def test_chat_downgrades_to_registered_cheap_model_over_budget(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                llm_provider="fake",
                llm_model="fake-expensive",
                session_token_budget=8,
                token_budget_action="downgrade",
                token_budget_fallback_provider="fake",
                token_budget_fallback_model="fake-cheap",
                workspace_allowed_roots=(str(Path(temp_dir).resolve()),),
            )
            with TestClient(create_app(settings=settings)) as client:
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "user_1"},
                ).json()["id"]

                response = client.post(
                    "/api/v1/chat/stream",
                    json={
                        "conversation_id": session_id,
                        "message": "hello",
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn('"model": "fake-cheap"', response.text)
                self.assertIn('"requested_model": "fake-expensive"', response.text)
                self.assertIn('"budget_decision": "downgraded"', response.text)
                usage = client.get(
                    f"/api/v1/sessions/{session_id}/token-usage"
                ).json()
                self.assertEqual(usage["records"][0]["model"], "fake-cheap")
                self.assertEqual(
                    usage["records"][0]["requested_model"],
                    "fake-expensive",
                )
                self.assertEqual(
                    usage["records"][0]["budget_decision"],
                    "downgraded",
                )

    def test_chat_enforces_workspace_budget_and_exposes_status(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                llm_provider="fake",
                llm_model="fake-primary",
                workspace_token_budget=8,
                token_budget_action="reject",
                workspace_allowed_roots=(str(Path(temp_dir).resolve()),),
            )
            with TestClient(create_app(settings=settings)) as client:
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "user_1"},
                ).json()["id"]
                client.put(
                    "/api/v1/workspaces/workspace_main",
                    json={"root_path": temp_dir},
                )

                response = client.post(
                    "/api/v1/chat/stream",
                    json={
                        "conversation_id": session_id,
                        "workspace_id": "workspace_main",
                        "message": "hello",
                    },
                )

                self.assertEqual(response.status_code, 429)
                status = client.get(
                    "/api/v1/workspaces/workspace_main/token-usage"
                ).json()
                self.assertEqual(status["budget"]["workspace"]["limit"], 8)
                self.assertEqual(status["budget"]["workspace"]["used"], 0)
                self.assertEqual(status["budget"]["workspace"]["remaining"], 8)

    def test_chat_stream_reports_google_max_tokens_as_error(self) -> None:
        class TruncatedLLMClient:
            def set_usage_ledger(self, usage_ledger):
                self.usage_ledger = usage_ledger

            def prepare_chat_request(self, messages, **kwargs):
                return LLMRequestPlan(
                    requested_provider="google",
                    requested_model="gemini-3.5-flash",
                    provider="google",
                    model="gemini-3.5-flash",
                    input_tokens=12,
                    max_output_tokens=2048,
                    input_count_method="test_exact_count",
                    usage_context=current_model_usage_context(),
                )

            def stream_chat(self, messages, **kwargs):
                self.thinking_level = kwargs.get("thinking_level")
                yield LLMStreamEvent(type="delta", text="partial answer")
                usage = LLMUsage(
                    input_tokens=12,
                    output_tokens=900,
                    thoughts_tokens=1100,
                )
                plan = kwargs["request_plan"]
                self.usage_ledger.record(
                    provider=plan.provider,
                    model=plan.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    thoughts_tokens=usage.thoughts_tokens,
                    input_count_method=plan.input_count_method,
                    context=plan.usage_context,
                )
                yield LLMStreamEvent(
                    type="usage",
                    usage=usage,
                )
                raise LLMProviderError(
                    "Gemini reached the configured output token limit",
                    code="max_output_tokens",
                    finish_reason="MAX_TOKENS",
                )

        truncated_llm = TruncatedLLMClient()
        with TemporaryDirectory() as temp_dir:
            client = TestClient(
                create_app(
                    settings=Settings(
                        llm_provider="google",
                        llm_model="gemini-3.5-flash",
                        workspace_allowed_roots=(str(Path(temp_dir).resolve()),),
                    ),
                    llm_client=truncated_llm,
                )
            )
            with client:
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "user_1"},
                ).json()["id"]
                response = client.post(
                    "/api/v1/chat/stream",
                    json={
                        "conversation_id": session_id,
                        "message": "long answer",
                        "thinking_level": "high",
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn("event: delta", response.text)
                self.assertIn('"thoughts_tokens": 1100', response.text)
                self.assertIn("event: error", response.text)
                self.assertIn('"code": "max_output_tokens"', response.text)
                self.assertIn('"finish_reason": "MAX_TOKENS"', response.text)
                self.assertIn('"partial_response": true', response.text)
                self.assertNotIn("event: done", response.text)
                self.assertEqual(truncated_llm.thinking_level, "high")
                counters = client.get("/api/v1/metrics").json()["counters"]
                self.assertEqual(counters["chat_streams_failed_total"], 1)
                self.assertEqual(counters["llm_thoughts_tokens_total"], 1100)
                usage = client.get(
                    f"/api/v1/sessions/{session_id}/token-usage"
                ).json()
                self.assertEqual(usage["thoughts_tokens"], 1100)
                self.assertEqual(usage["total_tokens"], 2012)
                self.assertIsNone(usage["records"][0]["workspace_id"])

    def test_chat_stream_rejects_missing_session_and_oversized_message(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                llm_max_input_chars=4,
                workspace_allowed_roots=(str(Path(temp_dir).resolve()),),
            )
            with TestClient(create_app(settings=settings)) as client:
                missing = client.post(
                    "/api/v1/chat/stream",
                    json={
                        "conversation_id": "sess_missing",
                        "message": "hi",
                    },
                )
                self.assertEqual(missing.status_code, 404)
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "user_1"},
                ).json()["id"]
                oversized = client.post(
                    "/api/v1/chat/stream",
                    json={
                        "conversation_id": session_id,
                        "message": "hello",
                    },
                )
                self.assertEqual(oversized.status_code, 413)

    def test_independent_knowledge_base_still_ingests_searches_and_answers(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            session_id = client.post(
                "/api/v1/sessions",
                json={"user_id": "user_1"},
            ).json()["id"]
            created = client.post(
                "/api/v1/knowledge-bases",
                json={
                    "id": "docs",
                    "name": "Documentation",
                    "description": "Falcon mode and offline testing guides.",
                    "tags": ["falcon", "testing"],
                },
            )
            self.assertEqual(created.status_code, 201)
            ingested = upload_document(
                client,
                "docs",
                "guide.md",
                "Falcon mode enables deterministic offline testing.",
            )
            self.assertEqual(ingested.status_code, 201)
            self.assertEqual(ingested.json()["index_status"], "active")
            index_job_id = ingested.json()["index_job_id"]
            self.assertTrue(index_job_id.startswith("idx_"))
            jobs = client.get(
                "/api/v1/knowledge-bases/docs/index-jobs"
            ).json()["index_jobs"]
            self.assertEqual([job["status"] for job in jobs], ["active"])
            loaded_job = client.get(
                f"/api/v1/knowledge-bases/docs/index-jobs/{index_job_id}"
            )
            self.assertEqual(loaded_job.status_code, 200)
            self.assertEqual(loaded_job.json()["chunk_count"], 1)
            search = client.post(
                "/api/v1/knowledge-bases/docs/search",
                json={"query": "Falcon deterministic", "limit": 3},
            )
            self.assertEqual(search.status_code, 200)
            self.assertGreaterEqual(len(search.json()["results"]), 1)
            self.assertIsNotNone(search.json()["results"][0]["fusion_score"])
            self.assertFalse(search.json()["retrieval"]["rerank_applied"])
            answer = client.post(
                "/api/v1/knowledge-bases/docs/ask",
                json={
                    "question": "What enables offline testing?",
                    "conversation_id": session_id,
                    "limit": 3,
                },
            )
            self.assertEqual(answer.status_code, 200)
            self.assertGreaterEqual(len(answer.json()["citations"]), 1)
            self.assertFalse(answer.json()["retrieval"]["rerank_applied"])
            operations = {
                record.operation
                for record in client.app.state.usage_ledger.list_all()
            }
            self.assertIn("embedding", operations)
            self.assertIn("rag_ask", operations)
            session_operations = {
                item["operation"]
                for item in client.get(
                    f"/api/v1/sessions/{session_id}/token-usage"
                ).json()["operations"]
            }
            self.assertEqual(session_operations, {"embedding", "rag_ask"})

    def test_knowledge_base_catalog_crud_and_cascade_delete(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            missing_ingest = upload_document(client, "missing", "missing.md", "missing")
            self.assertEqual(missing_ingest.status_code, 404)

            created = client.post(
                "/api/v1/knowledge-bases",
                json={
                    "id": "product_docs",
                    "name": "Product Docs",
                    "description": "Product manuals and API policies.",
                    "tags": ["product", "manual", "product"],
                },
            )
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["tags"], ["product", "manual"])
            duplicate = client.post(
                "/api/v1/knowledge-bases",
                json={
                    "id": "product_docs",
                    "name": "Duplicate",
                    "description": "",
                    "tags": [],
                },
            )
            self.assertEqual(duplicate.status_code, 409)

            ingested = upload_document(
                client,
                "product_docs",
                "manual.md",
                "Falcon mode is enabled from the product settings.",
            )
            self.assertEqual(ingested.status_code, 201)
            loaded = client.get("/api/v1/knowledge-bases/product_docs")
            self.assertEqual(loaded.json()["document_count"], 1)
            updated = client.put(
                "/api/v1/knowledge-bases/product_docs",
                json={
                    "name": "Product Knowledge",
                    "description": "Updated product reference.",
                    "tags": ["product", "reference"],
                },
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["name"], "Product Knowledge")
            listed = client.get("/api/v1/knowledge-bases").json()["knowledge_bases"]
            self.assertEqual([item["id"] for item in listed], ["product_docs"])

            deleted = client.delete("/api/v1/knowledge-bases/product_docs")
            self.assertEqual(deleted.status_code, 204)
            self.assertEqual(
                client.get("/api/v1/knowledge-bases/product_docs").status_code,
                404,
            )
            self.assertEqual(
                client.post(
                    "/api/v1/knowledge-bases/product_docs/search",
                    json={"query": "Falcon"},
                ).status_code,
                404,
            )

    def test_knowledge_document_management_crud_replace_and_bulk_delete(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            client.post(
                "/api/v1/knowledge-bases",
                json={
                    "id": "managed_docs",
                    "name": "Managed Docs",
                    "description": "",
                    "tags": [],
                },
            ).raise_for_status()

            created = client.post(
                "/api/v1/knowledge-bases/managed_docs/documents",
                files={"file": ("guide.md", b"legacy falcon instructions")},
                data={
                    "title": "Falcon guide",
                    "description": "Operator reference",
                    "tags": "falcon, operations",
                },
            )
            self.assertEqual(created.status_code, 201)
            document = created.json()["document"]
            document_id = document["id"]
            self.assertEqual(document["title"], "Falcon guide")
            self.assertEqual(document["tags"], ["falcon", "operations"])
            self.assertEqual(document["byte_size"], len(b"legacy falcon instructions"))
            self.assertEqual(document["last_index_status"], "active")

            duplicate = upload_document(
                client,
                "managed_docs",
                "guide.md",
                "silent replacement must not happen",
            )
            self.assertEqual(duplicate.status_code, 409)
            self.assertEqual(
                duplicate.json()["detail"]["code"],
                "document_filename_conflict",
            )
            self.assertEqual(
                duplicate.json()["detail"]["existing_document_id"],
                document_id,
            )

            listed = client.get(
                "/api/v1/knowledge-bases/managed_docs/documents",
                params={"query": "Falcon", "sort": "title_asc"},
            ).json()
            self.assertEqual(listed["total"], 1)
            self.assertEqual(listed["items"][0]["filename"], "guide.md")

            updated = client.patch(
                f"/api/v1/knowledge-bases/managed_docs/documents/{document_id}",
                json={
                    "title": "Current Falcon guide",
                    "description": "Updated metadata only",
                    "tags": ["current"],
                },
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["title"], "Current Falcon guide")
            self.assertEqual(updated.json()["content_hash"], document["content_hash"])

            replaced = client.put(
                f"/api/v1/knowledge-bases/managed_docs/documents/{document_id}/content",
                files={"file": ("guide.md", b"phoenix mode is the current policy")},
            )
            self.assertEqual(replaced.status_code, 200)
            self.assertEqual(replaced.json()["document_id"], document_id)
            self.assertEqual(
                replaced.json()["document"]["title"],
                "Current Falcon guide",
            )
            search = client.post(
                "/api/v1/knowledge-bases/managed_docs/search",
                json={"query": "phoenix current policy", "limit": 5},
            ).json()
            self.assertTrue(
                any("phoenix" in item["text"] for item in search["results"])
            )
            self.assertFalse(
                any("legacy falcon" in item["text"] for item in search["results"])
            )

            second = upload_document(
                client,
                "managed_docs",
                "runbook.md",
                "runbook content",
            ).json()["document"]
            first_page = client.get(
                "/api/v1/knowledge-bases/managed_docs/documents",
                params={"page": 1, "page_size": 1, "status": "active"},
            ).json()
            second_page = client.get(
                "/api/v1/knowledge-bases/managed_docs/documents",
                params={"page": 2, "page_size": 1, "status": "active"},
            ).json()
            self.assertEqual(first_page["total"], 2)
            self.assertEqual(len(first_page["items"]), 1)
            self.assertEqual(len(second_page["items"]), 1)
            self.assertNotEqual(
                first_page["items"][0]["id"],
                second_page["items"][0]["id"],
            )
            deleted = client.post(
                "/api/v1/knowledge-bases/managed_docs/documents/bulk-delete",
                json={"document_ids": [document_id, "doc_missing", second["id"]]},
            )
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(set(deleted.json()["deleted_ids"]), {document_id, second["id"]})
            self.assertEqual(
                deleted.json()["failures"],
                [
                    {
                        "document_id": "doc_missing",
                        "code": "document_not_found",
                        "message": "document not found in this knowledge base",
                    }
                ],
            )
            self.assertEqual(
                client.get(
                    "/api/v1/knowledge-bases/managed_docs/documents"
                ).json()["total"],
                0,
            )

    def test_failed_document_replacement_keeps_previous_content_searchable(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            client.post(
                "/api/v1/knowledge-bases",
                json={"id": "safe_replace", "name": "Safe", "tags": []},
            ).raise_for_status()
            document_id = upload_document(
                client,
                "safe_replace",
                "policy.md",
                "durable bluebird policy",
            ).json()["document_id"]

            failed = client.put(
                f"/api/v1/knowledge-bases/safe_replace/documents/{document_id}/content",
                files={"file": ("policy.exe", b"not a supported document")},
            )
            self.assertEqual(failed.status_code, 400)
            loaded = client.get(
                f"/api/v1/knowledge-bases/safe_replace/documents/{document_id}"
            ).json()
            self.assertTrue(loaded["is_searchable"])
            self.assertEqual(loaded["last_index_status"], "failed")
            results = client.post(
                "/api/v1/knowledge-bases/safe_replace/search",
                json={"query": "bluebird policy", "limit": 3},
            ).json()["results"]
            self.assertTrue(any("bluebird" in item["text"] for item in results))

    def test_document_upload_rejects_empty_invalid_and_oversized_files(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            client.post(
                "/api/v1/knowledge-bases",
                json={
                    "id": "uploads",
                    "name": "Uploads",
                    "description": "",
                    "tags": [],
                },
            ).raise_for_status()

            empty = upload_document(client, "uploads", "empty.md", b"")
            invalid_utf8 = upload_document(
                client,
                "uploads",
                "invalid.md",
                b"\xff\xfe",
            )
            with patch(
                "ai_agent_platform.api.routes.knowledge_bases.MAX_DOCUMENT_BYTES",
                4,
            ):
                oversized = upload_document(
                    client,
                    "uploads",
                    "large.md",
                    b"12345",
                )

            self.assertEqual(empty.status_code, 400)
            self.assertIn("document text is empty", empty.json()["detail"])
            self.assertEqual(invalid_utf8.status_code, 400)
            self.assertIn("UTF-8", invalid_utf8.json()["detail"])
            self.assertEqual(oversized.status_code, 413)
            self.assertIn("20 MiB", oversized.json()["detail"])

    def test_agent_automatically_routes_to_rag_and_hybrid_context(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text(
                "FALCON_ENABLED = False\n",
                encoding="utf-8",
            )
            with self._client(root) as client:
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "routing-test"},
                ).json()["id"]
                client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(root)},
                ).raise_for_status()
                client.post(
                    "/api/v1/knowledge-bases",
                    json={
                        "id": "falcon_docs",
                        "name": "Falcon Guide",
                        "description": "Falcon mode product policy and setup manual.",
                        "tags": ["Falcon", "manual", "policy"],
                    },
                ).raise_for_status()
                upload_document(
                    client,
                    "falcon_docs",
                    "falcon.md",
                    "Falcon mode enables deterministic offline testing.",
                ).raise_for_status()

                rag_run = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "workspace_id": "project",
                        "message": "根据 Falcon 知识库文档说明它的用途",
                    },
                )
                rag_result = wait_for_run(client, rag_run.json()["run_id"])["result"]
                self.assertEqual(rag_result["context_route"], "rag")
                self.assertEqual(
                    rag_result["selected_knowledge_base_ids"],
                    ["falcon_docs"],
                )
                self.assertTrue(
                    any(
                        item["kind"] == "knowledge_chunk"
                        and item["knowledge_base_id"] == "falcon_docs"
                        and item["path"].startswith("knowledge://falcon_docs/")
                        for item in rag_result["context_sources"]
                    )
                )

                hybrid_run = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "workspace_id": "project",
                        "focus_files": ["app.py"],
                        "message": "根据 Falcon 规范修改 app.py 的实现方案",
                    },
                )
                hybrid_result = wait_for_run(
                    client,
                    hybrid_run.json()["run_id"],
                )["result"]
                self.assertEqual(hybrid_result["context_route"], "hybrid")
                source_kinds = {
                    item["kind"] for item in hybrid_result["context_sources"]
                }
                self.assertIn("knowledge_chunk", source_kinds)
                self.assertIn("file", source_kinds)
                trace_nodes = [item["node"] for item in hybrid_result["trace"]]
                self.assertIn("retrieve_knowledge", trace_nodes)
                self.assertIn("merge_evidence", trace_nodes)

    def test_rag_search_is_scoped_and_rejects_unsupported_types(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            capabilities = client.get("/api/v1/rag/capabilities")
            self.assertEqual(capabilities.status_code, 200)
            self.assertTrue(capabilities.json()["reranker"]["available"])
            self.assertFalse(capabilities.json()["reranker"]["default_enabled"])
            self.assertEqual(
                capabilities.json()["reranker"]["model"],
                "BAAI/bge-reranker-base",
            )
            for knowledge_base_id, name in (
                ("customer_faq", "Customer FAQ"),
                ("hr_policy", "HR Policy"),
            ):
                response = client.post(
                    "/api/v1/knowledge-bases",
                    json={
                        "id": knowledge_base_id,
                        "name": name,
                        "description": name,
                        "tags": [],
                    },
                )
                self.assertEqual(response.status_code, 201)
            upload_document(
                client,
                "customer_faq",
                "refund.md",
                "退款申请需要在订单完成后 7 天内提交。",
            )
            upload_document(
                client,
                "hr_policy",
                "vacation.md",
                "年假需要提前 3 个工作日提交审批。",
            )
            response = client.post(
                "/api/v1/knowledge-bases/hr_policy/search",
                json={"query": "退款规则是什么？", "limit": 5},
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            results = body["results"]
            self.assertGreaterEqual(len(results), 1)
            self.assertFalse(body["retrieval"]["rerank_requested"])
            self.assertFalse(body["retrieval"]["rerank_applied"])
            self.assertEqual(body["retrieval"]["result_count"], len(results))
            self.assertTrue(
                all(result["knowledge_base_id"] == "hr_policy" for result in results)
            )
            unsupported = upload_document(
                client,
                "hr_policy",
                "legacy.doc",
                b"legacy Word binary",
            )
            self.assertEqual(unsupported.status_code, 400)
            self.assertIn("unsupported document type", unsupported.json()["detail"])

    def test_rag_rejects_requested_reranking_when_provider_is_disabled(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(
            Path(temp_dir),
            rag_reranker_provider="none",
        ) as client:
            capabilities = client.get("/api/v1/rag/capabilities")
            self.assertFalse(capabilities.json()["reranker"]["available"])
            created = client.post(
                "/api/v1/knowledge-bases",
                json={
                    "id": "docs",
                    "name": "Docs",
                    "description": "",
                    "tags": [],
                },
            )
            self.assertEqual(created.status_code, 201)

            response = client.post(
                "/api/v1/knowledge-bases/docs/search",
                json={
                    "query": "reranking",
                    "limit": 5,
                    "rerank_enabled": True,
                },
            )

            self.assertEqual(response.status_code, 409)
            self.assertIn("reranker is not configured", response.json()["detail"])

    def test_missing_agent_run_returns_404(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            response = client.get("/api/v1/agent/runs/run_missing")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["detail"], "agent run not found")

    def test_persistent_session_preferences_listing_and_archive_lifecycle(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            with self._client(root) as client:
                client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(workspace)},
                ).raise_for_status()
                preferences = client.get(
                    "/api/v1/users/me/preferences",
                    headers={"X-User-ID": "session_user"},
                ).json()
                self.assertEqual(preferences["default_provider"], "fake")
                self.assertEqual(preferences["default_model"], "demo-stream-model")

                first = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "session_user"},
                ).json()
                client.post(
                    f"/api/v1/sessions/{first['id']}/messages",
                    json={"role": "user", "content": "  Alpha   durable\nconversation  "},
                ).raise_for_status()
                second = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "session_user"},
                ).json()

                listed = client.get(
                    "/api/v1/sessions",
                    params={"limit": 30},
                    headers={"X-User-ID": "session_user"},
                ).json()
                self.assertEqual(
                    [item["id"] for item in listed["sessions"]],
                    [first["id"]],
                )
                self.assertIsNone(listed["next_cursor"])
                self.assertEqual(
                    client.get(
                        "/api/v1/users/me/preferences",
                        headers={"X-User-ID": "session_user"},
                    ).json()["last_active_session_id"],
                    first["id"],
                )
                empty_activation = client.patch(
                    "/api/v1/users/me/preferences",
                    json={"last_active_session_id": second["id"]},
                    headers={"X-User-ID": "session_user"},
                )
                self.assertEqual(empty_activation.status_code, 409)
                client.post(
                    f"/api/v1/sessions/{second['id']}/messages",
                    json={"role": "user", "content": "Beta conversation"},
                    headers={"X-User-ID": "session_user"},
                ).raise_for_status()
                paged = client.get(
                    "/api/v1/sessions",
                    params={"limit": 1},
                    headers={"X-User-ID": "session_user"},
                ).json()
                self.assertEqual(paged["sessions"][0]["id"], second["id"])
                self.assertIsNotNone(paged["next_cursor"])
                searched = client.get(
                    "/api/v1/sessions",
                    params={"q": "DURABLE"},
                    headers={"X-User-ID": "session_user"},
                ).json()["sessions"]
                self.assertEqual([item["id"] for item in searched], [first["id"]])
                self.assertEqual(searched[0]["title"], "Alpha durable conversation")
                self.assertEqual(searched[0]["message_count"], 1)
                self.assertEqual(
                    searched[0]["last_message_preview"],
                    "Alpha durable conversation",
                )

                configured = client.patch(
                    f"/api/v1/sessions/{first['id']}",
                    json={
                        "configuration": {
                            "provider": "fake",
                            "model": "demo-stream-model",
                            "thinking_level": "medium",
                            "workspace_id": "project",
                            "composer_mode": "agent",
                        },
                        "save_configuration_as_default": True,
                    },
                    headers={"X-User-ID": "session_user"},
                )
                self.assertEqual(configured.status_code, 200)
                saved_preferences = client.get(
                    "/api/v1/users/me/preferences",
                    headers={"X-User-ID": "session_user"},
                ).json()
                self.assertEqual(saved_preferences["default_workspace_id"], "project")
                self.assertEqual(saved_preferences["default_composer_mode"], "agent")

                archived = client.patch(
                    f"/api/v1/sessions/{first['id']}",
                    json={"archived": True},
                    headers={"X-User-ID": "session_user"},
                )
                self.assertIsNotNone(archived.json()["archived_at"])
                blocked_message = client.post(
                    f"/api/v1/sessions/{first['id']}/messages",
                    json={"role": "user", "content": "blocked"},
                    headers={"X-User-ID": "session_user"},
                )
                self.assertEqual(blocked_message.status_code, 409)
                blocked_chat = client.post(
                    "/api/v1/chat/stream",
                    json={"conversation_id": first["id"], "message": "blocked"},
                    headers={"X-User-ID": "session_user"},
                )
                self.assertEqual(blocked_chat.status_code, 409)
                archived_list = client.get(
                    "/api/v1/sessions",
                    params={"archived": True},
                    headers={"X-User-ID": "session_user"},
                ).json()["sessions"]
                self.assertEqual([item["id"] for item in archived_list], [first["id"]])

                restored = client.patch(
                    f"/api/v1/sessions/{first['id']}",
                    json={"archived": False},
                    headers={"X-User-ID": "session_user"},
                )
                self.assertIsNone(restored.json()["archived_at"])
                continued = client.post(
                    f"/api/v1/sessions/{first['id']}/messages",
                    json={"role": "user", "content": "continued"},
                    headers={"X-User-ID": "session_user"},
                )
                self.assertEqual(continued.status_code, 201)

    def test_google_usage_metadata_is_normalized(self) -> None:
        class UsageMetadata:
            prompt_token_count = 12
            candidates_token_count = 7
            thoughts_token_count = 5

        usage = _google_usage(UsageMetadata())

        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage.input_tokens, 12)
        self.assertEqual(usage.output_tokens, 7)
        self.assertEqual(usage.thoughts_tokens, 5)
        self.assertEqual(usage.total_tokens, 24)

    @staticmethod
    def _client(allowed_root: Path, **settings_overrides) -> TestClient:
        settings_values = {
            "llm_provider": "fake",
            "embedding_provider": "local",
            "workspace_allowed_roots": (str(allowed_root.resolve()),),
            "background_task_workers": 2,
        }
        settings_values.update(settings_overrides)
        settings = Settings(
            **settings_values,
        )
        return TestClient(create_app(settings=settings))


if __name__ == "__main__":
    unittest.main()
