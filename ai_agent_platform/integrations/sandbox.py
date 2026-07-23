from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import difflib
import os
import shlex
import shutil
import subprocess
import tempfile
from threading import Lock
from typing import Any

from ai_agent_platform.integrations.tools import ToolExecutionContext


DEFAULT_SANDBOX_IGNORES = {
    ".chroma",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}

DEFAULT_DENIED_COMMANDS = {
    "docker",
    "kubectl",
    "rm",
    "scp",
    "ssh",
    "sudo",
}


@dataclass(frozen=True)
class SandboxWorkspace:
    key: str
    path: Path
    source_root: Path
    baseline: dict[str, bytes]


class SandboxRuntime:
    """Creates per-run workspaces and executes controlled commands inside them."""

    def __init__(
        self,
        *,
        mode: str = "local",
        docker_image: str = "python:3.11-slim",
        command_timeout_seconds: float = 30.0,
        workspace_parent: Path | str | None = None,
        denied_commands: set[str] | None = None,
    ) -> None:
        if mode not in {"local", "docker"}:
            raise ValueError("sandbox mode must be local or docker")
        self._mode = mode
        self._docker_image = docker_image
        self._command_timeout_seconds = command_timeout_seconds
        self._workspace_parent = (
            Path(workspace_parent).expanduser().resolve()
            if workspace_parent is not None
            else None
        )
        self._denied_commands = denied_commands or DEFAULT_DENIED_COMMANDS
        self._workspaces: dict[str, SandboxWorkspace] = {}
        self._lock = Lock()

    @property
    def mode(self) -> str:
        return self._mode

    def workspace_status(
        self,
        *,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspace_for(context)
        return {
            "mode": self._mode,
            "workspace": str(workspace.path),
            "root": str(workspace.source_root),
            "changed_files": self.changed_files(context=context),
        }

    def write_file(
        self,
        *,
        path: str,
        content: str,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspace_for(context)
        target = _resolve_inside(workspace.path, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {
            "path": _relative_to(target, workspace.path),
            "bytes": len(content.encode("utf-8")),
            "workspace": str(workspace.path),
        }

    def apply_patch(
        self,
        *,
        patch: str,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        if not patch.strip():
            raise ValueError("patch must not be empty")
        workspace = self._workspace_for(context)
        _validate_patch_paths(patch)
        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            input=patch,
            text=True,
            cwd=workspace.path,
            capture_output=True,
            timeout=self._command_timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git apply failed")
        return {
            "workspace": str(workspace.path),
            "changed_files": self.changed_files(context=context),
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

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
        _validate_command(args, denied_commands=self._denied_commands)
        working_dir = _resolve_inside(workspace.path, cwd)
        if not working_dir.exists() or not working_dir.is_dir():
            raise ValueError(f"cwd is not an existing directory: {cwd}")
        timeout = timeout_seconds or self._command_timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")

        if self._mode == "docker":
            result = self._run_docker_command(
                args=args,
                working_dir=working_dir,
                workspace=workspace,
                timeout=timeout,
            )
        else:
            result = subprocess.run(
                args,
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        return {
            "mode": self._mode,
            "command": args,
            "cwd": _relative_to(working_dir, workspace.path),
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "workspace": str(workspace.path),
        }

    def diff(
        self,
        *,
        context: ToolExecutionContext | None = None,
        max_chars: int = 20000,
    ) -> dict[str, Any]:
        workspace = self._workspace_for(context)
        diff_text = _workspace_diff(workspace)
        truncated = len(diff_text) > max_chars
        return {
            "workspace": str(workspace.path),
            "changed_files": self.changed_files(context=context),
            "diff": diff_text[:max_chars],
            "truncated": truncated,
        }

    def changed_files(
        self,
        *,
        context: ToolExecutionContext | None = None,
    ) -> list[str]:
        workspace = self._workspace_for(context)
        current = _snapshot_files(workspace.path)
        paths = sorted(set(workspace.baseline) | set(current))
        return [path for path in paths if workspace.baseline.get(path) != current.get(path)]

    def _workspace_for(self, context: ToolExecutionContext | None) -> SandboxWorkspace:
        key = _workspace_key(context)
        source_root = _source_root(context)
        with self._lock:
            existing = self._workspaces.get(key)
            if existing is not None:
                return existing
            workspace_path = Path(
                tempfile.mkdtemp(
                    prefix=f"agent-sandbox-{_safe_key(key)}-",
                    dir=str(self._workspace_parent) if self._workspace_parent else None,
                )
            )
            _copy_source_tree(source_root, workspace_path)
            workspace = SandboxWorkspace(
                key=key,
                path=workspace_path,
                source_root=source_root,
                baseline=_snapshot_files(workspace_path),
            )
            self._workspaces[key] = workspace
            return workspace

    def _run_docker_command(
        self,
        *,
        args: list[str],
        working_dir: Path,
        workspace: SandboxWorkspace,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        relative_cwd = _relative_to(working_dir, workspace.path)
        container_cwd = "/workspace" if relative_cwd == "." else f"/workspace/{relative_cwd}"
        docker_command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--cpus",
            "2",
            "--memory",
            "1g",
            "-v",
            f"{workspace.path}:/workspace:rw",
            "-w",
            container_cwd,
            self._docker_image,
            *args,
        ]
        return subprocess.run(
            docker_command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )


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


def _copy_source_tree(source: Path, destination: Path) -> None:
    if not source.exists() or not source.is_dir():
        raise ValueError("sandbox root_path must be an existing directory")
    for item in source.iterdir():
        if item.name in DEFAULT_SANDBOX_IGNORES:
            continue
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(
                item,
                target,
                ignore=lambda _, names: [
                    name for name in names if name in DEFAULT_SANDBOX_IGNORES
                ],
            )
        elif item.is_file():
            shutil.copy2(item, target)


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
            difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"a/{relative_path}" if before is not None else "/dev/null",
                tofile=f"b/{relative_path}" if after is not None else "/dev/null",
            )
        )
    return "".join(lines)


def _decode_for_diff(value: bytes | None) -> str | None:
    if value is None:
        return ""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _command_args(command: str | list[str]) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command)
    return [str(item) for item in command]


def _validate_command(args: list[str], *, denied_commands: set[str]) -> None:
    if not args:
        raise ValueError("command must not be empty")
    command_name = Path(args[0]).name
    if command_name in denied_commands:
        raise ValueError(f"command is not allowed in sandbox: {command_name}")
    if any("\x00" in arg for arg in args):
        raise ValueError("command arguments must not contain null bytes")


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
