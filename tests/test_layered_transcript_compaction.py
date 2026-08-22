from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ai_agent_platform.agents.coding.tool_loop_nodes import (
    _native_messages_tokens,
    _reduce_native_messages,
)
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.core import MetricsRegistry
from ai_agent_platform.integrations.llm import (
    LLMProviderError,
    LLMToolDecision,
    _http_error_code,
    _safe_provider_error_detail,
)


def _transcript(rounds: int, *, body_chars: int = 400) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "task"},
    ]
    for index in range(rounds):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": f"reason {index}",
                    "tool_calls": [
                        {
                            "call_id": f"c{index}",
                            "name": "demo.lookup",
                            "arguments": {"query": index},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "call_id": f"c{index}",
                    "name": "demo.lookup",
                    "content": {
                        "ok": True,
                        "result": {"text": "x" * body_chars + str(index)},
                    },
                    "is_error": False,
                },
            ]
        )
    return messages


def _assert_complete_pairs(
    testcase: unittest.TestCase,
    messages: list[dict[str, object]],
) -> None:
    proposed = {
        str(call.get("call_id"))
        for message in messages
        if message.get("role") == "assistant"
        for call in message.get("tool_calls", [])
        if isinstance(call, dict)
    }
    results = {
        str(message.get("call_id"))
        for message in messages
        if message.get("role") == "tool"
    }
    testcase.assertEqual(proposed, results)


class _OverflowPlanner:
    uses_native_tool_calling = True
    single_tool_per_turn = False

    def __init__(self, *, always_overflow: bool = False, never_call: bool = False):
        self.always_overflow = always_overflow
        self.never_call = never_call
        self.calls = 0

    def classify_intent(self, user_input: str) -> dict[str, object]:
        del user_input
        return {
            "intent": "code_explanation",
            "reason": "layered compaction test",
            "confidence": 1.0,
            "source": "test",
        }

    def plan_tool_calls(self, state, tool_specs):
        del state, tool_specs
        return []

    def decide_tool_calls(self, messages, tool_specs, **kwargs):
        del messages, tool_specs, kwargs
        self.calls += 1
        if self.never_call:
            raise AssertionError("provider must not be called after preflight exhaustion")
        if self.calls == 1 or self.always_overflow:
            raise LLMProviderError(
                "maximum context length exceeded",
                code="context_overflow",
            )
        return LLMToolDecision(
            text="Recovered after compaction.",
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
        return "fallback"


class LayeredTranscriptCompactionTests(unittest.TestCase):
    def test_microcompact_keeps_all_pairs_and_only_evicts_old_tool_bodies(self) -> None:
        messages = _transcript(8)

        reduction = _reduce_native_messages(
            messages,
            max_chars=100_000,
            max_tokens=1100,
            keep_messages=4,
            tool_result_keep_recent=2,
            previous_compactions=0,
            max_compactions=3,
        )

        self.assertEqual([stage["stage"] for stage in reduction.stages], [
            "tool_result_eviction"
        ])
        self.assertEqual(reduction.stages[0]["evicted"], 6)
        self.assertEqual(reduction.compactions, 0)
        self.assertFalse(reduction.exhausted)
        self.assertEqual(len(reduction.messages), len(messages))
        _assert_complete_pairs(self, reduction.messages)
        assistants = [
            message for message in reduction.messages if message.get("role") == "assistant"
        ]
        self.assertEqual(
            [message["content"] for message in assistants],
            [f"reason {index}" for index in range(8)],
        )
        tools = [
            message for message in reduction.messages if message.get("role") == "tool"
        ]
        self.assertTrue(all(tool.get("is_error") is False for tool in tools))
        self.assertTrue(all(tool.get("call_id") for tool in tools))
        self.assertTrue(all(tool["content"].get("evicted") for tool in tools[:6]))
        self.assertTrue(all("result" in tool["content"] for tool in tools[-2:]))
        self.assertIn("result", messages[3]["content"])

    def test_ladder_rechecks_fold_then_drops_and_truncates_to_fit(self) -> None:
        reduction = _reduce_native_messages(
            _transcript(8),
            max_chars=100_000,
            max_tokens=300,
            keep_messages=4,
            tool_result_keep_recent=2,
            previous_compactions=0,
            max_compactions=3,
        )

        self.assertEqual(
            [stage["stage"] for stage in reduction.stages],
            ["tool_result_eviction", "fold", "drop_truncate"],
        )
        self.assertFalse(reduction.stages[1]["fits"])
        self.assertTrue(reduction.stages[2]["fits"])
        self.assertGreater(reduction.stages[2]["dropped"], 0)
        self.assertGreater(reduction.stages[2]["truncated"], 0)
        self.assertLessEqual(_native_messages_tokens(reduction.messages), 300)
        _assert_complete_pairs(self, reduction.messages)

    def test_tool_truncation_fits_final_group_cost_and_preserves_artifact(self) -> None:
        messages: list[dict[str, object]] = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {
                "role": "assistant",
                "content": "r",
                "tool_calls": [
                    {"call_id": "c", "name": "demo.lookup", "arguments": {}}
                ],
            },
            {
                "role": "tool",
                "call_id": "c",
                "name": "demo.lookup",
                "content": {
                    "ok": True,
                    "artifact_id": "artifact_123",
                    "result": {"text": "x" * 4000},
                },
                "is_error": False,
            },
        ]

        reduction = _reduce_native_messages(
            messages,
            max_chars=100_000,
            max_tokens=200,
            keep_messages=10,
            tool_result_keep_recent=6,
            previous_compactions=0,
            max_compactions=3,
        )

        self.assertFalse(reduction.exhausted)
        self.assertLessEqual(reduction.estimated_tokens, 200)
        content = reduction.messages[-1]["content"]
        self.assertTrue(content["truncated"])
        self.assertEqual(content["artifact_id"], "artifact_123")
        self.assertTrue(content["ok"])
        self.assertNotIn('"preview": "{\\"truncated\\"', content["preview"])
        _assert_complete_pairs(self, reduction.messages)

    def test_compaction_limit_skips_another_fold_and_reports_exhaustion(self) -> None:
        reduction = _reduce_native_messages(
            _transcript(4),
            max_chars=100_000,
            max_tokens=50,
            keep_messages=4,
            tool_result_keep_recent=4,
            previous_compactions=3,
            max_compactions=3,
        )

        fold = next(stage for stage in reduction.stages if stage["stage"] == "fold")
        self.assertEqual(fold["compacted"], 0)
        self.assertTrue(fold["limit_reached"])
        self.assertEqual(reduction.compactions, 3)
        self.assertTrue(reduction.exhausted)

    def test_preflight_exhaustion_blocks_without_calling_provider(self) -> None:
        planner = _OverflowPlanner(never_call=True)
        metrics = MetricsRegistry()
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            runtime = CodingAgentRuntime(
                planner=planner,
                native_context_max_chars=200,
                native_max_compactions=1,
                metrics=metrics,
            )
            result = runtime.run(
                conversation_id="preflight_exhaustion",
                user_input="explain app.py " + "x" * 1000,
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
                focus_files=["app.py"],
            )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(planner.calls, 0)
        self.assertIn("stopped at stage drop_truncate", result.answer)
        plan = [item for item in result.trace if item["node"] == "plan_tools"][-1]
        self.assertEqual(plan["output"]["stop_reason"], "context_compaction_exhausted")
        self.assertEqual(
            metrics.snapshot()["counters"][
                "agent_native_context_compaction_exhausted_total"
            ],
            1,
        )

    def test_provider_overflow_gets_one_forced_compaction_and_one_retry(self) -> None:
        planner = _OverflowPlanner()
        metrics = MetricsRegistry()
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            runtime = CodingAgentRuntime(planner=planner, metrics=metrics)
            result = runtime.run(
                conversation_id="reactive_recovery",
                user_input="explain app.py " + "x" * 1000,
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
                focus_files=["app.py"],
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer, "Recovered after compaction.")
        self.assertEqual(planner.calls, 2)
        self.assertEqual(
            metrics.snapshot()["counters"][
                "agent_native_context_overflow_retries_total"
            ],
            1,
        )
        context_events = [
            event for event in runtime.list_events(result.run_id) if event.type == "context"
        ]
        self.assertTrue(context_events)
        self.assertTrue(all("stage" in event.output for event in context_events))

    def test_second_provider_overflow_blocks_instead_of_retrying_again(self) -> None:
        planner = _OverflowPlanner(always_overflow=True)
        metrics = MetricsRegistry()
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            result = CodingAgentRuntime(planner=planner, metrics=metrics).run(
                conversation_id="reactive_failure",
                user_input="explain app.py " + "x" * 1000,
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
                focus_files=["app.py"],
            )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(planner.calls, 2)
        plan = [item for item in result.trace if item["node"] == "plan_tools"][-1]
        self.assertEqual(plan["output"]["stop_reason"], "context_compaction_exhausted")
        self.assertEqual(
            plan["output"]["context_reduction_stages"][-1]["stage"],
            "overflow_retry_failed",
        )
        self.assertEqual(
            metrics.snapshot()["counters"][
                "agent_native_context_overflow_retry_failed_total"
            ],
            1,
        )
        self.assertEqual(
            metrics.snapshot()["counters"][
                "agent_native_context_compaction_exhausted_total"
            ],
            1,
        )

    def test_http_context_length_details_map_to_context_overflow(self) -> None:
        for detail in (
            "maximum context length is 128000 tokens",
            "context_length_exceeded",
            "prompt is too long",
            "input token count exceeds the model limit",
        ):
            with self.subTest(detail=detail):
                self.assertEqual(
                    _http_error_code(400, detail=detail),
                    "context_overflow",
                )
        self.assertEqual(
            _http_error_code(400, detail="reasoning_content is required"),
            "llm_http_error",
        )

        class _CodeOnlyResponse:
            @staticmethod
            def json():
                return {
                    "error": {
                        "code": "context_length_exceeded",
                        "message": "bad request",
                    }
                }

        coded_detail = _safe_provider_error_detail(_CodeOnlyResponse())
        self.assertIn("context_length_exceeded", coded_detail)
        self.assertEqual(
            _http_error_code(400, detail=coded_detail),
            "context_overflow",
        )


if __name__ == "__main__":
    unittest.main()
