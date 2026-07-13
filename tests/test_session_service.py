import unittest

from ai_agent_platform.agents import GameAgentRuntime
from ai_agent_platform.repositories import InMemorySessionRepository
from ai_agent_platform.services import SessionService


class SessionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SessionService(
            repository=InMemorySessionRepository(),
            agent_runtime=GameAgentRuntime(),
        )

    def test_creates_session_and_records_user_message(self) -> None:
        session = self.service.create_session(user_id="user_1")
        messages = self.service.add_message(
            session_id=session.id,
            role="user",
            content="hello",
        )

        self.assertEqual(session.user_id, "user_1")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, "user")

    def test_can_run_agent_after_user_message(self) -> None:
        session = self.service.create_session(user_id="user_1")
        messages = self.service.add_message(
            session_id=session.id,
            role="user",
            content="攻击附近的敌人",
            run_agent=True,
        )

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1].role, "assistant")
        self.assertIn("combat.attack", messages[1].content)

    def test_gets_session_summary(self) -> None:
        session = self.service.create_session(user_id="user_1")
        self.service.add_message(
            session_id=session.id,
            role="user",
            content="hello",
        )
        self.service.add_message(
            session_id=session.id,
            role="assistant",
            content="hi, how can I help?",
        )

        summary = self.service.get_session_summary(session_id=session.id)

        self.assertEqual(summary.session_id, session.id)
        self.assertEqual(summary.message_count, 2)
        self.assertEqual(summary.last_message, "hi, how can I help?")

    def test_records_token_usage(self) -> None:
        session = self.service.create_session(user_id="user_1")

        self.service.record_token_usage(
            session_id=session.id,
            provider="fake",
            model="demo-stream-model",
            input_tokens=10,
            output_tokens=20,
        )
        usage_records = self.service.list_token_usage(session_id=session.id)

        self.assertEqual(len(usage_records), 1)
        self.assertEqual(usage_records[0].total_tokens, 30)


if __name__ == "__main__":
    unittest.main()
