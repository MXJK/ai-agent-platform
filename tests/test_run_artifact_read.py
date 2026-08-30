from __future__ import annotations

import hashlib
import logging
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
from ai_agent_platform.domain import RunContextSnapshot
from ai_agent_platform.integrations.llm import LLMProviderError, LLMToolDecision
from ai_agent_platform.integrations.mcp import MCPTool, MCPToolProvider
from ai_agent_platform.integrations.tools import ToolCall, ToolExecutionContext
from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.repositories.postgres import (
    _agent_result_from_json,
    _agent_result_to_json,
)
from ai_agent_platform.repositories.sqlite import SQLiteAgentRunRepository
from ai_agent_platform.services.query_events import AgentEventEncoder
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


class _SmallResultMCPClient:
    def list_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                name="lookup",
                description="Return a small deterministic lookup result.",
                input_schema={
                    "type": "object",
                    "required": ["query"],
                    "properties": {"query": {"type": "string"}},
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {"match": {"type": "string"}},
                    "required": ["match"],
                    "additionalProperties": False,
                },
                permission_level="read_only",
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        if name != "lookup":
            raise ValueError(name)
        return {"match": f"found:{arguments['query']}:前缀🙂"}


class _OverflowAfterSmallResultsPlanner(_InputThenCompletePlanner):
    def __init__(self) -> None:
        super().__init__()
        self.pre_overflow_messages: list[dict[str, object]] = []

    def decide_tool_calls(self, messages, tool_specs, **kwargs):
        del tool_specs, kwargs
        self.decisions += 1
        if self.decisions in (1, 2):
            label = "old" if self.decisions == 1 else "recent"
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id=f"small_{label}",
                        name="repo.read_file",
                        arguments={
                            "path": f"{label}.txt",
                            "start_line": 1,
                            "end_line": 1,
                        },
                    )
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        if self.decisions == 3:
            self.pre_overflow_messages = list(messages)
            raise LLMProviderError(
                "maximum context length exceeded",
                code="context_overflow",
            )
        return LLMToolDecision(
            text="Recovered after one retry.",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )


class _CheckpointReplayPlanner(_InputThenCompletePlanner):
    def __init__(self) -> None:
        super().__init__()
        self.mode = "seed"

    def reset_for_replay(self) -> None:
        self.mode = "replay"
        self.decisions = 0

    def decide_tool_calls(self, messages, tool_specs, **kwargs):
        if self.mode == "seed":
            return super().decide_tool_calls(messages, tool_specs, **kwargs)
        del messages, tool_specs, kwargs
        self.decisions += 1
        if self.decisions == 1:
            raise LLMProviderError(
                "maximum context length exceeded",
                code="context_overflow",
            )
        return LLMToolDecision(
            text="replayed",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
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
        harness_max_tokens = 512
        self.nodes._tool_result_max_tokens = harness_max_tokens
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "tiny.txt").write_text("small built-in 前缀🙂\n", encoding="utf-8")
            registry = create_coding_tool_registry(
                mcp_providers=[
                    MCPToolProvider(
                        server_name="demo",
                        client=_SmallResultMCPClient(),
                    )
                ]
            )
            context = ToolExecutionContext(
                conversation_id="artifact_small",
                workspace_id="workspace",
                workspace_root=str(root),
                authorized_workspace_root=str(root),
                run_id="run_small_results",
                approval_policy="never",
            )
            cases = (
                ("repo.read_file", {"path": "tiny.txt"}),
                ("mcp.demo.lookup", {"query": "alpha"}),
            )
            for tool_name, arguments in cases:
                with self.subTest(tool_name=tool_name):
                    first = registry.execute(
                        ToolCall(
                            call_id=f"first_{tool_name}",
                            name=tool_name,
                            arguments=arguments,
                        ),
                        context=context,
                    ).to_response()
                    recent = registry.execute(
                        ToolCall(
                            call_id=f"recent_{tool_name}",
                            name=tool_name,
                            arguments=arguments,
                        ),
                        context=context,
                    ).to_response()
                    self.assertTrue(first["ok"], first)
                    self.assertTrue(recent["ok"], recent)
                    first_call_id = str(first["call_id"])
                    recent_call_id = str(recent["call_id"])
                    messages = [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "request"},
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "call_id": first_call_id,
                                    "name": tool_name,
                                    "arguments": arguments,
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "call_id": first_call_id,
                            "name": tool_name,
                            "content": first,
                            "is_error": False,
                        },
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "call_id": recent_call_id,
                                    "name": tool_name,
                                    "arguments": arguments,
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "call_id": recent_call_id,
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
                        harness_max_tokens,
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
                    self.assertEqual(
                        [item["call_id"] for item in artifacts],
                        [first_call_id],
                    )
                    marker = next(
                        item
                        for item in reduction.messages
                        if item.get("call_id") == first_call_id
                    )
                    self.assertTrue(marker["content"]["evicted"])
                    self.assertEqual(
                        marker["content"]["artifact_id"],
                        artifacts[0]["id"],
                    )
                    page = read_run_artifact(
                        artifacts,
                        {"artifact_id": artifacts[0]["id"], "max_tokens": 2000},
                    )
                    self.assertEqual(
                        "".join(item["content"] for item in page["ranges"]),
                        canonical_tool_result(first),
                    )

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

    def test_artifact_from_another_run_state_is_not_found(self) -> None:
        other_artifact = build_run_tool_result_artifact(
            {
                "call_id": "run_b_source",
                "name": "repo.read_file",
                "ok": True,
                "result": {"text": "run-b-only"},
            }
        )
        response = self.nodes._read_artifact(
            {
                "run_id": "run_b",
                "artifacts": [other_artifact],
                "run_artifact_read_enabled": True,
            },  # type: ignore[arg-type]
            ToolCall(
                call_id="cross_run_read",
                name=RUN_ARTIFACT_READ_TOOL,
                arguments={"artifact_id": self.artifact["id"]},
            ),
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

    def test_provider_overflow_lazy_externalizes_small_result_in_recovery_update(self) -> None:
        planner = _OverflowAfterSmallResultsPlanner()
        metrics = MetricsRegistry()
        harness_max_tokens = 512
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "old.txt").write_text("old small result 前缀🙂\n", encoding="utf-8")
            (root / "recent.txt").write_text(
                "recent small result 后缀🙂\n",
                encoding="utf-8",
            )
            runtime = CodingAgentRuntime(
                planner=planner,
                tool_result_keep_recent=1,
                tool_result_max_tokens=harness_max_tokens,
                metrics=metrics,
            )
            completed = runtime.run(
                conversation_id="artifact_overflow_lazy",
                user_input="read both files, then recover from provider overflow",
                history=[],
                workspace_id="workspace",
                workspace_root=str(root),
            )

        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.answer, "Recovered after one retry.")
        self.assertEqual(planner.decisions, 4)
        pre_overflow_results = [
            message
            for message in planner.pre_overflow_messages
            if message.get("role") == "tool"
        ]
        self.assertEqual(len(pre_overflow_results), 2)
        for message in pre_overflow_results:
            content = message["content"]
            self.assertFalse(content.get("truncated", False))
            self.assertFalse(content.get("evicted", False))
            self.assertNotIn("artifact_id", content)
            self.assertLessEqual(
                estimate_text_tokens(_serialize_tool_result(content)),
                harness_max_tokens,
            )

        current = runtime._checkpoint_coordinator.snapshot_for(
            runtime._checkpoint_coordinator.config(completed.thread_id)
        )
        artifacts = list(current.values.get("artifacts", []))
        markers = [
            message
            for message in current.values.get("native_tool_messages", [])
            if message.get("role") == "tool"
            and isinstance(message.get("content"), dict)
            and message["content"].get("evicted") is True
        ]
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(len(markers), 1)
        self.assertEqual(artifacts[0]["call_id"], "small_old")
        self.assertEqual(markers[0]["call_id"], "small_old")
        self.assertEqual(markers[0]["content"]["artifact_id"], artifacts[0]["id"])
        self.assertEqual(completed.artifacts, artifacts)
        coherent_recovery_checkpoints = 0
        for checkpoint in runtime.list_checkpoints(completed.run_id):
            snapshot = runtime._checkpoint_coordinator.snapshot_by_id(
                completed.thread_id,
                checkpoint.checkpoint_id,
            )
            snapshot_marker_ids = {
                str(message["content"]["artifact_id"])
                for message in snapshot.values.get("native_tool_messages", [])
                if message.get("role") == "tool"
                and isinstance(message.get("content"), dict)
                and message["content"].get("evicted") is True
            }
            if not snapshot_marker_ids:
                continue
            coherent_recovery_checkpoints += 1
            snapshot_artifact_ids = {
                str(item["id"])
                for item in snapshot.values.get("artifacts", [])
            }
            self.assertLessEqual(snapshot_marker_ids, snapshot_artifact_ids)
        self.assertGreater(coherent_recovery_checkpoints, 0)
        self.assertEqual(
            metrics.snapshot()["counters"][
                "agent_native_context_overflow_retries_total"
            ],
            1,
        )
        recovery_stages = [
            stage
            for trace in completed.trace
            for stage in (trace.get("output") or {}).get(
                "context_reduction_stages",
                [],
            )
            if stage.get("forced")
        ]
        self.assertTrue(recovery_stages)

    def test_two_checkpoint_replays_persist_identical_lazy_artifact_markers(self) -> None:
        planner = _CheckpointReplayPlanner()
        harness_max_tokens = 512
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "old.txt").write_text("checkpoint old 前缀🙂\n", encoding="utf-8")
            (root / "recent.txt").write_text(
                "checkpoint recent 后缀🙂\n",
                encoding="utf-8",
            )
            registry = create_coding_tool_registry()
            runtime = CodingAgentRuntime(
                tool_registry=registry,
                planner=planner,
                tool_result_keep_recent=1,
                tool_result_max_tokens=harness_max_tokens,
            )
            source = runtime.run(
                run_id="run_artifact_replay_source",
                conversation_id="artifact_replay_source",
                user_input="prepare replay checkpoint",
                history=[],
                workspace_id="workspace",
                workspace_root=str(root),
            )
            self.assertEqual(source.status, "waiting_input")
            selected_info = next(
                item
                for item in runtime.list_checkpoints(source.run_id)
                if item.next_nodes == ["plan_tools"]
            )
            selected = runtime._checkpoint_coordinator.snapshot_by_id(
                source.thread_id,
                selected_info.checkpoint_id,
            )
            execution_context = ToolExecutionContext(
                conversation_id=source.conversation_id,
                workspace_id=source.workspace_id,
                workspace_root=str(root),
                authorized_workspace_root=str(root),
                run_id=source.run_id,
                approval_policy="never",
            )
            tool_results = [
                registry.execute(
                    ToolCall(
                        call_id=f"checkpoint_{label}",
                        name="repo.read_file",
                        arguments={"path": f"{label}.txt"},
                    ),
                    context=execution_context,
                ).to_response()
                for label in ("old", "recent")
            ]
            self.assertTrue(all(result["ok"] for result in tool_results))
            self.assertTrue(
                all(
                    estimate_text_tokens(_serialize_tool_result(result))
                    <= harness_max_tokens
                    for result in tool_results
                )
            )
            replay_messages: list[dict[str, object]] = [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "replay request"},
            ]
            for label, result in zip(("old", "recent"), tool_results):
                replay_messages.extend(
                    [
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "call_id": f"checkpoint_{label}",
                                    "name": "repo.read_file",
                                    "arguments": {"path": f"{label}.txt"},
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "call_id": f"checkpoint_{label}",
                            "name": "repo.read_file",
                            "content": result,
                            "is_error": False,
                        },
                    ]
                )
            replay_config = runtime._graph.update_state(
                selected.config,
                {
                    "native_tool_messages": replay_messages,
                    "tool_results": tool_results,
                    "artifacts": [],
                    "native_tool_call_count": 2,
                    "native_tool_signatures": [],
                    "native_context_compactions": 0,
                    "native_context_reduction_stages": [],
                },
            )
            replay_snapshot = runtime._checkpoint_coordinator.snapshot_for(
                replay_config
            )
            replay_checkpoint_id = str(
                replay_snapshot.config["configurable"]["checkpoint_id"]
            )
            branches = [
                runtime.prepare_checkpoint_branch(
                    source_run_id=source.run_id,
                    checkpoint_id=replay_checkpoint_id,
                    conversation_id=f"artifact_replay_{label}",
                    mode="fork",
                    run_id=f"run_artifact_replay_{label}",
                )
                for label in ("a", "b")
            ]
            for branch in branches:
                runtime.restore_record(branch)

            replay_shapes: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
            for branch in branches:
                planner.reset_for_replay()
                completed = runtime.run_from_checkpoint(branch.run_id)
                self.assertEqual(completed.status, "completed")
                current = runtime._checkpoint_coordinator.snapshot_for(
                    runtime._checkpoint_coordinator.config(branch.thread_id)
                )
                artifacts = list(current.values.get("artifacts", []))
                marker_ids = tuple(
                    str(message["content"]["artifact_id"])
                    for message in current.values.get("native_tool_messages", [])
                    if message.get("role") == "tool"
                    and isinstance(message.get("content"), dict)
                    and message["content"].get("evicted") is True
                )
                artifact_ids = tuple(str(item["id"]) for item in artifacts)
                self.assertEqual(len(artifact_ids), 1)
                self.assertEqual(marker_ids, artifact_ids)
                self.assertEqual(completed.artifacts, artifacts)
                self.assertTrue(
                    read_run_artifact(
                        artifacts,
                        {"artifact_id": artifact_ids[0], "max_tokens": 64},
                    )["ranges"]
                )
                replay_shapes.append((artifact_ids, marker_ids))

        self.assertEqual(replay_shapes[0], replay_shapes[1])

    def test_native_model_loop_reads_and_reassembles_checkpoint_artifact_pages(self) -> None:
        sentinel = "ARTIFACT-READ-AUDIT-SENTINEL-91f34c"
        artifact = build_run_tool_result_artifact(
            {
                "call_id": "native_paged_source",
                "name": "mcp.demo.lookup",
                "ok": True,
                "result": {
                    "text": ("native-visible-page-前缀🙂-" * 15)
                    + sentinel
                    + ("-tail-后缀🙂" * 15)
                },
            }
        )
        planner = _PagedArtifactPlanner(str(artifact["id"]))
        metrics = MetricsRegistry()
        run_store = InMemoryAgentRunStore()
        captured_logs: list[str] = []

        class _CaptureHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured_logs.append(self.format(record))

        log_handler = _CaptureHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("demo\n", encoding="utf-8")
            runtime = CodingAgentRuntime(
                planner=planner,
                run_store=run_store,
                tool_result_max_tokens=128,
                metrics=metrics,
            )
            try:
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
            finally:
                root_logger.removeHandler(log_handler)

        self.assertEqual(completed.status, "completed")
        self.assertTrue(planner.artifact_tool_visible)
        self.assertGreater(len(planner.pages), 1)
        self.assertEqual(
            "".join(planner.pages),
            canonical_tool_result(artifact["content"]),
        )
        self.assertIn(sentinel, "".join(planner.pages))
        self.assertTrue(planner.model_envelopes)
        for envelope in planner.model_envelopes:
            self.assertEqual(envelope["name"], RUN_ARTIFACT_READ_TOOL)
            self.assertTrue(envelope["ok"])
            self.assertLessEqual(
                estimate_text_tokens(_serialize_tool_result(envelope)),
                128,
            )
        serialized_trace = _serialize_tool_result(completed.trace)
        self.assertNotIn(sentinel, serialized_trace)
        artifact_reads = [
            read
            for trace in completed.trace
            for read in (trace.get("output") or {}).get("artifact_reads", [])
        ]
        self.assertGreater(len(artifact_reads), 1)
        required_metadata = {
            "artifact_id",
            "call_id",
            "tool",
            "view",
            "ranges",
            "returned_chars",
            "estimated_tokens",
            "sha256",
            "error_code",
        }
        for read in artifact_reads:
            self.assertTrue(required_metadata.issubset(read))
            self.assertEqual(read["artifact_id"], artifact["id"])
            self.assertEqual(read["tool"], RUN_ARTIFACT_READ_TOOL)
            self.assertTrue(read["ranges"])

        stored_events = run_store.list_events(completed.run_id)
        serialized_events = _serialize_tool_result(
            [
                {
                    "sequence": event.sequence,
                    "type": event.type,
                    "output": event.output,
                }
                for event in stored_events
            ]
        )
        self.assertNotIn(sentinel, serialized_events)
        encoder = AgentEventEncoder()
        sse = "".join(
            encoder.encode_sse(encoder.from_stored(completed.run_id, event))
            for event in stored_events
        )
        self.assertNotIn(sentinel, sse)
        self.assertIn(str(artifact["id"]), sse)
        self.assertNotIn(sentinel, _serialize_tool_result(metrics.snapshot()))
        self.assertGreater(
            metrics.snapshot()["counters"]["agent_run_artifact_reads_total"],
            1,
        )
        self.assertNotIn(sentinel, "\n".join(captured_logs))

    def test_legacy_run_context_tool_views_do_not_gain_read_artifact(self) -> None:
        registry = create_coding_tool_registry()
        coordinator = ToolAccessCoordinator(
            tools=registry,
            default_approval_policy="never",
        )
        project_config: dict[str, object] = {}
        config_json = json.dumps(
            project_config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload: dict[str, object] = {
            "identity": {
                "actor_user_id": "legacy_user",
                "auth_mode": "disabled",
                "workspace_role": "admin",
            },
            "session": {
                "conversation_id": "legacy_conversation",
                "user_message": "resume legacy checkpoint",
                "controlled_history": [],
                "summary": None,
                "model_selection": {},
            },
            "project": {
                "workspace_id": "legacy_workspace",
                "workspace_root": "/workspace",
                "workspace_revision": 1,
                "cwd": "/workspace",
                "git": {
                    "available": False,
                    "is_repository": False,
                    "head": None,
                    "branch": None,
                    "dirty": {
                        "is_dirty": False,
                        "changed_count": 0,
                        "staged_count": 0,
                        "unstaged_count": 0,
                        "untracked_count": 0,
                        "sample_paths": [],
                        "truncated": False,
                    },
                    "diagnostics": [],
                },
                "project_config": project_config,
            },
            "instructions": {
                "sources": [],
                "focus_files": [],
                "max_chars": 0,
                "diagnostics": [],
            },
            "additional_directories": [],
            "metadata": {
                "run_id": "legacy_v1",
                "created_at": "2026-08-24T00:00:00Z",
                "entrypoint_type": "api",
                "config_version": "sha256:"
                + hashlib.sha256(config_json.encode("utf-8")).hexdigest()[:16],
                "schema_version": 1,
                "entrypoint_metadata": {},
            },
        }
        legacy_v1 = RunContextSnapshot.from_dict(payload)
        self.assertIsNone(legacy_v1.tools.enabled_tools)
        restored_v1 = coordinator.restore_snapshot(legacy_v1)
        self.assertNotIn(RUN_ARTIFACT_READ_TOOL, restored_v1.allowed_names)

        enabled_v2 = ["repo.read_file"]
        encoded_tools = json.dumps(
            enabled_v2,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        payload_v2 = json.loads(json.dumps(payload))
        payload_v2["metadata"]["run_id"] = "legacy_v2"
        payload_v2["metadata"]["schema_version"] = 2
        payload_v2["tools"] = {
            "enabled_tools": enabled_v2,
            "source": "legacy_registry_view",
            "version": "sha256:"
            + hashlib.sha256(encoded_tools.encode("utf-8")).hexdigest()[:16],
        }
        legacy_v2 = RunContextSnapshot.from_dict(payload_v2)
        restored_v2 = coordinator.restore_snapshot(legacy_v2)
        self.assertEqual(restored_v2.allowed_names, ("repo.read_file",))
        self.assertNotIn(RUN_ARTIFACT_READ_TOOL, restored_v2.allowed_names)

        nodes = object.__new__(ToolLoopNodes)
        nodes._metrics = MetricsRegistry()
        nodes._tool_result_max_tokens = 128
        nodes._visible_tool_specs = coordinator.visible_tool_specs
        artifact = self._artifact()
        pending_response = nodes._read_artifact(
            {
                "run_id": legacy_v1.metadata.run_id,
                "enabled_tools": list(restored_v1.allowed_names),
                "artifacts": [artifact],
            },  # type: ignore[arg-type]
            ToolCall(
                call_id="legacy_restored_pending_read",
                name=RUN_ARTIFACT_READ_TOOL,
                arguments={"artifact_id": artifact["id"]},
            ),
        )
        self.assertFalse(pending_response["ok"])
        self.assertEqual(pending_response["error_code"], "artifact_not_found")

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
