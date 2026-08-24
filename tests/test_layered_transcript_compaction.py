from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ai_agent_platform.agents.coding.models import AgentRunRecord
from ai_agent_platform.agents.coding.store import InMemoryAgentRunStore
from ai_agent_platform.agents.coding.tool_loop_nodes import (
    _fold_native_messages,
    _native_messages_tokens,
    _native_tool_pair_error,
    _reduce_native_messages,
)
from ai_agent_platform.agents.coding_agent import (
    CodingAgentRuntime,
    _checkpoint_branch_trace,
)
from ai_agent_platform.core import MetricsRegistry
from ai_agent_platform.integrations.llm import (
    LLMProviderError,
    LLMToolDecision,
    _http_error_code,
    _safe_provider_error_detail,
)
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry


def _transcript(
    rounds: int,
    *,
    body_chars: int = 400,
    calls_per_round: int = 1,
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "task"},
    ]
    for round_index in range(rounds):
        calls = [
            {
                "call_id": f"c{round_index}_{call_index}",
                "name": "demo.lookup",
                "arguments": {"query": call_index},
            }
            for call_index in range(calls_per_round)
        ]
        messages.append(
            {
                "role": "assistant",
                "content": f"reason {round_index}",
                "tool_calls": calls,
            }
        )
        messages.extend(
            {
                "role": "tool",
                "call_id": call["call_id"],
                "name": "demo.lookup",
                "content": {
                    "ok": True,
                    "result": {"text": "x" * body_chars + str(call["call_id"])},
                },
                "is_error": False,
            }
            for call in calls
        )
    return messages


class _OverflowPlanner:
    uses_native_tool_calling = True
    single_tool_per_turn = False

    def __init__(
        self,
        *,
        always_overflow: bool = False,
        never_call: bool = False,
        overflow_first: bool = True,
    ):
        self.always_overflow = always_overflow
        self.never_call = never_call
        self.overflow_first = overflow_first
        self.calls = 0
        self.last_messages: list[dict[str, object]] = []

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
        del tool_specs, kwargs
        self.calls += 1
        self.last_messages = list(messages)
        if self.never_call:
            raise AssertionError("provider must not be called after preflight exhaustion")
        if (self.calls == 1 and self.overflow_first) or self.always_overflow:
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


class _OverflowAfterToolPlanner(_OverflowPlanner):
    def decide_tool_calls(self, messages, tool_specs, **kwargs):
        del tool_specs, kwargs
        self.calls += 1
        self.last_messages = list(messages)
        if self.calls == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="large_result",
                        name="demo.large_result",
                        arguments={},
                    )
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        if self.calls == 2 or self.always_overflow:
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


def _large_result_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        "demo.large_result",
        lambda: {"text": "x" * 6000},
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    return registry


class LayeredTranscriptCompactionTests(unittest.TestCase):
    def test_fold_keeps_old_user_steering_exact_beyond_keep_window(self) -> None:
        steering = "old-steering-" + "逐字保留" * 200
        messages = _transcript(6, body_chars=500)
        messages.insert(4, {"role": "user", "content": steering})

        folded, compactions, _ = _fold_native_messages(
            messages,
            max_chars=100_000,
            max_tokens=0,
            keep_messages=2,
            previous_compactions=0,
            force=True,
        )

        self.assertEqual(compactions, 1)
        self.assertEqual(
            [
                message["content"]
                for message in folded
                if message.get("role") == "user" and "old-steering" in str(message.get("content"))
            ],
            [steering],
        )

    def test_fold_keeps_multiple_interleaved_user_messages_exact_and_ordered(self) -> None:
        first = "first-interleaved-" + "甲" * 500
        second = "second-interleaved-" + "乙" * 500
        messages = _transcript(6, body_chars=500)
        messages.insert(4, {"role": "user", "content": first})
        messages.insert(9, {"role": "user", "content": second})

        folded, compactions, _ = _fold_native_messages(
            messages,
            max_chars=100_000,
            max_tokens=0,
            keep_messages=2,
            previous_compactions=0,
            force=True,
        )

        self.assertEqual(compactions, 1)
        self.assertEqual(
            [
                message["content"]
                for message in folded
                if message.get("role") == "user"
                and "interleaved" in str(message.get("content"))
            ],
            [first, second],
        )
        timeline = []
        for message in folded[2:]:
            content = str(message.get("content") or "")
            if content.startswith("Earlier native tool transcript summary"):
                timeline.append("summary")
            elif content == first:
                timeline.append("first")
            elif content == second:
                timeline.append("second")
        self.assertEqual(
            timeline,
            ["summary", "first", "summary", "second", "summary"],
        )

    def test_microcompact_keeps_pairs_and_only_evicts_old_tool_bodies(self) -> None:
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

        self.assertEqual(
            [stage["stage"] for stage in reduction.stages],
            ["tool_result_eviction"],
        )
        self.assertEqual(reduction.stages[0]["evicted"], 6)
        self.assertEqual(reduction.compactions, 0)
        self.assertFalse(reduction.exhausted)
        self.assertEqual(len(reduction.messages), len(messages))
        self.assertEqual(_native_tool_pair_error(reduction.messages), "")
        assistants = [
            message
            for message in reduction.messages
            if message.get("role") == "assistant"
        ]
        self.assertEqual(
            [message["content"] for message in assistants],
            [f"reason {index}" for index in range(8)],
        )
        tools = [
            message for message in reduction.messages if message.get("role") == "tool"
        ]
        self.assertTrue(all(tool.get("is_error") is False for tool in tools))
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
        self.assertEqual(_native_tool_pair_error(reduction.messages), "")

    def test_multi_call_assistant_group_is_atomic_under_drop_and_truncate(self) -> None:
        reduction = _reduce_native_messages(
            _transcript(6, calls_per_round=3),
            max_chars=100_000,
            max_tokens=420,
            keep_messages=5,
            tool_result_keep_recent=2,
            previous_compactions=0,
            max_compactions=3,
        )

        self.assertFalse(reduction.exhausted)
        self.assertEqual(_native_tool_pair_error(reduction.messages), "")
        self.assertTrue(
            any(stage["dropped"] or stage["truncated"] for stage in reduction.stages)
        )
        for index, message in enumerate(reduction.messages):
            if message.get("role") != "assistant" or not message.get("tool_calls"):
                continue
            expected = {
                call["call_id"] for call in message["tool_calls"]
            }
            observed: set[str] = set()
            cursor = index + 1
            while (
                cursor < len(reduction.messages)
                and reduction.messages[cursor].get("role") == "tool"
            ):
                observed.add(str(reduction.messages[cursor].get("call_id")))
                cursor += 1
            self.assertEqual(observed, expected)

    def test_tool_truncation_preserves_artifact_metadata(self) -> None:
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
        content = reduction.messages[-1]["content"]
        self.assertTrue(content["truncated"])
        self.assertEqual(content["artifact_id"], "artifact_123")
        self.assertTrue(content["ok"])
        self.assertNotIn('"preview": "{\\"truncated\\"', content["preview"])

    def test_verbatim_user_steering_is_never_truncated(self) -> None:
        direction = "restore-direction-" + "z" * 5000
        messages = _transcript(4, body_chars=600)
        messages.append(
            {
                "role": "user",
                "content": "User steering for the active run: " + direction,
            }
        )

        reduction = _reduce_native_messages(
            messages,
            max_chars=100_000,
            max_tokens=180,
            keep_messages=4,
            tool_result_keep_recent=1,
            previous_compactions=3,
            max_compactions=3,
            force=True,
        )

        self.assertTrue(reduction.exhausted)
        steering = next(
            message for message in reduction.messages if message.get("role") == "user"
            and "restore-direction" in str(message.get("content"))
        )
        self.assertEqual(
            steering["content"],
            "User steering for the active run: " + direction,
        )

    def test_queued_restore_direction_reaches_planner_verbatim_under_pressure(self) -> None:
        direction = "checkpoint-direction-rollback-" + "逐字保留" * 80
        planner = _OverflowPlanner(overflow_first=False)
        runtime = CodingAgentRuntime(
            planner=planner,
            native_context_max_chars=2200,
            native_max_compactions=1,
        )
        record = AgentRunRecord(
            run_id="run_restore_pressure",
            thread_id="run_restore_pressure",
            conversation_id="session",
            workspace_id="workspace",
            workspace_root="/workspace",
            status="running",
            checkpoint_id="checkpoint",
            latest_node="plan_tools",
            next_nodes=["plan_tools"],
            trace=[],
            steering_messages=[direction],
        )
        runtime._run_store.save(record)
        state = {
            "run_id": record.run_id,
            "conversation_id": record.conversation_id,
            "workspace_id": record.workspace_id,
            "workspace_root": record.workspace_root,
            "authorized_workspace_root": record.workspace_root,
            "workspace_role": "owner",
            "actor_user_id": "demo_user",
            "approval_policy": "never",
            "enabled_tools": [],
            "native_tool_messages": _transcript(6, body_chars=900),
            "native_context_compactions": 1,
            "native_tool_round": 0,
            "native_tool_call_count": 0,
            "native_tool_signatures": [],
            "native_soft_limit_warned": False,
            "native_no_progress_rounds": 0,
            "native_unfulfilled_change_rounds": 0,
            "native_consecutive_failures": 0,
            "context_warnings": [],
            "intent": "code_explanation",
            "tool_calls": [],
            "tool_results": [],
            "trace": [],
        }

        update = runtime._tool_loop_nodes._plan_tools(state)

        expected = "User steering for the active run: " + direction
        self.assertEqual(
            next(
                str(message.get("content") or "")
                for message in planner.last_messages
                if "checkpoint-direction" in str(message.get("content") or "")
            ),
            expected,
        )
        self.assertEqual(update["terminal_status"], "completed")
        self.assertEqual(runtime.get_run(record.run_id).steering_messages, [])

    def test_multiple_queued_steering_messages_block_instead_of_dropping_first(self) -> None:
        first = "first-steering-" + "甲" * 700
        second = "second-steering-" + "乙" * 700
        planner = _OverflowPlanner(never_call=True)
        runtime = CodingAgentRuntime(
            planner=planner,
            native_context_max_chars=1200,
            native_max_compactions=1,
        )
        record = AgentRunRecord(
            run_id="run_multiple_steering",
            thread_id="run_multiple_steering",
            conversation_id="session",
            workspace_id="workspace",
            workspace_root="/workspace",
            status="running",
            checkpoint_id="checkpoint",
            latest_node="plan_tools",
            next_nodes=["plan_tools"],
            trace=[],
            steering_messages=[first, second],
        )
        runtime._run_store.save(record)
        state = {
            "run_id": record.run_id,
            "conversation_id": record.conversation_id,
            "workspace_id": record.workspace_id,
            "workspace_root": record.workspace_root,
            "authorized_workspace_root": record.workspace_root,
            "workspace_role": "owner",
            "actor_user_id": "demo_user",
            "approval_policy": "never",
            "enabled_tools": [],
            "native_tool_messages": _transcript(4, body_chars=500),
            "native_context_compactions": 1,
            "native_tool_round": 0,
            "native_tool_call_count": 0,
            "native_tool_signatures": [],
            "native_soft_limit_warned": False,
            "native_no_progress_rounds": 0,
            "native_unfulfilled_change_rounds": 0,
            "native_consecutive_failures": 0,
            "context_warnings": [],
            "intent": "code_explanation",
            "tool_calls": [],
            "tool_results": [],
            "trace": [],
        }

        update = runtime._tool_loop_nodes._plan_tools(state)

        self.assertEqual(update["terminal_status"], "blocked")
        self.assertEqual(
            update["terminal_reason"],
            "context_compaction_exhausted",
        )
        self.assertEqual(planner.calls, 0)
        user_contents = [
            str(message.get("content") or "")
            for message in update["native_tool_messages"]
            if message.get("role") == "user"
        ]
        self.assertIn("User steering for the active run: " + first, user_contents)
        self.assertIn("User steering for the active run: " + second, user_contents)
        self.assertEqual(runtime.get_run(record.run_id).steering_messages, [])

    def test_invalid_multi_call_boundary_blocks_before_reduction(self) -> None:
        messages = _transcript(1, calls_per_round=2)
        messages.pop()

        reduction = _reduce_native_messages(
            messages,
            max_chars=100_000,
            max_tokens=10_000,
            keep_messages=4,
            tool_result_keep_recent=2,
            previous_compactions=0,
            max_compactions=3,
        )

        self.assertTrue(reduction.exhausted)
        self.assertEqual(reduction.stages[0]["stage"], "invalid_transcript")
        self.assertIn("mismatch", reduction.stages[0]["detail"])

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
        planner = _OverflowAfterToolPlanner()
        metrics = MetricsRegistry()
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            runtime = CodingAgentRuntime(
                tool_registry=_large_result_registry(),
                planner=planner,
                metrics=metrics,
            )
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
        self.assertEqual(planner.calls, 3)
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

    def test_second_provider_overflow_blocks_without_a_second_retry(self) -> None:
        planner = _OverflowAfterToolPlanner(always_overflow=True)
        metrics = MetricsRegistry()
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            result = CodingAgentRuntime(
                tool_registry=_large_result_registry(),
                planner=planner,
                metrics=metrics,
            ).run(
                conversation_id="reactive_failure",
                user_input="explain app.py " + "x" * 1000,
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
                focus_files=["app.py"],
            )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(planner.calls, 3)
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


class ContextStageEventTests(unittest.TestCase):
    def test_context_stage_events_are_idempotent_and_cursor_stable(self) -> None:
        store = InMemoryAgentRunStore()
        record = AgentRunRecord(
            run_id="run_context",
            thread_id="run_context",
            conversation_id="session",
            workspace_id="workspace",
            workspace_root="/workspace",
            status="running",
            checkpoint_id="checkpoint_1",
            latest_node="plan_tools",
            next_nodes=["inspect_repository"],
            trace=[
                {
                    "step": 0,
                    "node": "plan_tools",
                    "summary": "reduced",
                    "output": {
                        "context_reduction_stages": [
                            {
                                "stage": "tool_result_eviction",
                                "evicted": 2,
                                "fits": True,
                            },
                            {"stage": "fold", "compacted": 1, "fits": True},
                        ]
                    },
                }
            ],
        )

        store.save(record)
        first = store.list_events(record.run_id)
        store.save(replace(record, checkpoint_id="checkpoint_2"))
        second = store.list_events(record.run_id)

        first_context = [event for event in first if event.type == "context"]
        second_context = [event for event in second if event.type == "context"]
        self.assertEqual(len(first_context), 2)
        self.assertEqual(second_context, first_context)
        self.assertEqual(
            [event.output["stage"] for event in first_context],
            ["tool_result_eviction", "fold"],
        )
        cursor = first_context[0].sequence
        after = store.list_events(record.run_id, after=cursor)
        self.assertEqual(
            [event.sequence for event in after if event.type == "context"],
            [first_context[1].sequence],
        )

    def test_checkpoint_branch_marks_inherited_context_stages_as_replayed(self) -> None:
        source_trace = [
            {
                "step": 3,
                "node": "plan_tools",
                "summary": "source compaction",
                "output": {
                    "context_reduction_stages": [
                        {"stage": "fold", "compacted": 1, "fits": True}
                    ]
                },
            }
        ]
        branch_trace = _checkpoint_branch_trace(
            source_trace,
            source_run_id="run_source",
            source_checkpoint_id="checkpoint_source",
        )
        store = InMemoryAgentRunStore()
        branch = AgentRunRecord(
            run_id="run_branch",
            thread_id="run_branch",
            conversation_id="session",
            workspace_id="workspace",
            workspace_root="/workspace",
            status="running",
            checkpoint_id="checkpoint_branch",
            latest_node="plan_tools",
            next_nodes=["inspect_repository"],
            trace=branch_trace,
        )

        store.save(branch)
        context = next(
            event for event in store.list_events(branch.run_id) if event.type == "context"
        )

        self.assertTrue(context.output["replayed"])
        self.assertEqual(context.output["replayed_from_run_id"], "run_source")
        self.assertEqual(
            context.output["replayed_from_checkpoint_id"],
            "checkpoint_source",
        )
        self.assertNotIn(
            "replayed",
            source_trace[0]["output"]["context_reduction_stages"][0],
        )


if __name__ == "__main__":
    unittest.main()
