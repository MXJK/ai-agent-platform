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
from ai_agent_platform.project_memory import ProjectMemory, RetrievedMemory


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
        self.list_calls = 0

    def list(self) -> list[KnowledgeBaseRecord]:
        self.list_calls += 1
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


class FakeMemoryProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def retrieve(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        query: str,
    ) -> list[RetrievedMemory]:
        del actor_user_id, query
        self.calls += 1
        if self.fail:
            raise RuntimeError("memory vector unavailable")
        now = datetime(2026, 7, 30, tzinfo=timezone.utc)
        memory = ProjectMemory(
            id="mem_0000000000000001",
            workspace_id=workspace_id,
            workspace_revision=1,
            kind="architecture_fact",
            title="Historical value",
            content="Historically VALUE was 0; verify against live source.",
            canonical_key="architecture_fact:historical-value",
            status="active",
            confidence=0.9,
            importance=3,
            version=1,
            created_by="tester",
            created_at=now,
            updated_at=now,
            last_confirmed_at=now,
        )
        return [RetrievedMemory(memory=memory, score=0.02)]


class AgentContextRoutingTests(unittest.TestCase):
    def test_isolated_eval_does_not_list_global_kbs_or_read_project_memory(self) -> None:
        knowledge = FakeKnowledgeProvider()
        memory = FakeMemoryProvider()
        runtime = CodingAgentRuntime(
            planner=RoutingPlanner(
                route="rag", intent="repository_question", selected=["kb_00"],
            ),
            knowledge_context_provider=knowledge,
            project_memory_provider=memory,
        )
        state: CodingAgentState = {
            "user_input": "global product documentation",
            "workspace_id": "eval_workspace",
            "actor_user_id": "real_owner",
            "context_warnings": [],
            "evaluation_isolated": True,
            "evaluation_knowledge_base_ids": [],
            "intent": "repository_question",
            "trace": [],
        }

        classified = runtime._context_nodes._classify_request(state)
        memory_result = runtime._context_nodes._retrieve_project_memory(state)

        self.assertEqual(knowledge.list_calls, 0)
        self.assertEqual(memory.calls, 0)
        self.assertEqual(classified["knowledge_base_catalog"], [])
        self.assertEqual(classified["selected_knowledge_base_ids"], [])
        self.assertEqual(classified["context_route"], "repo")
        self.assertEqual(memory_result["memory_context_sources"], [])

    def test_generic_project_overview_prefers_live_repo_over_unrelated_rag(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text(
                "# Live project\nThis is the current workspace.\n",
                encoding="utf-8",
            )
            provider = FakeKnowledgeProvider(count=1)
            runtime = CodingAgentRuntime(
                planner=RoutingPlanner(
                    route="rag",
                    intent="repository_question",
                    selected=["kb_00"],
                ),
                knowledge_context_provider=provider,
            )

            result = runtime.run(
                conversation_id="session",
                user_input="这个项目是干什么的？",
                history=[],
                workspace_id="workspace",
                workspace_root=str(root),
            )

        self.assertEqual(result.context_route, "repo")
        self.assertEqual(result.selected_knowledge_base_ids, [])
        self.assertIn(
            "README.md",
            [source.path for source in result.context_sources],
        )
        self.assertFalse(
            any(source.kind == "knowledge_chunk" for source in result.context_sources)
        )
        route_trace = next(
            item for item in result.trace if item["node"] == "decide_context_source"
        )
        self.assertIn(
            "live workspace entry files",
            route_trace["output"]["route_reason"],
        )

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
            memory_provider = FakeMemoryProvider()
            runtime = CodingAgentRuntime(
                planner=RoutingPlanner(
                    route="repo",
                    intent="small_talk",
                    selected=[],
                ),
                knowledge_context_provider=FakeKnowledgeProvider(),
                project_memory_provider=memory_provider,
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
        self.assertEqual(memory_provider.calls, 0)

    def test_project_memory_is_orthogonal_and_live_repo_evidence_is_retained(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            memory_provider = FakeMemoryProvider()
            runtime = CodingAgentRuntime(
                planner=RoutingPlanner(
                    route="repo",
                    intent="code_explanation",
                    selected=[],
                ),
                project_memory_provider=memory_provider,
            )
            result = runtime.run(
                conversation_id="session",
                user_input="app.py 当前的 VALUE 是什么？",
                history=[],
                workspace_id="workspace",
                workspace_root=str(root),
                focus_files=["app.py"],
            )

        self.assertEqual(memory_provider.calls, 1)
        sources = {item.kind: item for item in result.context_sources}
        self.assertIn("project_memory", sources)
        self.assertIn("file", sources)
        self.assertEqual(sources["project_memory"].memory_kind, "architecture_fact")
        self.assertEqual(sources["project_memory"].confidence, 0.9)
        self.assertIn("VALUE = 1", sources["file"].text)
        nodes = [item["node"] for item in result.trace]
        self.assertLess(
            nodes.index("retrieve_project_memory"),
            nodes.index("plan_exploration"),
        )

    def test_memory_failure_does_not_fail_agent_answer(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime = CodingAgentRuntime(
                planner=RoutingPlanner(
                    route="none",
                    intent="repository_question",
                    selected=[],
                ),
                project_memory_provider=FakeMemoryProvider(fail=True),
            )
            result = runtime.run(
                conversation_id="session",
                user_input="summarize the project",
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.context_route, "repo")
        trace = next(
            item
            for item in result.trace
            if item["node"] == "retrieve_project_memory"
        )
        self.assertTrue(
            any(
                "memory vector unavailable" in warning
                for warning in trace["output"]["warnings"]
            )
        )


if __name__ == "__main__":
    unittest.main()
