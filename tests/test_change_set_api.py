from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.main import create_app


class ChangeSetApiTests(unittest.TestCase):
    def test_get_apply_and_reject_change_set_contract(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            (project / "app.py").write_text("before\n", encoding="utf-8")
            settings = Settings(
                workspace_allowed_roots=(str(root),),
                change_set_apply_mode="patch_only",
            )
            app = create_app(settings=settings)
            app.state.workspace_service.register(
                workspace_id="workspace-1",
                root_path=str(project),
            )
            patch_text = _diff("app.py", "before\n", "after\n")
            record = app.state.change_set_service.capture(
                run_id="run-api-1",
                conversation_id="conversation-1",
                workspace_id="workspace-1",
                workspace_root=str(project),
                created_by="author-1",
                snapshot={
                    "source_root": str(project),
                    "changed_files": ["app.py"],
                    "patch": patch_text,
                    "baseline_file_hashes": {
                        "app.py": hashlib.sha256(b"before\n").hexdigest()
                    },
                    "binary_files": [],
                },
                validation_status="passed",
                validation_summary={"passed": True},
            )
            assert record is not None

            with TestClient(app) as client:
                fetched = client.get("/api/v1/agent/runs/run-api-1/changes")
                self.assertEqual(fetched.status_code, 200)
                self.assertEqual(fetched.json()["patch"], patch_text)
                self.assertEqual(fetched.json()["patch_sha256"], record.patch_sha256)

                refused = client.post(
                    "/api/v1/agent/runs/run-api-1/changes/apply",
                    json={
                        "change_set_id": record.id,
                        "patch_sha256": record.patch_sha256,
                    },
                )
                self.assertEqual(refused.status_code, 409)
                self.assertIn("patch_only", refused.json()["detail"])

                rejected = client.post(
                    "/api/v1/agent/runs/run-api-1/changes/reject",
                    json={"change_set_id": record.id},
                )
                self.assertEqual(rejected.status_code, 200)
                self.assertEqual(rejected.json()["status"], "rejected")
                repeated = client.post(
                    "/api/v1/agent/runs/run-api-1/changes/reject",
                    json={"change_set_id": record.id},
                )
                self.assertEqual(repeated.json()["status"], "rejected")

            self.assertEqual(
                (project / "app.py").read_text(encoding="utf-8"),
                "before\n",
            )

    def test_unknown_run_and_mismatched_change_set_return_conflicts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = Settings(workspace_allowed_roots=(temp_dir,))
            with TestClient(create_app(settings=settings)) as client:
                missing = client.get("/api/v1/agent/runs/missing/changes")
                self.assertEqual(missing.status_code, 404)


def _diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


if __name__ == "__main__":
    unittest.main()
