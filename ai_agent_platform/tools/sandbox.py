from __future__ import annotations

from typing import Any

from ai_agent_platform.integrations.sandbox import SandboxRuntime
from ai_agent_platform.integrations.execution_workspace import ExecutionWorkspaceRuntime
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
        expected_sha256: str | None = None,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        return self._runtime.write_file(
            path=path,
            content=content,
            expected_sha256=expected_sha256,
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
    mode: str = "local",
    docker_image: str = "python:3.11-slim",
    command_timeout_seconds: float = 30.0,
    command_output_max_chars: int = 12000,
    workspace_parent: str | None = None,
    workspace_ttl_seconds: float = 86400.0,
    allowed_commands: tuple[str, ...] | None = None,
    execution_workspace_runtime: ExecutionWorkspaceRuntime | None = None,
) -> SandboxRuntime:
    runtime = SandboxRuntime(
        mode=mode,
        docker_image=docker_image,
        command_timeout_seconds=command_timeout_seconds,
        command_output_max_chars=command_output_max_chars,
        workspace_parent=workspace_parent,
        workspace_ttl_seconds=workspace_ttl_seconds,
        allowed_commands=allowed_commands,
        execution_workspace_runtime=execution_workspace_runtime,
    )
    setattr(registry, "execution_workspace_runtime", runtime.execution_workspace_runtime)
    registry.register_context_cleanup(
        lambda context: runtime.cleanup(context=context)
    )
    registry.register_context_exporter("sandbox", runtime.export_change_set)
    registry.register_close(runtime.cleanup_all)
    toolkit = SandboxToolKit(runtime)
    registry.register(
        "sandbox.workspace_status",
        toolkit.workspace_status,
        description="Inspect the current Run execution workspace and changed files.",
        input_schema={"type": "object"},
        provider=f"sandbox:{mode}",
        permission_level="read_only",
        risk_summary="Reports sandbox workspace metadata and changed file paths.",
    )
    registry.register(
        "sandbox.write_file",
        toolkit.write_file,
        description=(
            "Create or replace a UTF-8 file inside the current Run execution "
            "workspace. Read an existing target first and pass its latest "
            "content_hash as expected_sha256."
        ),
        input_schema={
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "expected_sha256": {
                    "type": "string",
                    "pattern": "^[a-f0-9]{64}$",
                },
            },
        },
        provider=f"sandbox:{mode}",
        permission_level="write_safe",
        requires_approval=True,
        risk_summary=(
            "Writes only inside the server-selected Run execution workspace; "
            "the exact call still requires approval and conflict checks."
        ),
        max_output_chars=12000,
    )
    registry.register(
        "sandbox.apply_patch",
        toolkit.apply_patch,
        description=(
            "Apply a unified diff inside the current Run execution workspace. "
            "Every existing patch target must have been read in this Run first."
        ),
        input_schema={
            "type": "object",
            "required": ["patch"],
            "properties": {"patch": {"type": "string"}},
        },
        provider=f"sandbox:{mode}",
        permission_level="write_safe",
        requires_approval=True,
        risk_summary=(
            "Applies a contextual patch only inside the server-selected Run "
            "execution workspace; human approval is required."
        ),
        max_output_chars=12000,
    )
    registry.register(
        "sandbox.run_command",
        toolkit.run_command,
        description=(
            "Run an allowlisted validation command in the Run execution workspace. "
            "Use this after a workspace mutation; use repo.list_files for directory "
            "inventory instead of shell commands such as ls. Allowed executable "
            f"basenames: {', '.join(runtime.allowed_commands)}."
        ),
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
            "Runs an allowlisted command with a fixed timeout and bounded output. "
            "Docker mode adds a no-network, non-root, read-only container boundary."
        ),
        max_output_chars=20000,
        timeout_seconds=command_timeout_seconds + 5,
    )
    registry.register(
        "sandbox.git_diff",
        toolkit.git_diff,
        description="Return the unified diff between the Run baseline and execution workspace.",
        input_schema={
            "type": "object",
            "properties": {"max_chars": {"type": "integer"}},
        },
        provider=f"sandbox:{mode}",
        permission_level="read_only",
        risk_summary="Reads execution-workspace changes and returns a unified diff without side effects.",
        max_output_chars=24000,
    )
    return runtime
