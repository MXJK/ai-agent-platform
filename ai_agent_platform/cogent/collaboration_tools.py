from __future__ import annotations

from typing import Any


_OBJECT_OUTPUT = {"type": "object"}


class SubagentExecutionError(RuntimeError):
    code = "subagent_failed"


def _spawn_handler(manager: Any):
    def spawn(
        *,
        prompt: str,
        description: str,
        subagent_type: str | None = None,
        model: str | None = None,
        run_in_background: bool = False,
        name: str | None = None,
        isolation: str | None = None,
        team_name: str | None = None,
        plan_mode_required: bool = False,
        context: Any,
    ) -> dict[str, Any]:
        result = manager.spawn(
            prompt=prompt,
            description=description,
            subagent_type=subagent_type,
            model=model,
            run_in_background=run_in_background,
            name=name,
            isolation=isolation,
            team_name=team_name,
            plan_mode_required=plan_mode_required,
            context=context,
        )
        if result.get("status") in {"failed", "blocked", "cancelled"}:
            child_run_id = str(result.get("child_run_id") or "unknown")
            detail = str(result.get("error") or result.get("status") or "failed")
            raise SubagentExecutionError(
                f"Subagent {child_run_id} failed: {detail}"
            )
        return result

    return spawn


def _register(
    registry: Any,
    name: str,
    handler: Any,
    *,
    description: str,
    schema: dict[str, Any],
    read_only: bool = False,
    timeout_seconds: float = 30.0,
) -> None:
    registry.register(
        name,
        handler,
        description=description,
        input_schema={
            "type": "object",
            **schema,
            "additionalProperties": False,
        },
        output_schema=_OBJECT_OUTPUT,
        provider="cogent",
        permission_level="read_only" if read_only else "write_safe",
        requires_approval=False,
        risk_summary=(
            "Reads server-owned collaboration state."
            if read_only
            else "Creates or changes server-owned Agent collaboration state."
        ),
        timeout_seconds=timeout_seconds,
        idempotent=read_only,
    )


def register_collaboration_tools(registry: Any, manager: Any) -> None:
    _register(
        registry,
        "agent.spawn",
        _spawn_handler(manager),
        description=(
            "Spawn a typed Subagent as a durable child Run. Omit subagent_type to "
            "fork the current conversation. Background and team workers use isolated "
            "Git worktrees."
        ),
        schema={
            "required": ["prompt", "description"],
            "properties": {
                "prompt": {"type": "string", "minLength": 1, "maxLength": 100000},
                "description": {"type": "string", "minLength": 1, "maxLength": 1000},
                "subagent_type": {"type": "string", "minLength": 1, "maxLength": 128},
                "model": {"type": "string", "minLength": 1, "maxLength": 256},
                "run_in_background": {"type": "boolean"},
                "name": {"type": "string", "minLength": 1, "maxLength": 64},
                "isolation": {"type": "string", "enum": ["", "worktree"]},
                "team_name": {"type": "string", "minLength": 1, "maxLength": 64},
                "plan_mode_required": {"type": "boolean"},
            },
        },
        timeout_seconds=900.0,
    )
    _register(
        registry,
        "collaboration.task_get",
        manager.task_get,
        description=(
            "Read one background Subagent task, or a shared team task when "
            "team_name is supplied."
        ),
        schema={
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "team_name": {"type": "string", "maxLength": 64},
            },
        },
        read_only=True,
    )
    _register(
        registry,
        "collaboration.task_list",
        manager.task_list,
        description=(
            "List background Subagent tasks, or shared team tasks when team_name "
            "is supplied."
        ),
        schema={
            "properties": {
                "team_name": {"type": "string", "maxLength": 64},
                "status": {"type": "string", "maxLength": 32},
                "assignee": {"type": "string", "maxLength": 128},
            },
        },
        read_only=True,
    )
    _register(
        registry,
        "collaboration.task_stop",
        manager.task_stop,
        description="Request cancellation of one active background Subagent task.",
        schema={
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string", "minLength": 1, "maxLength": 128}
            },
        },
    )
    _register(
        registry,
        "team.create",
        manager.team_create,
        description="Create a persistent Agent Team owned by the current lead Run.",
        schema={
            "required": ["team_name"],
            "properties": {
                "team_name": {"type": "string", "minLength": 1, "maxLength": 64},
                "description": {"type": "string", "maxLength": 4000},
            },
        },
    )
    _register(
        registry,
        "team.delete",
        manager.team_delete,
        description="Delete an idle Agent Team and optionally discard its worktrees.",
        schema={
            "required": ["team_name"],
            "properties": {
                "team_name": {"type": "string", "minLength": 1, "maxLength": 64},
                "discard_worktrees": {"type": "boolean"},
            },
        },
    )
    _register(
        registry,
        "team.send_message",
        manager.send_message,
        description=(
            "Send a persistent text, shutdown, or plan-approval message to a "
            "teammate, the lead, or '*' for broadcast."
        ),
        schema={
            "required": ["team_name", "to", "content"],
            "properties": {
                "team_name": {"type": "string", "minLength": 1, "maxLength": 64},
                "to": {"type": "string", "minLength": 1, "maxLength": 128},
                "content": {"type": "string", "maxLength": 20000},
                "type": {
                    "type": "string",
                    "enum": [
                        "text",
                        "shutdown_request",
                        "shutdown_response",
                        "plan_approval_request",
                        "plan_approval_response",
                    ],
                },
                "request_id": {"type": "string", "maxLength": 128},
                "approve": {"type": "boolean"},
            },
        },
    )
    _register(
        registry,
        "team.task_create",
        manager.shared_task_create,
        description="Create a dependency-aware task on an Agent Team shared task board.",
        schema={
            "required": ["team_name", "title"],
            "properties": {
                "team_name": {"type": "string", "minLength": 1, "maxLength": 64},
                "title": {"type": "string", "minLength": 1, "maxLength": 500},
                "description": {"type": "string", "maxLength": 10000},
                "assignee": {"type": "string", "maxLength": 128},
                "blocks": {"type": "array", "items": {"type": "string"}},
                "blocked_by": {"type": "array", "items": {"type": "string"}},
            },
        },
    )
    _register(
        registry,
        "team.task_update",
        manager.shared_task_update,
        description="Update a shared Agent Team task and its dependencies.",
        schema={
            "required": ["team_name", "task_id"],
            "properties": {
                "team_name": {"type": "string", "minLength": 1, "maxLength": 64},
                "task_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "status": {"type": "string", "maxLength": 32},
                "assignee": {"type": "string", "maxLength": 128},
                "description": {"type": "string", "maxLength": 10000},
                "add_blocks": {"type": "array", "items": {"type": "string"}},
                "add_blocked_by": {"type": "array", "items": {"type": "string"}},
            },
        },
    )
    _register(
        registry,
        "worktree.enter",
        manager.enter_worktree,
        description=(
            "Create or recover a named server-owned Git worktree. The current Run "
            "keeps its immutable execution root."
        ),
        schema={
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 128},
                "base_ref": {"type": "string", "minLength": 1, "maxLength": 256},
            },
        },
    )
    _register(
        registry,
        "worktree.exit",
        manager.exit_worktree,
        description="Keep or remove one named server-owned Git worktree.",
        schema={
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 128},
                "action": {"type": "string", "enum": ["keep", "remove"]},
                "discard_changes": {"type": "boolean"},
            },
        },
    )
    _register(
        registry,
        "worktree.list",
        manager.list_worktrees,
        description="List named server-owned worktrees and their change status.",
        schema={"properties": {}},
        read_only=True,
    )


__all__ = ["register_collaboration_tools"]
