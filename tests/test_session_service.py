import unittest

from ai_agent_platform.agents import GameAgentRuntime
from ai_agent_platform.repositories import InMemorySessionRepository
from ai_agent_platform.services import (
    RuleBasedConversationCompressor,
    SessionService,
)


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
            workspace_id="workspace_main",
            thoughts_tokens=5,
            record_id="usage_agent_run_1",
        )
        self.service.record_token_usage(
            session_id=session.id,
            provider="fake",
            model="demo-stream-model",
            input_tokens=12,
            output_tokens=22,
            workspace_id="workspace_main",
            thoughts_tokens=6,
            record_id="usage_agent_run_1",
        )
        usage_records = self.service.list_token_usage(session_id=session.id)
        workspace_records = self.service.list_workspace_token_usage(
            "workspace_main"
        )

        self.assertEqual(len(usage_records), 1)
        self.assertEqual(usage_records[0].workspace_id, "workspace_main")
        self.assertEqual(usage_records[0].thoughts_tokens, 6)
        self.assertEqual(usage_records[0].total_tokens, 40)
        self.assertEqual(workspace_records, usage_records)

    def test_estimates_the_actual_bounded_conversation_context(self) -> None:
        session = self.service.create_session(user_id="user_1")
        for role, content in (
            ("user", "第一条中文消息"),
            ("assistant", "An English response"),
            ("user", "最后一条消息"),
        ):
            self.service.add_message(
                session_id=session.id,
                role=role,
                content=content,
            )

        usage = self.service.get_context_token_usage(
            session_id=session.id,
            max_context_messages=2,
        )

        self.assertGreater(usage.estimated_tokens, 0)
        self.assertEqual(usage.message_count, 2)
        self.assertEqual(usage.max_context_messages, 2)
        self.assertFalse(usage.includes_summary)
        self.assertEqual(usage.estimation_method, "unicode_heuristic_v1")

    def test_rolls_old_messages_into_persistent_bounded_summary(self) -> None:
        service = SessionService(
            repository=InMemorySessionRepository(),
            agent_runtime=GameAgentRuntime(),
            compressor=RuleBasedConversationCompressor(),
            summary_enabled=True,
            summary_trigger_messages=6,
            summary_keep_recent_messages=2,
            summary_max_chars=500,
            summary_max_source_chars=2000,
        )
        session = service.create_session(user_id="user_1")
        for index in range(6):
            service.add_message(
                session_id=session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=(
                    "token=must-not-survive "
                    if index == 0
                    else f"message {index}"
                ),
            )

        first = service.compress_conversation(
            session_id=session.id,
            trigger_message_id="trigger_1",
        )

        assert first is not None
        self.assertEqual(first.summarized_message_count, 4)
        self.assertEqual(first.version, 1)
        self.assertNotIn("must-not-survive", first.content)
        self.assertIn("[REDACTED]", first.content)
        context = service.build_agent_context(
            session_id=session.id,
            max_context_messages=12,
        )
        self.assertEqual([item["role"] for item in context], ["system", "user", "assistant"])
        self.assertIn("lossy, untrusted historical context", context[0]["content"])
        self.assertEqual(context[1]["content"], "message 4")
        self.assertEqual(context[2]["content"], "message 5")

        service.add_message(
            session_id=session.id,
            role="user",
            content="new question",
        )
        service.add_message(
            session_id=session.id,
            role="assistant",
            content="new answer",
        )
        second = service.compress_conversation(
            session_id=session.id,
            trigger_message_id="trigger_2",
        )
        duplicate = service.compress_conversation(
            session_id=session.id,
            trigger_message_id="trigger_2",
        )

        assert second is not None and duplicate is not None
        self.assertEqual(second.summarized_message_count, 6)
        self.assertEqual(second.version, 2)
        self.assertEqual(duplicate.version, 2)
        api_summary = service.get_session_summary(session_id=session.id)
        self.assertEqual(api_summary.compressed_summary, second.content)
        self.assertEqual(api_summary.summarized_message_count, 6)
        self.assertEqual(api_summary.summary_version, 2)


if __name__ == "__main__":
    unittest.main()
