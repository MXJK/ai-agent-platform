import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from ai_agent_platform.agents.coding.models import (
    AgentRunEvent,
    AgentRunRecord,
    AgentToolExecution,
)
from ai_agent_platform.domain import ConversationSummary
from ai_agent_platform.integrations.rag import (
    DocumentChunk,
    IndexJob,
    KnowledgeDocument,
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
    def test_session_listing_maps_metadata_search_and_cursor_contract(self) -> None:
        created = datetime(2026, 8, 1, tzinfo=timezone.utc)
        updated = datetime(2026, 8, 2, tzinfo=timezone.utc)
        row = (
            "sess_1",
            "alice",
            created,
            "Persistent conversation",
            "auto",
            updated,
            None,
            "workspace_main",
            "fake",
            "demo-stream-model",
            "low",
            "chat",
            2,
            "latest answer",
        )
        connection = FakeConnection([[row]])
        with patch(
            "ai_agent_platform.repositories.postgres._require_psycopg",
            return_value=object(),
        ):
            repository = PostgresSessionRepository(
                database_url="postgresql://test"
            )
            repository._connect = lambda: connection
            sessions = repository.list_sessions(
                user_id="alice",
                query="persist%",
                archived=False,
                limit=31,
                before=(updated, "sess_2"),
            )

        self.assertEqual(sessions[0].title, "Persistent conversation")
        self.assertEqual(sessions[0].message_count, 2)
        self.assertEqual(sessions[0].last_message_preview, "latest answer")
        sql, params = connection.calls[0]
        self.assertIn("messages history_messages", sql)
        self.assertIn("messages searched_messages", sql)
        self.assertIn("sessions.archived_at IS NULL", sql)
        self.assertIn("ORDER BY sessions.updated_at DESC, sessions.id DESC", sql)
        self.assertEqual(params, ("alice", "%persist\\%%", "%persist\\%%", updated, "sess_2", 31))

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
        row = ("workspace_main", "/workspace/code", now, now, 1, None)
        removed_row = ("workspace_main", "/workspace/code", now, now, 1, now)
        connection = FakeConnection([row, row, [row], removed_row])
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
            removed = repository.remove("workspace_main")
        self.assertEqual(created.root_path, "/workspace/code")
        self.assertEqual(loaded.id, "workspace_main")
        self.assertEqual([item.id for item in listed], ["workspace_main"])
        self.assertEqual(removed.removed_at, now)
        self.assertIn("workspaces", connection.calls[0][0])
        self.assertIn("removed_at IS NULL", connection.calls[1][0])
        self.assertIn("UPDATE workspaces", connection.calls[3][0])
        self.assertNotIn("DELETE FROM workspaces", connection.calls[3][0])

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

    def test_document_repository_lists_manageable_metadata(self) -> None:
        now = datetime(2026, 8, 7, tzinfo=timezone.utc)
        row = (
            "doc_1",
            "docs",
            "Guide",
            "guide.md",
            "Operator guide",
            ["ops"],
            "text/markdown",
            128,
            "abc123",
            3,
            None,
            now,
            now,
            now,
            "active",
            None,
        )
        connection = FakeConnection([(1,), [row]])
        with patch(
            "ai_agent_platform.repositories.postgres._require_psycopg",
            return_value=object(),
        ):
            repository = PostgresDocumentRepository(
                database_url="postgresql://test"
            )
            repository._connect = lambda: connection
            documents, total = repository.list_documents(
                knowledge_base_id="docs",
                query="Guide%",
                status="active",
                sort="updated_at_desc",
                page=2,
                page_size=20,
            )

        self.assertEqual(total, 1)
        self.assertEqual(
            documents,
            [
                KnowledgeDocument(
                    id="doc_1",
                    knowledge_base_id="docs",
                    title="Guide",
                    filename="guide.md",
                    description="Operator guide",
                    tags=["ops"],
                    media_type="text/markdown",
                    byte_size=128,
                    content_hash="abc123",
                    chunk_count=3,
                    is_searchable=True,
                    last_index_status="active",
                    last_index_error=None,
                    created_at=now,
                    updated_at=now,
                    indexed_at=now,
                )
            ],
        )
        count_sql, count_params = connection.calls[0]
        list_sql, list_params = connection.calls[1]
        self.assertIn("latest_job.status", count_sql)
        self.assertIn("ORDER BY documents.updated_at DESC", list_sql)
        self.assertEqual(count_params[-1], "active")
        self.assertEqual(list_params[-2:], (20, 20))

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

    def test_agent_runtime_events_and_tool_identity_use_durable_tables(self) -> None:
        connection = FakeConnection(
            [
                [
                    (41, "run_started", "running", "setup_workspace", "started", {}),
                    (42, "run_paused", "paused", "plan_tools", "paused", {"safe": True}),
                ],
                (43,),
                (
                    "run_1",
                    "call_1",
                    "repo.read_file",
                    "args-hash",
                    "completed",
                    {"ok": True},
                ),
                None,
            ]
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
            events = repository.list_events("run_1", after=40)
            appended = repository.append_event(
                "run_1",
                AgentRunEvent(
                    sequence=0,
                    type="control_pause_requested",
                    status="running",
                    node="plan_tools",
                    summary="pause requested",
                ),
            )
            execution = repository.get_tool_execution("run_1", "call_1")
            repository.save_tool_execution(
                AgentToolExecution(
                    run_id="run_1",
                    call_id="call_1",
                    name="repo.read_file",
                    arguments_hash="args-hash",
                    status="completed",
                    response={"ok": True},
                )
            )

        self.assertEqual([event.sequence for event in events], [41, 42])
        self.assertEqual(events[1].output, {"safe": True})
        self.assertEqual(appended.sequence, 43)
        assert execution is not None
        self.assertEqual(execution.arguments_hash, "args-hash")
        self.assertIn("id > %s", connection.calls[0][0])
        self.assertEqual(connection.calls[0][1], ("run_1", 40))
        self.assertIn("agent_run_events", connection.calls[1][0])
        self.assertIn("agent_tool_executions", connection.calls[2][0])
        self.assertIn("ON CONFLICT (run_id, call_id)", connection.calls[3][0])

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
