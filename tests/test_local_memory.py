from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import stat
import time
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
import pytest

from ai_agent_platform.agents.coding.models import AgentRunRecord
from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.permissions import ToolExecutionContext
from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.main import create_app
from ai_agent_platform.repositories.query import SQLiteQueryUnitOfWork
from ai_agent_platform.repositories.memory import SessionNotFoundError
from ai_agent_platform.repositories.sqlite import (
    SQLiteAgentRunRepository,
    SQLiteSessionRepository,
    SQLiteWorkspaceRepository,
)
from ai_agent_platform.tools.memory import ConversationMemoryToolkit


def _database(root: str) -> LocalStateDatabase:
    return LocalStateDatabase(str(Path(root) / "state.sqlite3"))


def _local_settings(root: Path) -> Settings:
    return Settings(
        local_state_path=str(root / "state.sqlite3"),
        session_repository="sqlite",
        agent_run_store="sqlite",
        workspace_store="sqlite",
        workspace_access_store="sqlite",
        cogent_user_memory_root=str(root / "user-memory"),
        workspace_allowed_roots=(str(root),),
        model_secret_backend="memory",
        rag_reranker_provider="none",
    )


def test_local_state_migration_permissions_wal_and_transaction_rollback() -> None:
    with TemporaryDirectory() as root:
        database = _database(root)
        assert database.path.exists()
        assert stat.S_IMODE(database.path.stat().st_mode) == 0o600
        with database.connect() as connection:
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            assert "workspace_members" in tables
            assert {
                "project_memories",
                "project_memory_evidence",
                "project_memory_vectors",
                "user_memories",
                "user_memory_evidence",
                "user_profile_snapshots",
            }.isdisjoint(tables)

        with pytest.raises(RuntimeError):
            with database.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO workspaces VALUES (?, ?, ?, ?, ?, NULL)",
                    ("rolled-back", "/tmp/rolled-back", "now", "now", 1),
                )
                with database.connect() as reader:
                    assert reader.execute(
                        "SELECT 1 FROM workspaces WHERE id = 'rolled-back'"
                    ).fetchone() is None
                raise RuntimeError("force rollback")
        assert SQLiteWorkspaceRepository(database=database).get("rolled-back") is None

        workspaces = SQLiteWorkspaceRepository(database=database)
        persisted = workspaces.upsert(
            workspace_id="removed",
            root_path="/tmp/removed",
        )
        workspaces.remove(persisted.id)
        assert workspaces.list() == []
        assert [
            item.id for item in workspaces.list_including_removed()
        ] == ["removed"]
        assert workspaces.purge("removed")
        assert workspaces.list_including_removed() == []

        reopened = _database(root)
        assert reopened.fts5_available == database.fts5_available


def test_l0_search_is_user_scoped_persists_and_falls_back_to_like() -> None:
    with TemporaryDirectory() as root:
        database = _database(root)
        sessions = SQLiteSessionRepository(database=database)
        first = sessions.create_session("user-a")
        second = sessions.create_session("user-b")
        sessions.add_message(
            session_id=first.id,
            role="user",
            content="Falcon cache policy belongs to user A",
        )
        sessions.add_message(
            session_id=second.id,
            role="user",
            content="Falcon secret belongs to user B",
        )

        hits = sessions.search_conversations(user_id="user-a", query="Falcon")
        assert [item.session_id for item in hits] == [first.id]
        assert not sessions.search_conversations(
            user_id="user-a", query="secret"
        )

        reopened = SQLiteSessionRepository(database=_database(root))
        assert reopened.search_conversations(user_id="user-a", query="cache")
        reopened.database.fts5_available = False
        assert reopened.search_conversations(user_id="user-a", query="cache")

        toolkit = ConversationMemoryToolkit(reopened)
        result = toolkit.search_conversations(
            "cache",
            context=ToolExecutionContext(
                conversation_id=first.id,
                workspace_id="workspace",
                workspace_root=root,
                actor_user_id="user-a",
            ),
        )
        assert result["count"] == 1


def test_l0_search_indexes_chinese_and_lists_recent_messages_without_query() -> None:
    with TemporaryDirectory() as root:
        sessions = SQLiteSessionRepository(database=_database(root))
        first = sessions.create_session("user-a")
        second = sessions.create_session("user-b")
        sessions.add_message(
            session_id=first.id,
            role="user",
            content="请帮我做一个五子棋游戏",
        )
        sessions.add_message(
            session_id=second.id,
            role="user",
            content="五子棋是另一个用户的消息",
        )

        hits = sessions.search_conversations(user_id="user-a", query="五子棋")
        assert [item.session_id for item in hits] == [first.id]
        recent = sessions.search_conversations(user_id="user-a", query="")
        assert [item.session_id for item in recent] == [first.id]


def test_sqlite_ephemeral_session_delete_cascades_messages() -> None:
    with TemporaryDirectory() as root:
        sessions = SQLiteSessionRepository(database=_database(root))
        session = sessions.create_session("eval-principal")
        sessions.add_message(
            session_id=session.id,
            role="user",
            content="ephemeral",
        )

        assert sessions.delete_session(session.id)

        with pytest.raises(SessionNotFoundError):
            sessions.get_session(session.id)


def test_sqlite_query_start_rolls_back_run_when_message_fails() -> None:
    with TemporaryDirectory() as root:
        database = _database(root)
        sessions = SQLiteSessionRepository(database=database)
        runs = SQLiteAgentRunRepository(database=database)
        unit = SQLiteQueryUnitOfWork(
            session_repository=sessions,
            run_store=runs,
        )
        record = AgentRunRecord(
            run_id="run_rollback",
            thread_id="run_rollback",
            conversation_id="missing-session",
            workspace_id="workspace",
            workspace_root=root,
            status="queued",
            checkpoint_id=None,
            latest_node=None,
            next_nodes=["setup_workspace"],
            trace=[],
        )
        preferences = sessions.create_session("user-a")
        user_preferences = sessions.get_user_preferences("user-a")
        assert user_preferences is None
        from ai_agent_platform.domain import UserPreferences

        with pytest.raises(SessionNotFoundError):
            unit.persist_start(
                record=record,
                message_id="msg_rollback",
                message="must rollback",
                preferences=UserPreferences(user_id=preferences.user_id),
            )
        with pytest.raises(KeyError):
            runs.get(record.run_id)


def test_sqlite_agent_runs_list_recent_in_reverse_creation_order() -> None:
    with TemporaryDirectory() as root:
        runs = SQLiteAgentRunRepository(database=_database(root))
        old = AgentRunRecord(
            run_id="run_old",
            thread_id="run_old",
            conversation_id="session_1",
            workspace_id="workspace",
            workspace_root=root,
            status="queued",
            checkpoint_id=None,
            latest_node=None,
            next_nodes=["setup_workspace"],
            trace=[],
        )
        runs.save(old)
        runs.save(
            replace(
                old,
                run_id="run_new",
                thread_id="run_new",
                conversation_id="session_2",
            )
        )

        assert [record.run_id for record in runs.list_recent(limit=2)] == [
            "run_new",
            "run_old",
        ]


def test_file_memory_and_database_sessions_survive_restart() -> None:
    with TemporaryDirectory() as root_value:
        root = Path(root_value)
        workspace = root / "project"
        workspace.mkdir()
        settings = _local_settings(root)

        with TestClient(create_app(settings=settings)) as client:
            session = client.post(
                "/api/v1/sessions", json={"user_id": "demo_user"}
            )
            assert session.status_code == 201
            session_id = session.json()["id"]
            assert client.put(
                "/api/v1/workspaces/project",
                json={"root_path": str(workspace)},
            ).status_code == 200
            assert client.get("/api/v1/users/me/memories").status_code == 404
            assert client.get(
                "/api/v1/workspaces/project/memories"
            ).status_code == 404
            project_memory = client.post(
                "/api/v1/memory/files",
                json={
                    "workspace_id": "project",
                    "scope": "project",
                    "name": "local-storage",
                    "type": "project",
                    "description": "Local storage decision",
                    "body": "项目使用 SQLite 保存会话状态",
                },
            )
            assert project_memory.status_code == 201
            created = client.post(
                "/api/v1/memory/files",
                json={
                    "workspace_id": "project",
                    "scope": "user",
                    "name": "answer-language",
                    "type": "feedback",
                    "description": "Answer language",
                    "body": "请使用中文回答",
                },
            )
            assert created.status_code == 201
            chat = client.post(
                "/api/v1/agent/runs",
                json={
                    "conversation_id": session_id,
                    "message": "durable-falcon conversation marker",
                    "workspace_id": "project",
                },
            )
            assert chat.status_code == 202
            from test_api import wait_for_run
            assert wait_for_run(client, chat.json()['run_id'])['status'] == 'completed'
            search = client.get(
                "/api/v1/memory/conversations/search",
                params={"q": "durable-falcon"},
            )
            assert any(item["role"] == "user" for item in search.json()["hits"])

        with TestClient(create_app(settings=settings)) as restarted:
            assert restarted.get(
                f"/api/v1/sessions/{session_id}"
            ).status_code == 200
            project_files = restarted.get("/api/v1/memory/files", params={
                "workspace_id": "project", "scope": "project"}).json()["files"]
            user_files = restarted.get("/api/v1/memory/files", params={
                "workspace_id": "project", "scope": "user"}).json()["files"]
            assert "SQLite" in project_files[0]["text"]
            assert "中文" in user_files[0]["text"]
            assert restarted.get(
                "/api/v1/memory/conversations/search",
                params={"q": "durable-falcon"},
            ).json()["hits"]
