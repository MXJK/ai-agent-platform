import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from ai_agent_platform.agents.coding.models import AgentRunRecord
from ai_agent_platform.domain import ConversationSummary
from ai_agent_platform.integrations.rag import (
    DocumentChunk,
    IndexJob,
    ParsedDocument,
)
from ai_agent_platform.repositories.postgres import (
    PostgresAgentRunRepository,
    PostgresDocumentRepository,
    PostgresKnowledgeBaseRepository,
    PostgresSessionRepository,
    PostgresWorkspaceRepository,
    _agent_result_from_json,
)
from ai_agent_platform.project_memory.models import (
    MemoryAuditEvent,
    MemoryEvidence,
    ProjectMemory,
)
from ai_agent_platform.repositories.project_memory import (
    PostgresProjectMemoryRepository,
)


class PostgresRepositoryTests(unittest.TestCase):
    def test_conversation_summary_upsert_uses_optimistic_version(self) -> None:
        now = datetime(2026, 7, 30, tzinfo=timezone.utc)
        summary = ConversationSummary(
            session_id="sess_1",
            content="The user chose PostgreSQL.",
            summarized_message_count=8,
            through_message_id="msg_8",
            version=1,
            source_chars=2400,
            created_at=now,
            updated_at=now,
        )
        row = (
            summary.session_id,
            summary.content,
            summary.summarized_message_count,
            summary.through_message_id,
            summary.version,
            summary.source_chars,
            summary.created_at,
            summary.updated_at,
        )
        connection = FakeConnection(
            [
                ("sess_1", "alice", now),
                row,
            ]
        )
        with patch(
            "ai_agent_platform.repositories.postgres._require_psycopg",
            return_value=object(),
        ):
            repository = PostgresSessionRepository(
                database_url="postgresql://test"
            )
            repository._connect = lambda: connection
            stored = repository.upsert_conversation_summary(
                summary,
                expected_version=0,
            )

        assert stored is not None
        self.assertEqual(stored.content, summary.content)
        insert_sql, insert_params = connection.calls[1]
        self.assertIn("INSERT INTO conversation_summaries", insert_sql)
        self.assertIn("ON CONFLICT (session_id) DO NOTHING", insert_sql)
        self.assertEqual(insert_params[4], 1)

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

    def test_document_repository_persists_lexical_and_provenance_metadata(self) -> None:
        connection = FakeConnection([None, None, None])
        document = ParsedDocument(
            id="doc_1",
            knowledge_base_id="docs",
            filename="guide.py",
            text="def healthcheck(): return True",
        )
        chunk = DocumentChunk(
            id="chk_1",
            knowledge_base_id="docs",
            document_id="doc_1",
            filename="guide.py",
            chunk_index=0,
            text=document.text,
            start_line=4,
            end_line=4,
            symbols=["healthcheck"],
        )
        with patch(
            "ai_agent_platform.repositories.postgres._require_psycopg",
            return_value=object(),
        ), patch(
            "ai_agent_platform.repositories.postgres._require_jsonb",
            return_value=lambda value: value,
        ):
            repository = PostgresDocumentRepository(
                database_url="postgresql://test"
            )
            repository._connect = lambda: connection
            repository.save_document(document, [chunk])

        sql, params = connection.calls[2]
        self.assertIn("search_text", sql)
        self.assertIn("start_line", sql)
        self.assertIn("healthcheck", params[9])
        self.assertEqual(params[6:9], (4, 4, ["healthcheck"]))

    def test_document_repository_maps_index_job_state(self) -> None:
        now = datetime(2026, 7, 27, tzinfo=timezone.utc)
        row = (
            "idx_1",
            "docs",
            "guide.md",
            "active",
            "doc_1",
            2,
            None,
            now,
            now,
            now,
        )
        connection = FakeConnection([row, [row]])
        with patch(
            "ai_agent_platform.repositories.postgres._require_psycopg",
            return_value=object(),
        ):
            repository = PostgresDocumentRepository(
                database_url="postgresql://test"
            )
            repository._connect = lambda: connection
            loaded = repository.get_index_job("idx_1")
            listed = repository.list_index_jobs(
                knowledge_base_id="docs",
                limit=20,
            )

        self.assertEqual(loaded, IndexJob(*row))
        self.assertEqual([item.status for item in listed], ["active"])

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

    def test_project_memory_repository_maps_evidence_and_scope(self) -> None:
        now = datetime(2026, 7, 30, tzinfo=timezone.utc)
        memory_row = (
            "mem_0000000000000001",
            "project",
            2,
            "decision",
            "Storage authority",
            "PostgreSQL is authoritative.",
            "decision:storage",
            "active",
            0.95,
            5,
            3,
            "alice",
            now,
            now,
            None,
            None,
            now,
            None,
            4,
            False,
        )
        evidence_row = (
            "mev_0000000000000001",
            memory_row[0],
            "file",
            "run_1",
            "app.py",
            1,
            3,
            "a" * 64,
            "source excerpt",
            now,
        )
        connection = FakeConnection([memory_row, [evidence_row]])
        with patch(
            "ai_agent_platform.repositories.project_memory._require_psycopg",
            return_value=object(),
        ):
            repository = PostgresProjectMemoryRepository(
                database_url="postgresql://test"
            )
            repository._connect = lambda: connection
            loaded = repository.get_memory(memory_row[0])

        assert loaded is not None
        self.assertEqual(loaded.workspace_revision, 2)
        self.assertEqual(loaded.version, 3)
        self.assertEqual(loaded.evidence[0].path, "app.py")
        self.assertIn("project_memories", connection.calls[0][0])
        self.assertIn("project_memory_evidence", connection.calls[1][0])

    def test_project_memory_write_and_outbox_share_one_transaction(self) -> None:
        now = datetime(2026, 7, 30, tzinfo=timezone.utc)
        memory = ProjectMemory(
            id="mem_0000000000000001",
            workspace_id="project",
            workspace_revision=1,
            kind="constraint",
            title="No secrets",
            content="Credentials must not be persisted.",
            canonical_key="constraint:no-secrets",
            status="active",
            confidence=1.0,
            importance=5,
            version=1,
            created_by="alice",
            created_at=now,
            updated_at=now,
        )
        evidence = MemoryEvidence(
            id="mev_0000000000000001",
            memory_id=memory.id,
            source_kind="manual",
            source_id="alice",
            path=None,
            start_line=None,
            end_line=None,
            content_hash=None,
            excerpt=memory.content,
            created_at=now,
        )
        audit = MemoryAuditEvent(
            id="maud_0000000000000001",
            workspace_id="project",
            memory_id=memory.id,
            action="created",
            actor_user_id="alice",
            metadata={"status": "active"},
            created_at=now,
        )
        connection = FakeConnection([None, None, None, None])
        with patch(
            "ai_agent_platform.repositories.project_memory._require_psycopg",
            return_value=object(),
        ), patch(
            "ai_agent_platform.repositories.project_memory._require_jsonb",
            return_value=lambda value: value,
        ):
            repository = PostgresProjectMemoryRepository(
                database_url="postgresql://test"
            )
            repository._connect = lambda: connection
            stored = repository.create_memory(
                memory,
                evidence=[evidence],
                audit=audit,
            )

        self.assertEqual(stored.id, memory.id)
        sql = "\n".join(call[0] for call in connection.calls)
        self.assertIn("INSERT INTO project_memories", sql)
        self.assertIn("INSERT INTO project_memory_evidence", sql)
        self.assertIn("INSERT INTO memory_audit_events", sql)
        self.assertIn("INSERT INTO memory_index_outbox", sql)
        audit_params = connection.calls[2][1]
        self.assertNotIn(memory.content, repr(audit_params))

    def test_project_memory_optimistic_update_uses_expected_version(self) -> None:
        now = datetime(2026, 7, 30, tzinfo=timezone.utc)
        memory = ProjectMemory(
            id="mem_0000000000000001",
            workspace_id="project",
            workspace_revision=1,
            kind="decision",
            title="Outbox",
            content="Use a transactional outbox.",
            canonical_key="decision:outbox",
            status="active",
            confidence=1.0,
            importance=4,
            version=2,
            created_by="alice",
            created_at=now,
            updated_at=now,
        )
        row = (
            memory.id,
            memory.workspace_id,
            memory.workspace_revision,
            memory.kind,
            memory.title,
            memory.content,
            memory.canonical_key,
            memory.status,
            memory.confidence,
            memory.importance,
            memory.version,
            memory.created_by,
            memory.created_at,
            memory.updated_at,
            None,
            None,
            None,
            None,
            0,
            False,
        )
        audit = MemoryAuditEvent(
            id="maud_0000000000000001",
            workspace_id="project",
            memory_id=memory.id,
            action="edited",
            actor_user_id="alice",
            metadata={},
            created_at=now,
        )
        connection = FakeConnection(
            [(memory.id,), None, None, row, []]
        )
        with patch(
            "ai_agent_platform.repositories.project_memory._require_psycopg",
            return_value=object(),
        ), patch(
            "ai_agent_platform.repositories.project_memory._require_jsonb",
            return_value=lambda value: value,
        ):
            repository = PostgresProjectMemoryRepository(
                database_url="postgresql://test"
            )
            repository._connect = lambda: connection
            stored = repository.update_memory(
                memory,
                expected_version=1,
                evidence=[],
                audit=audit,
            )

        assert stored is not None
        self.assertEqual(stored.version, 2)
        update_sql, update_params = connection.calls[0]
        self.assertIn("WHERE id = %s AND version = %s", update_sql)
        self.assertEqual(update_params[-1], 1)


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
