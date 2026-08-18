"""Server-owned execution workspaces for one coding Agent run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import base64
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4


EXECUTION_WORKSPACE_MODES = frozenset({"patch_only", "direct", "worktree"})
EXECUTION_DIRECTORY_PREFIX = "agent-sandbox-"
WORKTREE_DIRECTORY_PREFIX = "agent-worktree-"
_DIFF_HUNK_POSITION = re.compile(
    r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@"
)
_IGNORED_NAMES = {
    ".chroma",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".sandbox-home",
    ".sandbox-tmp",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "venv",
}
_SENSITIVE_FILENAMES = {
    ".env",
    ".envrc",
    ".git-credentials",
    ".env.local",
    ".env.production",
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
_SENSITIVE_DIRNAMES = {".aws", ".azure", ".docker", ".gnupg", ".kube", ".ssh"}
_SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
_SAFE_ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}


class ExecutionWorkspaceError(ValueError):
    """An execution workspace could not be created or restored safely."""


class ExecutionWorkspaceConflictError(ExecutionWorkspaceError):
    """A user or another run changed a target after its known baseline."""

    code = "workspace_conflict"


@dataclass
class ExecutionWorkspaceRecord:
    run_id: str
    workspace_id: str
    source_root: Path
    execution_root: Path
    mode: str
    baseline: dict[str, bytes]
    baseline_digest: str
    base_git_head: str | None
    branch_name: str | None
    worktree_path: Path | None
    cleanup_policy: str
    created_at: str
    status: str = "ready"
    copy_warnings: tuple[str, ...] = ()
    known_hashes: dict[str, str | None] = field(default_factory=dict)
    post_write_hashes: dict[str, str | None] = field(default_factory=dict)
    mutation_count: int = 0
    journal_path: Path | None = None
    command_scratch: Path | None = None
    direct_lock_fd: int | None = None
    command_before: dict[str, bytes] | None = field(default=None, repr=False)
    mutation_lock: RLock = field(default_factory=RLock, repr=False)

    @property
    def path(self) -> Path:
        """Compatibility alias used by the command sandbox."""

        return self.execution_root

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "source_root": str(self.source_root),
            "execution_root": str(self.execution_root),
            "mode": self.mode,
            "baseline": self.baseline_digest,
            "base_git_head": self.base_git_head,
            "branch_name": self.branch_name,
            "worktree_path": (
                str(self.worktree_path) if self.worktree_path is not None else None
            ),
            "cleanup_policy": self.cleanup_policy,
            "created_at": self.created_at,
            "status": self.status,
        }


class ExecutionWorkspaceRuntime:
    """Own execution-root selection, mutation baselines, journals, and cleanup."""

    def __init__(
        self,
        *,
        runtime_parent: str | Path | None = None,
        worktree_parent: str | Path | None = None,
        branch_prefix: str = "codex/",
        command_timeout_seconds: float = 30.0,
    ) -> None:
        self._runtime_parent = Path(
            runtime_parent or tempfile.gettempdir()
        ).expanduser().resolve()
        self._worktree_parent = Path(
            worktree_parent or self._runtime_parent
        ).expanduser().resolve()
        self._branch_prefix = branch_prefix
        self._command_timeout_seconds = command_timeout_seconds
        self._journal_parent = self._runtime_parent / "agent-execution-journals"
        self._lock_parent = self._runtime_parent / "agent-execution-locks"
        self._scratch_parent = self._runtime_parent / "agent-command-env"
        for directory in (
            self._runtime_parent,
            self._journal_parent,
            self._lock_parent,
            self._scratch_parent,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._records: dict[tuple[str, str], ExecutionWorkspaceRecord] = {}
        self._records_lock = RLock()

    @property
    def runtime_parent(self) -> Path:
        return self._runtime_parent

    def prepare(
        self,
        *,
        run_id: str,
        workspace_id: str,
        source_root: str | Path,
        mode: str,
        directory_key: str | None = None,
    ) -> ExecutionWorkspaceRecord:
        if mode not in EXECUTION_WORKSPACE_MODES:
            raise ExecutionWorkspaceError(f"unsupported execution workspace mode: {mode}")
        source = Path(source_root).expanduser().resolve(strict=True)
        if not source.is_dir():
            raise ExecutionWorkspaceError("registered source workspace is not a directory")
        key = (workspace_id, run_id)
        with self._records_lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing.source_root != source or existing.mode != mode:
                    raise ExecutionWorkspaceError(
                        "run execution workspace is already frozen differently"
                    )
                return existing

            if mode == "patch_only":
                self._assert_parent_outside_source(source, self._runtime_parent)
                execution = Path(
                    tempfile.mkdtemp(
                        prefix=(
                            f"{EXECUTION_DIRECTORY_PREFIX}"
                            f"{_directory_safe_key(directory_key or run_id)}-"
                        ),
                        dir=str(self._runtime_parent),
                    )
                ).resolve()
                warnings = tuple(_copy_source_tree(source, execution))
                branch = None
                worktree = None
                cleanup_policy = "delete_on_terminal"
            elif mode == "direct":
                execution = source
                warnings = ()
                branch = None
                worktree = None
                cleanup_policy = "retain_source"
            else:
                self._assert_parent_outside_source(source, self._worktree_parent)
                head = _require_clean_git_checkout(
                    source,
                    timeout=self._command_timeout_seconds,
                )
                self._worktree_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                safe_run = _safe_key(run_id)
                branch = f"{self._branch_prefix}{safe_run}"
                worktree = (
                    self._worktree_parent
                    / f"{WORKTREE_DIRECTORY_PREFIX}{safe_run}-{uuid4().hex[:8]}"
                ).resolve()
                _run_command(
                    [
                        "git",
                        "-C",
                        str(source),
                        "worktree",
                        "add",
                        "-b",
                        branch,
                        str(worktree),
                        head,
                    ],
                    timeout=self._command_timeout_seconds,
                )
                execution = worktree
                warnings = ()
                cleanup_policy = "retain_worktree"

            baseline = _snapshot_files(execution)
            base_git_head = _git_head(source)
            record = ExecutionWorkspaceRecord(
                run_id=run_id,
                workspace_id=workspace_id,
                source_root=source,
                execution_root=execution,
                mode=mode,
                baseline=baseline,
                baseline_digest=_baseline_digest(baseline),
                base_git_head=base_git_head,
                branch_name=branch,
                worktree_path=worktree,
                cleanup_policy=cleanup_policy,
                created_at=datetime.now(timezone.utc).isoformat(),
                copy_warnings=warnings,
                known_hashes={path: _sha256(value) for path, value in baseline.items()},
                journal_path=self._journal_path(run_id),
                command_scratch=self._scratch_parent / _safe_key(run_id),
            )
            self._records[key] = record
            return record

    def restore(
        self,
        snapshot: Mapping[str, object],
        *,
        authorized_source_root: str | Path,
    ) -> ExecutionWorkspaceRecord:
        run_id = str(snapshot.get("run_id") or "")
        workspace_id = str(snapshot.get("workspace_id") or "")
        mode = str(snapshot.get("mode") or "patch_only")
        source = Path(authorized_source_root).expanduser().resolve(strict=True)
        frozen_source = Path(str(snapshot.get("source_root") or source)).resolve()
        if not run_id or not workspace_id or frozen_source != source:
            raise ExecutionWorkspaceError(
                "execution workspace snapshot does not match the authorized source root"
            )
        key = (workspace_id, run_id)
        with self._records_lock:
            existing = self._records.get(key)
            if existing is not None:
                return existing
            execution = Path(str(snapshot.get("execution_root") or "")).resolve()
            if mode == "direct":
                if execution != source:
                    raise ExecutionWorkspaceError("direct execution root must equal source root")
                cleanup_policy = "retain_source"
            elif mode == "patch_only":
                if not _is_owned_child(execution, self._runtime_parent, EXECUTION_DIRECTORY_PREFIX):
                    raise ExecutionWorkspaceError(
                        "patch_only execution root is not server-owned"
                    )
                cleanup_policy = "delete_on_terminal"
            elif mode == "worktree":
                frozen_worktree = Path(str(snapshot.get("worktree_path") or "")).resolve()
                if (
                    execution != frozen_worktree
                    or not _is_owned_child(
                        execution,
                        self._worktree_parent,
                        WORKTREE_DIRECTORY_PREFIX,
                    )
                ):
                    raise ExecutionWorkspaceError("worktree execution root is unavailable")
                cleanup_policy = "retain_worktree"
            else:
                raise ExecutionWorkspaceError("execution workspace snapshot has invalid mode")
            if not execution.exists() or not execution.is_dir():
                raise ExecutionWorkspaceError("frozen execution workspace is unavailable")

            current = _snapshot_files(execution)
            journal_path = self._journal_path(run_id)
            baseline, known, post, mutation_count = self._restore_journal(
                journal_path,
                snapshot=snapshot,
                current=current,
            )
            expected_baseline = str(snapshot.get("baseline") or "")
            if expected_baseline and _baseline_digest(baseline) != expected_baseline:
                raise ExecutionWorkspaceError("execution workspace baseline cannot be restored")
            record = ExecutionWorkspaceRecord(
                run_id=run_id,
                workspace_id=workspace_id,
                source_root=source,
                execution_root=execution,
                mode=mode,
                baseline=baseline,
                baseline_digest=_baseline_digest(baseline),
                base_git_head=_optional_string(snapshot.get("base_git_head")),
                branch_name=_optional_string(snapshot.get("branch_name")),
                worktree_path=(
                    Path(str(snapshot.get("worktree_path"))).resolve()
                    if snapshot.get("worktree_path")
                    else None
                ),
                cleanup_policy=cleanup_policy,
                created_at=str(snapshot.get("created_at") or ""),
                status=str(snapshot.get("status") or "ready"),
                known_hashes=known,
                post_write_hashes=post,
                mutation_count=mutation_count,
                journal_path=journal_path,
                command_scratch=self._scratch_parent / _safe_key(run_id),
            )
            self._records[key] = record
            return record

    def for_context(self, context: Any) -> ExecutionWorkspaceRecord:
        if context is None or not context.run_id or not context.workspace_id:
            raise ExecutionWorkspaceError("workspace context is required")
        key = (str(context.workspace_id), str(context.run_id))
        with self._records_lock:
            existing = self._records.get(key)
        if existing is not None:
            source = Path(str(context.workspace_root)).expanduser().resolve()
            if existing.source_root != source:
                raise ExecutionWorkspaceError(
                    "execution workspace mapping differs from the authorized source root"
                )
            requested_mode = str(
                getattr(context, "execution_workspace_mode", None) or existing.mode
            )
            if requested_mode != existing.mode:
                raise ExecutionWorkspaceError("execution workspace mode is frozen")
            return existing
        requested_mode = str(
            getattr(context, "execution_workspace_mode", None) or "patch_only"
        )
        if requested_mode != "patch_only":
            raise ExecutionWorkspaceError(
                "live execution workspace was not prepared by the server"
            )
        return self.prepare(
            run_id=str(context.run_id),
            workspace_id=str(context.workspace_id),
            source_root=str(context.workspace_root),
            mode="patch_only",
            directory_key=(
                f"{context.conversation_id}:{context.workspace_id}:{context.run_id}"
            ),
        )

    def workspace_status(self, context: Any) -> dict[str, Any]:
        record = self.for_context(context)
        return {
            "mode": record.mode,
            "execution_root": str(record.execution_root),
            "source_root": str(record.source_root),
            "workspace": str(record.execution_root),
            "root": str(record.source_root),
            "changed_files": self.changed_files(context),
            "copy_warnings": list(record.copy_warnings),
            "branch_name": record.branch_name,
            "worktree_path": (
                str(record.worktree_path) if record.worktree_path is not None else None
            ),
            "cleanup_policy": record.cleanup_policy,
        }

    def write_file(
        self,
        *,
        path: str,
        content: str,
        expected_sha256: str | None,
        context: Any,
    ) -> dict[str, Any]:
        record = self.for_context(context)
        relative = _validate_relative_path(path)
        _reject_sensitive_path(relative)
        encoded = content.encode("utf-8")
        with record.mutation_lock:
            self._acquire_direct_writer(record)
            target = _safe_target(record.execution_root, relative)
            current = self._check_known_version(
                record,
                relative,
                expected_sha256=expected_sha256,
            )
            if current is not None and _decode_text(current) is None:
                raise ExecutionWorkspaceError(f"binary file cannot be changed: {relative}")
            self._journal_before(record, {relative: current})
            _atomic_write(target, encoded)
            post_hash = _sha256(encoded)
            record.known_hashes[relative] = post_hash
            record.post_write_hashes[relative] = post_hash
            record.mutation_count += 1
            self._journal_after(record, {relative: post_hash})
            return {
                "path": relative,
                "bytes": len(encoded),
                "workspace": str(record.execution_root),
                "execution_root": str(record.execution_root),
                "mode": record.mode,
                "sha256": post_hash,
            }

    def apply_patch(self, *, patch: str, context: Any) -> dict[str, Any]:
        if not patch.strip():
            raise ExecutionWorkspaceError("patch must not be empty")
        if "\x00" in patch or " mode 120000" in patch or "GIT binary patch" in patch:
            raise ExecutionWorkspaceError("binary or symbolic-link patches are not allowed")
        record = self.for_context(context)
        paths = sorted(_patch_paths(patch))
        if not paths:
            raise ExecutionWorkspaceError("patch contains no file changes")
        with record.mutation_lock:
            self._acquire_direct_writer(record)
            originals: dict[str, bytes | None] = {}
            for relative in paths:
                _reject_sensitive_path(relative)
                originals[relative] = self._check_known_version(
                    record,
                    relative,
                    expected_sha256=None,
                )
                if originals[relative] is not None and _decode_text(
                    originals[relative]
                ) is None:
                    raise ExecutionWorkspaceError(
                        f"binary file cannot be changed: {relative}"
                    )
            _run_git_apply(
                record.execution_root,
                patch,
                check=True,
                timeout=self._command_timeout_seconds,
            )
            self._journal_before(record, originals)
            try:
                _run_git_apply(
                    record.execution_root,
                    patch,
                    check=False,
                    timeout=self._command_timeout_seconds,
                )
                post_hashes: dict[str, str | None] = {}
                for relative in paths:
                    target = _safe_target(record.execution_root, relative)
                    if target.is_symlink():
                        raise ExecutionWorkspaceError(
                            f"symbolic-link mutation is not allowed: {relative}"
                        )
                    if target.exists():
                        raw = target.read_bytes()
                        if _decode_text(raw) is None:
                            raise ExecutionWorkspaceError(
                                f"binary file cannot be changed: {relative}"
                            )
                        post_hashes[relative] = _sha256(raw)
                    else:
                        post_hashes[relative] = None
            except Exception:
                _restore_files(record.execution_root, originals)
                raise
            record.known_hashes.update(post_hashes)
            record.post_write_hashes.update(post_hashes)
            record.mutation_count += 1
            self._journal_after(record, post_hashes)
            return {
                "workspace": str(record.execution_root),
                "execution_root": str(record.execution_root),
                "mode": record.mode,
                "changed_files": self.changed_files(context),
                "stdout": "",
                "stderr": "",
            }

    def changed_files(self, context: Any) -> list[str]:
        record = self.for_context(context)
        current = _snapshot_files(record.execution_root)
        return [
            path
            for path in sorted(set(record.baseline) | set(current))
            if record.baseline.get(path) != current.get(path)
        ]

    def diff(self, context: Any, *, max_chars: int = 20000) -> dict[str, Any]:
        record = self.for_context(context)
        text = _workspace_diff(record.baseline, _snapshot_files(record.execution_root))
        return {
            "workspace": str(record.execution_root),
            "execution_root": str(record.execution_root),
            "mode": record.mode,
            "changed_files": self.changed_files(context),
            "diff": text[:max_chars],
            "truncated": len(text) > max_chars,
        }

    def export_change_set(self, context: Any) -> dict[str, Any]:
        record = self.for_context(context)
        current = _snapshot_files(record.execution_root)
        changed = [
            path
            for path in sorted(set(record.baseline) | set(current))
            if record.baseline.get(path) != current.get(path)
        ]
        return {
            "mode": record.mode,
            "source_root": str(record.source_root),
            "execution_root": str(record.execution_root),
            "changed_files": changed,
            "patch": _workspace_diff(record.baseline, current),
            "baseline_file_hashes": {
                path: (
                    _sha256(record.baseline[path])
                    if path in record.baseline
                    else None
                )
                for path in changed
            },
            "post_write_file_hashes": {
                path: (_sha256(current[path]) if path in current else None)
                for path in changed
            },
            "binary_files": [
                path
                for path in changed
                if _decode_text(record.baseline.get(path)) is None
                or _decode_text(current.get(path)) is None
            ],
            "copy_warnings": list(record.copy_warnings),
            "base_git_head": record.base_git_head,
            "base_git_dirty": _git_dirty(record.source_root),
            "branch_name": record.branch_name,
            "worktree_path": (
                str(record.worktree_path) if record.worktree_path is not None else None
            ),
            "cleanup_policy": record.cleanup_policy,
            "mutation_journal": str(record.journal_path) if record.journal_path else None,
        }

    def command_scratch(self, context: Any) -> Path:
        record = self.for_context(context)
        assert record.command_scratch is not None
        record.command_scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        return record.command_scratch

    def prepare_command(self, context: Any) -> ExecutionWorkspaceRecord:
        """Acquire the direct-mode writer lease before an approved command."""

        record = self.for_context(context)
        with record.mutation_lock:
            self._acquire_direct_writer(record)
            current = _snapshot_files(record.execution_root)
            current_hashes = {path: _sha256(raw) for path, raw in current.items()}
            for path in set(record.known_hashes) | set(current_hashes):
                if current_hashes.get(path) != record.known_hashes.get(path):
                    raise ExecutionWorkspaceConflictError(
                        f"workspace changed before command execution: {path}"
                    )
            self._journal_before(record, current)
            record.command_before = current
        return record

    def complete_command(self, context: Any) -> None:
        """Record command-created mutations so resume and ChangeSet capture agree."""

        record = self.for_context(context)
        with record.mutation_lock:
            before = record.command_before
            if before is None:
                return
            current = _snapshot_files(record.execution_root)
            paths = set(before) | set(current)
            post_hashes = {
                path: (_sha256(current[path]) if path in current else None)
                for path in paths
            }
            if any(before.get(path) != current.get(path) for path in paths):
                record.mutation_count += 1
            record.known_hashes = {
                path: _sha256(raw) for path, raw in current.items()
            }
            for path in paths - set(current):
                record.known_hashes[path] = None
            record.post_write_hashes.update(post_hashes)
            record.command_before = None
            self._journal_after(record, post_hashes)

    def cleanup(self, context: Any) -> bool:
        if context is None or not context.run_id or not context.workspace_id:
            return False
        key = (str(context.workspace_id), str(context.run_id))
        with self._records_lock:
            record = self._records.pop(key, None)
        if record is None:
            return False
        self._release_direct_writer(record)
        if record.command_scratch is not None:
            _remove_owned_directory(record.command_scratch, self._scratch_parent)
        if record.mode == "patch_only":
            _remove_owned_directory(
                record.execution_root,
                self._runtime_parent,
                required_prefix=EXECUTION_DIRECTORY_PREFIX,
            )
        record.status = "retained" if record.mode != "patch_only" else "cleaned"
        return True

    def cleanup_all(self) -> None:
        with self._records_lock:
            records = list(self._records.values())
            self._records.clear()
        for record in records:
            self._release_direct_writer(record)
            if record.command_scratch is not None:
                _remove_owned_directory(record.command_scratch, self._scratch_parent)
            if record.mode == "patch_only":
                _remove_owned_directory(
                    record.execution_root,
                    self._runtime_parent,
                    required_prefix=EXECUTION_DIRECTORY_PREFIX,
                )

    def _assert_parent_outside_source(self, source: Path, parent: Path) -> None:
        if parent == source or source in parent.parents:
            raise ExecutionWorkspaceError(
                "execution workspace parent must not be inside the source workspace"
            )

    def _check_known_version(
        self,
        record: ExecutionWorkspaceRecord,
        relative: str,
        *,
        expected_sha256: str | None,
    ) -> bytes | None:
        target = _safe_target(record.execution_root, relative)
        _reject_symlink_components(record.execution_root, relative)
        if target.exists():
            if not target.is_file() or target.is_symlink():
                raise ExecutionWorkspaceError(
                    f"workspace path is not a regular file: {relative}"
                )
            current = target.read_bytes()
            current_hash: str | None = _sha256(current)
        else:
            current = None
            current_hash = None
        if relative not in record.known_hashes:
            record.known_hashes[relative] = (
                _sha256(record.baseline[relative])
                if relative in record.baseline
                else None
            )
        known = record.known_hashes[relative]
        if current_hash != known:
            raise ExecutionWorkspaceConflictError(
                f"workspace file changed outside this Agent run: {relative}"
            )
        if expected_sha256 is not None:
            if re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None:
                raise ExecutionWorkspaceError("expected_sha256 must be a SHA-256 digest")
            if current_hash != expected_sha256:
                raise ExecutionWorkspaceConflictError(
                    f"expected_sha256 does not match the current file: {relative}"
                )
        return current

    def _acquire_direct_writer(self, record: ExecutionWorkspaceRecord) -> None:
        if record.mode != "direct" or record.direct_lock_fd is not None:
            return
        name = hashlib.sha256(str(record.source_root).encode("utf-8")).hexdigest()
        lock_path = self._lock_parent / f"{name}.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise ExecutionWorkspaceConflictError(
                "another direct Agent run is already writing this Workspace"
            ) from exc
        record.direct_lock_fd = descriptor

    @staticmethod
    def _release_direct_writer(record: ExecutionWorkspaceRecord) -> None:
        if record.direct_lock_fd is None:
            return
        try:
            fcntl.flock(record.direct_lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(record.direct_lock_fd)
            record.direct_lock_fd = None

    def _journal_path(self, run_id: str) -> Path:
        return self._journal_parent / f"{_safe_key(run_id)}.json"

    def _journal_before(
        self,
        record: ExecutionWorkspaceRecord,
        originals: Mapping[str, bytes | None],
    ) -> None:
        payload = self._journal_payload(record)
        mutations = dict(payload.get("mutations") or {})
        for path, raw in originals.items():
            existing = mutations.get(path)
            if isinstance(existing, dict):
                continue
            mutations[path] = {
                "original_base64": (
                    base64.b64encode(raw).decode("ascii") if raw is not None else None
                ),
                "original_sha256": _sha256(raw) if raw is not None else None,
                "post_write_sha256": None,
                "state": "pending",
            }
        payload["mutations"] = mutations
        payload["status"] = "mutation_pending"
        self._write_journal(record, payload)

    def _journal_after(
        self,
        record: ExecutionWorkspaceRecord,
        post_hashes: Mapping[str, str | None],
    ) -> None:
        payload = self._journal_payload(record)
        mutations = dict(payload.get("mutations") or {})
        for path, post_hash in post_hashes.items():
            item = dict(mutations.get(path) or {})
            item["post_write_sha256"] = post_hash
            item["state"] = "written"
            mutations[path] = item
        payload["mutations"] = mutations
        payload["status"] = "mutated"
        payload["mutation_count"] = record.mutation_count
        self._write_journal(record, payload)

    def _journal_payload(self, record: ExecutionWorkspaceRecord) -> dict[str, Any]:
        if record.journal_path is not None and record.journal_path.exists():
            try:
                payload = json.loads(record.journal_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ExecutionWorkspaceError("mutation journal is unreadable") from exc
            if not isinstance(payload, dict):
                raise ExecutionWorkspaceError("mutation journal is malformed")
            return payload
        return {
            "schema_version": 1,
            "run_id": record.run_id,
            "workspace_id": record.workspace_id,
            "mode": record.mode,
            "source_root": str(record.source_root),
            "execution_root": str(record.execution_root),
            "baseline": record.baseline_digest,
            "baseline_paths": sorted(record.baseline),
            "created_at": record.created_at,
            "mutations": {},
        }

    @staticmethod
    def _write_journal(
        record: ExecutionWorkspaceRecord,
        payload: Mapping[str, object],
    ) -> None:
        if record.journal_path is None:
            raise ExecutionWorkspaceError("mutation journal path is unavailable")
        temporary = record.journal_path.with_name(
            f".{record.journal_path.name}.{uuid4().hex}.tmp"
        )
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, record.journal_path)
            _fsync_directory(record.journal_path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _restore_journal(
        self,
        journal_path: Path,
        *,
        snapshot: Mapping[str, object],
        current: dict[str, bytes],
    ) -> tuple[dict[str, bytes], dict[str, str | None], dict[str, str | None], int]:
        baseline = dict(current)
        known = {path: _sha256(raw) for path, raw in current.items()}
        post: dict[str, str | None] = {}
        if not journal_path.exists():
            return baseline, known, post, 0
        try:
            payload = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExecutionWorkspaceError("mutation journal is unreadable") from exc
        if not isinstance(payload, dict) or any(
            str(payload.get(name) or "") != str(snapshot.get(name) or "")
            for name in ("run_id", "workspace_id", "mode", "execution_root")
        ):
            raise ExecutionWorkspaceError("mutation journal mapping is invalid")
        mutations = payload.get("mutations") or {}
        if not isinstance(mutations, dict):
            raise ExecutionWorkspaceError("mutation journal mutations are malformed")
        baseline_paths = payload.get("baseline_paths")
        if isinstance(baseline_paths, list):
            allowed_baseline = {str(path) for path in baseline_paths}
            baseline = {
                path: raw for path, raw in baseline.items() if path in allowed_baseline
            }
        for path, value in mutations.items():
            if not isinstance(value, dict):
                raise ExecutionWorkspaceError("mutation journal entry is malformed")
            encoded = value.get("original_base64")
            if encoded is None:
                baseline.pop(str(path), None)
            else:
                try:
                    baseline[str(path)] = base64.b64decode(str(encoded), validate=True)
                except ValueError as exc:
                    raise ExecutionWorkspaceError(
                        "mutation journal baseline is malformed"
                    ) from exc
            post_hash = value.get("post_write_sha256")
            if value.get("state") == "pending" and post_hash is None:
                current_raw = current.get(str(path))
                known[str(path)] = (
                    _sha256(current_raw) if current_raw is not None else None
                )
            else:
                known[str(path)] = str(post_hash) if post_hash is not None else None
            post[str(path)] = known[str(path)]
        return baseline, known, post, int(payload.get("mutation_count") or 0)


def _snapshot_files(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if any(part in _IGNORED_NAMES for part in PurePosixPath(relative).parts):
            continue
        if _is_sensitive_relative(relative):
            continue
        snapshot[relative] = path.read_bytes()
    return snapshot


def _copy_source_tree(source: Path, destination: Path) -> list[str]:
    warnings: list[str] = []
    for item in sorted(source.iterdir(), key=lambda path: path.name):
        _copy_source_item(
            item,
            destination / item.name,
            relative=PurePosixPath(item.name),
            warnings=warnings,
        )
    return warnings


def _copy_source_item(
    source: Path,
    destination: Path,
    *,
    relative: PurePosixPath,
    warnings: list[str],
) -> None:
    try:
        if source.is_symlink():
            _append_warning(warnings, f"skipped symbolic link: {relative.as_posix()}")
            return
        if source.name in _IGNORED_NAMES:
            return
        if _is_sensitive_name(source.name):
            _append_warning(warnings, f"skipped sensitive file: {relative.as_posix()}")
            return
        mode = source.stat(follow_symlinks=False).st_mode
        if stat.S_ISDIR(mode):
            destination.mkdir(mode=0o700)
            for child in sorted(source.iterdir(), key=lambda path: path.name):
                _copy_source_item(
                    child,
                    destination / child.name,
                    relative=relative / child.name,
                    warnings=warnings,
                )
        elif stat.S_ISREG(mode):
            shutil.copy2(source, destination, follow_symlinks=False)
        else:
            _append_warning(warnings, f"skipped special file: {relative.as_posix()}")
    except OSError:
        _append_warning(warnings, f"skipped unreadable path: {relative.as_posix()}")


def _append_warning(warnings: list[str], value: str) -> None:
    if len(warnings) < 100:
        warnings.append(value)


def _validate_relative_path(value: str) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise ExecutionWorkspaceError(f"invalid execution workspace path: {value!r}")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ExecutionWorkspaceError(f"path escapes sandbox execution workspace: {value}")
    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        raise ExecutionWorkspaceError("path must name a file")
    return normalized


def _safe_target(root: Path, relative: str) -> Path:
    normalized = _validate_relative_path(relative)
    target = root.joinpath(*PurePosixPath(normalized).parts)
    resolved_parent = target.parent.resolve()
    if resolved_parent != root and root not in resolved_parent.parents:
        raise ExecutionWorkspaceError(
            f"path escapes sandbox execution workspace: {relative}"
        )
    return target


def _reject_symlink_components(root: Path, relative: str) -> None:
    current = root
    parts = PurePosixPath(relative).parts
    for part in parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ExecutionWorkspaceError(
                f"symbolic-link path cannot be changed: {relative}"
            )


def _reject_sensitive_path(relative: str) -> None:
    parts = PurePosixPath(relative).parts
    if any(part.lower() in _SENSITIVE_DIRNAMES for part in parts[:-1]):
        raise ExecutionWorkspaceError(f"sensitive directory cannot be changed: {relative}")
    if _is_sensitive_name(parts[-1]):
        raise ExecutionWorkspaceError(f"sensitive file cannot be changed: {relative}")


def _is_sensitive_relative(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return any(part.lower() in _SENSITIVE_DIRNAMES for part in parts[:-1]) or (
        bool(parts) and _is_sensitive_name(parts[-1])
    )


def _is_sensitive_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in _SENSITIVE_DIRNAMES
        or lowered in _SENSITIVE_FILENAMES
        or Path(lowered).suffix in _SENSITIVE_SUFFIXES
        or (lowered.startswith(".env.") and lowered not in _SAFE_ENV_TEMPLATES)
    )


def _patch_paths(patch: str) -> set[str]:
    old_path: str | None = None
    paths: set[str] = set()
    for line in patch.splitlines():
        if line.startswith("--- "):
            old_path = _patch_path(line[4:])
        elif line.startswith("+++ "):
            new_path = _patch_path(line[4:])
            if old_path is None:
                raise ExecutionWorkspaceError("patch file headers are incomplete")
            selected = new_path if new_path is not None else old_path
            if selected is None:
                raise ExecutionWorkspaceError("patch has invalid /dev/null headers")
            paths.add(selected)
            old_path = None
    return paths


def _patch_path(raw: str) -> str | None:
    value = raw.strip().split("\t", 1)[0]
    if value == "/dev/null":
        return None
    if not value.startswith(("a/", "b/")):
        raise ExecutionWorkspaceError("patch paths must use a/ and b/ prefixes")
    return _validate_relative_path(value[2:])


def _workspace_diff(before: dict[str, bytes], after: dict[str, bytes]) -> str:
    output: list[str] = []
    for relative in sorted(set(before) | set(after)):
        old = before.get(relative)
        new = after.get(relative)
        if old == new:
            continue
        old_text = _decode_text(old)
        new_text = _decode_text(new)
        if old_text is None or new_text is None:
            output.append(f"Binary files a/{relative} and b/{relative} differ\n")
            continue
        output.extend(
            _unified_diff(
                old_text,
                new_text,
                fromfile=f"a/{relative}" if old is not None else "/dev/null",
                tofile=f"b/{relative}" if new is not None else "/dev/null",
            )
        )
    return "".join(output)


def _unified_diff(
    before: str,
    after: str,
    *,
    fromfile: str,
    tofile: str,
) -> list[str]:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    before_missing = bool(before_lines) and not before_lines[-1].endswith("\n")
    after_missing = bool(after_lines) and not after_lines[-1].endswith("\n")
    normalized_before = [line if line.endswith("\n") else f"{line}\n" for line in before_lines]
    normalized_after = [line if line.endswith("\n") else f"{line}\n" for line in after_lines]
    raw = difflib.unified_diff(
        normalized_before,
        normalized_after,
        fromfile=fromfile,
        tofile=tofile,
    )
    output: list[str] = []
    old_line = 0
    new_line = 0
    in_hunk = False
    for line in raw:
        if line.startswith("@@ "):
            match = _DIFF_HUNK_POSITION.match(line)
            if match is None:
                return []
            old_line = int(match.group(1))
            new_line = int(match.group(2))
            in_hunk = True
            output.append(line)
            continue
        output.append(line)
        if not in_hunk or not line:
            continue
        prefix = line[0]
        missing = False
        if prefix == " ":
            missing = (before_missing and old_line == len(before_lines)) or (
                after_missing and new_line == len(after_lines)
            )
            old_line += 1
            new_line += 1
        elif prefix == "-":
            missing = before_missing and old_line == len(before_lines)
            old_line += 1
        elif prefix == "+":
            missing = after_missing and new_line == len(after_lines)
            new_line += 1
        if missing:
            output.append("\\ No newline at end of file\n")
    return output


def _decode_text(value: bytes | None) -> str | None:
    if value is None:
        return ""
    if b"\x00" in value[:8192]:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _atomic_write(target: Path, value: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    target_mode = 0o600
    if target.exists() and not target.is_symlink():
        target_mode = stat.S_IMODE(target.stat(follow_symlinks=False).st_mode)
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        target_mode,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), target_mode)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _restore_files(root: Path, originals: Mapping[str, bytes | None]) -> None:
    for relative, value in originals.items():
        target = _safe_target(root, relative)
        if value is None:
            target.unlink(missing_ok=True)
        else:
            _atomic_write(target, value)


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
        raise ExecutionWorkspaceConflictError(
            result.stderr.strip() or "patch context no longer matches the execution workspace"
        )


def _require_clean_git_checkout(root: Path, *, timeout: float) -> str:
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        raise ExecutionWorkspaceError(
            "worktree mode requires a registered Git repository; choose direct or patch_only"
        )
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if status.returncode != 0:
        raise ExecutionWorkspaceError("could not inspect Git checkout state")
    if status.stdout.strip():
        raise ExecutionWorkspaceConflictError(
            "worktree mode requires a clean checkout; choose direct or patch_only to preserve current changes"
        )
    head = _git_head(root)
    if head is None:
        raise ExecutionWorkspaceError("worktree mode requires a Git commit at HEAD")
    return head


def _run_command(arguments: list[str], *, timeout: float) -> None:
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise ExecutionWorkspaceError(result.stderr.strip() or "Git worktree command failed")


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _git_dirty(root: Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def _baseline_digest(snapshot: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(snapshot):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(snapshot[path]).encode("ascii"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_key(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return normalized[:80] or "run"


def _directory_safe_key(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() else "-"
        for character in value.casefold()
    )
    return normalized.strip("-")[:120] or "run"


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None and str(value) else None


def _is_owned_child(path: Path, parent: Path, prefix: str) -> bool:
    return (
        path != parent
        and parent in path.parents
        and path.name.startswith(prefix)
        and path.exists()
        and path.is_dir()
        and not path.is_symlink()
    )


def _remove_owned_directory(
    path: Path,
    parent: Path,
    *,
    required_prefix: str | None = None,
) -> None:
    if not path.exists():
        return
    resolved = path.resolve()
    resolved_parent = parent.resolve()
    if (
        resolved == resolved_parent
        or resolved_parent not in resolved.parents
        or (required_prefix is not None and not resolved.name.startswith(required_prefix))
    ):
        raise ExecutionWorkspaceError(f"refusing to remove unowned directory: {path}")
    shutil.rmtree(resolved)


__all__ = [
    "EXECUTION_WORKSPACE_MODES",
    "ExecutionWorkspaceConflictError",
    "ExecutionWorkspaceError",
    "ExecutionWorkspaceRecord",
    "ExecutionWorkspaceRuntime",
]
