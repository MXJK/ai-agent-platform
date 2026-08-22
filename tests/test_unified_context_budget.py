from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ai_agent_platform.agents.coding.tool_loop_nodes import (
    _NativeContextBudgetPolicy,
    _NativeMessageGroup,
    _native_message_groups,
    _tool_schema_tokens,
)
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.core import MetricsRegistry
from ai_agent_platform.integrations.llm import ContextBudget
from ai_agent_platform.integrations.tools import ToolSpec


class _ScriptedPlanner:
    """Planner that answers immediately so only assembly is exercised."""

    uses_native_tool_calling = True
    single_tool_per_turn = False

    def __init__(self) -> None:
        self.calls = 0

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
        del messages, tool_specs, kwargs
        self.calls += 1
        from ai_agent_platform.integrations.llm import LLMToolDecision

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


class _TinyWindowClient:
    """LLM client whose window leaves no room once schemas are counted."""

    def __init__(self, input_tokens: int) -> None:
        self._input_tokens = input_tokens

    def resolve_context_budget(self, **kwargs) -> ContextBudget:
        del kwargs
        return ContextBudget(
            window_tokens=self._input_tokens,
            reserved_output_tokens=0,
            input_tokens=self._input_tokens,
        )


class SeedVerbatimTests(unittest.TestCase):
    """The seed is sized at assembly, so reduction never cuts through it."""

    def _seed_group(self) -> _NativeMessageGroup:
        payload = json.dumps(
            {"task": "explain", "evidence": ["body " + "e" * 800]},
            ensure_ascii=False,
        )
        groups = _native_message_groups(
            [
                {"role": "system", "content": "system prompt " + "s" * 400},
                {"role": "user", "content": payload},
                {"role": "assistant", "content": "thinking " + "t" * 400},
            ]
        )
        return groups[1]

    def test_seed_groups_are_marked_verbatim(self) -> None:
        groups = _native_message_groups(
            [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "{}"},
                {"role": "assistant", "content": "later"},
            ]
        )

        self.assertTrue(groups[0].verbatim)
        self.assertTrue(groups[1].verbatim)
        self.assertFalse(groups[2].verbatim)

    def test_truncating_the_seed_returns_it_unchanged(self) -> None:
        seed = self._seed_group()

        fitted = _NativeContextBudgetPolicy().truncate(
            seed,
            overflow_tokens=10_000,
            minimum_tokens=32,
        )

        self.assertIs(fitted, seed)

    def test_the_seed_payload_stays_parseable_after_reduction(self) -> None:
        seed = self._seed_group()

        fitted = _NativeContextBudgetPolicy().truncate(
            seed,
            overflow_tokens=10_000,
            minimum_tokens=32,
        )

        json.loads(fitted.messages[0]["content"])

    def test_non_seed_groups_are_still_truncatable(self) -> None:
        groups = _native_message_groups(
            [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "{}"},
                {"role": "assistant", "content": "thinking " + "t" * 4000},
            ]
        )

        fitted = _NativeContextBudgetPolicy().truncate(
            groups[2],
            overflow_tokens=500,
            minimum_tokens=32,
        )

        self.assertIsNot(fitted, groups[2])


class ToolSchemaOverheadTests(unittest.TestCase):
    def test_schema_tokens_grow_with_the_pool(self) -> None:
        def spec(name: str) -> ToolSpec:
            return ToolSpec(
                name=name,
                description="a tool that does something specific",
                input_schema={"type": "object", "properties": {"path": {}}},
                output_schema={"type": "object"},
                provider="test",
            )

        one = _tool_schema_tokens([spec("repo.read_file")])
        many = _tool_schema_tokens([spec(f"repo.tool_{i}") for i in range(10)])

        self.assertGreater(one, 0)
        self.assertGreater(many, one * 5)

    def test_an_empty_pool_costs_nothing(self) -> None:
        self.assertEqual(_tool_schema_tokens([]), 0)


class ContextBudgetTooSmallTests(unittest.TestCase):
    def test_a_window_with_no_transcript_room_blocks_before_calling(self) -> None:
        planner = _ScriptedPlanner()
        metrics = MetricsRegistry()
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            result = CodingAgentRuntime(
                planner=planner,
                metrics=metrics,
                llm_client=_TinyWindowClient(40),
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
        self.assertEqual(
            plan["output"]["stop_reason"], "context_budget_too_small"
        )
        self.assertIn("LLM_CONTEXT_INPUT_TOKEN_RATIO", result.answer)

    def test_a_normal_window_still_reaches_the_model(self) -> None:
        planner = _ScriptedPlanner()
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            result = CodingAgentRuntime(
                planner=planner,
                metrics=MetricsRegistry(),
                llm_client=_TinyWindowClient(200_000),
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
