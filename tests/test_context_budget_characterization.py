from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import unittest

from ai_agent_platform.agents import GameAgentRuntime
from ai_agent_platform.repositories import InMemorySessionRepository
from ai_agent_platform.services import (
    RuleBasedConversationCompressor,
    SessionService,
)


GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "context_budget_chat.json").read_text(
        encoding="utf-8"
    )
)


def _service(*, summary_enabled: bool = False) -> SessionService:
    return SessionService(
        repository=InMemorySessionRepository(),
        agent_runtime=GameAgentRuntime(),
        compressor=RuleBasedConversationCompressor(),
        summary_enabled=summary_enabled,
        summary_trigger_messages=6,
        summary_keep_recent_messages=2,
        summary_max_chars=500,
        summary_max_source_chars=2000,
    )


def _snapshot(assembly) -> dict[str, object]:
    return {
        "messages": assembly.messages,
        "usage": asdict(assembly.usage),
    }


class ContextBudgetCharacterizationTests(unittest.TestCase):
    def test_chat_reduction_matches_golden(self) -> None:
        dropping = _service()
        drop_session = dropping.create_session(user_id="drop")
        for index in range(6):
            dropping.add_message(
                session_id=drop_session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index} " + "padding " * 10,
            )

        truncating = _service()
        truncate_session = truncating.create_session(user_id="truncate")
        truncating.add_message(
            session_id=truncate_session.id,
            role="user",
            content="head " + "x" * 1000 + " tail",
        )

        summarized = _service(summary_enabled=True)
        summary_session = summarized.create_session(user_id="summary")
        for index in range(6):
            summarized.add_message(
                session_id=summary_session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index} " + "padding " * 10,
            )
        summarized.compress_conversation(session_id=summary_session.id)

        actual = {
            "drop_oldest": _snapshot(
                dropping.assemble_agent_context(
                    session_id=drop_session.id,
                    max_context_messages=6,
                    max_context_tokens=120,
                )
            ),
            "truncate_single": _snapshot(
                truncating.assemble_agent_context(
                    session_id=truncate_session.id,
                    max_context_messages=6,
                    max_context_tokens=80,
                )
            ),
            "protect_summary_and_live_turn": _snapshot(
                summarized.assemble_agent_context(
                    session_id=summary_session.id,
                    max_context_messages=6,
                    max_context_tokens=100,
                    allow_sync_compaction=False,
                )
            ),
        }

        self.assertEqual(actual, GOLDEN)


if __name__ == "__main__":
    unittest.main()
