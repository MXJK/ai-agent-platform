from __future__ import annotations

import unittest
from types import SimpleNamespace

from ai_agent_platform.agents.coding.run_artifacts import (
    ArtifactReadError,
    RUN_ARTIFACT_READ_TOOL,
    build_run_tool_result_artifact,
    canonical_tool_result,
    read_run_artifact,
)
from ai_agent_platform.agents.coding.models import CodingAgentState
from ai_agent_platform.agents.coding.tool_access import ToolAccessCoordinator
from ai_agent_platform.agents.coding.tool_loop_nodes import (
    ToolLoopNodes,
    _native_artifact_read_message,
    _native_reduction_artifact_candidates,
    _serialize_tool_result,
)
from ai_agent_platform.agents.coding.tools import create_coding_tool_registry
from ai_agent_platform.core.metrics import MetricsRegistry
from ai_agent_platform.integrations.tools import ToolCall
from ai_agent_platform.token_counting import estimate_text_tokens


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
            {"artifacts": [self.artifact]},  # type: ignore[arg-type]
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
            {"artifacts": [self.artifact]},  # type: ignore[arg-type]
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


if __name__ == "__main__":
    unittest.main()
