import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from ai_agent_platform.agents.coding.models import AgentRunRecord
from ai_agent_platform.repositories.postgres import (
    PostgresAgentRunRepository,
    PostgresDocumentRepository,
    PostgresKnowledgeBaseRepository,
    PostgresSessionRepository,
    PostgresWorkspaceRepository,
    _agent_result_from_json,
)


class PostgresRepositoryTests(unittest.TestCase):
    def test_constructors_do_not_initialize_schema(self) -> None:
        database_url = "postgresql://tester:secret@localhost/test"
        with patch(
            "ai_agent_platform.repositories.postgres._require_psycopg",
            return_value=object(),
        ) as require_psycopg:
            repositories = [
                PostgresSessionRepository(database_url=database_url),
                PostgresAgentRunRepository(database_url=database_url),
                PostgresDocumentRepository(database_url=database_url),
                PostgresKnowledgeBaseRepository(database_url=database_url),
                PostgresWorkspaceRepository(database_url=database_url),
            ]
        self.assertTrue(all(item._database_url == database_url for item in repositories))
        self.assertEqual(require_psycopg.call_count, 5)

    def test_knowledge_base_catalog_maps_document_count(self) -> None:
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        row = ("docs", "Docs", "Reference", ["guide"], now, now, 3)
        connection = FakeConnection([row, [row]])
        with patch(
            "ai_agent_platform.repositories.postgres._require_psycopg",
            return_value=object(),
        ):
            repository = PostgresKnowledgeBaseRepository(
                database_url="postgresql://test"
            )
            repository._connect = lambda: connection
            loaded = repository.get("docs")
            listed = repository.list()
        self.assertEqual(loaded.document_count, 3)
        self.assertEqual(loaded.tags, ["guide"])
        self.assertEqual([item.id for item in listed], ["docs"])

    def test_workspace_upsert_get_and_list_map_rows(self) -> None:
        now = datetime(2026, 7, 23, tzinfo=timezone.utc)
        row = ("workspace_main", "/workspace/code", now, now)
        connection = FakeConnection([row, row, [row]])
        with patch(
            "ai_agent_platform.repositories.postgres._require_psycopg",
            return_value=object(),
        ):
            repository = PostgresWorkspaceRepository(database_url="postgresql://test")
            repository._connect = lambda: connection
            created = repository.upsert(
                workspace_id="workspace_main",
                root_path="/workspace/code",
            )
            loaded = repository.get("workspace_main")
            listed = repository.list()
        self.assertEqual(created.root_path, "/workspace/code")
        self.assertEqual(loaded.id, "workspace_main")
        self.assertEqual([item.id for item in listed], ["workspace_main"])
        self.assertIn("workspaces", connection.calls[0][0])

    def test_agent_run_persists_workspace_root_snapshot(self) -> None:
        connection = FakeConnection([None])
        record = AgentRunRecord(
            run_id="run_1",
            thread_id="run_1",
            conversation_id="session_1",
            workspace_id="workspace_main",
            workspace_root="/workspace/code",
            status="queued",
            checkpoint_id=None,
            latest_node=None,
            next_nodes=["setup_workspace"],
            trace=[],
        )
        with patch(
            "ai_agent_platform.repositories.postgres._require_psycopg",
            return_value=object(),
        ), patch(
            "ai_agent_platform.repositories.postgres._require_jsonb",
            return_value=lambda value: value,
        ):
            repository = PostgresAgentRunRepository(database_url="postgresql://test")
            repository._connect = lambda: connection
            repository.save(record)
        sql, params = connection.calls[0]
        self.assertIn("workspace_root", sql)
        self.assertIn("/workspace/code", params)

    def test_legacy_result_json_is_adapted_only_at_storage_boundary(self) -> None:
        result = _agent_result_from_json(
            {
                "run_id": "run_legacy",
                "thread_id": "run_legacy",
                "conversation_id": "session_1",
                "repository_id": "legacy_repo",
                "status": "completed",
                "checkpoint_id": None,
                "role": "code agent",
                "objective": "answer",
                "intent": "repository_question",
                "answer": "legacy",
                "graph_engine": "langgraph",
                "rag_context": [
                    {
                        "filename": "app.py",
                        "text": "value = 1",
                        "start_line": 1,
                        "end_line": 1,
                    }
                ],
                "tool_calls": [],
                "tool_results": [],
                "trace": [],
            }
        )
        self.assertEqual(result.workspace_id, "legacy_repo")
        self.assertEqual(result.context_route, "repo")
        self.assertEqual(result.selected_knowledge_base_ids, [])
        self.assertEqual(result.context_sources[0].kind, "legacy_index")
        self.assertEqual(result.context_sources[0].path, "app.py")


class FakeCursor:
    def __init__(self, result):
        self._result = result

    def fetchone(self):
        return self._result

    def fetchall(self):
        return list(self._result)


class FakeConnection:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        result = self._results.pop(0) if self._results else None
        return FakeCursor(result)


if __name__ == "__main__":
    unittest.main()
