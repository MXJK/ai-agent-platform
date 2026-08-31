from __future__ import annotations

from dataclasses import replace
import json

import pytest
from fastapi.testclient import TestClient

from ai_agent_platform.agents.coding.context_compaction import (
    SUMMARY_KEYS,
    apply_snip,
    auto_compact_threshold,
    context_blocks,
    full_compact,
    micro_compact,
)
from ai_agent_platform.agents.coding.models import (
    AgentRunInvalidStateError,
    AgentRunRecord,
    ContextSource,
)
from ai_agent_platform.agents.coding.run_artifacts import read_run_artifact
from ai_agent_platform.agents.coding.store import InMemoryAgentRunStore, events_for_record
from ai_agent_platform.agents.coding.tool_loop_nodes import (
    _compaction_seed_messages,
    _native_messages_tokens,
)
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.llm import LLMToolDecision
from ai_agent_platform.integrations.tools import ToolSpec
from ai_agent_platform.local_state.database import LocalStateDatabase, SCHEMA_VERSION
from ai_agent_platform.main import create_app
from ai_agent_platform.repositories.postgres import _agent_run_from_row
from ai_agent_platform.repositories.sqlite import SQLiteAgentRunRepository


def _spec(name: str, *, read_only: bool = True, idempotent: bool = True) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        provider="test",
        permission_level="read_only" if read_only else "write",
        requires_approval=not read_only,
        accepts_context=False,
        idempotent=idempotent,
    )


def _group(index: int, name: str = "repo.read_file", *, ok: bool = True):
    call_id = f"call-{index}"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"call_id": call_id, "name": name, "arguments": {}}],
        },
        {
            "role": "tool",
            "call_id": call_id,
            "name": name,
            "content": {
                "call_id": call_id,
                "name": name,
                "ok": ok,
                "result": {"text": f"result-{index}-" + "x" * 500},
            },
        },
    ]


def _messages(group_count: int = 8):
    messages = [
        {"role": "system", "content": "frozen system"},
        {"role": "user", "content": "initial task"},
    ]
    for index in range(group_count):
        messages.extend(_group(index))
    return messages


class _CompletingPlanner:
    uses_native_tool_calling = True
    single_tool_per_turn = False

    def __init__(self) -> None:
        self.messages = []
        self.tool_names = []

    def decide_tool_calls(self, messages, tool_specs, **kwargs):
        del kwargs
        self.messages = list(messages)
        self.tool_names = [item.name for item in tool_specs]
        return LLMToolDecision(
            text="done",
            tool_calls=[],
            model="test",
            provider="test",
            stop_reason="end_turn",
        )


def test_auto_compact_threshold_uses_quarter_or_reserved_buffer() -> None:
    assert auto_compact_threshold(100_000) == 93_856
    assert auto_compact_threshold(16_000) == 12_000


def test_snip_candidates_are_stable_and_keep_recent_groups() -> None:
    messages = _messages()
    specs = {"repo.read_file": _spec("repo.read_file")}
    first = context_blocks(messages, tool_specs=specs, keep_recent_groups=4)
    second = context_blocks(messages, tool_specs=specs, keep_recent_groups=4)
    assert [item.block_id for item in first] == [item.block_id for item in second]
    assert len(first) == 4
    assert all(item.block_id.startswith("ctx_") for item in first)


def test_snip_fails_closed_then_writes_a_readable_transcript_artifact() -> None:
    messages = _messages()
    specs = {"repo.read_file": _spec("repo.read_file")}
    blocks = context_blocks(messages, tool_specs=specs, keep_recent_groups=4)
    rejected = apply_snip(
        messages,
        selected_ids=["ctx_00000000000000000000"],
        candidate_ids=[item.block_id for item in blocks],
        reason="stale",
        artifacts=[],
    )
    assert not rejected.changed
    assert rejected.error == "stale_or_protected_block"

    result = apply_snip(
        messages,
        selected_ids=[blocks[0].block_id, blocks[1].block_id],
        candidate_ids=[item.block_id for item in blocks],
        reason="superseded",
        artifacts=[],
    )
    assert result.changed
    assert result.stage["stage"] == "snip"
    assert result.stage["block_count"] == 2
    artifact_id = result.artifacts[0]["id"]
    assert artifact_id.startswith("context_transcript_")
    page = read_run_artifact(result.artifacts, {"artifact_id": artifact_id})
    assert page["sha256"]
    assert "call-0" in "".join(item["content"] for item in page["ranges"])


def test_micro_compact_only_decays_old_idempotent_read_results() -> None:
    messages = _messages(7)
    messages.extend(_group(8, "sandbox.run_command"))
    specs = {
        "repo.read_file": _spec("repo.read_file"),
        "sandbox.run_command": _spec(
            "sandbox.run_command", read_only=False, idempotent=False
        ),
    }
    result = micro_compact(
        messages,
        tool_specs=specs,
        artifacts=[],
        keep_recent_results=5,
    )
    assert result.changed
    assert result.stage["block_count"] == 2
    compacted = [
        item
        for item in result.messages
        if isinstance(item.get("content"), dict)
        and item["content"].get("micro_compacted")
    ]
    assert [item["call_id"] for item in compacted] == ["call-0", "call-1"]
    command = next(item for item in result.messages if item.get("call_id") == "call-8")
    assert not command["content"].get("micro_compacted")


def test_micro_compact_zero_recent_results_decays_every_safe_result() -> None:
    messages = _messages(2)
    result = micro_compact(
        messages,
        tool_specs={"repo.read_file": _spec("repo.read_file")},
        artifacts=[],
        keep_recent_results=0,
    )
    assert result.changed
    assert result.stage["block_count"] == 2


def test_idle_boundary_runs_micro_compact_before_the_next_model_request(
    monkeypatch,
) -> None:
    planner = _CompletingPlanner()
    store = InMemoryAgentRunStore()
    store.save(_record())
    runtime = CodingAgentRuntime(
        run_store=store,
        planner=planner,
        micro_compact_idle_seconds=3600,
        micro_compact_keep_recent_results=5,
    )
    monkeypatch.setattr(
        "ai_agent_platform.agents.coding.tool_loop_nodes.time.time",
        lambda: 4600.0,
    )
    update = runtime._tool_loop_nodes._plan_tools(
        {
            "run_id": "run-1",
            "conversation_id": "session-1",
            "workspace_id": "workspace-1",
            "workspace_root": "/tmp/workspace",
            "authorized_workspace_root": "/tmp/workspace",
            "workspace_role": "admin",
            "actor_user_id": "demo_user",
            "approval_policy": "never",
            "enabled_tools": ["repo.read_file"],
            "task_tool_profile": ["repo.read_file"],
            "native_tool_messages": _messages(7),
            "native_last_model_request_at": 1000.0,
            "native_context_compactions": 0,
            "native_auto_compactions": 0,
            "native_compaction_failures": 0,
            "native_model_compaction_disabled": False,
            "native_tool_round": 0,
            "native_tool_call_count": 0,
            "task_model_request_count": 0,
            "native_tool_signatures": [],
            "native_soft_limit_warned": False,
            "native_no_progress_rounds": 0,
            "native_unfulfilled_change_rounds": 0,
            "native_consecutive_failures": 0,
            "context_warnings": [],
            "context_shares": {
                "message_tokens": 100_000,
                "transcript_tokens": 90_000,
                "total_tokens": 100_000,
            },
            "intent": "code_explanation",
            "tool_calls": [],
            "tool_results": [],
            "trace": [],
            "artifacts": [],
        }
    )
    stage = next(
        item
        for item in update["native_context_reduction_stages"]
        if item["stage"] == "micro_compact"
    )
    assert stage["reason"] == "idle_timeout"
    assert stage["block_count"] == 2
    compacted = [
        item
        for item in planner.messages
        if isinstance(item.get("content"), dict)
        and item["content"].get("micro_compacted")
    ]
    assert len(compacted) == 2
    assert update["native_last_model_request_at"] == 4600.0


def test_snip_tool_is_only_advertised_as_a_dynamic_pressure_suffix() -> None:
    planner = _CompletingPlanner()
    store = InMemoryAgentRunStore()
    store.save(_record())
    runtime = CodingAgentRuntime(run_store=store, planner=planner)
    messages = _messages(8)
    estimated = _native_messages_tokens(messages)
    message_budget = max(1, int(estimated / 0.70))
    update = runtime._tool_loop_nodes._plan_tools(
        {
            "run_id": "run-1",
            "conversation_id": "session-1",
            "workspace_id": "workspace-1",
            "workspace_root": "/tmp/workspace",
            "authorized_workspace_root": "/tmp/workspace",
            "workspace_role": "admin",
            "actor_user_id": "demo_user",
            "approval_policy": "never",
            "enabled_tools": ["repo.read_file"],
            "task_tool_profile": ["repo.read_file"],
            "native_tool_messages": messages,
            "native_context_compactions": 0,
            "native_auto_compactions": 0,
            "native_compaction_failures": 0,
            "native_model_compaction_disabled": False,
            "native_tool_round": 0,
            "native_tool_call_count": 0,
            "task_model_request_count": 0,
            "native_tool_signatures": [],
            "native_soft_limit_warned": False,
            "native_no_progress_rounds": 0,
            "native_unfulfilled_change_rounds": 0,
            "native_consecutive_failures": 0,
            "context_warnings": [],
            "context_shares": {
                "message_tokens": message_budget,
                "transcript_tokens": message_budget,
                "total_tokens": message_budget,
            },
            "intent": "code_explanation",
            "tool_calls": [],
            "tool_results": [],
            "trace": [],
            "artifacts": [],
        }
    )
    assert "agent.snip_context" in planner.tool_names
    assert planner.messages[-1]["ephemeral"] is True
    assert "ctx_" in planner.messages[-1]["content"]
    assert all(
        item.get("ephemeral") is not True
        for item in update["native_tool_messages"]
    )
    assert len(update["native_snip_candidates"]) == 4


def test_micro_prepass_skips_auto_compact_after_pressure_is_relieved() -> None:
    planner = _CompletingPlanner()
    compressor = _CapturingCompressor()
    messages = _messages(7)
    micro = micro_compact(
        messages,
        tool_specs={"repo.read_file": _spec("repo.read_file")},
        artifacts=[],
        keep_recent_results=5,
        reason="auto_compact_prepass",
    )
    before = _native_messages_tokens(messages)
    after = _native_messages_tokens(micro.messages)
    message_budget = int(((before / 0.75) + (after / 0.75)) / 2)
    store = InMemoryAgentRunStore()
    store.save(_record())
    runtime = CodingAgentRuntime(
        run_store=store,
        planner=planner,
        context_compressor=compressor,
        micro_compact_keep_recent_results=5,
        compaction_min_reclaimable_tokens=1,
    )

    update = runtime._tool_loop_nodes._plan_tools(
        _native_state(messages, message_budget=message_budget)
    )

    assert compressor.calls == 0
    stages = update["native_context_reduction_stages"]
    assert any(
        item["stage"] == "micro_compact"
        and item["reason"] == "auto_compact_prepass"
        for item in stages
    )
    assert all(item["stage"] != "auto_compact" for item in stages)


def test_auto_compact_runs_after_micro_when_pressure_remains() -> None:
    planner = _CompletingPlanner()
    compressor = _CapturingCompressor()
    messages = _messages(8)
    before = _native_messages_tokens(messages)
    store = InMemoryAgentRunStore()
    store.save(_record())
    runtime = CodingAgentRuntime(
        run_store=store,
        planner=planner,
        context_compressor=compressor,
        micro_compact_keep_recent_results=100,
        compaction_min_reclaimable_tokens=1,
    )

    update = runtime._tool_loop_nodes._plan_tools(
        _native_state(messages, message_budget=int(before / 0.80))
    )

    assert compressor.calls == 1
    auto = next(
        item
        for item in update["native_context_reduction_stages"]
        if item["stage"] == "auto_compact"
    )
    assert auto["before_tokens"] > auto["after_tokens"]
    assert update["native_auto_compactions"] == 1
    assert any(
        item["type"] == "context_transcript"
        for item in update["artifacts"]
    )


def test_three_model_compaction_failures_open_the_deterministic_breaker() -> None:
    planner = _CompletingPlanner()
    compressor = _FailingCompressor()
    messages = _messages(8)
    before = _native_messages_tokens(messages)
    store = InMemoryAgentRunStore()
    store.save(_record())
    runtime = CodingAgentRuntime(
        run_store=store,
        planner=planner,
        context_compressor=compressor,
        micro_compact_keep_recent_results=100,
        compaction_min_reclaimable_tokens=1,
    )
    state = _native_state(messages, message_budget=int(before / 0.80))
    state["native_compaction_failures"] = 2

    third = runtime._tool_loop_nodes._plan_tools(state)

    assert compressor.calls == 1
    assert third["native_compaction_failures"] == 3
    assert third["native_model_compaction_disabled"] is True
    failed = next(
        item
        for item in third["native_context_reduction_stages"]
        if item["stage"] == "compaction_failed"
    )
    assert failed["failure_count"] == 3
    assert failed["artifact_ids"][0].startswith("context_transcript_")

    disabled_state = _native_state(messages, message_budget=int(before / 0.80))
    disabled_state["native_compaction_failures"] = 3
    disabled_state["native_model_compaction_disabled"] = True
    disabled = runtime._tool_loop_nodes._plan_tools(disabled_state)

    assert compressor.calls == 1
    assert all(
        item["stage"] != "auto_compact"
        for item in disabled["native_context_reduction_stages"]
    )


class _Compressor:
    def compress_agent_transcript(self, **kwargs):
        del kwargs
        return {key: [] for key in SUMMARY_KEYS}


class _FailingCompressor:
    def __init__(self) -> None:
        self.calls = 0

    def compress_agent_transcript(self, **kwargs):
        del kwargs
        self.calls += 1
        raise RuntimeError("provider failed")


class _CapturingCompressor:
    def __init__(self) -> None:
        self.artifact_ids = []
        self.calls = 0

    def compress_agent_transcript(self, **kwargs):
        self.calls += 1
        self.artifact_ids = kwargs["artifact_ids"]
        return {key: [] for key in SUMMARY_KEYS}


def test_full_compact_preserves_every_user_message_verbatim() -> None:
    messages = _messages(3)
    messages.insert(4, {"role": "user", "content": "steer exactly"})
    result = full_compact(
        messages,
        artifacts=[],
        compressor=_Compressor(),
        max_output_tokens=4096,
    )
    assert result.changed
    assert [item["content"] for item in result.messages if item["role"] == "user"] == [
        "initial task",
        "steer exactly",
    ]
    summary = next(
        item["content"]
        for item in result.messages
        if item["role"] == "system" and "Compacted agent working state" in item["content"]
    )
    assert all(key in summary for key in SUMMARY_KEYS)


def test_full_compact_failure_still_checkpoints_original_transcript() -> None:
    result = full_compact(
        _messages(3),
        artifacts=[],
        compressor=_FailingCompressor(),
        max_output_tokens=4096,
    )
    assert not result.changed
    assert result.error == "summary_failed"
    assert len(result.artifacts) == 1
    assert result.artifacts[0]["id"].startswith("context_transcript_")


def test_full_compact_exposes_its_transcript_artifact_to_the_summary() -> None:
    compressor = _CapturingCompressor()
    result = full_compact(
        _messages(3),
        artifacts=[],
        compressor=compressor,
        max_output_tokens=4096,
    )
    assert result.changed
    assert compressor.artifact_ids == [result.artifacts[0]["id"]]


def test_compaction_seed_rebuilds_state_with_projected_evidence() -> None:
    source = ContextSource(
        kind="file",
        path="app.py",
        start_line=1,
        end_line=20,
        text="SECRET_BODY " + "x" * 2000,
        reason="focused file",
        content_hash="sha256:original",
    )
    seed = _compaction_seed_messages(
        {
            "user_input": "fix app.py",
            "workspace_id": "workspace",
            "intent": "change_planning",
            "task_shape": {},
            "evidence_contract": {},
            "evidence_coverage": [],
            "unresolved_requirements": [],
            "new_evidence_count": 0,
            "coverage_delta": 0,
            "evidence_extension_rounds": 0,
            "focus_files": ["app.py"],
            "conversation_history": [],
            "project_instructions": [],
            "context_sources": [source],
            "context_warnings": [],
            "workspace_role": "admin",
            "approval_policy": "on_request",
            "task_tool_profile": ["repo.read_file"],
            "change_iteration": 2,
            "validation_status": "pending",
        },
        artifacts=[{"id": "tool_result_0123456789abcdef0123"}],
        max_parallel_read_calls=3,
    )
    payload = json.loads(seed[1]["content"])
    assert payload["task"] == "fix app.py"
    assert payload["evidence"][0]["path"] == "app.py"
    assert "text" not in payload["evidence"][0]
    assert "x" * 500 not in json.dumps(payload)
    assert payload["evidence"][0]["content_sha256"].startswith("sha256:")
    assert len(payload["evidence"][0]["short_summary"]) <= 320
    assert payload["runtime_attachment"]["tool_profile"] == ["repo.read_file"]
    assert payload["runtime_attachment"]["artifact_ids"] == [
        "tool_result_0123456789abcdef0123"
    ]


def _record(status: str = "running") -> AgentRunRecord:
    return AgentRunRecord(
        run_id="run-1",
        thread_id="run-1",
        conversation_id="session-1",
        workspace_id="workspace-1",
        workspace_root="/tmp/workspace",
        status=status,  # type: ignore[arg-type]
        checkpoint_id="checkpoint-1",
        latest_node="plan_tools",
        next_nodes=["plan_tools"],
        trace=[],
    )


def _native_state(
    messages: list[dict],
    *,
    message_budget: int,
) -> dict:
    return {
        "run_id": "run-1",
        "conversation_id": "session-1",
        "workspace_id": "workspace-1",
        "workspace_root": "/tmp/workspace",
        "authorized_workspace_root": "/tmp/workspace",
        "workspace_role": "admin",
        "actor_user_id": "demo_user",
        "approval_policy": "never",
        "enabled_tools": ["repo.read_file"],
        "task_tool_profile": ["repo.read_file"],
        "user_input": "initial task",
        "focus_files": [],
        "conversation_history": [],
        "project_instructions": [],
        "context_sources": [],
        "task_shape": {},
        "evidence_contract": {},
        "evidence_coverage": [],
        "unresolved_requirements": [],
        "new_evidence_count": 0,
        "coverage_delta": 0,
        "evidence_extension_rounds": 0,
        "native_tool_messages": messages,
        "native_context_compactions": 0,
        "native_auto_compactions": 0,
        "native_compaction_failures": 0,
        "native_model_compaction_disabled": False,
        "native_tool_round": 0,
        "native_tool_call_count": 0,
        "task_model_request_count": 0,
        "native_tool_signatures": [],
        "native_soft_limit_warned": False,
        "native_no_progress_rounds": 0,
        "native_unfulfilled_change_rounds": 0,
        "native_consecutive_failures": 0,
        "context_warnings": [],
        "context_shares": {
            "message_tokens": message_budget,
            "transcript_tokens": message_budget,
            "total_tokens": message_budget,
        },
        "intent": "code_explanation",
        "tool_calls": [],
        "tool_results": [],
        "trace": [],
        "artifacts": [],
    }


def test_manual_compaction_is_single_pending_control_request() -> None:
    store = InMemoryAgentRunStore()
    store.save(_record())
    runtime = CodingAgentRuntime(run_store=store)
    updated = runtime.request_compaction(
        run_id="run-1", instruction="preserve migration work"
    )
    assert updated.pending_compaction["instruction"] == "preserve migration work"
    with pytest.raises(AgentRunInvalidStateError):
        runtime.request_compaction(run_id="run-1")


def test_compact_http_control_queues_running_run_and_rejects_duplicate() -> None:
    app = create_app(
        settings=Settings(
            llm_provider="fake",
            embedding_provider="local",
            auth_mode="disabled",
            native_directory_picker_mode="disabled",
        )
    )
    with TestClient(app) as client:
        store = app.state.query_service._runtime._run_store
        store.save(_record())
        first = client.post(
            "/api/v1/agent/runs/run-1/compact",
            json={"instruction": "keep migrations"},
        )
        duplicate = client.post(
            "/api/v1/agent/runs/run-1/compact",
            json={"instruction": "again"},
        )
    assert first.status_code == 202
    assert first.json()["pending_compaction"]["instruction"] == "keep migrations"
    assert duplicate.status_code == 409


def test_sqlite_v3_persists_pending_compaction(tmp_path) -> None:
    database = LocalStateDatabase(str(tmp_path / "state.sqlite3"))
    assert SCHEMA_VERSION == 3
    repository = SQLiteAgentRunRepository(database=database)
    record = replace(
        _record(),
        pending_compaction={"instruction": "keep migrations", "requested_at": 1.0},
    )
    repository.save(record)
    restored = repository.get(record.run_id)
    assert restored.pending_compaction == record.pending_compaction
    with database.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_postgres_row_mapping_restores_pending_compaction() -> None:
    pending = {"instruction": "keep migrations", "requested_at": 1.0}
    record = _agent_run_from_row(
        (
            "run-1",
            "run-1",
            "session-1",
            "workspace-1",
            "/tmp/workspace",
            "running",
            "checkpoint-1",
            "plan_tools",
            ["plan_tools"],
            [],
            None,
            None,
            None,
            [],
            None,
            [],
            pending,
            None,
        )
    )
    assert record.pending_compaction == pending


def test_context_events_do_not_include_removed_body() -> None:
    record = replace(
        _record(),
        trace=[
            {
                "step": 1,
                "node": "plan_tools",
                "summary": "compact",
                "output": {
                    "context_reduction_stages": [
                        {
                            "stage": "micro_compact",
                            "before_tokens": 9000,
                            "after_tokens": 800,
                            "reclaimed_tokens": 8200,
                            "block_count": 7,
                            "artifact_ids": ["tool_result_0123456789abcdef0123"],
                        }
                    ]
                },
            }
        ],
    )
    event = next(event for _key, event in events_for_record(record) if event.type == "context")
    serialized = json.dumps(event.output)
    assert "result-0" not in serialized
    assert event.output["reclaimed_tokens"] == 8200
