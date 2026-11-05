from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest

from ai_agent_platform.agents.coding import InMemoryAgentRunStore
from ai_agent_platform.core import Settings
from ai_agent_platform.cogent.agents import AgentLoader
from ai_agent_platform.cogent.collaboration import CollaborationManager
from ai_agent_platform.cogent.collaboration_tools import register_collaboration_tools
from ai_agent_platform.cogent.runtime import CogentRuntime
from ai_agent_platform.cogent.state import CogentState
from ai_agent_platform.cogent.teams import Mailbox, TeamManager, TeammateInfo, create_message
from ai_agent_platform.cogent.tools import CogentToolAdapter
from ai_agent_platform.cogent.worktree import WorktreeManager
from ai_agent_platform.domain import Session
from ai_agent_platform.integrations.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceRuntime,
)
from ai_agent_platform.integrations.llm import LLMToolDecision
from ai_agent_platform.integrations.permissions import PermissionResolver, ToolUseContext
from ai_agent_platform.integrations.tool_pool import ToolPoolBuilder
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry
from ai_agent_platform.model_registry import ModelSelection
from ai_agent_platform.repositories import (
    InMemorySessionRepository,
    InMemoryWorkspaceRepository,
)
from ai_agent_platform.services import ExecutionContextFactory, WorkspaceService
from ai_agent_platform.usage_ledger import UsageLedgerService


class _SessionService:
    def __init__(self, session_id: str = "conversation-parent") -> None:
        self.session = Session(
            id=session_id,
            user_id="owner",
            created_at=datetime.now(timezone.utc),
        )

    def get_session(self, *, session_id: str):
        assert session_id == self.session.id
        return self.session

    def resolve_execution_config(self, **values):
        return values

    def build_agent_context(self, **_):
        return []

    def get_conversation_summary(self, _):
        return None


class _Client:
    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    def decide_tools(self, messages, tools, **kwargs):
        self.requests.append(messages)
        kwargs["on_delta"]("child complete")
        return LLMToolDecision(
            text="child complete",
            tool_calls=[],
            provider="test",
            model="test-model",
            stop_reason="end_turn",
        )


class _UsageAwareClient(_Client):
    def __init__(self, sessions: InMemorySessionRepository) -> None:
        super().__init__()
        self.ledger = UsageLedgerService(sessions, Settings())

    def decide_tools(self, messages, tools, **kwargs):
        authorization = self.ledger.authorize(
            requested_provider="test",
            requested_model="test-model",
            input_tokens=10,
            max_output_tokens=100,
            input_count_method="test",
        )
        self.ledger.record(
            provider="test",
            model="test-model",
            input_tokens=authorization.input_tokens,
            output_tokens=2,
            requested_provider=authorization.requested_provider,
            requested_model=authorization.requested_model,
            input_count_method=authorization.input_count_method,
            budget_decision=authorization.budget_decision,
        )
        return super().decide_tools(messages, tools, **kwargs)


class _ChildFailureClient:
    def __init__(self) -> None:
        self.index = 0

    def decide_tools(self, messages, tools, **kwargs):
        self.index += 1
        if self.index == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        "Agent",
                        {
                            "prompt": "Inspect the child scope.",
                            "description": "child inspection",
                            "subagent_type": "general-purpose",
                        },
                        "spawn-failure",
                    )
                ],
                provider="test",
                model="test-model",
                stop_reason="tool_use",
            )
        if self.index == 2:
            raise RuntimeError("child provider failed")
        kwargs["on_delta"]("lead observed child failure")
        return LLMToolDecision(
            text="lead observed child failure",
            tool_calls=[],
            provider="test",
            model="test-model",
            stop_reason="end_turn",
        )


class _SequenceClient:
    def __init__(self) -> None:
        self.index = 0

    def decide_tools(self, messages, tools, **kwargs):
        self.index += 1
        if self.index == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        "Agent",
                        {
                            "prompt": "Inspect the child scope.",
                            "description": "child inspection",
                            "subagent_type": "general-purpose",
                        },
                        "spawn-1",
                    )
                ],
                provider="test",
                model="test-model",
                stop_reason="tool_use",
            )
        text = "child complete" if self.index == 2 else "lead complete"
        kwargs["on_delta"](text)
        return LLMToolDecision(
            text=text,
            tool_calls=[],
            provider="test",
            model="test-model",
            stop_reason="end_turn",
        )


class _PlanClient:
    def __init__(self) -> None:
        self.index = 0

    def decide_tools(self, messages, tools, **kwargs):
        self.index += 1
        if self.index == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        "ExitPlanMode",
                        {"summary": "Inspect the API, then implement the approved change."},
                        "plan-1",
                    )
                ],
                provider="test",
                model="test-model",
                stop_reason="tool_use",
            )
        kwargs["on_delta"]("approved work complete")
        return LLMToolDecision(
            text="approved work complete",
            tool_calls=[],
            provider="test",
            model="test-model",
            stop_reason="end_turn",
        )


def test_empty_mailbox_consume_does_not_take_write_lock(tmp_path, monkeypatch):
    mailbox = Mailbox(tmp_path / "mailbox")

    def fail_mutation(*_args, **_kwargs):
        raise AssertionError("empty mailbox should not be mutated")

    monkeypatch.setattr(mailbox, "_mutate", fail_mutation)
    assert mailbox.consume("run-parent") == []


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Tests")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "base")
    return repo


def test_agent_loader_uses_project_before_user_and_expands_includes(tmp_path):
    project = tmp_path / "project"
    project_agents = project / ".cogent" / "agents"
    project_agents.mkdir(parents=True)
    (project_agents / "rules.txt").write_text("project rule", encoding="utf-8")
    (project_agents / "review.md").write_text(
        "---\nname: review\ndescription: project reviewer\n---\n@./rules.txt\n",
        encoding="utf-8",
    )
    user_agents = tmp_path / "user-agents"
    user_agents.mkdir()
    (user_agents / "review.md").write_text(
        "---\nname: review\ndescription: user reviewer\n---\nuser rule\n",
        encoding="utf-8",
    )

    loader = AgentLoader(project, user_root=user_agents)
    definitions = loader.load_all()

    assert definitions["review"].source == "project"
    assert definitions["review"].when_to_use == "project reviewer"
    assert definitions["review"].system_prompt.strip().endswith("project rule")
    assert "general-purpose" in definitions


def test_team_mailbox_shared_tasks_and_cleanup(tmp_path):
    manager = TeamManager(tmp_path / "teams")
    team = manager.create(name="review-team", lead_agent_id="run-lead")
    member = TeammateInfo(
        name="reviewer",
        agent_id="agent-reviewer",
        agent_type="review",
        model="inherit",
        worktree_name="team/reviewer",
        worktree_path="/tmp/worktree",
    )
    manager.add_member(team.name, member)
    manager.mailbox(team.name).write(
        member.agent_id,
        create_message("lead", "inspect the API"),
    )
    shared = manager.tasks(team.name).create(title="Review API", assignee=member.name)

    assert [message.text for message in manager.mailbox(team.name).consume(member.agent_id)] == [
        "inspect the API"
    ]
    assert manager.mailbox(team.name).consume(member.agent_id) == []
    assert manager.tasks(team.name).get(shared.id).assignee == "reviewer"
    manager.update_member(team.name, member.agent_id, status="idle")
    manager.delete(team.name)
    assert manager.get(team.name) is None


def test_worktree_clean_cleanup_and_dirty_retention(tmp_path):
    repo = _repository(tmp_path)
    manager = WorktreeManager(parent=tmp_path / "worktrees")

    clean = manager.create(repo_root=repo, name="clean")
    assert manager.auto_cleanup(clean).kept is False
    assert not Path(clean.path).exists()

    dirty = manager.create(repo_root=repo, name="team/reviewer")
    (Path(dirty.path) / "review.txt").write_text("finding\n", encoding="utf-8")
    result = manager.auto_cleanup(dirty)
    assert result.kept is True
    assert result.changes.uncommitted == 1
    assert Path(result.path).exists()
    _git(Path(dirty.path), "add", "review.txt")
    _git(Path(dirty.path), "commit", "-qm", "review finding")
    recovered_manager = WorktreeManager(parent=tmp_path / "worktrees")
    recovered = recovered_manager.create(repo_root=repo, name=dirty.name)
    assert recovered.path == dirty.path
    assert recovered_manager.changes(recovered).new_commits == 1
    recovered_manager.remove(repo_root=repo, name=dirty.name, discard_changes=True)


def test_collaboration_aliases_and_child_tool_filtering(tmp_path):
    registry = ToolRegistry(PermissionResolver())

    class _Manager:
        def spawn(self, *, context, **kwargs):
            return kwargs

        def __getattr__(self, _name):
            return self.spawn

    register_collaboration_tools(registry, _Manager())
    adapter = CogentToolAdapter(
        registry,
        allowed_visible_tools={"Agent", "TaskList"},
        disabled_visible_tools={"Agent"},
    )
    assert [spec.name for spec in adapter.list_specs()] == ["TaskList"]


def test_foreground_execution_workspace_share_survives_runtime_restart(tmp_path):
    repo = _repository(tmp_path)
    runtime_parent = tmp_path / "execution"
    original = ExecutionWorkspaceRuntime(runtime_parent=runtime_parent)
    parent_record = original.prepare(
        run_id="run-parent",
        workspace_id="workspace",
        source_root=repo,
        mode="patch_only",
    )
    execution_root = parent_record.execution_root

    restored = ExecutionWorkspaceRuntime(runtime_parent=runtime_parent)
    child_record = restored.restore_shared(
        parent_record.to_dict(),
        child_run_id="run-child",
        authorized_source_root=repo,
    )
    parent_context = ToolUseContext(
        conversation_id="conversation-parent",
        workspace_id="workspace",
        workspace_root=str(repo),
        run_id="run-parent",
        actor_user_id="owner",
        workspace_role="admin",
        execution_workspace_mode="patch_only",
    )
    child_context = replace(parent_context, run_id="run-child")

    assert restored.for_context(parent_context) is child_record
    assert restored.for_context(child_context) is child_record
    assert restored.cleanup(child_context) is True
    assert execution_root.exists()
    assert restored.cleanup(parent_context) is True
    assert not execution_root.exists()


def test_foreground_subagent_is_a_hidden_durable_child_run(tmp_path):
    repo = _repository(tmp_path)
    execution = ExecutionWorkspaceRuntime(
        runtime_parent=tmp_path / "execution",
        worktree_parent=tmp_path / "worktrees",
    )
    manager = CollaborationManager(
        teams_root=tmp_path / "teams",
        worktree_parent=execution.worktree_parent,
        execution_workspaces=execution,
        user_agents_root=tmp_path / "agents",
    )
    registry = ToolRegistry(PermissionResolver())
    register_collaboration_tools(registry, manager)
    pool_builder = ToolPoolBuilder(registry)
    runtime = CogentRuntime(
        tool_registry=registry,
        run_store=InMemoryAgentRunStore(),
        llm_client=_Client(),
        tool_pool_builder=pool_builder,
        execution_workspace_runtime=execution,
        collaboration_manager=manager,
    )
    workspaces = WorkspaceService(
        store=InMemoryWorkspaceRepository(),
        allowed_roots=(str(repo),),
    )
    workspaces.register(workspace_id="workspace", root_path=str(repo))
    snapshot = ExecutionContextFactory(
        session_service=_SessionService(),
        workspace_service=workspaces,
        auth_mode="disabled",
        tool_registry=registry,
        tool_pool_builder=pool_builder,
        execution_workspace_runtime=execution,
    ).create(
        conversation_id="conversation-parent",
        user_message="lead request",
        workspace_id="workspace",
        model_selection=ModelSelection(),
        actor_user_id="owner",
        run_id="run-parent",
    )
    parent = runtime.create_queued_run(
        conversation_id="conversation-parent",
        workspace_id="workspace",
        workspace_root=str(repo),
        run_id="run-parent",
        context_snapshot=snapshot,
    )
    tool_context = ToolUseContext(
        conversation_id=parent.conversation_id,
        workspace_id=parent.workspace_id,
        workspace_root=parent.workspace_root,
        run_id=parent.run_id,
        actor_user_id="owner",
        workspace_role="admin",
        authorized_workspace_root=parent.workspace_root,
        execution_root=snapshot.execution_workspace.execution_root,
        execution_workspace_mode=snapshot.execution_workspace.mode,
    )

    task = manager.spawn(
        prompt="Inspect the repository and report.",
        description="repository inspection",
        subagent_type="general-purpose",
        model="child-model",
        context=tool_context,
    )

    child = runtime.get_run(task["child_run_id"])
    assert task["status"] == "completed"
    assert task["result"] == "child complete"
    assert child.status == "completed"
    assert child.context_snapshot.session.model_selection.preferred_model_id == "child-model"
    assert child.context_snapshot.session.model_selection.fallback_enabled is False
    assert child.runtime_state["collaboration"]["parent_run_id"] == parent.run_id
    assert child.runtime_state["collaboration"]["usage_session_id"] == parent.conversation_id
    assert child.runtime_state["collaboration"]["internal"] is True
    assert child.runtime_state["collaboration"]["finalized"] is True
    assert [record.run_id for record in runtime.list_recent_runs()] == [parent.run_id]
    assert {record.run_id for record in runtime.list_recoverable_runs()} == {
        parent.run_id,
        child.run_id,
    }
    restored_manager = CollaborationManager(
        teams_root=tmp_path / "teams",
        worktree_parent=execution.worktree_parent,
        execution_workspaces=execution,
        user_agents_root=tmp_path / "agents",
    )
    restored_manager.bind_runtime(runtime)
    restored = restored_manager.task_get(task_id=task["id"], context=tool_context)
    assert restored["status"] == "completed"
    assert restored["result"] == "child complete"
    restored_manager.close()

    team = manager.team_create(
        team_name="api-team",
        description="parallel API review",
        context=tool_context,
    )
    team_task = manager.spawn(
        prompt="Review the API surface.",
        description="API review",
        subagent_type="general-purpose",
        team_name=team["name"],
        name="reviewer",
        context=tool_context,
    )
    manager._tasks[team_task["task_id"]].future.result(timeout=5)
    finished = manager.task_get(task_id=team_task["task_id"], context=tool_context)
    assert finished["status"] == "completed"
    team_child = runtime.get_run(team_task["child_run_id"])
    assert team_child.runtime_state["collaboration"]["allowed_visible_tools"] == []
    persisted_team = manager.teams.get(team["name"])
    assert persisted_team.members[0].status == "idle"
    notifications = manager.drain_notifications(parent.run_id)
    assert any("child complete" in item for item in notifications)
    assert not Path(finished["worktree"]["path"]).exists()
    assert manager.team_delete(team_name=team["name"], context=tool_context)["deleted"]
    manager.close()


def test_failed_worktree_adoption_rolls_back_unregistered_team_worktree(tmp_path):
    repo = _repository(tmp_path)
    execution = ExecutionWorkspaceRuntime(
        runtime_parent=tmp_path / "execution",
        worktree_parent=tmp_path / "owned-worktrees",
    )
    manager = CollaborationManager(
        teams_root=tmp_path / "teams",
        worktree_parent=tmp_path / "collaboration-worktrees",
        execution_workspaces=execution,
        user_agents_root=tmp_path / "agents",
    )
    registry = ToolRegistry(PermissionResolver())
    register_collaboration_tools(registry, manager)
    pool_builder = ToolPoolBuilder(registry)
    runtime = CogentRuntime(
        tool_registry=registry,
        run_store=InMemoryAgentRunStore(),
        llm_client=_Client(),
        tool_pool_builder=pool_builder,
        execution_workspace_runtime=execution,
        collaboration_manager=manager,
    )
    workspaces = WorkspaceService(
        store=InMemoryWorkspaceRepository(),
        allowed_roots=(str(repo),),
    )
    workspaces.register(workspace_id="workspace", root_path=str(repo))
    snapshot = ExecutionContextFactory(
        session_service=_SessionService(),
        workspace_service=workspaces,
        auth_mode="disabled",
        tool_registry=registry,
        tool_pool_builder=pool_builder,
        execution_workspace_runtime=execution,
    ).create(
        conversation_id="conversation-parent",
        user_message="lead request",
        workspace_id="workspace",
        model_selection=ModelSelection(),
        actor_user_id="owner",
        run_id="run-parent",
    )
    parent = runtime.create_queued_run(
        conversation_id="conversation-parent",
        workspace_id="workspace",
        workspace_root=str(repo),
        run_id="run-parent",
        context_snapshot=snapshot,
    )
    tool_context = ToolUseContext(
        conversation_id=parent.conversation_id,
        workspace_id=parent.workspace_id,
        workspace_root=parent.workspace_root,
        run_id=parent.run_id,
        actor_user_id="owner",
        workspace_role="admin",
        authorized_workspace_root=parent.workspace_root,
        execution_root=snapshot.execution_workspace.execution_root,
        execution_workspace_mode=snapshot.execution_workspace.mode,
    )
    team = manager.team_create(team_name="rollback-team", context=tool_context)

    try:
        with pytest.raises(
            ExecutionWorkspaceError,
            match="collaboration worktree is not server-owned",
        ):
            manager.spawn(
                prompt="Inspect the API surface.",
                description="API review",
                subagent_type="general-purpose",
                team_name=team["name"],
                name="reviewer",
                context=tool_context,
            )

        assert manager.worktrees.list_worktrees(repo_root=repo) == []
        assert manager.teams.get(team["name"]).members == []
        assert not list((tmp_path / "collaboration-worktrees").glob("agent-worktree-*"))
    finally:
        manager.close()


def test_subagent_usage_is_charged_to_existing_parent_session(tmp_path):
    repo = _repository(tmp_path)
    sessions = InMemorySessionRepository()
    session = sessions.create_session("owner")
    execution = ExecutionWorkspaceRuntime(runtime_parent=tmp_path / "execution")
    manager = CollaborationManager(
        teams_root=tmp_path / "teams",
        worktree_parent=tmp_path / "execution",
        execution_workspaces=execution,
        user_agents_root=tmp_path / "agents",
    )
    registry = ToolRegistry(PermissionResolver())
    register_collaboration_tools(registry, manager)
    pool_builder = ToolPoolBuilder(registry)
    runtime = CogentRuntime(
        tool_registry=registry,
        run_store=InMemoryAgentRunStore(),
        llm_client=_UsageAwareClient(sessions),
        tool_pool_builder=pool_builder,
        execution_workspace_runtime=execution,
        collaboration_manager=manager,
    )
    workspaces = WorkspaceService(
        store=InMemoryWorkspaceRepository(),
        allowed_roots=(str(repo),),
    )
    workspaces.register(workspace_id="workspace", root_path=str(repo))
    snapshot = ExecutionContextFactory(
        session_service=_SessionService(session.id),
        workspace_service=workspaces,
        auth_mode="disabled",
        tool_registry=registry,
        tool_pool_builder=pool_builder,
        execution_workspace_runtime=execution,
    ).create(
        conversation_id=session.id,
        user_message="lead request",
        workspace_id="workspace",
        model_selection=ModelSelection(),
        actor_user_id="owner",
        run_id="run-parent",
    )
    parent = runtime.create_queued_run(
        conversation_id=session.id,
        workspace_id="workspace",
        workspace_root=str(repo),
        run_id="run-parent",
        context_snapshot=snapshot,
    )
    result = manager.spawn(
        prompt="Inspect the repository and report.",
        description="repository inspection",
        subagent_type="general-purpose",
        context=ToolUseContext(
            conversation_id=parent.conversation_id,
            workspace_id=parent.workspace_id,
            workspace_root=parent.workspace_root,
            run_id=parent.run_id,
            actor_user_id="owner",
            workspace_role="admin",
            authorized_workspace_root=parent.workspace_root,
            execution_root=snapshot.execution_workspace.execution_root,
            execution_workspace_mode=snapshot.execution_workspace.mode,
        ),
    )

    assert result["status"] == "completed"
    usage = sessions.list_token_usage(session.id)
    assert len(usage) == 1
    assert usage[0].operation == "subagent"
    assert usage[0].resource_id == result["child_run_id"]
    assert runtime.get_run(result["child_run_id"]).runtime_state["collaboration"][
        "finalized"
    ] is True
    manager.close()


def test_agent_tool_runs_child_and_returns_result_to_lead(tmp_path):
    repo = _repository(tmp_path)
    execution = ExecutionWorkspaceRuntime(runtime_parent=tmp_path / "execution")
    manager = CollaborationManager(
        teams_root=tmp_path / "teams",
        worktree_parent=tmp_path / "execution",
        execution_workspaces=execution,
        user_agents_root=tmp_path / "agents",
    )
    registry = ToolRegistry(PermissionResolver())
    register_collaboration_tools(registry, manager)
    pool_builder = ToolPoolBuilder(registry)
    runtime = CogentRuntime(
        tool_registry=registry,
        run_store=InMemoryAgentRunStore(),
        llm_client=_SequenceClient(),
        tool_pool_builder=pool_builder,
        execution_workspace_runtime=execution,
        collaboration_manager=manager,
    )
    workspaces = WorkspaceService(
        store=InMemoryWorkspaceRepository(),
        allowed_roots=(str(repo),),
    )
    workspaces.register(workspace_id="workspace", root_path=str(repo))
    snapshot = ExecutionContextFactory(
        session_service=_SessionService(),
        workspace_service=workspaces,
        auth_mode="disabled",
        tool_registry=registry,
        tool_pool_builder=pool_builder,
        execution_workspace_runtime=execution,
    ).create(
        conversation_id="conversation-parent",
        user_message="lead request",
        workspace_id="workspace",
        model_selection=ModelSelection(),
        actor_user_id="owner",
        run_id="run-parent",
    )
    parent = runtime.create_queued_run(
        conversation_id="conversation-parent",
        workspace_id="workspace",
        workspace_root=str(repo),
        run_id="run-parent",
        context_snapshot=snapshot,
    )
    parent_state = CogentState.from_mapping(parent.runtime_state)
    parent_state.permission_mode = "bypassPermissions"
    runtime.restore_record(replace(parent, runtime_state=parent_state.to_dict()))

    result = runtime.run(
        run_id=parent.run_id,
        conversation_id=parent.conversation_id,
        user_input="lead request",
        history=[],
        workspace_id=parent.workspace_id,
        workspace_root=parent.workspace_root,
        focus_files=[],
        actor_user_id="owner",
        run_context=snapshot,
    )

    assert result.status == "completed"
    assert result.answer == "lead complete"
    agent_result = next(item for item in result.tool_results if item["name"] == "Agent")
    assert agent_result["ok"] is True
    assert agent_result["result"]["result"] == "child complete"
    manager.close()


def test_agent_tool_surfaces_foreground_child_failure(tmp_path):
    repo = _repository(tmp_path)
    execution = ExecutionWorkspaceRuntime(runtime_parent=tmp_path / "execution")
    manager = CollaborationManager(
        teams_root=tmp_path / "teams",
        worktree_parent=tmp_path / "execution",
        execution_workspaces=execution,
        user_agents_root=tmp_path / "agents",
    )
    registry = ToolRegistry(PermissionResolver())
    register_collaboration_tools(registry, manager)
    pool_builder = ToolPoolBuilder(registry)
    runtime = CogentRuntime(
        tool_registry=registry,
        run_store=InMemoryAgentRunStore(),
        llm_client=_ChildFailureClient(),
        tool_pool_builder=pool_builder,
        execution_workspace_runtime=execution,
        collaboration_manager=manager,
    )
    workspaces = WorkspaceService(
        store=InMemoryWorkspaceRepository(),
        allowed_roots=(str(repo),),
    )
    workspaces.register(workspace_id="workspace", root_path=str(repo))
    snapshot = ExecutionContextFactory(
        session_service=_SessionService(),
        workspace_service=workspaces,
        auth_mode="disabled",
        tool_registry=registry,
        tool_pool_builder=pool_builder,
        execution_workspace_runtime=execution,
    ).create(
        conversation_id="conversation-parent",
        user_message="lead request",
        workspace_id="workspace",
        model_selection=ModelSelection(),
        actor_user_id="owner",
        run_id="run-parent",
    )
    parent = runtime.create_queued_run(
        conversation_id="conversation-parent",
        workspace_id="workspace",
        workspace_root=str(repo),
        run_id="run-parent",
        context_snapshot=snapshot,
    )
    parent_state = CogentState.from_mapping(parent.runtime_state)
    parent_state.permission_mode = "bypassPermissions"
    runtime.restore_record(replace(parent, runtime_state=parent_state.to_dict()))

    result = runtime.run(
        run_id=parent.run_id,
        conversation_id=parent.conversation_id,
        user_input="lead request",
        history=[],
        workspace_id=parent.workspace_id,
        workspace_root=parent.workspace_root,
        focus_files=[],
        actor_user_id="owner",
        run_context=snapshot,
    )

    assert result.status == "completed"
    agent_result = next(item for item in result.tool_results if item["name"] == "Agent")
    assert agent_result["ok"] is False
    assert agent_result["error_code"] == "subagent_failed"
    assert "child provider failed" in agent_result["error"]
    child = next(
        record
        for record in runtime.list_recoverable_runs()
        if record.run_id != parent.run_id
    )
    assert child.status == "failed"
    assert child.runtime_state["collaboration"]["finalized"] is True
    manager.close()


def test_team_plan_approval_resumes_waiting_child(tmp_path):
    repo = _repository(tmp_path)
    execution = ExecutionWorkspaceRuntime(runtime_parent=tmp_path / "execution")
    manager = CollaborationManager(
        teams_root=tmp_path / "teams",
        worktree_parent=tmp_path / "execution",
        execution_workspaces=execution,
        user_agents_root=tmp_path / "agents",
    )
    registry = ToolRegistry(PermissionResolver())
    register_collaboration_tools(registry, manager)
    pool_builder = ToolPoolBuilder(registry)
    runtime = CogentRuntime(
        tool_registry=registry,
        run_store=InMemoryAgentRunStore(),
        llm_client=_PlanClient(),
        tool_pool_builder=pool_builder,
        execution_workspace_runtime=execution,
        collaboration_manager=manager,
    )
    workspaces = WorkspaceService(
        store=InMemoryWorkspaceRepository(),
        allowed_roots=(str(repo),),
    )
    workspaces.register(workspace_id="workspace", root_path=str(repo))
    snapshot = ExecutionContextFactory(
        session_service=_SessionService(),
        workspace_service=workspaces,
        auth_mode="disabled",
        tool_registry=registry,
        tool_pool_builder=pool_builder,
        execution_workspace_runtime=execution,
    ).create(
        conversation_id="conversation-parent",
        user_message="lead request",
        workspace_id="workspace",
        model_selection=ModelSelection(),
        actor_user_id="owner",
        run_id="run-parent",
    )
    parent = runtime.create_queued_run(
        conversation_id="conversation-parent",
        workspace_id="workspace",
        workspace_root=str(repo),
        run_id="run-parent",
        context_snapshot=snapshot,
    )
    tool_context = ToolUseContext(
        conversation_id=parent.conversation_id,
        workspace_id=parent.workspace_id,
        workspace_root=parent.workspace_root,
        run_id=parent.run_id,
        actor_user_id="owner",
        workspace_role="admin",
        authorized_workspace_root=parent.workspace_root,
        execution_root=snapshot.execution_workspace.execution_root,
        execution_workspace_mode=snapshot.execution_workspace.mode,
    )
    team = manager.team_create(team_name="planned-team", context=tool_context)
    spawned = manager.spawn(
        prompt="Plan before changing anything.",
        description="planned change",
        subagent_type="general-purpose",
        team_name=team["name"],
        name="planner",
        plan_mode_required=True,
        context=tool_context,
    )
    task = manager._tasks[spawned["task_id"]]
    task.future.result(timeout=5)

    assert task.status == "waiting_approval"
    messages = manager.teams.mailbox(team["name"]).consume(parent.run_id)
    request = next(
        message
        for message in messages
        if message.type == "plan_approval_request"
    )
    manager.send_message(
        team_name=team["name"],
        to="planner",
        content="Approved; follow the submitted plan.",
        type="plan_approval_response",
        request_id=request.request_id,
        approve=True,
        context=tool_context,
    )
    task.future.result(timeout=5)

    assert task.status == "completed"
    assert task.result == "approved work complete"
    child = runtime.get_run(task.child_run_id)
    assert child.status == "completed"
    assert child.runtime_state["permission_mode"] == "default"
    assert manager.teams.get(team["name"]).members[0].status == "idle"
    manager.team_delete(team_name=team["name"], context=tool_context)
    manager.close()
