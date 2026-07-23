import json
import unittest

from ai_agent_platform.agents.coding.planner import tool_planning_prompt
from ai_agent_platform.agents.coding.runtime_support import (
    MAX_AGENT_HISTORY_CHARS,
    build_workspace_query,
    recent_conversation_context,
)


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

    def test_structured_tool_planning_receives_recent_context(self) -> None:
        prompt = tool_planning_prompt(self.state, [])
        payload = json.loads(prompt.split("\n", 1)[1])

        self.assertEqual(payload["user_input"], "继续检查刚才提到的调用链")
        self.assertIn("历史消息 7", payload["conversation_context"])


if __name__ == "__main__":
    unittest.main()
