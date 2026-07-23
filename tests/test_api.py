from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.main import create_app


def wait_for_run(client: TestClient, run_id: str) -> dict:
    for _ in range(200):
        body = client.get(f"/api/v1/agent/runs/{run_id}").json()
        if body["status"] in {"completed", "failed", "waiting_approval"}:
            return body
        time.sleep(0.01)
    raise AssertionError("agent run did not finish")


class ApiTests(unittest.TestCase):
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
                self.assertEqual(
                    client.get("/api/v1/workspaces/project").json()["id"],
                    "project",
                )
                listed = client.get("/api/v1/workspaces").json()["workspaces"]
                self.assertEqual([item["id"] for item in listed], ["project"])

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
                sources = result["result"]["context_sources"]
                self.assertTrue(
                    any(item["path"] == "app.py" and "first" in item["text"] for item in sources)
                )
                self.assertNotIn("rag_context", result["result"])

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

    def test_removed_repository_index_endpoints_return_404(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            response = client.post(
                "/api/v1/repositories/repo_main/index",
                json={"root_path": temp_dir},
            )
            self.assertEqual(response.status_code, 404)

    def test_independent_knowledge_base_still_ingests_searches_and_answers(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            ingested = client.post(
                "/api/v1/knowledge-bases/docs/documents",
                json={
                    "filename": "guide.md",
                    "content": "Falcon mode enables deterministic offline testing.",
                },
            )
            self.assertEqual(ingested.status_code, 201)
            search = client.post(
                "/api/v1/knowledge-bases/docs/search",
                json={"query": "Falcon deterministic", "limit": 3},
            )
            self.assertEqual(search.status_code, 200)
            self.assertGreaterEqual(len(search.json()["results"]), 1)
            answer = client.post(
                "/api/v1/knowledge-bases/docs/ask",
                json={"question": "What enables offline testing?", "limit": 3},
            )
            self.assertEqual(answer.status_code, 200)
            self.assertGreaterEqual(len(answer.json()["citations"]), 1)

    @staticmethod
    def _client(allowed_root: Path) -> TestClient:
        settings = Settings(
            workspace_allowed_roots=(str(allowed_root.resolve()),),
            background_task_workers=2,
        )
        return TestClient(create_app(settings))


if __name__ == "__main__":
    unittest.main()
