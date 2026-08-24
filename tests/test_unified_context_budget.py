from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ai_agent_platform.agents.coding.models import ContextSource
from ai_agent_platform.agents.coding.planner import native_tool_messages
from ai_agent_platform.agents.coding.tool_loop_nodes import (
    _NativeContextBudgetPolicy,
    _native_message_groups,
)
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.core import MetricsRegistry
from ai_agent_platform.integrations.llm import ContextBudget, LLMToolDecision
from ai_agent_platform.integrations.tools import ToolRegistry
from ai_agent_platform.token_counting import estimate_text_tokens


class _ScriptedPlanner:
    uses_native_tool_calling = True
    single_tool_per_turn = False

    def __init__(self) -> None:
        self.calls = 0
        self.last_messages: list[dict[str, object]] = []

    def classify_intent(self, user_input: str) -> dict[str, object]:
        del user_input
        return {
            "intent": "code_explanation",
            "reason": "unified budget test",
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
        return LLMToolDecision(
            text="Answered.",
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


class _WindowClient:
    def __init__(self, input_tokens: int) -> None:
        self._input_tokens = input_tokens

    def resolve_context_budget(self, **kwargs) -> ContextBudget:
        del kwargs
        return ContextBudget(
            window_tokens=self._input_tokens,
            reserved_output_tokens=0,
            input_tokens=self._input_tokens,
        )


class SeedAssemblyTests(unittest.TestCase):
    def _state(self, shares: dict[str, int] | None = None) -> dict[str, object]:
        state: dict[str, object] = {
            "user_input": "explain the retry path",
            "intent": "repository_question",
            "workspace_id": "workspace_main",
            "focus_files": [],
            "context_warnings": [],
            "project_instructions": [],
            "history": [
                {
                    "role": "user",
                    "content": f"earlier question {index} " + "q" * 200,
                }
                for index in range(8)
            ],
            "context_sources": [
                ContextSource(
                    kind="knowledge_chunk",
                    path=f"doc_{index}.md",
                    start_line=None,
                    end_line=None,
                    text="evidence body " + "e" * 400,
                    reason="retrieved",
                    content_hash=f"hash_{index}",
                )
                for index in range(6)
            ],
        }
        if shares is not None:
            state["context_shares"] = shares
        return state

    def _payload(self, state: dict[str, object]) -> dict[str, object]:
        return json.loads(native_tool_messages(state)[1]["content"])

    def test_a_smaller_window_produces_a_smaller_parseable_seed(self) -> None:
        small = self._state({"evidence_tokens": 100, "history_tokens": 50})
        large = self._state({"evidence_tokens": 1000, "history_tokens": 500})

        small_message = native_tool_messages(small)[1]["content"]
        large_message = native_tool_messages(large)[1]["content"]

        self.assertLess(
            estimate_text_tokens(small_message),
            estimate_text_tokens(large_message),
        )
        self.assertEqual(json.loads(small_message)["task"], "explain the retry path")

    def test_zero_shares_remove_optional_evidence_and_history(self) -> None:
        payload = self._payload(
            self._state({"evidence_tokens": 0, "history_tokens": 0})
        )

        self.assertEqual(payload["task"], "explain the retry path")
        self.assertEqual(payload["evidence"], [])
        self.assertEqual(payload["conversation_context"], "")

    def test_evidence_drops_low_ranked_sources_before_trimming(self) -> None:
        payload = self._payload(
            self._state({"evidence_tokens": 200, "history_tokens": 60})
        )

        self.assertLess(len(payload["evidence"]), 6)
        self.assertEqual(payload["evidence"][0]["path"], "doc_0.md")
        self.assertLessEqual(
            estimate_text_tokens(json.dumps(payload["evidence"], ensure_ascii=False)),
            200,
        )

    def test_absent_shares_keep_static_fallbacks(self) -> None:
        payload = self._payload(self._state())

        self.assertEqual(len(payload["evidence"]), 6)
        self.assertGreater(len(payload["conversation_context"]), 0)


class LayeredSeedProtectionTests(unittest.TestCase):
    def test_seed_and_all_user_steering_remain_truncation_protected(self) -> None:
        groups = _native_message_groups(
            [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "{}"},
                {"role": "assistant", "content": "later"},
                {"role": "user", "content": "steer verbatim"},
            ]
        )

        self.assertTrue(groups[0].truncation_protected)
        self.assertTrue(groups[1].truncation_protected)
        self.assertFalse(groups[2].truncation_protected)
        self.assertTrue(groups[3].truncation_protected)
        self.assertIs(
            _NativeContextBudgetPolicy().truncate(
                groups[3],
                overflow_tokens=10_000,
                minimum_tokens=32,
            ),
            groups[3],
        )


class ContextBudgetTooSmallTests(unittest.TestCase):
    def _seed_for_window(self, window_tokens: int) -> str:
        planner = _ScriptedPlanner()
        with TemporaryDirectory() as temp_dir:
            result = CodingAgentRuntime(
                planner=planner,
                tool_registry=ToolRegistry(),
                metrics=MetricsRegistry(),
                llm_client=_WindowClient(window_tokens),
                max_exploration_rounds=0,
            ).run(
                conversation_id=f"window_{window_tokens}",
                user_input="explain the retry path",
                history=[
                    {
                        "role": "user",
                        "content": f"earlier question {index} " + "h" * 1000,
                    }
                    for index in range(12)
                ],
                workspace_id="workspace",
                workspace_root=temp_dir,
                focus_files=[],
            )

        self.assertEqual(result.status, "completed")
        return str(planner.last_messages[1]["content"])

    def test_same_request_gets_a_smaller_seed_on_a_smaller_window(self) -> None:
        small = self._seed_for_window(2_000)
        large = self._seed_for_window(20_000)

        self.assertLess(estimate_text_tokens(small), estimate_text_tokens(large))
        self.assertEqual(
            json.loads(small)["task"],
            json.loads(large)["task"],
        )

    def test_large_share_can_use_history_beyond_the_legacy_message_cap(self) -> None:
        planner = _ScriptedPlanner()
        with TemporaryDirectory() as temp_dir:
            result = CodingAgentRuntime(
                planner=planner,
                tool_registry=ToolRegistry(),
                metrics=MetricsRegistry(),
                llm_client=_WindowClient(200_000),
                max_exploration_rounds=0,
            ).run(
                conversation_id="history_beyond_legacy_cap",
                user_input="continue",
                history=[
                    {
                        "role": "user",
                        "content": f"history item {index} " + "h" * 200,
                    }
                    for index in range(20)
                ],
                workspace_id="workspace",
                workspace_root=temp_dir,
                focus_files=[],
            )

        self.assertEqual(result.status, "completed")
        payload = json.loads(str(planner.last_messages[1]["content"]))
        self.assertIn("history item 0", payload["conversation_context"])
        self.assertIn("history item 19", payload["conversation_context"])

    def test_tiny_window_blocks_before_provider_call(self) -> None:
        planner = _ScriptedPlanner()
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            result = CodingAgentRuntime(
                planner=planner,
                metrics=MetricsRegistry(),
                llm_client=_WindowClient(40),
            ).run(
                conversation_id="tiny_window",
                user_input="explain app.py",
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
                focus_files=["app.py"],
            )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(planner.calls, 0)
        plan = [item for item in result.trace if item["node"] == "plan_tools"][-1]
        self.assertEqual(plan["output"]["stop_reason"], "context_budget_too_small")

    def test_verbatim_seed_over_unified_budget_reports_budget_too_small(self) -> None:
        planner = _ScriptedPlanner()
        with TemporaryDirectory() as temp_dir:
            result = CodingAgentRuntime(
                planner=planner,
                tool_registry=ToolRegistry(),
                metrics=MetricsRegistry(),
                llm_client=_WindowClient(1_000),
                max_exploration_rounds=0,
            ).run(
                conversation_id="verbatim_seed_overflow",
                user_input="keep this exact " + "逐字" * 2_000,
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
                focus_files=[],
            )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(planner.calls, 0)
        plan = [item for item in result.trace if item["node"] == "plan_tools"][-1]
        self.assertEqual(plan["output"]["stop_reason"], "context_budget_too_small")

    def test_normal_window_reaches_provider(self) -> None:
        planner = _ScriptedPlanner()
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            result = CodingAgentRuntime(
                planner=planner,
                metrics=MetricsRegistry(),
                llm_client=_WindowClient(200_000),
            ).run(
                conversation_id="normal_window",
                user_input="explain app.py",
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
                focus_files=["app.py"],
            )

        self.assertEqual(result.status, "completed")
        self.assertGreaterEqual(planner.calls, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
