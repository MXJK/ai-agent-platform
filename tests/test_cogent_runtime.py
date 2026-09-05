from dataclasses import replace
from pathlib import Path

import pytest

from ai_agent_platform.agents.coding.store import InMemoryAgentRunStore
from ai_agent_platform.cogent.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from ai_agent_platform.cogent.permissions.dangerous import is_safe_command
from ai_agent_platform.cogent.permissions.rules import Rule
from ai_agent_platform.cogent.runtime import CogentRuntime
from ai_agent_platform.cogent.state import CogentState
from ai_agent_platform.cogent.tools import CogentToolAdapter
from ai_agent_platform.cogent.tools.base import Tool
from ai_agent_platform.integrations.llm import LLMToolDecision, LLMUsage
from ai_agent_platform.integrations.permissions import PermissionResolver
from ai_agent_platform.integrations.tool_pool import ToolPoolBuilder
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry


class SimulatedCrash(BaseException):
    pass


class ScriptedClient:
    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests = []

    def decide_tools(self, messages, tools, **kwargs):
        self.requests.append(messages)
        step = self.steps.pop(0)
        if callable(step):
            return step(messages, tools, kwargs)
        if step.text:
            kwargs["on_delta"](step.text)
        return step


def response(text="", *calls, **kwargs):
    return LLMToolDecision(
        text=text, tool_calls=list(calls), provider="test", model="test-model",
        stop_reason=kwargs.pop("stop_reason", "tool_use" if calls else "end_turn"),
        **kwargs,
    )


def runtime_for(root, client, *, store=None, registry=None, approval_policy="on_request"):
    registry = registry or ToolRegistry(PermissionResolver())
    runtime = CogentRuntime(
        llm_client=client, run_store=store or InMemoryAgentRunStore(),
        tool_registry=registry, tool_pool_builder=ToolPoolBuilder(registry),
        approval_policy=approval_policy,
    )
    return runtime


def start(runtime, root):
    record = runtime.create_queued_run(
        conversation_id="conversation-test", workspace_id="workspace-test", workspace_root=str(root)
    )
    return record


def execute(runtime, root, record):
    return runtime.run(
        run_id=record.run_id, conversation_id=record.conversation_id,
        workspace_id=record.workspace_id, workspace_root=str(root),
        user_input="Update the selected files and verify the result.", history=[],
        focus_files=[], actor_user_id="owner",
    )


def register_read(registry, calls):
    registry.register("repo.read_file", lambda **args: calls.append("read") or {"text": "source"})


def register_write(registry, handler):
    registry.register("sandbox.write_file", handler, permission_level="write_safe", requires_approval=True)


def test_multi_round_pairing_raw_usage_and_immutable_snapshots(tmp_path):
    calls = []
    registry = ToolRegistry(PermissionResolver())
    register_read(registry, calls)
    usage = LLMUsage(input_tokens=100, output_tokens=40, thoughts_tokens=12,
                     cached_input_tokens=60, reported_total_tokens=140)
    client = ScriptedClient(
        response("Reading.", ToolCall("ReadFile", {"file_path": "a.py"}, "read-1"), usage=usage),
        response("Done.", usage=usage),
    )
    runtime = runtime_for(tmp_path, client, registry=registry)
    record = start(runtime, tmp_path)
    result = execute(runtime, tmp_path, record)
    assert result.status == "completed"
    assert result.answer == "Done."
    assert calls == ["read"]
    assert result.metrics.input_tokens == 200
    assert result.metrics.output_tokens == 80
    assert result.metrics.thoughts_tokens == 24
    assert result.metrics.total_tokens == 280
    assert result.metrics.cached_input_tokens == 120
    transcript = client.requests[-1]
    assert transcript[-2]["tool_calls"][0]["call_id"] == "read-1"
    assert transcript[-1]["role"] == "tool"
    assert transcript[-1]["call_id"] == "read-1"
    snapshots = runtime._run_store.list_runtime_snapshots(record.run_id)
    assert len(snapshots[0].state["messages"]) == 2
    assert len(snapshots[-1].state["messages"]) == 5


def test_new_run_in_same_conversation_keeps_canonical_tool_pairs(tmp_path):
    effects = []
    registry = ToolRegistry(PermissionResolver())
    register_read(registry, effects)
    client = ScriptedClient(response('', ToolCall('ReadFile', {'file_path': 'a.py'}, 'read-1')),
                            response('First answer.'), response('Second answer.'))
    runtime = runtime_for(tmp_path, client, registry=registry)
    first = start(runtime, tmp_path)
    execute(runtime, tmp_path, first)
    second = start(runtime, tmp_path)
    execute(runtime, tmp_path, second)
    messages = client.requests[-1]
    assert any(item.get('tool_calls') for item in messages)
    assert any(item.get('role') == 'tool' and item.get('call_id') == 'read-1' for item in messages)
    assert effects == ['read']


def test_cancel_waiting_approval_is_immediately_terminal_without_side_effects(tmp_path):
    effects = []
    registry = ToolRegistry(PermissionResolver())
    register_write(registry, lambda **args: effects.append('write') or {})
    runtime = runtime_for(tmp_path, ScriptedClient(response('', ToolCall('WriteFile', {'file_path': 'a.py', 'content': 'x'}, 'write-1'))), registry=registry)
    record = start(runtime, tmp_path)
    assert execute(runtime, tmp_path, record).status == 'waiting_approval'
    assert runtime.request_control(run_id=record.run_id, action='cancel').status == 'cancelled'
    assert effects == []


def test_entire_batch_waits_before_any_read_or_write(tmp_path):
    effects = []
    registry = ToolRegistry(PermissionResolver())
    register_read(registry, effects)
    register_write(registry, lambda **args: effects.append("write") or {})
    client = ScriptedClient(
        response("", ToolCall("ReadFile", {"file_path": "a.py"}, "read-1"),
                 ToolCall("WriteFile", {"file_path": "b.py", "content": "new"}, "write-1")),
        response("Done."),
    )
    runtime = runtime_for(tmp_path, client, registry=registry)
    record = start(runtime, tmp_path)
    assert execute(runtime, tmp_path, record).status == "waiting_approval"
    assert effects == []
    assert not any(event.type == "tool_started" for event in runtime.list_events(record.run_id))
    result = runtime.resume(run_id=record.run_id, approved=True, approved_by="owner")
    assert result.status == "completed"
    assert effects == ["read", "write"]
    state = runtime.get_run(record.run_id).runtime_state
    assert state["approvals"] == []
    assert len(state["consumed_approvals"]) == 1


def test_rejected_batch_has_no_side_effect(tmp_path):
    effects = []
    registry = ToolRegistry(PermissionResolver())
    register_write(registry, lambda **args: effects.append("write") or {})
    runtime = runtime_for(tmp_path, ScriptedClient(response("", ToolCall(
        "WriteFile", {"file_path": "a.py", "content": "new"}, "write-1"
    ))), registry=registry)
    record = start(runtime, tmp_path)
    execute(runtime, tmp_path, record)
    assert runtime.resume(run_id=record.run_id, approved=False, approved_by="owner").status == "blocked"
    assert effects == []


def test_writes_persist_before_the_next_write_starts(tmp_path):
    store = InMemoryAgentRunStore()
    registry = ToolRegistry(PermissionResolver())
    effects = []
    record = None

    def write(**arguments):
        if effects:
            assert store.get_tool_execution(record.run_id, "write-1").status == "completed"
            assert "write-1" in store.get(record.run_id).runtime_state["completed_call_ids"]
        effects.append(arguments["path"])
        return {}

    register_write(registry, write)
    runtime = runtime_for(tmp_path, ScriptedClient(response("", *[
        ToolCall("WriteFile", {"file_path": f"{n}.py", "content": "new"}, f"write-{n}")
        for n in (1, 2)
    ]), response("Done.")), registry=registry, store=store)
    record = start(runtime, tmp_path)
    execute(runtime, tmp_path, record)
    assert runtime.resume(run_id=record.run_id, approved=True, approved_by="owner").status == "completed"
    assert effects == ["1.py", "2.py"]


def test_interrupted_stream_restarts_from_complete_messages_only(tmp_path):
    def crash(messages, tools, kwargs):
        kwargs["on_delta"]("unfinished fragment")
        raise SimulatedCrash()

    client = ScriptedClient(crash, response("Complete answer."))
    runtime = runtime_for(tmp_path, client)
    record = start(runtime, tmp_path)
    with pytest.raises(SimulatedCrash):
        execute(runtime, tmp_path, record)
    assert runtime.get_run(record.run_id).runtime_state["retry_on_resume"]
    result = runtime.recover(record.run_id)
    assert result.answer == "Complete answer."
    assert client.requests[0] == client.requests[1]
    assert "unfinished fragment" not in str(runtime.get_run(record.run_id).runtime_state)
    assert any(event.type == "retry" and event.output["discard_partial_answer"] for event in runtime.list_events(record.run_id))


def test_completed_model_response_survives_crash_without_new_request(tmp_path):
    class CrashStore(InMemoryAgentRunStore):
        armed = True

        def save_runtime_snapshot(self, snapshot):
            super().save_runtime_snapshot(snapshot)
            if self.armed and snapshot.boundary == "model_response":
                self.armed = False
                raise SimulatedCrash()

    client = ScriptedClient(response("Durable answer."))
    runtime = runtime_for(tmp_path, client, store=CrashStore())
    record = start(runtime, tmp_path)
    with pytest.raises(SimulatedCrash):
        execute(runtime, tmp_path, record)
    assert runtime.recover(record.run_id).answer == "Durable answer."
    assert len(client.requests) == 1


def test_uncertain_write_is_not_replayed_after_restart(tmp_path, monkeypatch):
    effects = []
    registry = ToolRegistry(PermissionResolver())
    register_write(registry, lambda **args: effects.append("write") or {})
    client = ScriptedClient(response("", ToolCall("WriteFile", {"file_path": "a.py", "content": "new"}, "write-1")))
    runtime = runtime_for(tmp_path, client, registry=registry)
    record = start(runtime, tmp_path)
    execute(runtime, tmp_path, record)
    original = CogentToolAdapter.execute

    def crash(self, item, context):
        original(self, item, context)
        raise SimulatedCrash()

    monkeypatch.setattr(CogentToolAdapter, "execute", crash)
    with pytest.raises(SimulatedCrash):
        runtime.resume(run_id=record.run_id, approved=True, approved_by="owner")
    monkeypatch.setattr(CogentToolAdapter, "execute", original)
    restarted = runtime_for(tmp_path, client, store=runtime._run_store, registry=registry)
    result = restarted.recover(record.run_id)
    assert result.status == "blocked"
    assert result.terminal_reason == "tool_execution_uncertain"
    assert effects == ["write"]


def test_output_recovery_preserves_answer_fragments(tmp_path):
    client = ScriptedClient(response("Part one.", stop_reason="max_tokens"), response("Part two."))
    runtime = runtime_for(tmp_path, client)
    result = execute(runtime, tmp_path, start(runtime, tmp_path))
    assert result.answer == "Part one.\nPart two."


def test_private_reasoning_is_never_emitted(tmp_path):
    runtime = runtime_for(tmp_path, ScriptedClient(replace(response("Done.", provider_items=[
        {"type": "thinking", "thinking": "private-analysis", "signature": "private-signature"},
        {"type": "reasoning", "encrypted_content": "private-cipher", "summary": [{"type": "summary_text", "text": "Visible summary."}]},
    ]), provider="openai")))
    record = start(runtime, tmp_path)
    execute(runtime, tmp_path, record)
    emitted = str(runtime.list_events(record.run_id))
    assert "Visible summary." in emitted
    assert "private-analysis" not in emitted
    assert "private-signature" not in emitted
    assert "private-cipher" not in emitted


def checker(root, mode=PermissionMode.DEFAULT, rules=()):
    engine = RuleEngine()
    engine.snapshot = lambda: list(rules)
    return PermissionChecker(DangerousCommandDetector(), PathSandbox(str(root)), engine, mode=mode)


@pytest.mark.parametrize("command", ["sed -i x a.py", "find . -exec touch x ;", "npx evil", "git diff --output=x", "pwd\ntouch x", "xargs sh", "tee x"])
def test_mutating_commands_are_not_in_read_only_allowlist(command):
    assert not is_safe_command(command)


def test_deny_rule_precedes_safe_command(tmp_path):
    permission = checker(tmp_path, rules=[Rule("Bash", "pwd", "deny")])
    assert permission.check(Tool("Bash", "command"), {"command": "pwd"}).effect == "deny"


def test_plan_requires_exact_plan_path_and_cannot_be_overridden(tmp_path):
    permission = checker(tmp_path, PermissionMode.PLAN, [Rule("WriteFile", "*", "allow")])
    permission.plan_file_path = str(tmp_path / ".cogent/plans/current.md")
    assert permission.check(Tool("WriteFile", "write"), {"file_path": ".cogent/plans/current.md"}).effect == "allow"
    for path in ("current.md", ".cogent/plans/other.md", "nested/.cogent/plans/current.md", "../current.md"):
        assert permission.check(Tool("WriteFile", "write"), {"file_path": path}).effect == "deny"
    assert permission.check(Tool("Bash", "command"), {"command": "pwd"}).effect == "deny"


def test_bypass_does_not_override_protected_paths_or_missing_os_sandbox(tmp_path):
    permission = checker(tmp_path, PermissionMode.BYPASS)
    assert permission.check(Tool("WriteFile", "write"), {"file_path": ".cogent/permissions.yaml"}).effect == "deny"
    assert permission.check(Tool("WriteFile", "write"), {"file_path": "../outside"}).effect == "deny"
    assert permission.check(Tool("Bash", "command"), {"command": "touch a.py"}).effect == "ask"


def test_mcp_permissions_match_identity_not_filesystem_path(tmp_path):
    permission = checker(tmp_path, rules=[Rule("mcp_call", "notes__write", "deny")])
    assert permission.check(Tool("mcp_call", "write"), {"server": "notes", "tool": "write", "arguments": {}}).effect == "deny"
    assert permission.check(Tool("mcp_call", "read"), {"server": "notes", "tool": "read", "arguments": {}}).effect == "allow"
