from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ai_agent_platform.agents.coding.models import CodingAgentState
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.domain import KnowledgeBaseRecord
from ai_agent_platform.integrations.rag import RetrievedDocument
from ai_agent_platform.integrations.tools import ToolCall, ToolSpec


class RoutingPlanner:
    def __init__(self, *, route: str, intent: str, selected: list[str]) -> None:
        self.route = route
        self.intent = intent
        self.selected = selected

    def classify_intent(self, user_input: str) -> dict[str, object]:
        del user_input
        return {
            "intent": self.intent,
            "reason": "test",
            "confidence": 1.0,
            "source": "test",
        }

    def classify_request(
        self,
        user_input: str,
        knowledge_bases: list[dict[str, object]],
    ) -> dict[str, object]:
        del user_input, knowledge_bases
        return {
            **self.classify_intent(""),
            "context_route": self.route,
            "route_reason": "test routing decision",
            "selected_knowledge_base_ids": self.selected,
        }

    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        del state, tool_specs
        return []

    def plan_repair_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        del state, tool_specs
        return []

    def compose_answer(self, state: CodingAgentState) -> str:
        return (
            f"route={state.get('context_route')}; "
            f"sources={len(state.get('context_sources', []))}"
        )


class FakeKnowledgeProvider:
    def __init__(self, *, count: int = 4, fail: bool = False) -> None:
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        self.records = [
            KnowledgeBaseRecord(
                id=f"kb_{index:02d}",
                name=f"Knowledge {index}",
                description=f"Reference manual {index}",
                tags=["manual"],
                document_count=1,
                created_at=now,
                updated_at=now,
            )
            for index in range(count)
        ]
        self.fail = fail

    def list(self) -> list[KnowledgeBaseRecord]:
        return self.records

    def search(
        self,
        *,
        knowledge_base_id: str,
        query: str,
        limit: int,
        recall_limit: int | None,
    ) -> list[RetrievedDocument]:
        del query, limit, recall_limit
        if self.fail:
            raise RuntimeError("vector store unavailable")
        return [
            RetrievedDocument(
                id=f"chunk_{knowledge_base_id}",
                knowledge_base_id=knowledge_base_id,
                document_id=f"doc_{knowledge_base_id}",
                filename="guide.md",
                chunk_index=0,
                text=f"{knowledge_base_id} policy evidence",
                score=0.9,
            )
        ]


class AgentContextRoutingTests(unittest.TestCase):
    def test_hybrid_filters_catalog_selection_and_merges_sources(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            provider = FakeKnowledgeProvider(count=60)
            runtime = CodingAgentRuntime(
                planner=RoutingPlanner(
                    route="hybrid",
                    intent="code_explanation",
                    selected=["missing", "kb_00", "kb_01", "kb_02", "kb_03"],
                ),
                knowledge_context_provider=provider,
                max_rag_context_chars=1000,
            )

            result = runtime.run(
                conversation_id="session",
                user_input="根据手册解释 app.py",
                history=[],
                workspace_id="workspace",
                workspace_root=str(root),
                focus_files=["app.py"],
            )

        self.assertEqual(result.context_route, "hybrid")
        self.assertEqual(
            result.selected_knowledge_base_ids,
            ["kb_00", "kb_01", "kb_02"],
        )
        self.assertEqual(
            {
                source.kind
                for source in result.context_sources
                if source.kind != "project_instruction"
            },
            {"file", "knowledge_chunk"},
        )
        classify_trace = next(
            item for item in result.trace if item["node"] == "classify_request"
        )
        self.assertEqual(classify_trace["output"]["catalog_size"], 50)
        self.assertTrue(classify_trace["output"]["catalog_truncated"])

    def test_rag_failure_degrades_to_grounded_insufficient_answer(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime = CodingAgentRuntime(
                planner=RoutingPlanner(
                    route="rag",
                    intent="repository_question",
                    selected=["kb_00"],
                ),
                knowledge_context_provider=FakeKnowledgeProvider(fail=True),
            )
            result = runtime.run(
                conversation_id="session",
                user_input="查询知识库手册",
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.context_route, "rag")
        self.assertFalse(
            any(source.kind == "knowledge_chunk" for source in result.context_sources)
        )
        retrieve_trace = next(
            item for item in result.trace if item["node"] == "retrieve_knowledge"
        )
        self.assertTrue(
            any(
                "vector store unavailable" in warning
                for warning in retrieve_trace["output"]["warnings"]
            )
        )

    def test_small_talk_skips_repo_and_rag(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime = CodingAgentRuntime(
                planner=RoutingPlanner(
                    route="repo",
                    intent="small_talk",
                    selected=[],
                ),
                knowledge_context_provider=FakeKnowledgeProvider(),
            )
            result = runtime.run(
                conversation_id="session",
                user_input="你好",
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
            )

        self.assertEqual(result.context_route, "none")
        nodes = [item["node"] for item in result.trace]
        self.assertNotIn("plan_exploration", nodes)
        self.assertNotIn("retrieve_knowledge", nodes)
        self.assertIn("merge_evidence", nodes)


if __name__ == "__main__":
    unittest.main()
