from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import difflib
import hashlib
import os
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
from threading import Lock
import time
from typing import Any
from uuid import uuid4

from ai_agent_platform.integrations.tools import ToolExecutionContext
from ai_agent_platform.integrations.execution_workspace import (
    ExecutionWorkspaceRecord,
    ExecutionWorkspaceRuntime,
)


DEFAULT_SANDBOX_IGNORES = {
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
    "venv",
}

SENSITIVE_SANDBOX_FILENAMES = {
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
SENSITIVE_SANDBOX_DIRNAMES = {
    ".aws",
    ".azure",
    ".docker",
    ".gnupg",
    ".kube",
    ".ssh",
}
SENSITIVE_SANDBOX_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
SAFE_ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}
DEFAULT_ALLOWED_COMMANDS = {
    "alembic",
    "cargo",
    "git",
    "go",
    "mypy",
    "node",
    "npm",
    "npx",
    "poetry",
    "pytest",
    "python",
    "python3",
    "ruff",
    "rustc",
    "tox",
    "uv",
}
SHELL_WRAPPER_COMMANDS = {
    "bash",
    "csh",
    "dash",
    "fish",
    "ksh",
    "powershell",
    "pwsh",
    "sh",
    "tcsh",
    "zsh",
}
SANDBOX_DIRECTORY_PREFIX = "agent-sandbox-"
_DIFF_HUNK_POSITION = re.compile(
    r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@"
)


@dataclass(frozen=True)
class SandboxWorkspace:
    key: str
    path: Path
    source_root: Path
    baseline: dict[str, bytes]
    copy_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: str
    stderr: str
    output_truncated: bool
    timed_out: bool


class SandboxRuntime:
    """Creates per-run workspaces and executes controlled commands inside them."""

    def __init__(
        self,
        *,
        mode: str = "local",
        docker_image: str = "python:3.11-slim",
        command_timeout_seconds: float = 30.0,
        command_output_max_chars: int = 12000,
        workspace_parent: Path | str | None = None,
        workspace_ttl_seconds: float = 86400.0,
        allowed_commands: set[str] | tuple[str, ...] | None = None,
        execution_workspace_runtime: ExecutionWorkspaceRuntime | None = None,
    ) -> None:
        if mode not in {"local", "docker"}:
            raise ValueError("sandbox mode must be local or docker")
        self._mode = mode
        self._docker_image = docker_image
        self._command_timeout_seconds = command_timeout_seconds
        self._command_output_max_chars = command_output_max_chars
        self._workspace_ttl_seconds = workspace_ttl_seconds
        self._workspace_parent = (
            Path(workspace_parent).expanduser().resolve()
            if workspace_parent is not None
            else Path(tempfile.gettempdir()).resolve()
        )
        self._allowed_commands = {
            item.strip()
            for item in (allowed_commands or DEFAULT_ALLOWED_COMMANDS)
            if item.strip()
        }
        if command_timeout_seconds <= 0:
            raise ValueError("sandbox command timeout must be positive")
        if command_output_max_chars <= 0:
            raise ValueError("sandbox command output limit must be positive")
        if workspace_ttl_seconds <= 0:
            raise ValueError("sandbox workspace TTL must be positive")
        if not self._allowed_commands:
            raise ValueError("sandbox allowed commands must not be empty")
        if any(Path(item).name != item for item in self._allowed_commands):
            raise ValueError("sandbox allowed commands must be executable basenames")
        self._workspaces: dict[str, SandboxWorkspace] = {}
        self._lock = Lock()
        self._workspace_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._execution_workspaces = execution_workspace_runtime or ExecutionWorkspaceRuntime(
            runtime_parent=self._workspace_parent,
            command_timeout_seconds=command_timeout_seconds,
        )
        self.prune_stale_workspaces()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def allowed_commands(self) -> tuple[str, ...]:
        """Return the command contract exposed to native tool-calling models."""

        return tuple(sorted(self._allowed_commands))

    @property
    def execution_workspace_runtime(self) -> ExecutionWorkspaceRuntime:
        return self._execution_workspaces

    def workspace_status(
        self,
        *,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        result = self._execution_workspaces.workspace_status(context)
        result["sandbox_mode"] = self._mode
        return result

    def write_file(
        self,
        *,
        path: str,
        content: str,
        expected_sha256: str | None = None,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        return self._execution_workspaces.write_file(
            path=path,
            content=content,
            expected_sha256=expected_sha256,
            context=context,
        )

    def apply_patch(
        self,
        *,
        patch: str,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        return self._execution_workspaces.apply_patch(patch=patch, context=context)

    def run_command(
        self,
        *,
        command: str | list[str],
        cwd: str = ".",
        timeout_seconds: float | None = None,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspace_for(context)
        args = _command_args(command)
        _validate_command(args, allowed_commands=self._allowed_commands)
        working_dir = _resolve_inside(workspace.path, cwd)
        if not working_dir.exists() or not working_dir.is_dir():
            raise ValueError(f"cwd is not an existing directory: {cwd}")
        timeout = timeout_seconds or self._command_timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        timeout = min(timeout, self._command_timeout_seconds)
        self._execution_workspaces.prepare_command(context)

        try:
            if self._mode == "docker":
                result = self._run_docker_command(
                    args=args,
                    working_dir=working_dir,
                    workspace=workspace,
                    timeout=timeout,
                )
            else:
                result = _run_bounded_process(
                    args,
                    cwd=working_dir,
                    timeout=timeout,
                    output_max_chars=self._command_output_max_chars,
                    env=_local_sandbox_environment(
                        workspace,
                        scratch=self._execution_workspaces.command_scratch(context),
                    ),
                )
        finally:
            self._execution_workspaces.complete_command(context)
        output = {
            "mode": self._mode,
            "command": args,
            "cwd": _relative_to(working_dir, workspace.path),
            "exit_code": result.returncode,
            "timed_out": result.timed_out,
            "output_truncated": result.output_truncated,
            "workspace": str(workspace.path),
            "workspace_mode": workspace.mode,
        }
        if result.output_truncated:
            output["truncated_output_preview"] = _combined_output_preview(
                result.stdout,
                result.stderr,
                max_chars=self._command_output_max_chars,
            )
        else:
            output["stdout"] = result.stdout
            output["stderr"] = result.stderr
        return output

    def diff(
        self,
        *,
        context: ToolExecutionContext | None = None,
        max_chars: int = 20000,
    ) -> dict[str, Any]:
        return self._execution_workspaces.diff(context, max_chars=max_chars)

    def changed_files(
        self,
        *,
        context: ToolExecutionContext | None = None,
    ) -> list[str]:
        return self._execution_workspaces.changed_files(context)

    def export_change_set(
        self,
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        """Return the untruncated server-side snapshot used for ChangeSet audit."""
        return self._execution_workspaces.export_change_set(context)

    def cleanup(
        self,
        *,
        context: ToolExecutionContext | None = None,
    ) -> bool:
        return self._execution_workspaces.cleanup(context)

    def cleanup_all(self) -> None:
        self._execution_workspaces.cleanup_all()

    def prune_stale_workspaces(self) -> int:
        cutoff = time.time() - self._workspace_ttl_seconds
        removed = 0
        try:
            candidates = list(self._workspace_parent.iterdir())
        except OSError:
            return 0
        for candidate in candidates:
            if (
                not candidate.name.startswith(SANDBOX_DIRECTORY_PREFIX)
                or candidate.is_symlink()
                or not candidate.is_dir()
            ):
                continue
            try:
                stale = candidate.stat().st_mtime < cutoff
            except OSError:
                continue
            if not stale:
                continue
            try:
                _remove_sandbox_directory(
                    candidate,
                    parent=self._workspace_parent,
                )
            except OSError:
                continue
            removed += 1
        return removed

    def _workspace_for(
        self,
        context: ToolExecutionContext | None,
    ) -> ExecutionWorkspaceRecord:
        return self._execution_workspaces.for_context(context)

    def _run_docker_command(
        self,
        *,
        args: list[str],
        working_dir: Path,
        workspace: ExecutionWorkspaceRecord,
        timeout: float,
    ) -> BoundedProcessResult:
        relative_cwd = _relative_to(working_dir, workspace.path)
        container_cwd = "/workspace" if relative_cwd == "." else f"/workspace/{relative_cwd}"
        container_name = (
            f"{workspace.path.name[:45]}-cmd-{uuid4().hex[:8]}"
        )
        docker_command = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--cpus",
            "2",
            "--memory",
            "1g",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--env",
            "HOME=/tmp",
            "--env",
            "TMPDIR=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "-v",
            f"{workspace.path}:/workspace:rw",
            "-w",
            container_cwd,
            self._docker_image,
            *args,
        ]
        result = _run_bounded_process(
            docker_command,
            cwd=workspace.path,
            timeout=timeout,
            output_max_chars=self._command_output_max_chars,
            env=_docker_client_environment(),
        )
        if result.timed_out:
            _remove_timed_out_container(container_name)
        return result


def _workspace_key(context: ToolExecutionContext | None) -> str:
    if context is None:
        raise ValueError("workspace context is required")
    return (
        f"{context.conversation_id}:{context.workspace_id}:"
        f"{context.run_id or 'run'}"
    )


def _source_root(context: ToolExecutionContext | None) -> Path:
    if context is None or not context.workspace_root:
        raise ValueError("workspace context is required")
    root = Path(context.workspace_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("workspace_unavailable: captured workspace root is inaccessible")
    return root


def _safe_key(value: str) -> str:
    safe = "".join(char if char.isalnum() else "-" for char in value.lower())
    return safe.strip("-")[:48] or "default"


def _copy_source_tree(source: Path, destination: Path) -> list[str]:
    if not source.exists() or not source.is_dir():
        raise ValueError("sandbox root_path must be an existing directory")
    warnings: list[str] = []
    for item in sorted(source.iterdir(), key=lambda path: path.name):
        _copy_source_item(
            item,
            destination / item.name,
            relative_path=Path(item.name),
            warnings=warnings,
        )
    return warnings


def _copy_source_item(
    source: Path,
    destination: Path,
    *,
    relative_path: Path,
    warnings: list[str],
) -> None:
    relative = relative_path.as_posix()
    try:
        if source.is_symlink():
            _append_copy_warning(warnings, f"skipped symbolic link: {relative}")
            return
        if source.name in DEFAULT_SANDBOX_IGNORES:
            return
        if _is_sensitive_sandbox_path(source):
            _append_copy_warning(warnings, f"skipped sensitive file: {relative}")
            return
        mode = source.stat(follow_symlinks=False).st_mode
        if stat.S_ISDIR(mode):
            destination.mkdir(mode=0o700)
            for child in sorted(source.iterdir(), key=lambda path: path.name):
                _copy_source_item(
                    child,
                    destination / child.name,
                    relative_path=relative_path / child.name,
                    warnings=warnings,
                )
            return
        if stat.S_ISREG(mode):
            shutil.copy2(source, destination, follow_symlinks=False)
            if destination.is_symlink():
                destination.unlink(missing_ok=True)
                _append_copy_warning(
                    warnings,
                    f"skipped path changed to symbolic link: {relative}",
                )
            return
        _append_copy_warning(warnings, f"skipped special file: {relative}")
    except OSError:
        _append_copy_warning(warnings, f"skipped unreadable path: {relative}")


def _append_copy_warning(warnings: list[str], warning: str) -> None:
    if len(warnings) < 100:
        warnings.append(warning)


def _is_sensitive_sandbox_path(path: Path) -> bool:
    lowered = path.name.lower()
    return (
        lowered in SENSITIVE_SANDBOX_DIRNAMES
        or lowered in SENSITIVE_SANDBOX_FILENAMES
        or path.suffix.lower() in SENSITIVE_SANDBOX_SUFFIXES
        or (
            lowered.startswith(".env.")
            and lowered not in SAFE_ENV_TEMPLATES
        )
    )


def _snapshot_files(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if any(part in DEFAULT_SANDBOX_IGNORES for part in path.parts):
            continue
        snapshot[_relative_to(path, root)] = path.read_bytes()
    return snapshot


def _workspace_diff(workspace: SandboxWorkspace) -> str:
    current = _snapshot_files(workspace.path)
    lines: list[str] = []
    for relative_path in sorted(set(workspace.baseline) | set(current)):
        before = workspace.baseline.get(relative_path)
        after = current.get(relative_path)
        if before == after:
            continue
        before_text = _decode_for_diff(before)
        after_text = _decode_for_diff(after)
        if before_text is None or after_text is None:
            lines.extend(
                [
                    f"Binary files a/{relative_path} and b/{relative_path} differ\n",
                ]
            )
            continue
        lines.extend(
            _git_compatible_unified_diff(
                before_text,
                after_text,
                fromfile=f"a/{relative_path}" if before is not None else "/dev/null",
                tofile=f"b/{relative_path}" if after is not None else "/dev/null",
            )
        )
    return "".join(lines)


def _git_compatible_unified_diff(
    before_text: str,
    after_text: str,
    *,
    fromfile: str,
    tofile: str,
) -> list[str]:
    before_lines = before_text.splitlines(keepends=True)
    after_lines = after_text.splitlines(keepends=True)
    before_missing_newline = bool(before_lines) and not before_lines[-1].endswith("\n")
    after_missing_newline = bool(after_lines) and not after_lines[-1].endswith("\n")
    normalized_before = [
        line if line.endswith("\n") else f"{line}\n"
        for line in before_lines
    ]
    normalized_after = [
        line if line.endswith("\n") else f"{line}\n"
        for line in after_lines
    ]
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
        missing_newline = False
        if prefix == " ":
            missing_newline = (
                before_missing_newline and old_line == len(before_lines)
            ) or (
                after_missing_newline and new_line == len(after_lines)
            )
            old_line += 1
            new_line += 1
        elif prefix == "-":
            missing_newline = (
                before_missing_newline and old_line == len(before_lines)
            )
            old_line += 1
        elif prefix == "+":
            missing_newline = (
                after_missing_newline and new_line == len(after_lines)
            )
            new_line += 1
        if missing_newline:
            output.append("\\ No newline at end of file\n")
    return output


def _decode_for_diff(value: bytes | None) -> str | None:
    if value is None:
        return ""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
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


def _command_args(command: str | list[str]) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command)
    return [str(item) for item in command]


def _validate_command(args: list[str], *, allowed_commands: set[str]) -> None:
    if not args:
        raise ValueError("command must not be empty")
    command_name = Path(args[0]).name
    if command_name in SHELL_WRAPPER_COMMANDS:
        raise ValueError(f"shell wrappers are not allowed in sandbox: {command_name}")
    if not _is_allowed_command(command_name, allowed_commands):
        raise ValueError(
            f"command is not in the sandbox allowlist: {command_name}"
        )
    if any("\x00" in arg for arg in args):
        raise ValueError("command arguments must not contain null bytes")


def _is_allowed_command(command_name: str, allowed_commands: set[str]) -> bool:
    if command_name in allowed_commands:
        return True
    return (
        "python3" in allowed_commands
        and command_name.startswith("python3.")
        and command_name.removeprefix("python3.").replace(".", "").isdigit()
    )


def _run_bounded_process(
    args: list[str],
    *,
    cwd: Path,
    timeout: float,
    output_max_chars: int,
    env: dict[str, str],
) -> BoundedProcessResult:
    process = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    if process.stdout is None or process.stderr is None:
        _terminate_process_group(process)
        raise RuntimeError("sandbox command output pipes are unavailable")

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    captured = 0
    output_truncated = False
    timed_out = False
    deadline = time.monotonic() + timeout

    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0 and not timed_out:
                timed_out = True
                _terminate_process_group(process)
            select_timeout = (
                0.05 if timed_out else min(0.1, max(0.0, remaining))
            )
            events = selector.select(timeout=select_timeout)
            if not events:
                if process.poll() is not None:
                    continue
                if timed_out:
                    _terminate_process_group(process)
                continue
            for key, _ in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 8192)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                remaining_capacity = max(0, output_max_chars - captured)
                if remaining_capacity:
                    selected = chunk[:remaining_capacity]
                    chunks[str(key.data)].append(selected)
                    captured += len(selected)
                if len(chunk) > remaining_capacity:
                    output_truncated = True
        if process.poll() is None:
            _terminate_process_group(process)
        returncode = process.wait(timeout=1)
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if not stream.closed:
                stream.close()

    return BoundedProcessResult(
        returncode=124 if timed_out else returncode,
        stdout=b"".join(chunks["stdout"]).decode("utf-8", errors="replace"),
        stderr=b"".join(chunks["stderr"]).decode("utf-8", errors="replace"),
        output_truncated=output_truncated,
        timed_out=timed_out,
    )


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


def _local_sandbox_environment(
    workspace: SandboxWorkspace | ExecutionWorkspaceRecord,
    *,
    scratch: Path | None = None,
) -> dict[str, str]:
    scratch_root = scratch or workspace.path
    sandbox_home = scratch_root / ".sandbox-home"
    sandbox_temp = scratch_root / ".sandbox-tmp"
    sandbox_home.mkdir(mode=0o700, exist_ok=True)
    sandbox_temp.mkdir(mode=0o700, exist_ok=True)
    environment = {
        "CI": "1",
        "HOME": str(sandbox_home),
        "NO_COLOR": "1",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEMP": str(sandbox_temp),
        "TMP": str(sandbox_temp),
        "TMPDIR": str(sandbox_temp),
    }
    for name in ("LANG", "LC_ALL"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _docker_client_environment() -> dict[str, str]:
    environment = {
        "HOME": os.environ.get("HOME", tempfile.gettempdir()),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    for name in ("DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST", "LANG", "LC_ALL"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _combined_output_preview(stdout: str, stderr: str, *, max_chars: int) -> str:
    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    return "\n".join(parts)[:max_chars]


def _remove_timed_out_container(container_name: str) -> None:
    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_docker_client_environment(),
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _remove_sandbox_directory(path: Path, *, parent: Path) -> None:
    resolved_parent = parent.resolve()
    resolved = path.resolve()
    if (
        resolved == resolved_parent
        or resolved_parent not in resolved.parents
        or not resolved.name.startswith(SANDBOX_DIRECTORY_PREFIX)
    ):
        raise ValueError(f"refusing to remove non-sandbox directory: {path}")
    shutil.rmtree(resolved)


def _validate_patch_paths(patch: str) -> None:
    for raw_line in patch.splitlines():
        path = None
        if raw_line.startswith("+++ ") or raw_line.startswith("--- "):
            path = raw_line[4:].strip().split("\t", 1)[0]
        elif raw_line.startswith("diff --git "):
            parts = raw_line.split()
            if len(parts) >= 4:
                _validate_diff_path(parts[2])
                _validate_diff_path(parts[3])
                continue
        if path is not None:
            _validate_diff_path(path)


def _validate_diff_path(path: str) -> None:
    if path == "/dev/null":
        return
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or os.path.isabs(path):
        raise ValueError(f"patch path escapes sandbox: {path}")


def _resolve_inside(root: Path, path: str) -> Path:
    resolved_root = root.resolve()
    resolved = (resolved_root / path).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"path escapes sandbox workspace: {path}")
    return resolved


def _relative_to(path: Path, root: Path) -> str:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    return relative or "."
