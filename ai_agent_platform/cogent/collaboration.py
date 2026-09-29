from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import RLock
import time
from typing import Any
from uuid import uuid4

from ai_agent_platform.cogent.agents import AgentDefinition, AgentLoader
from ai_agent_platform.cogent.state import CogentState
from ai_agent_platform.cogent.teams import (
    Mailbox,
    TeamError,
    TeamManager,
    TeammateInfo,
    create_message,
)
from ai_agent_platform.cogent.teams import protocol
from ai_agent_platform.cogent.worktree import Worktree, WorktreeError, WorktreeManager
from ai_agent_platform.domain import (
    ExecutionWorkspaceContext,
    InstructionSourceSnapshot,
    RunMetadata,
)
from ai_agent_platform.integrations.permissions import ToolExecutionContext
from ai_agent_platform.model_registry.selection import ModelSelection, model_selection_scope
from ai_agent_platform.usage_ledger import model_usage_scope


FORK_MARKER = "<cogent-subagent>"
MAX_NOTIFICATION_CHARS = 5_000
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "blocked", "cancelled"})
COLLABORATION_VISIBLE_TOOLS = frozenset(
    {
        "Agent",
        "TaskGet",
        "TaskList",
        "TaskStop",
        "TeamCreate",
        "TeamDelete",
        "SendMessage",
        "TaskCreate",
        "TaskUpdate",
        "EnterWorktree",
        "ExitWorktree",
        "WorktreeList",
    }
)
TEAM_MEMBER_TOOLS = frozenset(
    {"SendMessage", "TaskCreate", "TaskGet", "TaskList", "TaskUpdate"}
)
TEAMMATE_ADDENDUM = """

IMPORTANT: You are running as an agent in a team. Plain response text is not
visible to teammates, so use SendMessage for coordination and final findings.
The user interacts primarily with the team lead; coordinate work through the
shared task board and mailbox. You are in an isolated Git worktree. Use paths
relative to the current working directory, never absolute paths from the
original checkout.
"""


@dataclass
class CollaborationTask:
    id: str
    parent_run_id: str
    child_run_id: str
    name: str
    agent_type: str
    prompt: str
    status: str = "queued"
    result: str = ""
    error: str = ""
    team_name: str = ""
    member_id: str = ""
    worktree: Worktree | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    plan_request_id: str = ""
    future: Future[None] | None = field(default=None, repr=False)
    finalized: bool = field(default=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_run_id": self.parent_run_id,
            "child_run_id": self.child_run_id,
            "name": self.name,
            "agent_type": self.agent_type,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "team_name": self.team_name or None,
            "member_id": self.member_id or None,
            "worktree": self.worktree.to_dict() if self.worktree else None,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class _PreparedChild:
    task: CollaborationTask
    context: Any
    history: list[dict[str, Any]]
    user_input: str
    actor_user_id: str
    model_selection: ModelSelection


class CollaborationManager:
    """Bridge mewcode-style coordination to durable Cogent child Runs."""

    def __init__(
        self,
        *,
        teams_root: str | Path,
        worktree_parent: str | Path,
        execution_workspaces: Any,
        branch_prefix: str = "codex/",
        command_timeout_seconds: float = 30.0,
        max_workers: int = 4,
        user_agents_root: str | Path = "~/.cogent/agents",
    ) -> None:
        self.teams = TeamManager(teams_root)
        self.notifications = Mailbox(
            Path(teams_root).expanduser().resolve().parent / "notifications"
        )
        self.worktrees = WorktreeManager(
            parent=worktree_parent,
            branch_prefix=branch_prefix,
            command_timeout_seconds=command_timeout_seconds,
        )
        self.execution_workspaces = execution_workspaces
        self.user_agents_root = Path(user_agents_root).expanduser()
        self._runtime: Any = None
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_workers),
            thread_name_prefix="cogent-subagent",
        )
        self._tasks: dict[str, CollaborationTask] = {}
        self._lock = RLock()

    def bind_runtime(self, runtime: Any) -> None:
        self._runtime = runtime
        self._hydrate_tasks()

    def close(self) -> None:
        with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            if task.status in {"queued", "running"}:
                try:
                    self._require_runtime().request_control(
                        run_id=task.child_run_id,
                        action="cancel",
                    )
                except Exception:
                    pass
        self._executor.shutdown(wait=False, cancel_futures=True)

    def spawn(
        self,
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
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        del description
        if not prompt.strip():
            raise ValueError("Subagent prompt is required")
        if plan_mode_required and not team_name:
            raise ValueError("plan_mode_required is only valid for an Agent Team member")
        parent = self._parent_record(context)
        loader = AgentLoader(
            parent.workspace_root,
            user_root=self.user_agents_root,
            enable_verification=True,
        )
        loader.load_all()
        definition = self._definition(loader, subagent_type)
        effective_isolation = isolation or definition.isolation
        if effective_isolation not in {"", "worktree"}:
            raise ValueError("Subagent isolation must be empty or 'worktree'")
        background = bool(run_in_background or definition.background or team_name)
        if background and not effective_isolation:
            effective_isolation = "worktree"
        task_id = uuid4().hex[:12]
        agent_name = name or definition.agent_type or f"agent-{task_id[:6]}"
        member: TeammateInfo | None = None
        resolved_team = ""
        if team_name:
            team = self.teams.get(team_name)
            if team is None:
                team = self.teams.create(
                    name=team_name,
                    lead_agent_id=context.run_id or parent.run_id,
                )
            elif team.lead_agent_id != context.run_id:
                raise PermissionError(
                    "only the team lead Run may add a teammate"
                )
            resolved_team = team.name
            agent_name = self.teams.unique_member_name(team, agent_name)
        prepared = self._prepare_child(
            parent=parent,
            parent_context=context,
            task_id=task_id,
            prompt=prompt,
            definition=definition,
            name=agent_name,
            model=model,
            isolation=effective_isolation,
            team_name=resolved_team,
            plan_mode_required=plan_mode_required,
        )
        task = prepared.task
        if resolved_team:
            member = TeammateInfo(
                name=agent_name,
                agent_id=f"agent-{uuid4().hex[:12]}",
                agent_type=definition.agent_type,
                model=model or definition.model,
                worktree_name=task.worktree.name if task.worktree else "",
                worktree_path=task.worktree.path if task.worktree else "",
                child_run_id=task.child_run_id,
            )
            task.member_id = member.agent_id
            self.teams.add_member(resolved_team, member)
        with self._lock:
            self._tasks[task.id] = task
        if background:
            task.future = self._executor.submit(self._execute_child, prepared)
            return {
                "task_id": task.id,
                "child_run_id": task.child_run_id,
                "agent": task.name,
                "agent_type": task.agent_type,
                "status": task.status,
                "team_name": resolved_team or None,
                "worktree": task.worktree.to_dict() if task.worktree else None,
                "notification": "Completion will be injected into the parent Run.",
            }
        self._execute_child(prepared)
        return task.to_dict()

    def task_get(
        self,
        *,
        task_id: str,
        team_name: str = "",
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if team_name:
            return self.shared_task_get(
                team_name=team_name,
                task_id=task_id,
                context=context,
            )
        task = self._task_for_parent(task_id, context.run_id)
        return task.to_dict()

    def task_list(
        self,
        *,
        status: str | None = None,
        team_name: str = "",
        assignee: str | None = None,
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if team_name:
            return self.shared_task_list(
                team_name=team_name,
                status=status,
                assignee=assignee,
                context=context,
            )
        self._hydrate_tasks()
        with self._lock:
            tasks = [
                task
                for task in self._tasks.values()
                if task.parent_run_id == context.run_id
                and (status is None or task.status == status)
            ]
        return {"tasks": [task.to_dict() for task in tasks], "count": len(tasks)}

    def task_stop(self, *, task_id: str, context: ToolExecutionContext) -> dict[str, Any]:
        task = self._task_for_parent(task_id, context.run_id)
        if task.status in TERMINAL_TASK_STATUSES:
            return {"task_id": task.id, "status": task.status, "stopped": False}
        self._require_runtime().request_control(run_id=task.child_run_id, action="cancel")
        task.status = "cancelled"
        return {"task_id": task.id, "status": task.status, "stopped": True}

    def team_create(
        self,
        *,
        team_name: str,
        description: str = "",
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        team = self.teams.create(
            name=team_name,
            lead_agent_id=context.run_id,
            description=description,
        )
        return team.to_dict()

    def team_delete(
        self,
        *,
        team_name: str,
        discard_worktrees: bool = False,
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        self._hydrate_tasks()
        team = self._owned_team(team_name, context.run_id)
        for member in team.members:
            if not member.worktree_name:
                continue
            worktree = self.worktrees.get(
                repo_root=self._parent_record(context).workspace_root,
                name=member.worktree_name,
            )
            if worktree is not None:
                self.worktrees.remove(
                    repo_root=worktree.repo_root,
                    name=worktree.name,
                    discard_changes=discard_worktrees,
                )
        self.teams.delete(team_name)
        return {"team_name": team_name, "deleted": True}

    def send_message(
        self,
        *,
        team_name: str,
        to: str,
        content: str,
        type: str = "text",
        request_id: str = "",
        approve: bool | None = None,
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if type not in protocol.VALID_MESSAGE_TYPES:
            raise ValueError(f"invalid team message type: {type}")
        if not content.strip() and type == protocol.TEXT:
            raise ValueError("team message content is required")
        team = self._team_for_context(team_name, context)
        sender_member = self._member_for_child_run(team, context.run_id)
        sender = sender_member.name if sender_member is not None else "lead"
        sender_id = sender_member.agent_id if sender_member is not None else team.lead_agent_id
        if type in {protocol.PLAN_APPROVAL_RESPONSE, protocol.SHUTDOWN_RESPONSE} and approve is None:
            raise ValueError(f"{type} requires approve")
        if type == protocol.PLAN_APPROVAL_RESPONSE and not request_id:
            raise ValueError("plan_approval_response requires request_id")
        target = team.member(to) if to != "*" else None
        plan_task: CollaborationTask | None = None
        if type == protocol.PLAN_APPROVAL_RESPONSE:
            if target is None:
                raise TeamError("plan approval must target one teammate")
            plan_task = self._task_for_child_run(target.child_run_id)
            child = self._require_runtime().get_run(target.child_run_id)
            pending = child.pending_approval or {}
            if child.status != "waiting_approval" or pending.get("type") != "plan_exit":
                raise TeamError("teammate is not waiting for plan approval")
            if plan_task.plan_request_id and plan_task.plan_request_id != request_id:
                raise TeamError("plan approval request_id does not match the pending plan")
        message = create_message(
            sender,
            content,
            message_type=type,
            request_id=request_id or (protocol.new_request_id() if type != protocol.TEXT else ""),
            approve=approve,
        )
        mailbox = self.teams.mailbox(team.name)
        if to == "*":
            recipients = [member.agent_id for member in team.members]
            recipients.append(team.lead_agent_id)
            count = mailbox.broadcast(recipients, message, exclude=sender_id)
            for member in team.members:
                if member.agent_id != sender_id and member.status == "running":
                    self._steer(member, self._message_prompt(team.name, message))
            return {"team_name": team.name, "broadcast": True, "recipient_count": count}
        recipient = self.teams.resolve_recipient(team, to)
        if recipient is None:
            raise TeamError(f"cannot resolve team recipient: {to}")
        mailbox.write(recipient, message)
        if target is not None and target.status == "running":
            self._steer(target, self._message_prompt(team.name, message))
        if plan_task is not None:
            plan_task.future = self._executor.submit(
                self._resume_team_child,
                plan_task,
                approved=bool(approve),
                feedback=content,
                approved_by=context.actor_user_id,
            )
        return {
            "team_name": team.name,
            "to": to,
            "recipient_id": recipient,
            "request_id": message.request_id or None,
            "sent": True,
        }

    def shared_task_create(
        self,
        *,
        team_name: str,
        title: str,
        description: str = "",
        assignee: str = "",
        blocks: list[str] | None = None,
        blocked_by: list[str] | None = None,
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        team = self._team_for_context(team_name, context)
        creator = self._member_for_child_run(team, context.run_id)
        task = self.teams.tasks(team.name).create(
            title=title,
            description=description,
            assignee=assignee,
            blocks=blocks,
            blocked_by=blocked_by,
            created_by=creator.name if creator else "lead",
        )
        return task.to_dict()

    def shared_task_get(
        self,
        *,
        team_name: str,
        task_id: str,
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        team = self._team_for_context(team_name, context)
        task = self.teams.tasks(team.name).get(task_id)
        if task is None:
            raise KeyError(f"shared task not found: {task_id}")
        return task.to_dict()

    def shared_task_list(
        self,
        *,
        team_name: str,
        status: str | None = None,
        assignee: str | None = None,
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        team = self._team_for_context(team_name, context)
        tasks = self.teams.tasks(team.name).list(status=status, assignee=assignee)
        return {"tasks": [task.to_dict() for task in tasks], "count": len(tasks)}

    def shared_task_update(
        self,
        *,
        team_name: str,
        task_id: str,
        status: str | None = None,
        assignee: str | None = None,
        description: str | None = None,
        add_blocks: list[str] | None = None,
        add_blocked_by: list[str] | None = None,
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        team = self._team_for_context(team_name, context)
        task = self.teams.tasks(team.name).update(
            task_id,
            status=status,
            assignee=assignee,
            description=description,
            add_blocks=add_blocks,
            add_blocked_by=add_blocked_by,
        )
        if task is None:
            raise KeyError(f"shared task not found: {task_id}")
        return task.to_dict()

    def enter_worktree(
        self,
        *,
        name: str,
        base_ref: str = "HEAD",
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        parent = self._parent_record(context)
        worktree = self.worktrees.create(
            repo_root=parent.workspace_root,
            name=name,
            base_ref=base_ref,
        )
        return {
            **worktree.to_dict(),
            "entered": False,
            "notice": (
                "The current Run keeps its frozen execution root. Pass "
                "isolation='worktree' to Agent to execute in an isolated worktree."
            ),
        }

    def exit_worktree(
        self,
        *,
        name: str,
        action: str = "keep",
        discard_changes: bool = False,
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        parent = self._parent_record(context)
        worktree = self.worktrees.get(repo_root=parent.workspace_root, name=name)
        if worktree is None:
            raise WorktreeError(f"worktree not found: {name}")
        if action == "keep":
            changes = self.worktrees.changes(worktree)
            return {
                "name": name,
                "kept": True,
                "path": worktree.path,
                "branch": worktree.branch,
                "changes": changes.__dict__,
            }
        if action != "remove":
            raise ValueError("worktree action must be 'keep' or 'remove'")
        result = self.worktrees.remove(
            repo_root=parent.workspace_root,
            name=name,
            discard_changes=discard_changes,
        )
        return {
            "name": name,
            "kept": result.kept,
            "changes": result.changes.__dict__,
        }

    def list_worktrees(self, *, context: ToolExecutionContext) -> dict[str, Any]:
        parent = self._parent_record(context)
        values = self.worktrees.list_worktrees(repo_root=parent.workspace_root)
        items = []
        for worktree in values:
            try:
                changes = self.worktrees.changes(worktree)
                status = changes.__dict__
            except WorktreeError as exc:
                status = {"error": str(exc)}
            items.append({**worktree.to_dict(), "changes": status})
        return {"worktrees": items, "count": len(items)}

    def drain_notifications(self, parent_run_id: str) -> list[str]:
        notifications = [
            message.text
            for message in self.notifications.consume(
                self._notification_recipient(parent_run_id)
            )
        ]
        for team in self.teams.list():
            recipient = ""
            if team.lead_agent_id == parent_run_id:
                recipient = team.lead_agent_id
            else:
                member = self._member_for_child_run(team, parent_run_id)
                if member is not None:
                    recipient = member.agent_id
            if not recipient:
                continue
            for message in self.teams.mailbox(team.name).consume(recipient):
                notifications.append(
                    "<team-notification "
                    f'team="{team.name}" type="{message.type}" '
                    f'from="{message.from_agent}" request_id="{message.request_id}">\n'
                    f"{message.text}\n"
                    "</team-notification>"
                )
        return notifications

    def _prepare_child(
        self,
        *,
        parent: Any,
        parent_context: ToolExecutionContext,
        task_id: str,
        prompt: str,
        definition: AgentDefinition,
        name: str,
        model: str | None,
        isolation: str,
        team_name: str,
        plan_mode_required: bool,
    ) -> _PreparedChild:
        runtime = self._require_runtime()
        if parent.context_snapshot is None:
            raise RuntimeError("Subagent requires a persisted Run context snapshot")
        child_run_id = f"run_sub_{uuid4().hex[:12]}"
        worktree: Worktree | None = None
        if isolation == "worktree":
            worktree_name = (
                f"team-{team_name}/{name}" if team_name else f"agent-{task_id}"
            )
            worktree = self.worktrees.create(
                repo_root=parent.workspace_root,
                name=worktree_name,
                base_ref="HEAD",
            )
            execution = self.execution_workspaces.adopt_worktree(
                run_id=child_run_id,
                workspace_id=parent.workspace_id,
                source_root=parent.workspace_root,
                worktree_path=worktree.path,
                branch_name=worktree.branch,
                base_git_head=worktree.head_commit,
            )
        else:
            execution = self.execution_workspaces.share(
                parent_context=parent_context,
                child_run_id=child_run_id,
            )
        snapshot = self._child_context(
            parent,
            child_run_id=child_run_id,
            task_id=task_id,
            prompt=prompt,
            definition=definition,
            name=name,
            execution=execution,
            team_name=team_name,
            plan_mode_required=plan_mode_required,
        )
        selection = snapshot.session.model_selection.to_dict()
        selected_model = model if model is not None else definition.model
        if selected_model and selected_model != "inherit":
            selection.update(
                mode="direct",
                preferred_model_id=selected_model,
                fallback_enabled=False,
            )
            snapshot = replace(
                snapshot,
                session=replace(
                    snapshot.session,
                    model_selection=type(snapshot.session.model_selection).from_mapping(
                        selection
                    ),
                ),
            )
        record = runtime.create_queued_record(
            conversation_id=snapshot.session.conversation_id,
            workspace_id=parent.workspace_id,
            workspace_root=parent.workspace_root,
            run_id=child_run_id,
            context_snapshot=snapshot,
        )
        state = CogentState.from_mapping(record.runtime_state)
        parent_state = CogentState.from_mapping(parent.runtime_state)
        post_plan_permission_mode = self._child_permission_mode(
            parent_state.permission_mode,
            definition.permission_mode,
        )
        state.permission_mode = (
            "plan" if plan_mode_required else post_plan_permission_mode
        )
        if plan_mode_required:
            state.sandbox["previous_permission_mode"] = post_plan_permission_mode
        state.collaboration = {
            "internal": True,
            "parent_run_id": parent.run_id,
            "usage_session_id": parent.conversation_id,
            "task_id": task_id,
            "agent_type": definition.agent_type,
            "team_name": team_name,
            "name": name,
            "max_turns": definition.max_turns,
            "post_plan_permission_mode": post_plan_permission_mode,
            "finalized": False,
            "worktree_name": worktree.name if worktree else "",
            "worktree_base_head": worktree.head_commit if worktree else "",
            "allowed_visible_tools": (
                sorted(
                    set(definition.tools)
                    | (TEAM_MEMBER_TOOLS if team_name else set())
                )
                if definition.tools
                else []
            ),
            "disabled_visible_tools": sorted(
                set(definition.disallowed_tools)
                | (COLLABORATION_VISIBLE_TOOLS - (TEAM_MEMBER_TOOLS if team_name else set()))
            ),
        }
        runtime.restore_record(replace(record, runtime_state=state.to_dict()))
        task = CollaborationTask(
            id=task_id,
            parent_run_id=parent.run_id,
            child_run_id=child_run_id,
            name=name,
            agent_type=definition.agent_type,
            prompt=prompt,
            team_name=team_name,
            worktree=worktree,
        )
        history: list[dict[str, Any]] = []
        user_input = prompt
        if definition.agent_type == "fork":
            history = self._fork_history(parent_state)
            user_input = (
                f"{FORK_MARKER}\nYou are a forked worker, not the lead. Do not "
                "spawn another Agent or ask the user questions. Stay within the "
                f"assigned scope.\n</cogent-subagent>\n\nTask:\n{prompt}"
            )
        return _PreparedChild(
            task=task,
            context=snapshot,
            history=history,
            user_input=user_input,
            actor_user_id=snapshot.identity.actor_user_id,
            model_selection=ModelSelection(**selection),
        )

    def _execute_child(self, prepared: _PreparedChild) -> None:
        task = prepared.task
        task.status = "running"
        runtime = self._require_runtime()
        child_record = runtime.get_run(task.child_run_id)
        try:
            with model_selection_scope(prepared.model_selection):
                with model_usage_scope(
                    session_id=self._usage_session_id(child_record),
                    workspace_id=prepared.context.project.workspace_id,
                    operation="subagent",
                    resource_id=task.child_run_id,
                ):
                    result = runtime.run(
                        run_id=task.child_run_id,
                        conversation_id=prepared.context.session.conversation_id,
                        user_input=prepared.user_input,
                        history=prepared.history,
                        workspace_id=prepared.context.project.workspace_id,
                        workspace_root=prepared.context.project.workspace_root,
                        focus_files=list(prepared.context.instructions.focus_files),
                        actor_user_id=prepared.actor_user_id,
                        run_context=prepared.context,
                    )
            task.status = (
                "completed"
                if result.status == "completed"
                else (
                    "cancelled"
                    if result.status == "cancelled"
                    else (
                        result.status
                        if result.status
                        in {"paused", "waiting_approval", "waiting_input"}
                        else "blocked"
                    )
                )
            )
            task.result = result.answer
            if result.status not in {"completed", "cancelled"}:
                task.error = result.terminal_reason or result.status
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            try:
                runtime.mark_run_failed(
                    run_id=task.child_run_id,
                    error=str(exc),
                    node="subagent",
                )
            except Exception:
                pass
        finally:
            if task.status in TERMINAL_TASK_STATUSES:
                task.completed_at = time.time()
            self._finish_task(task)

    def _finish_task(self, task: CollaborationTask) -> None:
        with self._lock:
            if task.finalized:
                return
            if task.status in TERMINAL_TASK_STATUSES:
                task.finalized = True
        try:
            self._finish_task_once(task)
        except Exception:
            if task.status in TERMINAL_TASK_STATUSES:
                with self._lock:
                    task.finalized = False
            raise

    def _finish_task_once(self, task: CollaborationTask) -> None:
        if task.worktree is not None and task.status in TERMINAL_TASK_STATUSES:
            try:
                cleanup = self.worktrees.auto_cleanup(task.worktree)
            except WorktreeError as exc:
                task.error = f"{task.error}; worktree cleanup failed: {exc}".strip("; ")
            else:
                if cleanup.kept:
                    suffix = (
                        f"\n[Worktree preserved at {cleanup.path}; branch "
                        f"{cleanup.branch}; {cleanup.changes.uncommitted} uncommitted, "
                        f"{cleanup.changes.new_commits} new commits]"
                    )
                    task.result = (task.result or "") + suffix
        if task.team_name and task.member_id:
            try:
                member = self.teams.update_member(
                    task.team_name,
                    task.member_id,
                    status="idle" if task.status == "completed" else task.status,
                    child_run_id=task.child_run_id,
                )
                team = self.teams.get(task.team_name)
                if team is not None:
                    mailbox = self.teams.mailbox(team.name)
                    child = self._require_runtime().get_run(task.child_run_id)
                    pending = child.pending_approval or {}
                    if (
                        task.status == "waiting_approval"
                        and pending.get("type") == "plan_exit"
                    ):
                        task.plan_request_id = (
                            task.plan_request_id or protocol.new_request_id()
                        )
                        summary = self._plan_summary(pending)
                        mailbox.write(
                            team.lead_agent_id,
                            create_message(
                                member.name,
                                summary,
                                message_type=protocol.PLAN_APPROVAL_REQUEST,
                                request_id=task.plan_request_id,
                            ),
                        )
                    else:
                        mailbox.write(
                            team.lead_agent_id,
                            create_message(
                                member.name,
                                self._notification(task),
                            ),
                        )
            except Exception:
                pass
        if not task.team_name:
            self.notifications.write(
                self._notification_recipient(task.parent_run_id),
                create_message(task.name, self._notification(task)),
            )
        if task.status in TERMINAL_TASK_STATUSES:
            self._persist_finalized(task)

    def child_completed(self, record: Any, result: Any) -> None:
        task = self._task_from_record(record)
        if task is None:
            return
        task.status = (
            "completed"
            if result.status == "completed"
            else ("cancelled" if result.status == "cancelled" else "blocked")
        )
        task.result = result.answer
        task.error = result.terminal_reason if task.status != "completed" else ""
        task.completed_at = time.time()
        self._finish_task(task)

    def child_failed(self, record: Any, error: str) -> None:
        task = self._task_from_record(record)
        if task is None:
            return
        task.status = "failed"
        task.error = error
        task.completed_at = time.time()
        self._finish_task(task)

    def _resume_team_child(
        self,
        task: CollaborationTask,
        *,
        approved: bool,
        feedback: str,
        approved_by: str,
    ) -> None:
        task.status = "running"
        runtime = self._require_runtime()
        record = runtime.get_run(task.child_run_id)
        snapshot = record.context_snapshot
        selection = (
            ModelSelection(**snapshot.session.model_selection.to_dict())
            if snapshot is not None
            else None
        )
        try:
            with model_selection_scope(selection):
                with model_usage_scope(
                    session_id=self._usage_session_id(record),
                    workspace_id=record.workspace_id,
                    operation="subagent",
                    resource_id=task.child_run_id,
                ):
                    result = runtime.resume(
                        run_id=task.child_run_id,
                        approved=approved,
                        feedback=feedback,
                        approved_by=approved_by,
                    )
            task.status = (
                result.status
                if result.status
                in {
                    "completed",
                    "cancelled",
                    "paused",
                    "waiting_approval",
                    "waiting_input",
                }
                else "blocked"
            )
            task.result = result.answer
            task.error = (
                result.terminal_reason
                if result.status not in {"completed", "cancelled"}
                else ""
            )
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            try:
                runtime.mark_run_failed(
                    run_id=task.child_run_id,
                    error=str(exc),
                    node="subagent_resume",
                )
            except Exception:
                pass
        finally:
            if task.status in TERMINAL_TASK_STATUSES:
                task.completed_at = time.time()
            self._finish_task(task)

    @staticmethod
    def _plan_summary(pending: dict[str, Any]) -> str:
        for item in pending.get("tool_calls") or []:
            if not isinstance(item, dict) or item.get("name") != "ExitPlanMode":
                continue
            arguments = item.get("arguments") or {}
            if isinstance(arguments, dict) and str(arguments.get("summary") or "").strip():
                return str(arguments["summary"]).strip()
        return "Teammate submitted a plan for approval."

    @staticmethod
    def _notification(task: CollaborationTask) -> str:
        result = task.result or task.error or "(no output)"
        if len(result) > MAX_NOTIFICATION_CHARS:
            result = result[:MAX_NOTIFICATION_CHARS] + "\n... (truncated)"
        return (
            "<task-notification>\n"
            f"Task ID: {task.id}\n"
            f"Child Run: {task.child_run_id}\n"
            f"Agent: {task.name}\n"
            f"Status: {task.status}\n"
            f"Result:\n{result}\n"
            "</task-notification>"
        )

    @staticmethod
    def _notification_recipient(run_id: str) -> str:
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]
        return f"run-{digest}"

    def _child_context(
        self,
        parent: Any,
        *,
        child_run_id: str,
        task_id: str,
        prompt: str,
        definition: AgentDefinition,
        name: str,
        execution: Any,
        team_name: str,
        plan_mode_required: bool,
    ) -> Any:
        snapshot = parent.context_snapshot
        instruction = definition.system_prompt.strip()
        if team_name:
            instruction = (
                instruction
                + TEAMMATE_ADDENDUM
                + f"\nTeam: {team_name}\nTeammate: {name}\n"
            ).strip()
        sources = list(snapshot.instructions.sources)
        if instruction:
            sources.append(
                InstructionSourceSnapshot(
                    kind="subagent_definition",
                    path=(
                        str(definition.file_path)
                        if definition.file_path is not None
                        else f"builtin://agents/{definition.agent_type}"
                    ),
                    start_line=1,
                    end_line=instruction.count("\n") + 1,
                    text=instruction,
                    reason=f"Subagent type {definition.agent_type}",
                    content_hash=hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
                    truncated=False,
                    priority=1_000,
                )
            )
        metadata_value = snapshot.metadata.entrypoint_metadata
        metadata_value["collaboration"] = {
            "internal": True,
            "parent_run_id": parent.run_id,
            "task_id": task_id,
            "agent_type": definition.agent_type,
            "team_name": team_name,
            "plan_mode_required": plan_mode_required,
        }
        execution_context = ExecutionWorkspaceContext(
            run_id=child_run_id,
            workspace_id=parent.workspace_id,
            source_root=str(execution.source_root),
            execution_root=str(execution.execution_root),
            mode=execution.mode,
            baseline=execution.baseline_digest,
            base_git_head=execution.base_git_head,
            branch_name=execution.branch_name,
            worktree_path=(str(execution.worktree_path) if execution.worktree_path else None),
            cleanup_policy=execution.cleanup_policy,
            created_at=execution.created_at,
            status=execution.status,
        )
        child_conversation = f"{parent.conversation_id}:subagent:{task_id}"
        return replace(
            snapshot,
            session=replace(
                snapshot.session,
                conversation_id=child_conversation,
                user_message=prompt,
                controlled_history=(),
                summary=None,
            ),
            project=replace(snapshot.project, cwd=str(execution.execution_root)),
            instructions=replace(snapshot.instructions, sources=tuple(sources)),
            metadata=RunMetadata(
                run_id=child_run_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                entrypoint_type="agent_loop",
                config_version=snapshot.metadata.config_version,
                schema_version=snapshot.metadata.schema_version,
                _entrypoint_metadata_json=json.dumps(
                    metadata_value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
            execution_workspace=execution_context,
        )

    @staticmethod
    def _fork_history(parent_state: CogentState) -> list[dict[str, Any]]:
        history = deepcopy(parent_state.messages[1:] if parent_state.messages else [])
        pending_ids = {
            str(item.get("call_id") or "")
            for item in parent_state.pending_calls
            if item.get("call_id")
        }
        completed = {
            str(item.get("call_id") or "")
            for item in parent_state.all_tool_results
            if item.get("call_id")
        }
        for call_id in sorted(pending_ids - completed):
            history.append(
                {
                    "role": "tool",
                    "call_id": call_id,
                    "name": "Agent",
                    "content": {"interrupted": True},
                    "is_error": False,
                }
            )
        return history

    @staticmethod
    def _child_permission_mode(parent: str, requested: str) -> str:
        if requested == "plan":
            return "plan"
        if parent == "bypassPermissions":
            return requested or "default"
        if parent == "acceptEdits" and requested in {"acceptEdits", "bypassPermissions"}:
            return "acceptEdits"
        return "default"

    @staticmethod
    def _definition(loader: AgentLoader, agent_type: str | None) -> AgentDefinition:
        if not agent_type:
            return AgentDefinition(
                agent_type="fork",
                when_to_use="Forked from the parent Run",
                model="inherit",
            )
        definition = loader.get(agent_type)
        if definition is None:
            available = ", ".join(name for name, _ in loader.list_agents())
            raise ValueError(f"unknown Subagent type '{agent_type}'; available: {available}")
        return definition

    def _parent_record(self, context: ToolExecutionContext) -> Any:
        if not context.run_id:
            raise ValueError("collaboration tool requires a Run context")
        record = self._require_runtime().get_run(context.run_id)
        collaboration = (record.runtime_state or {}).get("collaboration") or {}
        if collaboration.get("internal") and not collaboration.get("team_name"):
            raise PermissionError("Subagents cannot spawn nested Subagents")
        return record

    def _task_for_parent(self, task_id: str, parent_run_id: str | None) -> CollaborationTask:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            self._hydrate_tasks()
            with self._lock:
                task = self._tasks.get(task_id)
        if task is None or task.parent_run_id != parent_run_id:
            raise KeyError(f"Subagent task not found: {task_id}")
        return task

    def _task_for_child_run(self, child_run_id: str) -> CollaborationTask:
        with self._lock:
            task = next(
                (
                    item
                    for item in self._tasks.values()
                    if item.child_run_id == child_run_id
                ),
                None,
            )
        if task is None:
            self._hydrate_tasks()
            with self._lock:
                task = next(
                    (
                        item
                        for item in self._tasks.values()
                        if item.child_run_id == child_run_id
                    ),
                    None,
                )
        if task is None:
            raise KeyError(f"Subagent task not found for child Run: {child_run_id}")
        return task

    def _hydrate_tasks(self) -> None:
        runtime = self._require_runtime()
        list_runs = getattr(runtime, "list_recoverable_runs", None)
        if not callable(list_runs):
            return
        for record in list_runs(limit=1000):
            self._task_from_record(record)

    def _task_from_record(self, record: Any) -> CollaborationTask | None:
        metadata = collaboration_metadata(record)
        if not metadata.get("internal") or not metadata.get("task_id"):
            return None
        task_id = str(metadata["task_id"])
        with self._lock:
            existing = self._tasks.get(task_id)
        if existing is not None:
            return existing
        snapshot = record.context_snapshot
        execution = snapshot.execution_workspace if snapshot is not None else None
        worktree: Worktree | None = None
        worktree_name = str(metadata.get("worktree_name") or "")
        if execution is not None and execution.mode == "worktree" and worktree_name:
            worktree = Worktree(
                name=worktree_name,
                repo_root=record.workspace_root,
                path=str(execution.worktree_path or execution.execution_root),
                branch=str(execution.branch_name or ""),
                based_on="HEAD",
                head_commit=str(
                    metadata.get("worktree_base_head")
                    or execution.base_git_head
                    or ""
                ),
                created_at=execution.created_at,
            )
            try:
                self.worktrees.adopt(worktree)
            except (OSError, WorktreeError):
                worktree = None
        recovered_status = (
            "running"
            if record.status in {"queued", "running"}
            else (
                record.status
                if record.status
                in {
                    "completed",
                    "cancelled",
                    "failed",
                    "paused",
                    "waiting_approval",
                    "waiting_input",
                }
                else "blocked"
            )
        )
        task = CollaborationTask(
            id=task_id,
            parent_run_id=str(metadata.get("parent_run_id") or ""),
            child_run_id=record.run_id,
            name=str(metadata.get("name") or metadata.get("agent_type") or "agent"),
            agent_type=str(metadata.get("agent_type") or "general-purpose"),
            prompt=(snapshot.session.user_message if snapshot is not None else ""),
            status=recovered_status,
            result=(record.result.answer if record.result is not None else ""),
            error=str(record.error or ""),
            team_name=str(metadata.get("team_name") or ""),
            worktree=worktree,
            completed_at=(
                time.time() if recovered_status in TERMINAL_TASK_STATUSES else None
            ),
            finalized=bool(metadata.get("finalized")),
        )
        if task.team_name:
            team = self.teams.get(task.team_name)
            member = self._member_for_child_run(team, record.run_id) if team else None
            if member is not None:
                task.member_id = member.agent_id
        with self._lock:
            restored = self._tasks.setdefault(task.id, task)
        if (
            restored is task
            and task.status in TERMINAL_TASK_STATUSES
            and not task.finalized
        ):
            self._finish_task(task)
        return restored

    def _persist_finalized(self, task: CollaborationTask) -> None:
        runtime = self._require_runtime()
        record = runtime.get_run(task.child_run_id)
        state = CogentState.from_mapping(record.runtime_state)
        if state.collaboration.get("finalized"):
            return
        state.collaboration["finalized"] = True
        runtime.update_runtime_state(
            run_id=record.run_id,
            runtime_state=state.to_dict(),
        )

    def _usage_session_id(self, record: Any) -> str:
        collaboration = (record.runtime_state or {}).get("collaboration") or {}
        session_id = str(collaboration.get("usage_session_id") or "")
        if session_id:
            return session_id
        parent_run_id = str(collaboration.get("parent_run_id") or "")
        if parent_run_id:
            try:
                return str(self._require_runtime().get_run(parent_run_id).conversation_id)
            except Exception:
                pass
        return str(record.conversation_id)

    def _owned_team(self, team_name: str, run_id: str | None) -> Any:
        team = self.teams.get(team_name)
        if team is None:
            raise TeamError(f"team not found: {team_name}")
        if team.lead_agent_id != run_id:
            raise PermissionError("only the team lead Run may perform this operation")
        return team

    def _team_for_context(self, team_name: str, context: ToolExecutionContext) -> Any:
        team = self.teams.get(team_name)
        if team is None:
            raise TeamError(f"team not found: {team_name}")
        if team.lead_agent_id == context.run_id or self._member_for_child_run(team, context.run_id):
            return team
        raise PermissionError("Run is not a member of this Agent Team")

    @staticmethod
    def _member_for_child_run(team: Any, run_id: str | None) -> TeammateInfo | None:
        return next(
            (member for member in team.members if member.child_run_id == run_id),
            None,
        )

    def _steer(self, member: TeammateInfo, content: str) -> None:
        try:
            self._require_runtime().request_control(
                run_id=member.child_run_id,
                action="steer",
                message=content,
            )
        except Exception:
            pass

    @staticmethod
    def _message_prompt(team_name: str, message: Any) -> str:
        return (
            "<team-message "
            f'team="{team_name}" type="{message.type}" '
            f'from="{message.from_agent}" request_id="{message.request_id}">\n'
            f"{message.text}\n"
            "</team-message>"
        )

    def _require_runtime(self) -> Any:
        if self._runtime is None:
            raise RuntimeError("CollaborationManager is not bound to CogentRuntime")
        return self._runtime


def collaboration_metadata(record: Any) -> dict[str, Any]:
    value = (record.runtime_state or {}).get("collaboration") or {}
    return dict(value) if isinstance(value, dict) else {}


__all__ = [
    "COLLABORATION_VISIBLE_TOOLS",
    "CollaborationManager",
    "CollaborationTask",
    "TEAM_MEMBER_TOOLS",
    "collaboration_metadata",
]
