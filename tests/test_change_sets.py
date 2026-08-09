from __future__ import annotations

import difflib
import hashlib
from dataclasses import replace
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai_agent_platform.repositories import (
    InMemoryChangeSetRepository,
    InMemoryWorkspaceRepository,
)
from ai_agent_platform.services import (
    ChangeSetConflictError,
    ChangeSetInvalidStateError,
    ChangeSetPermissionError,
    ChangeSetService,
    WorkspaceService,
)


class ChangeSetServiceTests(unittest.TestCase):
    def test_capture_persists_full_patch_and_patch_only_never_writes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root, workspace_service = _workspace(temp_dir)
            target = root / "app.py"
            target.write_text("value = 1\n", encoding="utf-8")
            service = _service(workspace_service)

            record = _capture(
                service,
                root,
                before="value = 1\n",
                after="value = 2\n",
            )

            self.assertEqual(record.status, "ready")
            self.assertEqual(record.changed_files, ["app.py"])
            self.assertEqual(record.patch_sha256, _sha256(record.patch.encode()))
            self.assertEqual(
                record.baseline_file_hashes,
                {"app.py": _sha256(b"value = 1\n")},
            )
            with self.assertRaisesRegex(ChangeSetInvalidStateError, "patch_only"):
                service.apply(
                    record.id,
                    expected_patch_sha256=record.patch_sha256,
                    actor_user_id=None,
                )
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")

    def test_lifecycle_emits_metadata_only_audit_callbacks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root, workspace_service = _workspace(temp_dir)
            events: list[dict[str, object]] = []
            service = _service(
                workspace_service,
                audit=lambda **event: events.append(event),
            )
            record = _capture(service, root)
            service.reject(record.id, actor_user_id=None)

            self.assertEqual(
                [item["action"] for item in events],
                ["captured", "rejected"],
            )
            self.assertNotIn("patch", events[0])

    def test_direct_apply_is_digest_bound_and_idempotent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root, workspace_service = _workspace(temp_dir)
            target = root / "app.py"
            target.write_text("value = 1\n", encoding="utf-8")
            roles: list[str] = []
            service = _service(
                workspace_service,
                apply_mode="direct",
                live_writes_enabled=True,
                auth_mode="trusted_header",
                authorize=lambda **kwargs: roles.append(kwargs["required_role"]),
            )
            record = _capture(
                service,
                root,
                before="value = 1\n",
                after="value = 2\n",
            )

            with self.assertRaisesRegex(ChangeSetConflictError, "digest"):
                service.apply(
                    record.id,
                    expected_patch_sha256="0" * 64,
                    actor_user_id="editor-1",
                )
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")

            applied = service.apply(
                record.id,
                expected_patch_sha256=record.patch_sha256,
                actor_user_id="editor-1",
            )
            repeated = service.apply(
                record.id,
                expected_patch_sha256=record.patch_sha256,
                actor_user_id="editor-1",
            )

            self.assertEqual(applied.status, "applied")
            self.assertEqual(repeated, applied)
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 2\n")
            self.assertIn("editor", roles)

    def test_apply_rejects_a_stored_patch_that_no_longer_matches_its_digest(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root, workspace_service = _workspace(temp_dir)
            (root / "app.py").write_text("before\n", encoding="utf-8")
            repository = InMemoryChangeSetRepository()
            service = ChangeSetService(
                repository=repository,
                workspace_service=workspace_service,
                apply_mode="direct",
                live_writes_enabled=True,
                auth_mode="trusted_header",
                authorize=lambda **kwargs: None,
            )
            record = _capture(service, root)
            repository.compare_and_set(
                replace(record, patch=record.patch + "tampered"),
                expected_status="ready",
            )

            with self.assertRaisesRegex(ChangeSetConflictError, "stored"):
                service.apply(
                    record.id,
                    expected_patch_sha256=record.patch_sha256,
                    actor_user_id="editor-1",
                )
            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "before\n")

    def test_concurrent_source_edit_marks_conflict_without_overwrite(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root, workspace_service = _workspace(temp_dir)
            target = root / "app.py"
            target.write_text("value = 1\n", encoding="utf-8")
            service = _service(
                workspace_service,
                apply_mode="direct",
                live_writes_enabled=True,
                auth_mode="trusted_header",
                authorize=lambda **kwargs: None,
            )
            record = _capture(
                service,
                root,
                before="value = 1\n",
                after="value = 2\n",
            )
            target.write_text("value = 'user edit'\n", encoding="utf-8")

            with self.assertRaisesRegex(ChangeSetConflictError, "changed"):
                service.apply(
                    record.id,
                    expected_patch_sha256=record.patch_sha256,
                    actor_user_id="editor-1",
                )

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "value = 'user edit'\n",
            )
            self.assertEqual(
                service.get(record.id, actor_user_id="editor-1").status,
                "conflicted",
            )

    def test_apply_failure_restores_all_original_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root, workspace_service = _workspace(temp_dir)
            target = root / "app.py"
            target.write_text("value = 1\n", encoding="utf-8")
            service = _service(
                workspace_service,
                apply_mode="direct",
                live_writes_enabled=True,
                auth_mode="trusted_header",
                authorize=lambda **kwargs: None,
            )
            record = _capture(
                service,
                root,
                before="value = 1\n",
                after="value = 2\n",
            )

            def failing_apply(
                apply_root: Path,
                _patch: str,
                *,
                check: bool,
                timeout: float,
            ) -> None:
                del timeout
                if not check:
                    (apply_root / "app.py").write_text(
                        "partially written\n",
                        encoding="utf-8",
                    )
                    raise RuntimeError("simulated apply failure")

            with patch(
                "ai_agent_platform.services.change_set_service._run_git_apply",
                side_effect=failing_apply,
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated"):
                    service.apply(
                        record.id,
                        expected_patch_sha256=record.patch_sha256,
                        actor_user_id="editor-1",
                    )

            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")
            self.assertEqual(
                service.get(record.id, actor_user_id="editor-1").status,
                "failed",
            )

    def test_capture_rejects_unsafe_binary_and_mismatched_roots(self) -> None:
        cases = [
            {
                "path": "../outside.py",
                "binary_files": [],
                "source_root": None,
            },
            {
                "path": ".env",
                "binary_files": [],
                "source_root": None,
            },
            {
                "path": "asset.bin",
                "binary_files": ["asset.bin"],
                "source_root": None,
            },
        ]
        for index, case in enumerate(cases):
            with self.subTest(case=case), TemporaryDirectory() as temp_dir:
                root, workspace_service = _workspace(temp_dir)
                service = _service(workspace_service)
                relative = case["path"]
                snapshot = {
                    "source_root": str(root),
                    "changed_files": [relative],
                    "patch": _diff(relative, "before\n", "after\n"),
                    "baseline_file_hashes": {relative: _sha256(b"before\n")},
                    "binary_files": case["binary_files"],
                }
                record = service.capture(
                    run_id=f"run-{index}",
                    conversation_id="conversation-1",
                    workspace_id="workspace-1",
                    workspace_root=str(root),
                    created_by="author-1",
                    snapshot=snapshot,
                    validation_status="passed",
                    validation_summary={},
                )
                self.assertIsNotNone(record)
                self.assertEqual(record.status, "failed")

        with TemporaryDirectory() as temp_dir:
            root, workspace_service = _workspace(temp_dir)
            service = _service(workspace_service)
            record = service.capture(
                run_id="run-root-mismatch",
                conversation_id="conversation-1",
                workspace_id="workspace-1",
                workspace_root=str(root),
                created_by="author-1",
                snapshot={
                    "source_root": str(root.parent),
                    "changed_files": ["app.py"],
                    "patch": _diff("app.py", "before\n", "after\n"),
                    "baseline_file_hashes": {"app.py": _sha256(b"before\n")},
                    "binary_files": [],
                },
                validation_status="passed",
                validation_summary={},
            )
            self.assertEqual(record.status, "failed")
            self.assertIn("workspace root", record.error)

    def test_failed_validation_is_persisted_but_cannot_be_promoted(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root, workspace_service = _workspace(temp_dir)
            service = _service(workspace_service)
            record = service.capture(
                run_id="run-validation-failed",
                conversation_id="conversation-1",
                workspace_id="workspace-1",
                workspace_root=str(root),
                created_by="author-1",
                snapshot={
                    "source_root": str(root),
                    "changed_files": ["app.py"],
                    "patch": _diff("app.py", "before\n", "broken\n"),
                    "baseline_file_hashes": {"app.py": _sha256(b"before\n")},
                    "binary_files": [],
                },
                validation_status="validation_failed",
                validation_summary={"passed": False},
            )

            self.assertEqual(record.status, "failed")
            self.assertIn("not promotable", record.error)

    def test_reject_requires_editor_and_is_idempotent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root, workspace_service = _workspace(temp_dir)

            def authorize(**kwargs) -> None:
                if kwargs["required_role"] == "editor":
                    raise ChangeSetPermissionError("editor required")

            denied_service = _service(
                workspace_service,
                auth_mode="trusted_header",
                authorize=authorize,
            )
            denied = _capture(denied_service, root)
            with self.assertRaisesRegex(ChangeSetPermissionError, "editor"):
                denied_service.reject(denied.id, actor_user_id="viewer-1")

        with TemporaryDirectory() as temp_dir:
            root, workspace_service = _workspace(temp_dir)
            service = _service(
                workspace_service,
                auth_mode="trusted_header",
                authorize=lambda **kwargs: None,
            )
            record = _capture(service, root)
            rejected = service.reject(record.id, actor_user_id="editor-1")
            self.assertEqual(rejected.status, "rejected")
            self.assertEqual(
                service.reject(record.id, actor_user_id="editor-1"),
                rejected,
            )

    def test_worktree_mode_applies_to_new_branch_without_touching_source(self) -> None:
        with TemporaryDirectory() as temp_dir:
            allowed = Path(temp_dir)
            root = allowed / "project"
            worktrees = allowed / "worktrees"
            root.mkdir()
            target = root / "app.py"
            target.write_text("value = 1\n", encoding="utf-8")
            _git(root, "init")
            _git(root, "config", "user.email", "tests@example.invalid")
            _git(root, "config", "user.name", "ChangeSet Tests")
            _git(root, "add", "app.py")
            _git(root, "commit", "-m", "initial")
            head = _git(root, "rev-parse", "HEAD").strip()
            workspace_service = WorkspaceService(
                store=InMemoryWorkspaceRepository(),
                allowed_roots=(str(allowed),),
            )
            workspace_service.register(
                workspace_id="workspace-1",
                root_path=str(root),
            )
            service = _service(
                workspace_service,
                apply_mode="worktree",
                live_writes_enabled=True,
                auth_mode="trusted_header",
                authorize=lambda **kwargs: None,
                worktree_parent=str(worktrees),
            )
            record = _capture(
                service,
                root,
                before="value = 1\n",
                after="value = 2\n",
                base_git_head=head,
                base_git_dirty=False,
            )

            applied = service.apply(
                record.id,
                expected_patch_sha256=record.patch_sha256,
                actor_user_id="editor-1",
            )

            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")
            self.assertEqual(applied.status, "applied")
            self.assertTrue(applied.branch_name.startswith("codex/"))
            self.assertEqual(
                (Path(applied.worktree_path) / "app.py").read_text(encoding="utf-8"),
                "value = 2\n",
            )

    def test_live_writes_require_authentication(self) -> None:
        with TemporaryDirectory() as temp_dir:
            _root, workspace_service = _workspace(temp_dir)
            with self.assertRaisesRegex(ValueError, "authenticated"):
                _service(
                    workspace_service,
                    apply_mode="direct",
                    live_writes_enabled=True,
                    auth_mode="disabled",
                )
            service = _service(
                workspace_service,
                apply_mode="direct",
                live_writes_enabled=False,
                auth_mode="trusted_header",
                authorize=lambda **kwargs: None,
            )
            root = Path(workspace_service.resolve_for_run("workspace-1"))
            (root / "app.py").write_text("before\n", encoding="utf-8")
            record = _capture(service, root, before="before\n", after="after\n")
            with self.assertRaisesRegex(ChangeSetPermissionError, "disabled"):
                service.apply(
                    record.id,
                    expected_patch_sha256=record.patch_sha256,
                    actor_user_id="editor-1",
                )


def _workspace(temp_dir: str) -> tuple[Path, WorkspaceService]:
    allowed = Path(temp_dir)
    root = allowed / "project"
    root.mkdir()
    service = WorkspaceService(
        store=InMemoryWorkspaceRepository(),
        allowed_roots=(str(allowed),),
    )
    service.register(workspace_id="workspace-1", root_path=str(root))
    return root, service


def _service(
    workspace_service: WorkspaceService,
    **overrides,
) -> ChangeSetService:
    return ChangeSetService(
        repository=InMemoryChangeSetRepository(),
        workspace_service=workspace_service,
        **overrides,
    )


def _capture(
    service: ChangeSetService,
    root: Path,
    *,
    before: str = "before\n",
    after: str = "after\n",
    base_git_head: str | None = None,
    base_git_dirty: bool | None = None,
):
    patch_text = _diff("app.py", before, after)
    record = service.capture(
        run_id="run-1",
        conversation_id="conversation-1",
        workspace_id="workspace-1",
        workspace_root=str(root),
        created_by="author-1",
        snapshot={
            "source_root": str(root),
            "changed_files": ["app.py"],
            "patch": patch_text,
            "baseline_file_hashes": {"app.py": _sha256(before.encode())},
            "binary_files": [],
            "base_git_head": base_git_head,
            "base_git_dirty": base_git_dirty,
        },
        validation_status="passed",
        validation_summary={"passed": True},
    )
    assert record is not None
    return record


def _diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


if __name__ == "__main__":
    unittest.main()
