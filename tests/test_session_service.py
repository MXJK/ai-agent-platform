import unittest

from ai_agent_platform.agents import GameAgentRuntime
from ai_agent_platform.core import Settings
from ai_agent_platform.integrations import LLMClient
from ai_agent_platform.repositories import (
    InMemorySessionRepository,
    SessionArchivedError,
)
from ai_agent_platform.services import (
    LLMConversationCompressor,
    RuleBasedConversationCompressor,
    SessionService,
)
from ai_agent_platform.usage_ledger import UsageLedgerService


class SessionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemorySessionRepository()
        self.service = SessionService(
            repository=self.repository,
            agent_runtime=GameAgentRuntime(),
        )

    def test_copies_defaults_and_can_save_session_configuration_as_default(self) -> None:
        service = SessionService(
            repository=InMemorySessionRepository(),
            agent_runtime=GameAgentRuntime(),
            default_provider="fake",
            default_model="server-default",
            default_thinking_level="low",
        )

        first = service.create_session(user_id="user_defaults")
        updated = service.update_session(
            session_id=first.id,
            actor_user_id="user_defaults",
            provider="google",
            model="gemini-3-pro",
            thinking_level="high",
            composer_mode="agent",
            save_configuration_as_default=True,
        )
        second = service.create_session(user_id="user_defaults")

        self.assertEqual(first.provider, "fake")
        self.assertEqual(first.model, "server-default")
        self.assertEqual(updated.composer_mode, "agent")
        self.assertEqual(second.provider, "google")
        self.assertEqual(second.model, "gemini-3-pro")
        self.assertEqual(second.thinking_level, "high")
        self.assertEqual(second.composer_mode, "agent")

    def test_generates_deterministic_title_and_protects_manual_rename(self) -> None:
        session = self.service.create_session(user_id="user_title")
        source = "  A title   with\nwhitespace " + ("x" * 80)

        self.service.add_message(
            session_id=session.id,
            role="user",
            content=source,
        )
        generated = self.service.get_session(session.id)
        self.assertEqual(generated.title, " ".join(source.split())[:48])
        self.assertEqual(generated.title_source, "auto")

        self.service.update_session(
            session_id=session.id,
            actor_user_id="user_title",
            title="手工标题",
        )
        self.service.add_message(
            session_id=session.id,
            role="user",
            content="this must not replace the title",
        )
        renamed = self.service.get_session(session.id)
        self.assertEqual(renamed.title, "手工标题")
        self.assertEqual(renamed.title_source, "manual")

    def test_checkpoint_fork_copies_only_the_conversation_prefix(self) -> None:
        source = self.service.create_session(user_id="user_fork")
        self.service.add_message(source.id, "user", "earlier question")
        self.service.add_message(source.id, "assistant", "earlier answer")
        self.service.add_message(
            source.id,
            "user",
            "source run question",
            source_run_id="run_source",
        )
        self.service.add_message(
            source.id,
            "assistant",
            "source run answer",
            source_run_id="run_source",
        )

        forked = self.service.fork_session_from_run(
            source_session_id=source.id,
            source_run_id="run_source",
            actor_user_id="user_fork",
        )

        copied = self.service.list_messages(forked.id)
        self.assertEqual(
            [(item.role, item.content) for item in copied],
            [("user", "earlier question"), ("assistant", "earlier answer")],
        )
        self.assertTrue(forked.title.endswith("· 分叉"))
        self.assertEqual(forked.composer_mode, "agent")

    def test_search_cursor_and_archive_contract(self) -> None:
        first = self.service.create_session(user_id="user_list")
        self.service.add_message(first.id, "user", "alpha body")
        second = self.service.create_session(user_id="user_list")
        self.service.add_message(second.id, "user", "beta body")

        page, cursor = self.service.list_sessions_page(
            user_id="user_list",
            limit=1,
        )
        self.assertEqual([item.id for item in page], [second.id])
        self.assertIsNotNone(cursor)
        next_page, next_cursor = self.service.list_sessions_page(
            user_id="user_list",
            limit=1,
            cursor=cursor,
        )
        self.assertEqual([item.id for item in next_page], [first.id])
        self.assertIsNone(next_cursor)

        matches, _ = self.service.list_sessions_page(
            user_id="user_list",
            query="ALPHA",
        )
        self.assertEqual([item.id for item in matches], [first.id])

        self.service.update_session(
            session_id=second.id,
            actor_user_id="user_list",
            archived=True,
        )
        archived, _ = self.service.list_sessions_page(
            user_id="user_list",
            archived=True,
        )
        self.assertEqual([item.id for item in archived], [second.id])
        with self.assertRaises(SessionArchivedError):
            self.service.add_message(second.id, "user", "blocked")

    def test_empty_session_stays_out_of_history_until_first_message(self) -> None:
        previous = self.service.create_session(user_id="user_empty")
        self.service.add_message(previous.id, "user", "kept conversation")
        empty = self.service.create_session(user_id="user_empty")

        active, cursor = self.service.list_sessions_page(user_id="user_empty")
        self.assertEqual([item.id for item in active], [previous.id])
        self.assertIsNone(cursor)
        self.assertEqual(
            self.service.get_user_preferences("user_empty").last_active_session_id,
            previous.id,
        )
        with self.assertRaisesRegex(ValueError, "empty conversation"):
            self.service.activate_session(
                user_id="user_empty",
                session_id=empty.id,
            )

        self.service.add_message(empty.id, "user", "now it is history")

        active, _ = self.service.list_sessions_page(user_id="user_empty")
        self.assertEqual([item.id for item in active], [empty.id, previous.id])
        self.assertEqual(
            self.service.get_user_preferences("user_empty").last_active_session_id,
            empty.id,
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

    def test_llm_conversation_compression_enters_usage_ledger(self) -> None:
        repository = InMemorySessionRepository()
        settings = Settings(llm_provider="fake", llm_model="fake-summary")
        ledger = UsageLedgerService(repository, settings)
        service = SessionService(
            repository=repository,
            agent_runtime=GameAgentRuntime(),
            compressor=LLMConversationCompressor(
                LLMClient(settings, usage_ledger=ledger)
            ),
            summary_enabled=True,
            summary_trigger_messages=4,
            summary_keep_recent_messages=2,
            summary_max_chars=500,
            summary_max_source_chars=2000,
            usage_ledger=ledger,
        )
        session = service.create_session(user_id="user_1")
        for index in range(4):
            service.add_message(
                session_id=session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index}",
            )

        summary = service.compress_conversation(
            session_id=session.id,
            trigger_message_id="trigger_summary",
        )

        self.assertIsNotNone(summary)
        records = ledger.list_session(session.id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].operation, "conversation_compression")
        self.assertEqual(records[0].resource_id, "trigger_summary")
        self.assertGreater(records[0].total_tokens, 0)


if __name__ == "__main__":
    unittest.main()
