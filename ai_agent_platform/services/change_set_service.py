from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import hmac
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from threading import Lock
from typing import Any, Callable, Mapping
from uuid import uuid4

from ai_agent_platform.domain import ChangeSetRecord
from ai_agent_platform.repositories import ChangeSetRepository
from ai_agent_platform.services.workspace_service import WorkspaceService
from ai_agent_platform.integrations.permissions import (
    PermissionRequest,
    PermissionResolver,
    ToolUseContext,
)


SENSITIVE_FILENAMES = {
    ".env",
    ".envrc",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
    "service-account.json",
    "service_account.json",
}
SENSITIVE_DIRNAMES = {".aws", ".azure", ".docker", ".gnupg", ".kube", ".ssh"}
SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
SAFE_ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}
_HUNK_HEADER = re.compile(
    r"^@@ -(?:\d+)(?:,(\d+))? \+(?:\d+)(?:,(\d+))? @@(?: .*)?$"
)


class ChangeSetNotFoundError(Exception):
    pass


class ChangeSetInvalidStateError(Exception):
    pass


class ChangeSetValidationError(Exception):
    pass


class ChangeSetConflictError(Exception):
    pass


class ChangeSetPermissionError(PermissionError):
    pass


class ChangeSetService:
    def __init__(
        self,
        *,
        repository: ChangeSetRepository,
        workspace_service: WorkspaceService,
        authorize: Callable[..., None] | None = None,
        live_writes_enabled: bool = False,
        apply_mode: str = "patch_only",
        auth_mode: str = "disabled",
        max_files: int = 100,
        max_patch_chars: int = 1_000_000,
        worktree_parent: str | None = None,
        branch_prefix: str = "codex/",
        command_timeout_seconds: float = 30.0,
        audit: Callable[..., None] | None = None,
        permission_resolver: PermissionResolver | None = None,
        role_for: Callable[..., str | None] | None = None,
    ) -> None:
        if apply_mode not in {"patch_only", "direct", "worktree"}:
            raise ValueError("unsupported ChangeSet apply mode")
        if live_writes_enabled and auth_mode == "disabled":
            raise ValueError("live workspace writes require authenticated requests")
        self._repository = repository
        self._workspace_service = workspace_service
        self._authorize = authorize
        self._live_writes_enabled = live_writes_enabled
        self._apply_mode = apply_mode
        self._auth_mode = auth_mode
        self._max_files = max_files
        self._max_patch_chars = max_patch_chars
        self._worktree_parent = (
            Path(worktree_parent).expanduser().resolve()
            if worktree_parent
            else Path(tempfile.gettempdir()).resolve()
        )
        self._branch_prefix = branch_prefix
        self._command_timeout_seconds = command_timeout_seconds
        self._audit_callback = audit
        self._permission_resolver = permission_resolver
        self._role_for = role_for
        self._locks: dict[str, Lock] = {}
        self._locks_guard = Lock()
        self._run_write_guard: Callable[[str], None] | None = None

    def set_audit_callback(self, callback: Callable[..., None] | None) -> None:
        self._audit_callback = callback

    def set_run_write_guard(self, guard: Callable[[str], None]) -> None:
        self._run_write_guard = guard

    def _assert_run_writable(self, record: ChangeSetRecord) -> None:
        if self._run_write_guard is not None:
            try:
                self._run_write_guard(record.run_id)
            except Exception as exc:
                if getattr(exc, "code", None) == "legacy_run_read_only":
                    raise ChangeSetInvalidStateError("legacy_run_read_only") from exc
                from ai_agent_platform.agents.coding.models import AgentRunNotFoundError
                if isinstance(exc, AgentRunNotFoundError):
                    raise ChangeSetNotFoundError("The source Run is missing; changes cannot be applied") from exc
                raise

    def capture(
        self,
        *,
        run_id: str,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
        created_by: str,
        snapshot: dict[str, Any],
        validation_status: str,
        validation_summary: dict[str, object],
    ) -> ChangeSetRecord | None:
        changed_files = [str(item) for item in snapshot.get("changed_files") or []]
        if not changed_files:
            return None
        patch = str(snapshot.get("patch") or "")
        baseline_hashes = {
            str(path): (str(value) if value is not None else None)
            for path, value in dict(snapshot.get("baseline_file_hashes") or {}).items()
        }
        workspace = self._workspace_service.get(workspace_id)
        authoritative_root = self._workspace_service.resolve_for_run(workspace_id)
        execution_mode = str(snapshot.get("mode") or self._apply_mode)
        if execution_mode not in {"patch_only", "direct", "worktree"}:
            execution_mode = self._apply_mode
        execution_root = str(
            Path(snapshot.get("execution_root") or authoritative_root)
            .expanduser()
            .resolve()
        )
        recorded_live_change = bool(
            snapshot.get("mode") in {"direct", "worktree"}
        )
        validation_error = self._validate_capture(
            changed_files=changed_files,
            patch=patch,
            baseline_hashes=baseline_hashes,
            binary_files=[str(item) for item in snapshot.get("binary_files") or []],
        )
        post_write_hashes = {
            str(path): (str(value) if value is not None else None)
            for path, value in dict(
                snapshot.get("post_write_file_hashes") or {}
            ).items()
        }
        if recorded_live_change and (
            set(post_write_hashes) != set(changed_files)
            or any(
                value is not None and re.fullmatch(r"[a-f0-9]{64}", value) is None
                for value in post_write_hashes.values()
            )
        ):
            validation_error = validation_error or (
                "live ChangeSet post-write hashes are incomplete or invalid"
            )
        supplied_roots = {
            str(Path(value).expanduser().resolve())
            for value in (workspace_root, snapshot.get("source_root"))
            if value
        }
        if supplied_roots != {authoritative_root}:
            validation_error = (
                validation_error
                or "ChangeSet workspace root does not match the registered workspace"
            )
        validation_diagnostic = None
        if validation_status not in {"passed", "validated", "changes_ready"}:
            validation_diagnostic = (
                f"ChangeSet validation ended with status {validation_status}"
            )
            if not recorded_live_change:
                validation_error = validation_error or (
                    f"ChangeSet is not promotable from validation status {validation_status}"
                )
        if validation_status in {"passed", "validated"} and not bool(
            validation_summary.get("passed")
        ):
            validation_diagnostic = (
                "ChangeSet validation summary does not confirm a passing result"
            )
            if not recorded_live_change:
                validation_error = validation_error or validation_diagnostic
        if recorded_live_change and execution_mode == "direct" and execution_root != authoritative_root:
            validation_error = validation_error or (
                "direct ChangeSet execution root does not match the source workspace"
            )
        if recorded_live_change and execution_mode == "worktree":
            worktree_value = snapshot.get("worktree_path")
            if not worktree_value or execution_root != str(
                Path(str(worktree_value)).expanduser().resolve()
            ):
                validation_error = validation_error or (
                    "worktree ChangeSet is missing its frozen execution root"
                )
        base_git_head = (
            str(snapshot["base_git_head"])
            if snapshot.get("base_git_head")
            else None
        )
        if base_git_head is not None and re.fullmatch(
            r"[0-9a-fA-F]{40,64}",
            base_git_head,
        ) is None:
            validation_error = validation_error or "invalid captured Git HEAD"
        now = _now()
        record = ChangeSetRecord(
            id=f"chg_{uuid4().hex[:12]}",
            run_id=run_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=authoritative_root,
            workspace_revision=workspace.revision,
            created_by=created_by,
            apply_mode=execution_mode,
            base_git_head=base_git_head,
            baseline_file_hashes=baseline_hashes,
            changed_files=changed_files,
            patch=patch,
            patch_sha256=_sha256_text(patch),
            validation_status=validation_status,
            validation_summary={
                **validation_summary,
                "copy_warnings": list(snapshot.get("copy_warnings") or []),
                "base_git_dirty": snapshot.get("base_git_dirty"),
                "execution_root": execution_root,
                "post_write_file_hashes": {
                    **post_write_hashes
                },
                "cleanup_policy": snapshot.get("cleanup_policy"),
                "mutation_journal": snapshot.get("mutation_journal"),
                "validation_diagnostic": validation_diagnostic,
            },
            status=(
                "applied"
                if recorded_live_change
                else "failed"
                if validation_error
                else "ready"
            ),
            created_at=now,
            updated_at=now,
            applied_by=(created_by if recorded_live_change else None),
            applied_at=(now if recorded_live_change else None),
            error=validation_error,
            branch_name=(
                str(snapshot.get("branch_name"))
                if snapshot.get("branch_name")
                else None
            ),
            worktree_path=(
                str(snapshot.get("worktree_path"))
                if snapshot.get("worktree_path")
                else None
            ),
        )
        existing = self._repository.create(record)
        if existing.patch_sha256 != record.patch_sha256:
            raise ChangeSetConflictError(
                "run already has a ChangeSet with a different patch"
            )
        if existing.id == record.id:
            self._audit(existing, "captured", created_by)
        return existing

    def get_for_run(
        self,
        run_id: str,
        *,
        actor_user_id: str | None,
    ) -> ChangeSetRecord:
        record = self._repository.get_by_run(run_id)
        if record is None:
            raise ChangeSetNotFoundError(run_id)
        self._authorize_role(
            record,
            actor_user_id,
            "viewer",
            action="changeset.read",
        )
        return record

    def get(
        self,
        change_set_id: str,
        *,
        actor_user_id: str | None,
    ) -> ChangeSetRecord:
        record = self._repository.get(change_set_id)
        if record is None:
            raise ChangeSetNotFoundError(change_set_id)
        self._authorize_role(
            record,
            actor_user_id,
            "viewer",
            action="changeset.read",
        )
        return record

    def reject(
        self,
        change_set_id: str,
        *,
        actor_user_id: str | None,
    ) -> ChangeSetRecord:
        current = self.get(change_set_id, actor_user_id=actor_user_id)
        self._assert_run_writable(current)
        self._authorize_role(
            current,
            actor_user_id,
            "editor",
            action="changeset.reject",
        )
        if current.status == "rejected":
            return current
        if current.status != "ready":
            raise ChangeSetInvalidStateError(
                f"ChangeSet cannot be rejected from status {current.status}"
            )
        updated = replace(current, status="rejected", updated_at=_now())
        saved = self._repository.compare_and_set(updated, expected_status="ready")
        if saved is None:
            raise ChangeSetInvalidStateError("ChangeSet status changed concurrently")
        self._audit(saved, "rejected", actor_user_id)
        return saved

    def apply(
        self,
        change_set_id: str,
        *,
        expected_patch_sha256: str,
        actor_user_id: str | None,
    ) -> ChangeSetRecord:
        current = self.get(change_set_id, actor_user_id=actor_user_id)
        self._assert_run_writable(current)
        self._authorize_role(
            current,
            actor_user_id,
            "editor",
            action="changeset.apply",
            arguments={"patch_sha256": expected_patch_sha256},
        )
        if current.status == "applied":
            if hmac.compare_digest(current.patch_sha256, expected_patch_sha256):
                return current
            raise ChangeSetConflictError("approved patch digest does not match")
        if current.status != "ready":
            raise ChangeSetInvalidStateError(
                f"ChangeSet cannot be applied from status {current.status}"
            )
        if not hmac.compare_digest(current.patch_sha256, expected_patch_sha256):
            raise ChangeSetConflictError("approved patch digest does not match")
        if not hmac.compare_digest(
            _sha256_text(current.patch),
            current.patch_sha256,
        ):
            raise ChangeSetConflictError("stored ChangeSet patch digest is invalid")
        if current.apply_mode == "patch_only":
            raise ChangeSetInvalidStateError(
                "ChangeSet is configured for patch_only and cannot write a workspace"
            )
        if not self._live_writes_enabled:
            raise ChangeSetPermissionError("live workspace writes are disabled")
        if self._auth_mode == "disabled" or actor_user_id is None:
            raise ChangeSetPermissionError("authenticated editor identity is required")

        with self._workspace_lock(current.workspace_id):
            current = self._repository.get(change_set_id) or current
            if current.status == "applied":
                return current
            if current.status != "ready":
                raise ChangeSetInvalidStateError(
                    f"ChangeSet cannot be applied from status {current.status}"
                )
            try:
                root = self._validate_target(current)
            except ChangeSetConflictError as exc:
                conflicted = replace(
                    current,
                    status="conflicted",
                    updated_at=_now(),
                    error=str(exc),
                )
                saved = self._repository.compare_and_set(
                    conflicted,
                    expected_status="ready",
                )
                if saved is None:
                    raise ChangeSetInvalidStateError(
                        "ChangeSet status changed concurrently"
                    ) from exc
                self._audit(saved, "conflicted", actor_user_id)
                raise

            applying = replace(current, status="applying", updated_at=_now(), error=None)
            saved = self._repository.compare_and_set(
                applying,
                expected_status="ready",
            )
            if saved is None:
                raise ChangeSetInvalidStateError("ChangeSet status changed concurrently")
            try:
                if current.apply_mode == "direct":
                    branch_name = None
                    worktree_path = None
                    self._apply_direct(current, root)
                else:
                    branch_name, worktree_path = self._apply_worktree(current, root)
                completed = replace(
                    applying,
                    status="applied",
                    updated_at=_now(),
                    applied_by=actor_user_id,
                    applied_at=_now(),
                    branch_name=branch_name,
                    worktree_path=worktree_path,
                )
                final = self._repository.compare_and_set(
                    completed,
                    expected_status="applying",
                )
                if final is None:
                    raise ChangeSetInvalidStateError(
                        "ChangeSet completion status changed concurrently"
                    )
                self._audit(final, "applied", actor_user_id)
                return final
            except Exception as exc:
                failed = replace(
                    applying,
                    status="failed",
                    updated_at=_now(),
                    error=str(exc),
                )
                saved_failed = self._repository.compare_and_set(
                    failed,
                    expected_status="applying",
                )
                if saved_failed is not None:
                    self._audit(saved_failed, "failed", actor_user_id)
                raise

    def revert(
        self,
        change_set_id: str,
        *,
        expected_patch_sha256: str,
        actor_user_id: str | None,
    ) -> ChangeSetRecord:
        current = self.get(change_set_id, actor_user_id=actor_user_id)
        self._assert_run_writable(current)
        self._authorize_role(
            current,
            actor_user_id,
            "editor",
            action="changeset.revert",
            arguments={
                "change_set_id": change_set_id,
                "patch_sha256": expected_patch_sha256,
            },
        )
        if not hmac.compare_digest(current.patch_sha256, expected_patch_sha256):
            raise ChangeSetConflictError("approved patch digest does not match")
        if current.status == "reverted":
            return current
        if current.apply_mode == "patch_only":
            raise ChangeSetInvalidStateError(
                "patch_only ChangeSet did not write an execution target"
            )
        if current.status != "applied":
            raise ChangeSetInvalidStateError(
                f"ChangeSet cannot be reverted from status {current.status}"
            )
        if not self._live_writes_enabled:
            raise ChangeSetPermissionError("live workspace writes are disabled")
        if self._auth_mode == "disabled" or actor_user_id is None:
            raise ChangeSetPermissionError("authenticated editor identity is required")
        if not hmac.compare_digest(_sha256_text(current.patch), current.patch_sha256):
            raise ChangeSetConflictError("stored ChangeSet patch digest is invalid")

        with self._workspace_lock(current.workspace_id):
            current = self._repository.get(change_set_id) or current
            if current.status == "reverted":
                return current
            if current.status != "applied":
                raise ChangeSetInvalidStateError(
                    f"ChangeSet cannot be reverted from status {current.status}"
                )
            try:
                root = self._revert_target(current)
                post_hashes = {
                    str(path): (str(value) if value is not None else None)
                    for path, value in dict(
                        current.validation_summary.get("post_write_file_hashes")
                        or {}
                    ).items()
                }
                if set(post_hashes) != set(current.changed_files):
                    raise ChangeSetValidationError(
                        "ChangeSet post-write hashes are incomplete"
                    )
                _validate_file_hashes(
                    root,
                    post_hashes,
                    conflict_prefix="execution target changed after the Agent run",
                )
                backups = _backup_targets(root, current.changed_files)
                _run_git_apply_reverse(
                    root,
                    current.patch,
                    check=True,
                    timeout=self._command_timeout_seconds,
                )
                try:
                    _run_git_apply_reverse(
                        root,
                        current.patch,
                        check=False,
                        timeout=self._command_timeout_seconds,
                    )
                    _validate_file_hashes(
                        root,
                        current.baseline_file_hashes,
                        conflict_prefix="revert did not restore the Agent baseline",
                    )
                except Exception:
                    _restore_targets(root, backups)
                    raise
            except ChangeSetConflictError as exc:
                conflicted = replace(current, updated_at=_now(), error=str(exc))
                saved = self._repository.compare_and_set(
                    conflicted,
                    expected_status="applied",
                )
                if saved is not None:
                    self._audit(saved, "revert_conflicted", actor_user_id)
                raise

            now = _now()
            reverted = replace(
                current,
                status="reverted",
                updated_at=now,
                error=None,
                validation_summary={
                    **current.validation_summary,
                    "reverted_at": now.isoformat(),
                    "reverted_by": actor_user_id,
                },
            )
            saved = self._repository.compare_and_set(
                reverted,
                expected_status="applied",
            )
            if saved is None:
                raise ChangeSetInvalidStateError(
                    "ChangeSet status changed concurrently"
                )
            self._audit(saved, "reverted", actor_user_id)
            return saved

    def _revert_target(self, record: ChangeSetRecord) -> Path:
        workspace = self._workspace_service.get(record.workspace_id)
        source = Path(
            self._workspace_service.resolve_for_run(record.workspace_id)
        ).resolve()
        if (
            str(source) != record.workspace_root
            or workspace.revision != record.workspace_revision
        ):
            raise ChangeSetConflictError(
                "workspace registration changed after the Agent run"
            )
        if record.apply_mode == "direct":
            return source
        if not record.worktree_path:
            raise ChangeSetValidationError("worktree ChangeSet path is unavailable")
        worktree = Path(record.worktree_path).expanduser().resolve()
        if (
            self._worktree_parent not in worktree.parents
            or not worktree.name.startswith("agent-worktree-")
            or worktree.is_symlink()
            or not worktree.exists()
            or not worktree.is_dir()
        ):
            raise ChangeSetConflictError("Agent worktree is no longer available")
        return worktree

    def _validate_capture(
        self,
        *,
        changed_files: list[str],
        patch: str,
        baseline_hashes: dict[str, str | None],
        binary_files: list[str],
    ) -> str | None:
        try:
            if len(changed_files) > self._max_files:
                raise ChangeSetValidationError("ChangeSet exceeds the file limit")
            if not patch or len(patch) > self._max_patch_chars:
                raise ChangeSetValidationError("ChangeSet patch is empty or too large")
            if binary_files:
                raise ChangeSetValidationError(
                    "binary changes cannot be represented safely: "
                    + ", ".join(binary_files)
                )
            if any(
                marker in patch
                for marker in ("GIT binary patch", "Binary files ", " mode 120000")
            ):
                raise ChangeSetValidationError(
                    "binary or symbolic-link patches cannot be represented safely"
                )
            normalized = [_validate_relative_path(path) for path in changed_files]
            if len(set(normalized)) != len(normalized):
                raise ChangeSetValidationError("ChangeSet contains duplicate paths")
            if set(normalized) != set(baseline_hashes):
                raise ChangeSetValidationError("baseline hashes do not match changed files")
            if _patch_paths(patch) != set(normalized):
                raise ChangeSetValidationError("patch paths do not match changed files")
            for path in normalized:
                _reject_sensitive_path(path)
        except ChangeSetValidationError as exc:
            return str(exc)
        return None

    def _validate_target(self, record: ChangeSetRecord) -> Path:
        workspace = self._workspace_service.get(record.workspace_id)
        root = Path(self._workspace_service.resolve_for_run(record.workspace_id)).resolve()
        if str(root) != record.workspace_root or workspace.revision != record.workspace_revision:
            raise ChangeSetConflictError("workspace registration changed after the Agent run")
        for relative, expected_hash in record.baseline_file_hashes.items():
            target = _safe_target(root, relative)
            if expected_hash is None:
                if target.exists() or target.is_symlink():
                    raise ChangeSetConflictError(
                        f"workspace path was created after the Agent run: {relative}"
                    )
                continue
            if not target.exists() or target.is_symlink() or not target.is_file():
                raise ChangeSetConflictError(
                    f"workspace path changed type after the Agent run: {relative}"
                )
            if _sha256_bytes(target.read_bytes()) != expected_hash:
                raise ChangeSetConflictError(
                    f"workspace file changed after the Agent run: {relative}"
                )
        return root

    def _apply_direct(self, record: ChangeSetRecord, root: Path) -> None:
        backups = _backup_targets(root, record.changed_files)
        _run_git_apply(root, record.patch, check=True, timeout=self._command_timeout_seconds)
        try:
            _run_git_apply(
                root,
                record.patch,
                check=False,
                timeout=self._command_timeout_seconds,
            )
        except Exception:
            _restore_targets(root, backups)
            raise

    def _apply_worktree(
        self,
        record: ChangeSetRecord,
        root: Path,
    ) -> tuple[str, str]:
        if record.base_git_head is None:
            raise ChangeSetValidationError("worktree mode requires a Git repository")
        if record.validation_summary.get("base_git_dirty") is not False:
            raise ChangeSetConflictError(
                "worktree mode requires a clean source workspace at capture time"
            )
        self._worktree_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root == self._worktree_parent or root in self._worktree_parent.parents:
            raise ChangeSetValidationError(
                "worktree parent must not contain the source workspace"
            )
        safe_run = re.sub(r"[^A-Za-z0-9._-]+", "-", record.run_id).strip("-")
        branch = f"{self._branch_prefix}{safe_run or record.id}"
        worktree = self._worktree_parent / f"agent-worktree-{safe_run}-{uuid4().hex[:8]}"
        _run_command(
            [
                "git",
                "-C",
                str(root),
                "worktree",
                "add",
                "-b",
                branch,
                str(worktree),
                record.base_git_head,
            ],
            timeout=self._command_timeout_seconds,
        )
        try:
            _run_git_apply(
                worktree,
                record.patch,
                check=True,
                timeout=self._command_timeout_seconds,
            )
            _run_git_apply(
                worktree,
                record.patch,
                check=False,
                timeout=self._command_timeout_seconds,
            )
        except Exception:
            _remove_worktree(root, worktree, branch, self._command_timeout_seconds)
            raise
        return branch, str(worktree)

    def _authorize_role(
        self,
        record: ChangeSetRecord,
        actor_user_id: str | None,
        required_role: str,
        *,
        action: str,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        if actor_user_id is None:
            if self._auth_mode == "disabled":
                return
            raise ChangeSetPermissionError("authenticated identity is required")
        if self._authorize is not None:
            self._authorize(
                workspace_id=record.workspace_id,
                actor_user_id=actor_user_id,
                required_role=required_role,
            )
        if self._permission_resolver is None:
            return
        role = required_role
        if actor_user_id is not None and self._role_for is not None:
            role = str(
                self._role_for(
                    workspace_id=record.workspace_id,
                    actor_user_id=actor_user_id,
                )
                or ""
            )
        request = PermissionRequest(
            name=action,
            permission_level=(
                "read_only" if required_role == "viewer" else "write_safe"
            ),
            requires_approval=required_role != "viewer",
            provider="changeset",
            risk_summary=(
                "Reads a captured ChangeSet."
                if required_role == "viewer"
                else "Changes persistent ChangeSet or Workspace state."
            ),
        )
        call_id = f"{record.id}:{action}"
        context = ToolUseContext(
            conversation_id=record.conversation_id,
            workspace_id=record.workspace_id,
            workspace_root=record.workspace_root,
            authorized_workspace_root=self._workspace_service.resolve_for_run(
                record.workspace_id
            ),
            run_id=record.run_id,
            actor_user_id=actor_user_id or "",
            workspace_role=role,
            approval_policy="on_request",
            process_denied_tools=(
                ("changeset.apply", "changeset.revert")
                if not self._live_writes_enabled
                else ()
            ),
        ).bind(
            call_id=call_id,
            tool_name=action,
            arguments=arguments or {"change_set_id": record.id},
        )
        decision = self._permission_resolver.resolve(request, context, phase="plan")
        if decision.effect == "deny":
            raise ChangeSetPermissionError(decision.reason)
        if decision.effect == "ask":
            approval = self._permission_resolver.issue_approval(
                request,
                context,
                approved_by=actor_user_id or "",
            )
            context = context.with_approvals((approval,))
        final = self._permission_resolver.resolve(request, context, phase="execute")
        if final.effect != "allow":
            raise ChangeSetPermissionError(final.reason)

    def _workspace_lock(self, workspace_id: str) -> Lock:
        with self._locks_guard:
            return self._locks.setdefault(workspace_id, Lock())

    def _audit(
        self,
        record: ChangeSetRecord,
        action: str,
        actor_user_id: str | None,
    ) -> None:
        if self._audit_callback is None:
            return
        try:
            self._audit_callback(
                record=record,
                action=action,
                actor_user_id=actor_user_id,
            )
        except Exception:
            # The ChangeSet row is the durable audit source. Auxiliary Run-event
            # failure must not turn a completed workspace write into a retry.
            return


def _validate_relative_path(value: str) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise ChangeSetValidationError(f"invalid ChangeSet path: {value!r}")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or value.startswith(("a/", "b/")):
        raise ChangeSetValidationError(f"ChangeSet path escapes workspace: {value}")
    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        raise ChangeSetValidationError("ChangeSet path must name a file")
    return normalized


def _reject_sensitive_path(relative: str) -> None:
    path = PurePosixPath(relative)
    lowered_parts = [part.casefold() for part in path.parts]
    name = lowered_parts[-1]
    suffix = Path(name).suffix.casefold()
    if any(part in SENSITIVE_DIRNAMES for part in lowered_parts[:-1]):
        raise ChangeSetValidationError(f"sensitive directory cannot be changed: {relative}")
    if (
        name in SENSITIVE_FILENAMES
        or suffix in SENSITIVE_SUFFIXES
        or (name.startswith(".env.") and name not in SAFE_ENV_TEMPLATES)
    ):
        raise ChangeSetValidationError(f"sensitive file cannot be changed: {relative}")


def _patch_paths(patch: str) -> set[str]:
    """Validate the unified-diff shape and return both sides' file paths."""
    lines = patch.splitlines()
    paths: set[str] = set()
    index = 0
    saw_file = False
    while index < len(lines):
        line = lines[index]
        if not line:
            index += 1
            continue
        if not line.startswith("--- ") or index + 1 >= len(lines):
            raise ChangeSetValidationError("unsupported or malformed patch structure")
        next_line = lines[index + 1]
        if not next_line.startswith("+++ "):
            raise ChangeSetValidationError("patch file headers are incomplete")
        before = _patch_header_path(line)
        after = _patch_header_path(next_line)
        if before is None and after is None:
            raise ChangeSetValidationError("patch cannot delete and create /dev/null")
        if before is not None:
            paths.add(before)
        if after is not None:
            paths.add(after)
        if before is not None and after is not None and before != after:
            raise ChangeSetValidationError("renames are not supported in ChangeSets")
        saw_file = True
        index += 2
        saw_hunk = False
        while index < len(lines) and lines[index].startswith("@@ "):
            saw_hunk = True
            match = _HUNK_HEADER.match(lines[index])
            if match is None:
                raise ChangeSetValidationError("patch hunk header is malformed")
            old_remaining = int(match.group(1) or "1")
            new_remaining = int(match.group(2) or "1")
            index += 1
            while old_remaining or new_remaining:
                if index >= len(lines):
                    raise ChangeSetValidationError("patch hunk ended unexpectedly")
                body = lines[index]
                if body.startswith("\\ No newline at end of file"):
                    index += 1
                    continue
                if not body:
                    raise ChangeSetValidationError("patch hunk line is missing a prefix")
                prefix = body[0]
                if prefix == " ":
                    old_remaining -= 1
                    new_remaining -= 1
                elif prefix == "-":
                    old_remaining -= 1
                elif prefix == "+":
                    new_remaining -= 1
                else:
                    raise ChangeSetValidationError("patch hunk line is malformed")
                if old_remaining < 0 or new_remaining < 0:
                    raise ChangeSetValidationError("patch hunk line counts do not match")
                index += 1
            while (
                index < len(lines)
                and lines[index].startswith("\\ No newline at end of file")
            ):
                index += 1
        if not saw_hunk:
            raise ChangeSetValidationError("patch file has no hunks")
    if not saw_file:
        raise ChangeSetValidationError("patch contains no file changes")
    return paths


def _patch_header_path(line: str) -> str | None:
    value = line[4:].split("\t", 1)[0].strip()
    if value == "/dev/null":
        return None
    if not value.startswith(("a/", "b/")):
        raise ChangeSetValidationError("patch paths must use a/ and b/ prefixes")
    return _validate_relative_path(value[2:])


def _safe_target(root: Path, relative: str) -> Path:
    normalized = _validate_relative_path(relative)
    _reject_sensitive_path(normalized)
    current = root
    for part in PurePosixPath(normalized).parts:
        current = current / part
        if current.exists() or current.is_symlink():
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ChangeSetValidationError(
                    f"symbolic links cannot be changed: {relative}"
                )
    resolved_parent = current.parent.resolve()
    if resolved_parent != root and root not in resolved_parent.parents:
        raise ChangeSetValidationError(f"ChangeSet path escapes workspace: {relative}")
    return current


def _backup_targets(
    root: Path,
    changed_files: list[str],
) -> dict[str, tuple[bool, bytes, int]]:
    backups: dict[str, tuple[bool, bytes, int]] = {}
    for relative in changed_files:
        target = _safe_target(root, relative)
        if target.exists():
            mode = stat.S_IMODE(target.stat(follow_symlinks=False).st_mode)
            backups[relative] = (True, target.read_bytes(), mode)
        else:
            backups[relative] = (False, b"", 0)
    return backups


def _restore_targets(
    root: Path,
    backups: dict[str, tuple[bool, bytes, int]],
) -> None:
    for relative, (existed, content, mode) in backups.items():
        target = _safe_target(root, relative)
        if existed:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_path = target.with_name(f".{target.name}.restore-{uuid4().hex[:8]}")
            temp_path.write_bytes(content)
            os.chmod(temp_path, mode)
            os.replace(temp_path, target)
        elif target.exists() or target.is_symlink():
            target.unlink()


def _run_git_apply(root: Path, patch: str, *, check: bool, timeout: float) -> None:
    command = ["git", "apply", "--whitespace=nowarn"]
    if check:
        command.append("--check")
    command.append("-")
    result = subprocess.run(
        command,
        cwd=root,
        input=patch,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise ChangeSetValidationError(
            result.stderr.strip() or "git apply failed"
        )


def _run_git_apply_reverse(
    root: Path,
    patch: str,
    *,
    check: bool,
    timeout: float,
) -> None:
    command = ["git", "apply", "--reverse", "--whitespace=nowarn"]
    if check:
        command.append("--check")
    command.append("-")
    result = subprocess.run(
        command,
        cwd=root,
        input=patch,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise ChangeSetConflictError(
            result.stderr.strip() or "ChangeSet reverse patch no longer applies"
        )


def _validate_file_hashes(
    root: Path,
    expected: Mapping[str, str | None],
    *,
    conflict_prefix: str,
) -> None:
    for relative, expected_hash in expected.items():
        target = _safe_target(root, relative)
        if expected_hash is None:
            if target.exists() or target.is_symlink():
                raise ChangeSetConflictError(f"{conflict_prefix}: {relative}")
            continue
        if not target.exists() or not target.is_file() or target.is_symlink():
            raise ChangeSetConflictError(f"{conflict_prefix}: {relative}")
        if _sha256_bytes(target.read_bytes()) != expected_hash:
            raise ChangeSetConflictError(f"{conflict_prefix}: {relative}")


def _run_command(command: list[str], *, timeout: float) -> None:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise ChangeSetValidationError(
            result.stderr.strip() or f"command failed: {command[0]}"
        )


def _remove_worktree(root: Path, worktree: Path, branch: str, timeout: float) -> None:
    try:
        _run_command(
            ["git", "-C", str(root), "worktree", "remove", "--force", str(worktree)],
            timeout=timeout,
        )
    except Exception:
        if worktree.exists() and worktree.name.startswith("agent-worktree-"):
            shutil.rmtree(worktree)
    try:
        _run_command(
            ["git", "-C", str(root), "branch", "-D", branch],
            timeout=timeout,
        )
    except Exception:
        pass


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)
