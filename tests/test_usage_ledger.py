from __future__ import annotations

import unittest

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.rag import HashingEmbeddingProvider
from ai_agent_platform.repositories import InMemorySessionRepository
from ai_agent_platform.usage_ledger import (
    TokenBudgetExceededError,
    UsageLedgerService,
    model_usage_scope,
)


class UsageLedgerTests(unittest.TestCase):
    def test_records_scoped_and_global_operations_in_one_ledger(self) -> None:
        repository = InMemorySessionRepository()
        session = repository.create_session("tester")
        ledger = UsageLedgerService(repository, Settings())

        with model_usage_scope(
            session_id=session.id,
            workspace_id="workspace_main",
            operation="chat",
            resource_id="chat_1",
        ):
            ledger.record(
                provider="fake",
                model="fake-chat-1",
                input_tokens=10,
                output_tokens=4,
            )
            HashingEmbeddingProvider(
                usage_ledger=ledger,
                model="local-hashing",
            ).embed_texts(["one two"], task_type="query")
        with model_usage_scope(
            session_id=None,
            workspace_id=None,
            operation="embedding",
            resource_id="background_index",
        ):
            ledger.record(
                provider="local",
                model="local-hashing",
                input_tokens=3,
                output_tokens=0,
            )

        session_records = ledger.list_session(session.id)
        self.assertEqual(
            [record.operation for record in session_records],
            ["chat", "embedding"],
        )
        self.assertEqual(len(ledger.list_workspace("workspace_main")), 2)
        self.assertEqual(len(ledger.list_all()), 3)
        self.assertIsNone(ledger.list_all()[-1].session_id)

    def test_reject_budget_caps_output_and_rejects_crossing_input(self) -> None:
        repository = InMemorySessionRepository()
        session = repository.create_session("tester")
        ledger = UsageLedgerService(
            repository,
            Settings(
                llm_provider="fake",
                llm_model="fake-primary",
                session_token_budget=20,
                llm_max_output_tokens=100,
            ),
        )

        with model_usage_scope(session_id=session.id):
            authorization = ledger.authorize(
                requested_provider="fake",
                requested_model="fake-primary",
                input_tokens=6,
                max_output_tokens=100,
                input_count_method="exact_test",
            )
            self.assertEqual(authorization.max_output_tokens, 14)
            with self.assertRaises(TokenBudgetExceededError):
                ledger.authorize(
                    requested_provider="fake",
                    requested_model="fake-primary",
                    input_tokens=20,
                    max_output_tokens=100,
                    input_count_method="exact_test",
                )

    def test_downgrade_selects_configured_cheap_model(self) -> None:
        repository = InMemorySessionRepository()
        session = repository.create_session("tester")
        ledger = UsageLedgerService(
            repository,
            Settings(
                llm_provider="fake",
                llm_model="fake-expensive",
                session_token_budget=10,
                token_budget_action="downgrade",
                token_budget_fallback_provider="fake",
                token_budget_fallback_model="fake-cheap",
            ),
        )

        with model_usage_scope(session_id=session.id):
            authorization = ledger.authorize(
                requested_provider="fake",
                requested_model="fake-expensive",
                input_tokens=10,
                max_output_tokens=100,
                input_count_method="exact_test",
            )

        self.assertEqual(authorization.budget_decision, "downgraded")
        self.assertEqual(authorization.provider, "fake")
        self.assertEqual(authorization.model, "fake-cheap")
        self.assertIn("session", authorization.budget_reason or "")

    def test_unscoped_calls_do_not_consume_session_or_workspace_budget(self) -> None:
        ledger = UsageLedgerService(
            InMemorySessionRepository(),
            Settings(
                session_token_budget=5,
                workspace_token_budget=5,
            ),
        )

        authorization = ledger.authorize(
            requested_provider="fake",
            requested_model="fake-chat-1",
            input_tokens=100,
            max_output_tokens=200,
            input_count_method="exact_test",
        )

        self.assertEqual(authorization.budget_decision, "allowed")
        self.assertEqual(authorization.max_output_tokens, 200)
        self.assertEqual(authorization.budget.session.limit, 0)
        self.assertEqual(authorization.budget.workspace.limit, 0)


if __name__ == "__main__":
    unittest.main()
