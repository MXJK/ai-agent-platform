import json
import unittest

from ai_agent_platform.agents.coding.planner import native_tool_messages
from ai_agent_platform.agents.coding.runtime_support import (
    CONVERSATION_SUMMARY_PREFIX,
    MAX_AGENT_HISTORY_CHARS,
    build_repository_discovery_queries,
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

    def test_repository_discovery_queries_exclude_system_and_assistant_roles(self) -> None:
        self.state["model_target_terms"] = ["扫雷"]
        self.state["history"] = [
            {"role": "system", "content": "profile mentions system-ui"},
            {"role": "assistant", "content": "inspect gomoku.css"},
            {"role": "user", "content": "继续完成这个小游戏"},
        ]

        queries = build_repository_discovery_queries(self.state)

        self.assertEqual(queries[0], "扫雷")
        self.assertNotIn("system-ui", "\n".join(queries))
        self.assertNotIn("gomoku.css", "\n".join(queries))

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


class AgentConversationTokenShareTests(unittest.TestCase):
    def test_long_ascii_message_uses_share_without_legacy_280_char_cap(self) -> None:
        state = {
            "history": [
                {
                    "role": "user",
                    "content": "ascii-head " + "a" * 4_000 + " ascii-tail",
                }
            ]
        }

        context = recent_conversation_context(state, max_tokens=400)

        self.assertLessEqual(estimate_text_tokens(context), 400)
        self.assertGreater(len(context), 1_000)
        self.assertTrue(context.startswith("user: ascii-head"))
        self.assertTrue(context.endswith("ascii-tail"))

    def test_long_chinese_message_uses_exact_share_without_legacy_cap(self) -> None:
        state = {
            "history": [
                {
                    "role": "assistant",
                    "content": "中文开头" + "内容" * 500 + "中文结尾",
                }
            ]
        }

        context = recent_conversation_context(state, max_tokens=400)

        self.assertLessEqual(estimate_text_tokens(context), 400)
        self.assertGreater(len(context), 280)
        self.assertTrue(context.startswith("assistant: 中文开头"))
        self.assertTrue(context.endswith("中文结尾"))

    def test_long_summary_uses_token_share_not_fixed_600_char_snippet(self) -> None:
        state = {
            "history": [
                {
                    "role": "system",
                    "content": (
                        CONVERSATION_SUMMARY_PREFIX
                        + "summary-head "
                        + "s" * 4_000
                        + " summary-tail"
                    ),
                }
            ]
        }

        context = recent_conversation_context(state, max_tokens=400)

        self.assertLessEqual(estimate_text_tokens(context), 400)
        self.assertGreater(len(context), 1_000)
        self.assertTrue(context.startswith("system: " + CONVERSATION_SUMMARY_PREFIX))
        self.assertTrue(context.endswith("summary-tail"))

if __name__ == "__main__":
    unittest.main()
