import unittest

from ai_agent_platform.agents import GameAgentRuntime
from ai_agent_platform.core import Settings
from ai_agent_platform.integrations import LLMClient
from ai_agent_platform.repositories import InMemorySessionRepository
from ai_agent_platform.services import (
    RuleBasedConversationCompressor,
    SessionService,
)
from ai_agent_platform.token_counting import estimate_message_tokens


class _CountingCompressor(RuleBasedConversationCompressor):
    """Rule-based compressor that records how often it ran."""

    def __init__(self) -> None:
        super().__init__()
        object.__setattr__(self, "calls", 0)

    def compress(self, **kwargs: object) -> str:
        object.__setattr__(self, "calls", self.calls + 1)
        return super().compress(**kwargs)  # type: ignore[arg-type]


def _service(**overrides: object) -> SessionService:
    kwargs: dict[str, object] = {
        "repository": InMemorySessionRepository(),
        "agent_runtime": GameAgentRuntime(),
        "compressor": RuleBasedConversationCompressor(),
        "summary_enabled": True,
        "summary_trigger_messages": 6,
        "summary_keep_recent_messages": 2,
        "summary_max_chars": 500,
        "summary_max_source_chars": 2000,
    }
    kwargs.update(overrides)
    return SessionService(**kwargs)  # type: ignore[arg-type]


class ContextBudgetTests(unittest.TestCase):
    def test_token_budget_drops_oldest_messages_before_truncating(self) -> None:
        service = _service(summary_enabled=False)
        session = service.create_session(user_id="user_budget")
        for index in range(8):
            service.add_message(
                session_id=session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index} " + "padding " * 20,
            )

        assembly = service.assemble_agent_context(
            session_id=session.id,
            max_context_messages=8,
            max_context_tokens=200,
        )

        self.assertLessEqual(assembly.usage.estimated_tokens, 200)
        self.assertGreater(assembly.usage.dropped_messages, 0)
        self.assertEqual(assembly.usage.truncated_messages, 0)
        self.assertEqual(assembly.usage.budget_tokens, 200)
        self.assertIn("message 7", assembly.messages[-1]["content"])

    def test_single_oversized_message_is_truncated_not_dropped(self) -> None:
        service = _service(summary_enabled=False)
        session = service.create_session(user_id="user_oversized")
        service.add_message(
            session_id=session.id,
            role="user",
            content="head " + ("x" * 40000) + " tail",
        )

        assembly = service.assemble_agent_context(
            session_id=session.id,
            max_context_messages=12,
            max_context_tokens=300,
        )

        self.assertEqual(len(assembly.messages), 1)
        self.assertEqual(assembly.usage.truncated_messages, 1)
        self.assertLessEqual(assembly.usage.estimated_tokens, 300)
        content = assembly.messages[0]["content"]
        self.assertTrue(content.startswith("head "))
        self.assertTrue(content.endswith(" tail"))
        self.assertIn("truncated to fit the context budget", content)

    def test_overflow_compacts_synchronously_before_dropping_history(self) -> None:
        compressor = _CountingCompressor()
        service = _service(compressor=compressor)
        session = service.create_session(user_id="user_sync")
        for index in range(8):
            service.add_message(
                session_id=session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index} " + "padding " * 20,
            )

        assembly = service.assemble_agent_context(
            session_id=session.id,
            max_context_messages=8,
            max_context_tokens=250,
        )

        self.assertEqual(compressor.calls, 1)
        self.assertEqual(assembly.usage.synchronous_compactions, 1)
        self.assertTrue(assembly.usage.includes_summary)
        self.assertLessEqual(assembly.usage.estimated_tokens, 250)
        self.assertIsNotNone(service.get_conversation_summary(session.id))

    def test_sync_compaction_can_be_disabled(self) -> None:
        compressor = _CountingCompressor()
        service = _service(compressor=compressor, summary_sync_on_overflow=False)
        session = service.create_session(user_id="user_no_sync")
        for index in range(8):
            service.add_message(
                session_id=session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index} " + "padding " * 20,
            )

        assembly = service.assemble_agent_context(
            session_id=session.id,
            max_context_messages=8,
            max_context_tokens=250,
        )

        self.assertEqual(compressor.calls, 0)
        self.assertEqual(assembly.usage.synchronous_compactions, 0)
        self.assertLessEqual(assembly.usage.estimated_tokens, 250)

    def test_context_usage_preview_never_spends_a_model_call(self) -> None:
        compressor = _CountingCompressor()
        service = _service(compressor=compressor)
        session = service.create_session(user_id="user_preview")
        for index in range(8):
            service.add_message(
                session_id=session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index} " + "padding " * 20,
            )

        usage = service.get_context_token_usage(
            session_id=session.id,
            max_context_messages=8,
            max_context_tokens=250,
        )

        self.assertEqual(compressor.calls, 0)
        self.assertEqual(usage.synchronous_compactions, 0)
        self.assertIsNone(service.get_conversation_summary(session.id))
        self.assertLessEqual(usage.estimated_tokens, 250)

    def test_summary_boundary_survives_a_deleted_message(self) -> None:
        repository = InMemorySessionRepository()
        service = _service(repository=repository)
        session = service.create_session(user_id="user_align")
        for index in range(6):
            service.add_message(
                session_id=session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index}",
            )
        summary = service.compress_conversation(session_id=session.id)
        assert summary is not None
        self.assertEqual(summary.summarized_message_count, 4)

        # An earlier message disappears, so the stored offset now points one
        # message too far: identity alignment must win over the count.
        del repository._messages[session.id][0]

        assembly = service.assemble_agent_context(
            session_id=session.id,
            max_context_messages=12,
        )

        self.assertTrue(assembly.usage.summary_realigned)
        self.assertEqual(
            [item["content"] for item in assembly.messages[1:]],
            ["message 4", "message 5"],
        )

    def test_message_ceiling_only_grows_within_the_token_budget(self) -> None:
        service = _service(summary_enabled=False)
        session = service.create_session(user_id="user_ceiling")
        for index in range(30):
            service.add_message(
                session_id=session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index}",
            )

        capped = service.assemble_agent_context(
            session_id=session.id,
            max_context_messages=12,
            max_context_messages_ceiling=24,
        )
        widened = service.assemble_agent_context(
            session_id=session.id,
            max_context_messages=12,
            max_context_tokens=100000,
            max_context_messages_ceiling=24,
        )
        squeezed = service.assemble_agent_context(
            session_id=session.id,
            max_context_messages=12,
            max_context_tokens=120,
            max_context_messages_ceiling=24,
        )

        self.assertEqual(len(capped.messages), 12)
        self.assertEqual(len(widened.messages), 24)
        self.assertLess(len(squeezed.messages), 24)
        self.assertLessEqual(squeezed.usage.estimated_tokens, 120)

    def test_chat_context_reserves_room_for_the_live_turn(self) -> None:
        service = _service(summary_enabled=False)
        session = service.create_session(user_id="user_reserved")
        for index in range(6):
            service.add_message(
                session_id=session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index} " + "padding " * 10,
            )

        assembly = service.assemble_chat_context(
            session_id=session.id,
            user_message="the live question " + "word " * 40,
            max_context_messages=6,
            max_context_tokens=250,
            reserved_tokens=40,
        )

        self.assertEqual(assembly.messages[-1]["role"], "user")
        self.assertLessEqual(
            estimate_message_tokens(assembly.messages) + 40,
            250,
        )
        self.assertEqual(
            assembly.usage.estimated_tokens,
            estimate_message_tokens(assembly.messages),
        )

    def test_budget_accounting_matches_the_reported_estimate(self) -> None:
        service = _service(summary_enabled=False)
        session = service.create_session(user_id="user_exact")
        for index in range(20):
            service.add_message(
                session_id=session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index} " + "padding " * 15,
            )

        for budget in (120, 260, 500, 900):
            with self.subTest(budget=budget):
                assembly = service.assemble_agent_context(
                    session_id=session.id,
                    max_context_messages=20,
                    max_context_tokens=budget,
                )
                self.assertEqual(
                    assembly.usage.estimated_tokens,
                    estimate_message_tokens(assembly.messages),
                )
                self.assertLessEqual(assembly.usage.estimated_tokens, budget)

    def test_context_budget_follows_the_routed_model_window(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="fake",
                llm_model="fake-model",
                llm_model_context_window_tokens=32000,
                llm_max_output_tokens=4096,
                llm_context_input_token_ratio=0.5,
            )
        )

        budget = client.resolve_context_budget()

        self.assertEqual(budget.window_tokens, 32000)
        self.assertEqual(budget.reserved_output_tokens, 4096)
        self.assertEqual(budget.input_tokens, 16000 - 4096)
        self.assertEqual(budget.provider, "fake")

    def test_context_budget_ratio_override_scales_the_allowance(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="fake",
                llm_model="fake-model",
                llm_model_context_window_tokens=100000,
                llm_max_output_tokens=1000,
            )
        )

        self.assertEqual(
            client.resolve_context_budget(input_token_ratio=0.25).input_tokens,
            24000,
        )


if __name__ == "__main__":
    unittest.main()
