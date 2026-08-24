from __future__ import annotations

import unittest
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from ai_agent_platform.agents.coding.run_artifacts import (
    ArtifactReadError,
    RUN_ARTIFACT_READ_TOOL,
    artifact_read_trace,
    build_run_tool_result_artifact,
    canonical_tool_result,
    read_run_artifact,
    run_artifact_tool_spec,
)
from ai_agent_platform.agents.coding.models import (
    AgentRunRecord,
    AgentRunResult,
    CodingAgentState,
)
from ai_agent_platform.agents.coding.store import InMemoryAgentRunStore
from ai_agent_platform.agents.coding.tool_access import ToolAccessCoordinator
from ai_agent_platform.agents.coding.tool_loop_nodes import (
    ToolLoopNodes,
    _native_artifact_read_message,
    _native_messages_chars,
    _native_reduction_artifact_candidates,
    _serialize_tool_result,
)
from ai_agent_platform.agents.coding.tools import create_coding_tool_registry
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.core.metrics import MetricsRegistry
from ai_agent_platform.integrations.llm import LLMToolDecision
from ai_agent_platform.integrations.tools import ToolCall
from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.repositories.postgres import (
    _agent_result_from_json,
    _agent_result_to_json,
)
from ai_agent_platform.repositories.sqlite import SQLiteAgentRunRepository
from ai_agent_platform.token_counting import estimate_text_tokens


class _InputThenCompletePlanner:
    uses_native_tool_calling = True

    def __init__(self) -> None:
        self.decisions = 0

    def classify_intent(self, user_input: str) -> dict[str, object]:
        del user_input
        return {
            "intent": "code_explanation",
            "reason": "artifact checkpoint test",
            "confidence": 1.0,
            "source": "test",
        }

    def plan_tool_calls(self, state, tool_specs):
        del state, tool_specs
        return []

    def decide_tool_calls(self, messages, tool_specs, **kwargs):
        del messages, tool_specs, kwargs
        self.decisions += 1
        if self.decisions == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="checkpoint_input",
                        name="agent.request_user_input",
                        arguments={"question": "continue?"},
                    )
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        return LLMToolDecision(
            text="complete",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )

    def plan_repair_tool_calls(self, state, tool_specs):
        del state, tool_specs
        return []

    def compose_answer(self, state):
        del state
        return "complete"


class _PagedArtifactPlanner(_InputThenCompletePlanner):
    def __init__(self, artifact_id: str) -> None:
        super().__init__()
        self.artifact_id = artifact_id
        self.pages: list[str] = []
        self.processed_reads = 0
        self.model_envelopes: list[dict[str, object]] = []
        self.artifact_tool_visible = False

    def decide_tool_calls(self, messages, tool_specs, **kwargs):
        del kwargs
        self.decisions += 1
        if self.decisions == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="checkpoint_input",
                        name="agent.request_user_input",
                        arguments={"question": "continue?"},
                    )
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        self.artifact_tool_visible = any(
            spec.name == RUN_ARTIFACT_READ_TOOL for spec in tool_specs
        )
        reads = [
            message
            for message in messages
            if message.get("role") == "tool"
            and message.get("name") == RUN_ARTIFACT_READ_TOOL
        ]
        if len(reads) > self.processed_reads:
            envelope = reads[-1]["content"]
            self.model_envelopes.append(envelope)
            page = envelope["result"]
            self.pages.append(str(page["content"]))
            self.processed_reads = len(reads)
            next_offset = page["next_offset_chars"]
            if next_offset is None:
                return LLMToolDecision(
                    text="read complete",
                    tool_calls=[],
                    model="scripted",
                    provider="test",
                    stop_reason="end_turn",
                )
            offset = int(next_offset)
        else:
            offset = 0
        return LLMToolDecision(
            text="",
            tool_calls=[
                ToolCall(
                    call_id=f"read_page_{offset}",
                    name=RUN_ARTIFACT_READ_TOOL,
                    arguments={
                        "artifact_id": self.artifact_id,
                        "offset_chars": offset,
                        "max_tokens": 128,
                    },
                )
            ],
            model="scripted",
            provider="test",
            stop_reason="tool_use",
        )


class RunArtifactPrimitiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = {
            "call_id": "unicode_call",
            "name": "mcp.demo.lookup",
            "ok": True,
            "result": {"text": "前缀🙂" * 1500, "values": [3, 2, 1]},
        }
        self.artifact = build_run_tool_result_artifact(self.result)

    def test_runtime_tool_is_read_only_idempotent_and_strict(self) -> None:
        spec = create_coding_tool_registry().get_spec(RUN_ARTIFACT_READ_TOOL)
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.provider, "runtime")
        self.assertEqual(spec.permission_level, "read_only")
        self.assertFalse(spec.requires_approval)
        self.assertTrue(spec.idempotent)
        self.assertFalse(spec.accepts_context)
        self.assertEqual(spec.input_schema["additionalProperties"], False)
        self.assertEqual(set(spec.input_schema["required"]), {"artifact_id"})

    def test_consecutive_pages_reconstruct_canonical_unicode_json(self) -> None:
        offset = 0
        pages: list[str] = []
        while True:
            page = read_run_artifact(
                [self.artifact],
                {
                    "artifact_id": self.artifact["id"],
                    "offset_chars": offset,
                    "max_tokens": 64,
                },
            )
            pages.append("".join(item["content"] for item in page["ranges"]))
            next_offset = page["next_offset_chars"]
            if next_offset is None:
                break
            self.assertGreater(next_offset, offset)
            offset = next_offset
        self.assertEqual("".join(pages), canonical_tool_result(self.result))

    def test_hash_corruption_and_untrusted_flags_fail_closed(self) -> None:
        artifact_id = self.artifact["id"]
        for changed in (
            {**self.artifact, "content_sha256": "sha256:" + "0" * 64},
            {**self.artifact, "runtime_created": False},
            {**self.artifact, "model_readable": False},
            {**self.artifact, "type": "mcp_output"},
            {**self.artifact, "call_id": "different_call"},
            {**self.artifact, "name": "different.tool"},
            {**self.artifact, "estimated_tokens": -1},
        ):
            with self.assertRaises(ArtifactReadError) as caught:
                read_run_artifact([changed], {"artifact_id": artifact_id})
            self.assertEqual(caught.exception.code, "artifact_not_found")

    def test_strict_arguments_and_offsets_have_non_oracle_errors(self) -> None:
        artifact_id = self.artifact["id"]
        with self.assertRaises(ArtifactReadError) as invalid:
            read_run_artifact(
                [self.artifact],
                {"artifact_id": artifact_id, "run_id": "another"},
            )
        self.assertEqual(invalid.exception.code, "artifact_not_found")
        with self.assertRaises(ArtifactReadError) as missing:
            read_run_artifact(
                [self.artifact],
                {"artifact_id": "tool_result_" + "0" * 20},
            )
        self.assertEqual(missing.exception.code, "artifact_not_found")
        with self.assertRaises(ArtifactReadError) as offset:
            read_run_artifact(
                [self.artifact],
                {
                    "artifact_id": artifact_id,
                    "offset_chars": self.artifact["content_chars"],
                },
            )
        self.assertEqual(offset.exception.code, "artifact_offset_out_of_range")

    def test_head_tail_reports_exact_non_overlapping_ranges(self) -> None:
        result = read_run_artifact(
            [self.artifact],
            {
                "artifact_id": self.artifact["id"],
                "view": "head_tail",
                "offset_chars": 7,
                "max_tokens": 64,
            },
        )
        canonical = canonical_tool_result(self.result)
        first, second = result["ranges"]
        self.assertEqual(first["content"], canonical[first["start_char"] : first["end_char"]])
        self.assertEqual(second["content"], canonical[second["start_char"] : second["end_char"]])
        self.assertLessEqual(first["end_char"], second["start_char"])


class RunArtifactNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.full_result = {
            "call_id": "source_call",
            "name": "mcp.demo.lookup",
            "ok": True,
            "result": {"text": "payload-" * 2000},
        }
        self.artifact = build_run_tool_result_artifact(self.full_result)
        self.nodes = object.__new__(ToolLoopNodes)
        self.nodes._metrics = MetricsRegistry()
        self.nodes._tool_result_max_tokens = 128
        self.nodes._visible_tool_specs = lambda state: [run_artifact_tool_spec()]

    def test_checkpoint_state_reader_and_full_envelope_fit_harness_budget(self) -> None:
        call = ToolCall(
            call_id="read_call",
            name=RUN_ARTIFACT_READ_TOOL,
            arguments={
                "artifact_id": self.artifact["id"],
                "max_tokens": 800,
            },
        )
        response = self.nodes._read_artifact(
            {
                "artifacts": [self.artifact],
                "run_artifact_read_enabled": True,
            },  # type: ignore[arg-type]
            call,
        )
        message = _native_artifact_read_message(response, max_tokens=128)
        self.assertTrue(response["ok"])
        self.assertTrue(message["ephemeral"])
        self.assertLessEqual(
            estimate_text_tokens(_serialize_tool_result(message["content"])),
            128,
        )
        self.assertEqual(
            response["result"]["max_tokens"],
            128,
        )

    def test_readback_result_is_never_a_recursive_artifact_candidate(self) -> None:
        call = ToolCall(
            call_id="read_call",
            name=RUN_ARTIFACT_READ_TOOL,
            arguments={"artifact_id": self.artifact["id"]},
        )
        response = self.nodes._read_artifact(
            {
                "artifacts": [self.artifact],
                "run_artifact_read_enabled": True,
            },  # type: ignore[arg-type]
            call,
        )
        message = _native_artifact_read_message(response, max_tokens=128)
        candidates = _native_reduction_artifact_candidates(
            {"tool_results": [response]},  # type: ignore[arg-type]
            [message],
        )
        self.assertEqual(candidates, {})

    def test_legacy_checkpoint_without_capability_hides_runtime_tool(self) -> None:
        coordinator = object.__new__(ToolAccessCoordinator)
        pool = SimpleNamespace(
            list_specs=lambda context=None: [
                create_coding_tool_registry().get_spec(RUN_ARTIFACT_READ_TOOL)
            ]
        )
        coordinator.tools_for_state = lambda state: pool  # type: ignore[method-assign]
        coordinator.tool_use_context = lambda state: None  # type: ignore[method-assign]
        legacy: CodingAgentState = {}
        current: CodingAgentState = {"run_artifact_read_enabled": True}
        self.assertEqual(coordinator.visible_tool_specs(legacy), [])
        self.assertEqual(
            [spec.name for spec in coordinator.visible_tool_specs(current)],
            [RUN_ARTIFACT_READ_TOOL],
        )

    def test_small_builtin_and_mcp_results_externalize_only_when_evicted(self) -> None:
        for tool_name in ("repo.read_file", "mcp.demo.lookup"):
            with self.subTest(tool_name=tool_name):
                first = {
                    "call_id": "first_call",
                    "name": tool_name,
                    "ok": True,
                    "result": {"text": "small-original-🙂-" * 8},
                }
                recent = {
                    "call_id": "recent_call",
                    "name": tool_name,
                    "ok": True,
                    "result": {"text": "recent"},
                }
                messages = [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "request"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"call_id": "first_call", "name": tool_name, "arguments": {}}
                        ],
                    },
                    {
                        "role": "tool",
                        "call_id": "first_call",
                        "name": tool_name,
                        "content": first,
                        "is_error": False,
                    },
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"call_id": "recent_call", "name": tool_name, "arguments": {}}
                        ],
                    },
                    {
                        "role": "tool",
                        "call_id": "recent_call",
                        "name": tool_name,
                        "content": recent,
                        "is_error": False,
                    },
                ]
                state: CodingAgentState = {"tool_results": [first, recent]}
                inline_messages, eager_artifacts = self.nodes._budget_tool_results(
                    [first]
                )
                self.assertEqual(eager_artifacts, [])
                self.assertEqual(inline_messages[0]["content"], first)
                self.assertLessEqual(
                    estimate_text_tokens(_serialize_tool_result(first)),
                    self.nodes._tool_result_max_tokens,
                )
                unchanged, unchanged_artifacts = self.nodes._reduce_with_run_artifacts(
                    state,
                    messages,
                    max_chars=_native_messages_chars(messages) + 1,
                    max_tokens=0,
                    keep_messages=4,
                    tool_result_keep_recent=1,
                    previous_compactions=0,
                    max_compactions=3,
                    artifacts=[],
                )
                self.assertFalse(unchanged.changed)
                self.assertEqual(unchanged_artifacts, [])

                reduction, artifacts = self.nodes._reduce_with_run_artifacts(
                    state,
                    messages,
                    max_chars=_native_messages_chars(messages) - 50,
                    max_tokens=0,
                    keep_messages=4,
                    tool_result_keep_recent=1,
                    previous_compactions=0,
                    max_compactions=3,
                    artifacts=[],
                )
                self.assertTrue(reduction.changed)
                self.assertEqual([item["call_id"] for item in artifacts], ["first_call"])
                marker = next(
                    item
                    for item in reduction.messages
                    if item.get("call_id") == "first_call"
                )
                self.assertTrue(marker["content"]["evicted"])
                self.assertEqual(marker["content"]["artifact_id"], artifacts[0]["id"])
                page = read_run_artifact(
                    artifacts,
                    {"artifact_id": artifacts[0]["id"], "max_tokens": 64},
                )
                self.assertIn("small-original", page["ranges"][0]["content"])

    def test_forced_recovery_externalizes_and_replay_is_idempotent(self) -> None:
        first = {
            "call_id": "forced_old",
            "name": "repo.read_file",
            "ok": True,
            "result": {"text": "forced-body" * 100},
        }
        recent = {
            "call_id": "forced_recent",
            "name": "repo.read_file",
            "ok": True,
            "result": {"text": "recent"},
        }
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "request"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"call_id": "forced_old", "name": first["name"], "arguments": {}}],
            },
            {"role": "tool", "call_id": "forced_old", "name": first["name"], "content": first},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"call_id": "forced_recent", "name": recent["name"], "arguments": {}}],
            },
            {"role": "tool", "call_id": "forced_recent", "name": recent["name"], "content": recent},
        ]
        state: CodingAgentState = {"tool_results": [first, recent]}
        reduction, artifacts = self.nodes._reduce_with_run_artifacts(
            state,
            messages,
            max_chars=100_000,
            max_tokens=0,
            keep_messages=4,
            tool_result_keep_recent=1,
            previous_compactions=0,
            max_compactions=3,
            force=True,
            artifacts=[],
        )
        self.assertEqual(len(artifacts), 1)
        replay, replay_artifacts = self.nodes._reduce_with_run_artifacts(
            state,
            reduction.messages,
            max_chars=100_000,
            max_tokens=0,
            keep_messages=4,
            tool_result_keep_recent=1,
            previous_compactions=reduction.compactions,
            max_compactions=3,
            artifacts=artifacts,
        )
        self.assertEqual(replay_artifacts, artifacts)
        self.assertEqual(
            [item["id"] for item in replay_artifacts],
            list(dict.fromkeys(item["id"] for item in replay_artifacts)),
        )
        self.assertTrue(replay.messages)

    def test_reused_call_id_never_creates_a_dangling_or_mismatched_marker(self) -> None:
        old_result = {
            "call_id": "reused_call",
            "name": "mcp.demo.lookup",
            "ok": True,
            "result": {"text": "old-body-🙂" * 12},
        }
        new_result = {
            "call_id": "reused_call",
            "name": "mcp.demo.lookup",
            "ok": True,
            "result": {"text": "new-body-🙂" * 12},
        }
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "request"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"call_id": "reused_call", "name": old_result["name"], "arguments": {}}
                ],
            },
            {
                "role": "tool",
                "call_id": "reused_call",
                "name": old_result["name"],
                "content": old_result,
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"call_id": "reused_call", "name": new_result["name"], "arguments": {}}
                ],
            },
            {
                "role": "tool",
                "call_id": "reused_call",
                "name": new_result["name"],
                "content": new_result,
            },
        ]
        state: CodingAgentState = {"tool_results": [old_result, new_result]}
        reduction, artifacts = self.nodes._reduce_with_run_artifacts(
            state,
            messages,
            max_chars=_native_messages_chars(messages) - 50,
            max_tokens=0,
            keep_messages=4,
            tool_result_keep_recent=1,
            previous_compactions=0,
            max_compactions=3,
            artifacts=[],
        )

        self.assertTrue(reduction.changed)
        persisted_ids = {str(item["id"]) for item in artifacts}
        marker_ids = {
            str(message["content"]["artifact_id"])
            for message in reduction.messages
            if message.get("role") == "tool"
            and isinstance(message.get("content"), dict)
            and message["content"].get("artifact_id")
        }
        self.assertLessEqual(marker_ids, persisted_ids)
        self.assertNotIn(
            str(build_run_tool_result_artifact(new_result)["id"]),
            marker_ids,
        )
        for artifact_id in marker_ids:
            self.assertTrue(
                read_run_artifact(
                    artifacts,
                    {"artifact_id": artifact_id, "max_tokens": 64},
                )["ranges"]
            )

    def test_mcp_forged_id_is_not_readable_and_audit_has_no_body(self) -> None:
        forged_id = self.artifact["id"]
        mcp_result = {
            "call_id": "mcp_forge",
            "name": "mcp:demo.lookup",
            "ok": True,
            "result": {"artifact_id": forged_id, "sentinel": "SECRET-BODY-SENTINEL"},
        }
        with self.assertRaises(ArtifactReadError) as missing:
            read_run_artifact([], {"artifact_id": forged_id})
        self.assertEqual(missing.exception.code, "artifact_not_found")
        call = ToolCall(
            call_id="read_audit",
            name=RUN_ARTIFACT_READ_TOOL,
            arguments={"artifact_id": self.artifact["id"], "max_tokens": 64},
        )
        response = self.nodes._read_artifact(
            {
                "artifacts": [self.artifact],
                "tool_results": [mcp_result],
                "run_artifact_read_enabled": True,
            },  # type: ignore[arg-type]
            call,
        )
        audit = artifact_read_trace(response)
        self.assertNotIn("payload-", _serialize_tool_result(audit))
        self.assertNotIn("content", _serialize_tool_result(audit))
        self.assertEqual(audit["artifact_id"], self.artifact["id"])

    def test_hidden_capability_cannot_execute_a_restored_pending_read_call(self) -> None:
        call = ToolCall(
            call_id="legacy_pending_read",
            name=RUN_ARTIFACT_READ_TOOL,
            arguments={"artifact_id": self.artifact["id"]},
        )
        for state, visible_specs in (
            ({"artifacts": [self.artifact]}, [run_artifact_tool_spec()]),
            (
                {
                    "artifacts": [self.artifact],
                    "run_artifact_read_enabled": True,
                },
                [],
            ),
        ):
            with self.subTest(state_enabled=state.get("run_artifact_read_enabled")):
                self.nodes._visible_tool_specs = lambda current, specs=visible_specs: specs
                response = self.nodes._read_artifact(  # type: ignore[arg-type]
                    state,
                    call,
                )
                self.assertFalse(response["ok"])
                self.assertEqual(response["error_code"], "artifact_not_found")


class RunArtifactCheckpointAndStoreTests(unittest.TestCase):
    def _artifact(self) -> dict[str, object]:
        return build_run_tool_result_artifact(
            {
                "call_id": "persisted_call",
                "name": "mcp:demo.lookup",
                "ok": True,
                "result": {"text": "跨 checkpoint 与后端🙂" * 40},
            }
        )

    def test_pause_resume_and_selected_rollback_fork_inherit_exact_artifact_state(self) -> None:
        artifact = self._artifact()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("demo\n", encoding="utf-8")
            runtime = CodingAgentRuntime(planner=_InputThenCompletePlanner())
            waiting = runtime.run(
                conversation_id="artifact_checkpoint",
                user_input="inspect the artifact",
                history=[],
                workspace_id="workspace",
                workspace_root=str(root),
            )
            self.assertEqual(waiting.status, "waiting_input")
            before = next(
                item
                for item in runtime.list_checkpoints(waiting.run_id)
                if item.can_restore
                and not runtime._checkpoint_coordinator.snapshot_by_id(
                    waiting.thread_id,
                    item.checkpoint_id,
                ).values.get("artifacts")
            )
            current = runtime._checkpoint_coordinator.snapshot_for(
                runtime._checkpoint_coordinator.config(waiting.thread_id)
            )
            after_config = runtime._graph.update_state(
                current.config,
                {"artifacts": [artifact]},
            )
            after = runtime._checkpoint_coordinator.snapshot_for(after_config)
            after_id = str(after.config["configurable"]["checkpoint_id"])

            for label, checkpoint_id, expected in (
                ("before", before.checkpoint_id, []),
                ("after", after_id, [artifact]),
            ):
                for mode in ("rollback", "fork"):
                    with self.subTest(label=label, mode=mode):
                        branch = runtime.prepare_checkpoint_branch(
                            source_run_id=waiting.run_id,
                            checkpoint_id=checkpoint_id,
                            conversation_id=f"artifact_{label}_{mode}",
                            mode=mode,
                            message=f"{label} {mode}",
                        )
                        cloned = runtime._checkpoint_coordinator.snapshot_by_id(
                            branch.thread_id,
                            branch.checkpoint_id,
                        )
                        self.assertEqual(cloned.values.get("artifacts", []), expected)

            completed = runtime.resume(
                run_id=waiting.run_id,
                approved=True,
                feedback="continue",
            )
            self.assertEqual(completed.status, "completed")
            self.assertEqual(completed.artifacts, [artifact])

    def test_native_model_loop_reads_and_reassembles_checkpoint_artifact_pages(self) -> None:
        artifact = build_run_tool_result_artifact(
            {
                "call_id": "native_paged_source",
                "name": "mcp.demo.lookup",
                "ok": True,
                "result": {"text": "native-visible-page-前缀🙂-" * 30},
            }
        )
        planner = _PagedArtifactPlanner(str(artifact["id"]))
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("demo\n", encoding="utf-8")
            runtime = CodingAgentRuntime(
                planner=planner,
                tool_result_max_tokens=128,
            )
            waiting = runtime.run(
                conversation_id="artifact_native_pages",
                user_input="read every artifact page",
                history=[],
                workspace_id="workspace",
                workspace_root=str(root),
            )
            self.assertEqual(waiting.status, "waiting_input")
            current = runtime._checkpoint_coordinator.snapshot_for(
                runtime._checkpoint_coordinator.config(waiting.thread_id)
            )
            runtime._graph.update_state(current.config, {"artifacts": [artifact]})

            completed = runtime.resume(
                run_id=waiting.run_id,
                approved=True,
                feedback="continue",
            )

        self.assertEqual(completed.status, "completed")
        self.assertTrue(planner.artifact_tool_visible)
        self.assertGreater(len(planner.pages), 1)
        self.assertEqual(
            "".join(planner.pages),
            canonical_tool_result(artifact["content"]),
        )
        self.assertTrue(planner.model_envelopes)
        for envelope in planner.model_envelopes:
            self.assertEqual(envelope["name"], RUN_ARTIFACT_READ_TOOL)
            self.assertTrue(envelope["ok"])
            self.assertLessEqual(
                estimate_text_tokens(_serialize_tool_result(envelope)),
                128,
            )

    def test_legacy_run_context_tool_views_do_not_gain_read_artifact(self) -> None:
        registry = create_coding_tool_registry()
        coordinator = ToolAccessCoordinator(
            tools=registry,
            default_approval_policy="never",
        )
        for schema_version, enabled_tools in (
            (1, None),
            (2, ("repo.read_file",)),
        ):
            with self.subTest(schema_version=schema_version):
                legacy_snapshot = SimpleNamespace(
                    metadata=SimpleNamespace(
                        schema_version=schema_version,
                        run_id=f"legacy_v{schema_version}",
                    ),
                    tools=SimpleNamespace(enabled_tools=enabled_tools),
                )
                restored = coordinator.restore_snapshot(legacy_snapshot)
                self.assertNotIn(
                    RUN_ARTIFACT_READ_TOOL,
                    restored.allowed_names,
                )

    def test_agent_run_result_unicode_artifact_round_trips_memory_sqlite_postgres(self) -> None:
        artifact = self._artifact()
        result = AgentRunResult(
            run_id="artifact_store",
            thread_id="artifact_store",
            conversation_id="conversation",
            workspace_id="workspace",
            status="completed",
            checkpoint_id="checkpoint",
            role="coding",
            objective="readback",
            intent="code_explanation",
            context_route="repo",
            selected_knowledge_base_ids=[],
            answer="done",
            graph_engine="langgraph",
            context_sources=[],
            tool_calls=[],
            tool_results=[],
            trace=[],
            artifacts=[artifact],
        )
        record = AgentRunRecord(
            run_id=result.run_id,
            thread_id=result.thread_id,
            conversation_id=result.conversation_id,
            workspace_id=result.workspace_id,
            workspace_root="/workspace",
            status="completed",
            checkpoint_id=result.checkpoint_id,
            latest_node="compose_answer",
            next_nodes=[],
            trace=[],
            result=result,
        )

        memory = InMemoryAgentRunStore()
        memory.save(record)
        self.assertEqual(memory.get(record.run_id).result.artifacts, [artifact])

        with TemporaryDirectory() as temp_dir:
            sqlite = SQLiteAgentRunRepository(
                database=LocalStateDatabase(str(Path(temp_dir) / "state.sqlite3"))
            )
            sqlite.save(record)
            loaded = sqlite.get(record.run_id)
            assert loaded.result is not None
            self.assertEqual(loaded.result.artifacts, [artifact])

        postgres_json = _agent_result_to_json(result)
        assert postgres_json is not None
        postgres_roundtrip = _agent_result_from_json(
            json.loads(json.dumps(postgres_json, ensure_ascii=False))
        )
        assert postgres_roundtrip is not None
        self.assertEqual(postgres_roundtrip.artifacts, [artifact])


if __name__ == "__main__":
    unittest.main()
