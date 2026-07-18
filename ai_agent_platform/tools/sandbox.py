from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_agent_platform.integrations.sandbox import SandboxRuntime
from ai_agent_platform.integrations.tools import ToolExecutionContext, ToolRegistry


class SandboxToolKit:
    def __init__(self, runtime: SandboxRuntime) -> None:
        self._runtime = runtime

    def workspace_status(
        self,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        return self._runtime.workspace_status(context=context)

    def write_file(
        self,
        path: str,
        content: str,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        return self._runtime.write_file(
            path=path,
            content=content,
            context=context,
        )

    def apply_patch(
        self,
        patch: str,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        return self._runtime.apply_patch(
            patch=patch,
            context=context,
        )

    def run_command(
        self,
        command: str,
        cwd: str = ".",
        timeout_seconds: float | None = None,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        return self._runtime.run_command(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            context=context,
        )

    def git_diff(
        self,
        max_chars: int = 20000,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        return self._runtime.diff(
            context=context,
            max_chars=max_chars,
        )


def register_sandbox_tools(
    registry: ToolRegistry,
    *,
    root_path: Path | str | None = None,
    mode: str = "local",
    docker_image: str = "python:3.11-slim",
    command_timeout_seconds: float = 30.0,
    workspace_parent: Path | str | None = None,
) -> SandboxRuntime:
    runtime = SandboxRuntime(
        root_path=Path(root_path or "."),
        mode=mode,
        docker_image=docker_image,
        command_timeout_seconds=command_timeout_seconds,
        workspace_parent=workspace_parent,
    )
    toolkit = SandboxToolKit(runtime)
    registry.register(
        "sandbox.workspace_status",
        toolkit.workspace_status,
        description="Inspect the per-run sandbox workspace and changed files.",
        input_schema={"type": "object"},
        provider=f"sandbox:{mode}",
        permission_level="read_only",
        risk_summary="Reports sandbox workspace metadata and changed file paths.",
    )
    registry.register(
        "sandbox.write_file",
        toolkit.write_file,
        description="Write a UTF-8 file inside the isolated sandbox workspace.",
        input_schema={
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        },
        provider=f"sandbox:{mode}",
        permission_level="write_safe",
        requires_approval=True,
        risk_summary=(
            "Writes only inside the per-run sandbox workspace; review the path "
            "and final diff before applying changes to the real repository."
        ),
        max_output_chars=12000,
    )
    registry.register(
        "sandbox.apply_patch",
        toolkit.apply_patch,
        description="Apply a unified diff inside the isolated sandbox workspace.",
        input_schema={
            "type": "object",
            "required": ["patch"],
            "properties": {"patch": {"type": "string"}},
        },
        provider=f"sandbox:{mode}",
        permission_level="write_safe",
        requires_approval=True,
        risk_summary=(
            "Applies a patch only inside the per-run sandbox workspace; human "
            "approval is required before executing the patch."
        ),
        max_output_chars=12000,
    )
    registry.register(
        "sandbox.run_command",
        toolkit.run_command,
        description="Run a command in the sandbox workspace, locally or through Docker.",
        input_schema={
            "type": "object",
            "required": ["command"],
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout_seconds": {"type": "number"},
            },
        },
        provider=f"sandbox:{mode}",
        permission_level="write_safe",
        requires_approval=True,
        risk_summary=(
            "Runs a command in an isolated sandbox workspace. Docker mode uses "
            "a no-network container with CPU and memory limits."
        ),
        max_output_chars=20000,
    )
    registry.register(
        "sandbox.git_diff",
        toolkit.git_diff,
        description="Return the unified diff between the sandbox baseline and current workspace.",
        input_schema={
            "type": "object",
            "properties": {"max_chars": {"type": "integer"}},
        },
        provider=f"sandbox:{mode}",
        permission_level="read_only",
        risk_summary="Reads sandbox changes and returns a unified diff without side effects.",
        max_output_chars=24000,
    )
    return runtime
