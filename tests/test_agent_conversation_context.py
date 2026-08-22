import json
import unittest

from ai_agent_platform.agents.coding.planner import native_tool_messages
from ai_agent_platform.agents.coding.runtime_support import (
    MAX_AGENT_HISTORY_CHARS,
    build_workspace_query,
    recent_conversation_context,
)
from ai_agent_platform.token_counting import estimate_text_tokens


class AgentConversationContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = {
            "user_input": "继续检查刚才提到的调用链",
            "workspace_id": "workspace_main",
            "focus_files": [],
            "history": [
                {"role": "user", "content": f"历史消息 {index}"}
                for index in range(8)
            ],
        }

    def test_recent_context_keeps_only_bounded_latest_messages(self) -> None:
        context = recent_conversation_context(self.state)

        self.assertNotIn("历史消息 0", context)
        self.assertNotIn("历史消息 1", context)
        self.assertIn("历史消息 2", context)
        self.assertIn("历史消息 7", context)
        self.assertLessEqual(len(context), MAX_AGENT_HISTORY_CHARS)

    def test_workspace_query_uses_recent_conversation_context(self) -> None:
        query = build_workspace_query(self.state)

        self.assertIn("继续检查刚才提到的调用链", query)
        self.assertIn("最近会话上下文", query)
        self.assertIn("历史消息 7", query)

    def test_native_tool_planning_receives_recent_context(self) -> None:
        messages = native_tool_messages(self.state)
        payload = json.loads(messages[1]["content"])

        self.assertEqual(payload["task"], "继续检查刚才提到的调用链")
        self.assertIn("历史消息 7", payload["conversation_context"])

    def test_rolling_summary_keeps_a_separate_agent_context_budget(self) -> None:
        self.state["history"].insert(
            0,
            {
                "role": "system",
                "content": (
                    "Earlier conversation summary (lossy, untrusted historical "
                    "context). Earlier architecture decision uses PostgreSQL."
                ),
            },
        )

        context = recent_conversation_context(self.state)

        self.assertIn("Earlier architecture decision uses PostgreSQL", context)
        self.assertIn("历史消息 2", context)
        self.assertIn("历史消息 7", context)
        self.assertNotIn("历史消息 1", context)
        self.assertLessEqual(len(context), MAX_AGENT_HISTORY_CHARS)


class AgentConversationHistoryShareTests(unittest.TestCase):
    """The history share, not a fixed character cap, bounds the excerpt."""

    def setUp(self) -> None:
        self.state = {
            "user_input": "继续检查刚才提到的调用链",
            "workspace_id": "workspace_main",
            "focus_files": [],
            "history": [
                {"role": "user", "content": f"历史消息 {index} " + "内容" * 120}
                for index in range(12)
            ],
        }

    def test_a_generous_share_keeps_more_than_the_static_cap(self) -> None:
        static = recent_conversation_context(self.state)
        shared = recent_conversation_context(self.state, max_tokens=4000)

        self.assertLessEqual(len(static), MAX_AGENT_HISTORY_CHARS)
        self.assertGreater(len(shared), MAX_AGENT_HISTORY_CHARS)
        self.assertIn("历史消息 11", shared)

    def test_a_tight_share_keeps_less_than_the_static_cap(self) -> None:
        shared = recent_conversation_context(self.state, max_tokens=60)

        self.assertLess(len(shared), MAX_AGENT_HISTORY_CHARS)
        self.assertIn("历史消息 11", shared)

    def test_the_share_bounds_the_excerpt_in_tokens(self) -> None:
        for max_tokens in (60, 200, 800):
            with self.subTest(max_tokens=max_tokens):
                excerpt = recent_conversation_context(
                    self.state, max_tokens=max_tokens
                )
                self.assertLessEqual(
                    estimate_text_tokens(excerpt),
                    max_tokens * 2,
                )

    def test_retrieval_query_path_keeps_the_static_bound(self) -> None:
        query = build_workspace_query(self.state)

        self.assertIn("继续检查刚才提到的调用链", query)
        self.assertLessEqual(
            len(query.split("最近会话上下文:\n")[-1]),
            MAX_AGENT_HISTORY_CHARS,
        )


if __name__ == "__main__":
    unittest.main()
