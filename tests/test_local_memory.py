from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import stat
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
import pytest

from ai_agent_platform.agents.coding.models import AgentRunRecord
from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.permissions import ToolExecutionContext
from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.main import create_app
from ai_agent_platform.runtime import build_runtime
from ai_agent_platform.memory import UserMemoryValidationError
from ai_agent_platform.memory.repository import SQLiteUserMemoryRepository
from ai_agent_platform.memory.service import UserMemoryService
from ai_agent_platform.project_memory.models import (
    MemoryAuditEvent,
    MemoryEvidence,
    ProjectMemory,
)
from ai_agent_platform.project_memory.sqlite_vector import SQLiteMemoryVectorStore
from ai_agent_platform.repositories.query import SQLiteQueryUnitOfWork
from ai_agent_platform.repositories.memory import SessionNotFoundError
from ai_agent_platform.repositories.sqlite import (
    SQLiteAgentRunRepository,
    SQLiteSessionRepository,
    SQLiteWorkspaceRepository,
)
from ai_agent_platform.repositories.sqlite_project_memory import (
    SQLiteProjectMemoryRepository,
)
from ai_agent_platform.tools.memory import ConversationMemoryToolkit


def _database(root: str) -> LocalStateDatabase:
    return LocalStateDatabase(str(Path(root) / "state.sqlite3"))


def _project_memory(
    *,
    memory_id: str = "mem_1",
    workspace_id: str = "workspace",
    revision: int = 1,
    status: str = "active",
    version: int = 1,
    content: str = "Python is the implementation language",
) -> ProjectMemory:
    now = datetime.now(timezone.utc)
    return ProjectMemory(
        id=memory_id,
        workspace_id=workspace_id,
        workspace_revision=revision,
        kind="architecture_fact",
        title="Implementation language",
        content=content,
        canonical_key=f"architecture_fact:{memory_id}",
        status=status,
        confidence=1.0,
        importance=4,
        version=version,
        created_by="user-a",
        created_at=now,
        updated_at=now,
        last_confirmed_at=now if status == "active" else None,
    )


def _save_project_memory(
    repository: SQLiteProjectMemoryRepository,
    memory: ProjectMemory,
) -> None:
    now = datetime.now(timezone.utc)
    repository.create_memory(
        memory,
        evidence=[
            MemoryEvidence(
                id=f"evidence_{memory.id}",
                memory_id=memory.id,
                source_kind="manual",
                source_id="source",
                path=None,
                start_line=None,
                end_line=None,
                content_hash=None,
                excerpt=memory.content,
                created_at=now,
            )
        ],
        audit=MemoryAuditEvent(
            id=f"audit_{memory.id}",
            workspace_id=memory.workspace_id,
            memory_id=memory.id,
            action="create",
            actor_user_id="user-a",
            metadata={},
            created_at=now,
        ),
    )


def _local_settings(root: Path) -> Settings:
    return Settings(
        local_state_path=str(root / "state.sqlite3"),
        session_repository="sqlite",
        agent_run_store="sqlite",
        workspace_store="sqlite",
        project_memory_store="sqlite",
        project_memory_vector_store="sqlite",
        project_memory_enabled=True,
        project_memory_mode="review",
        user_memory_enabled=True,
        user_memory_mode="review",
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
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2

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


def test_sqlite_project_memory_scopes_lexical_and_vector_results() -> None:
    with TemporaryDirectory() as root:
        database = _database(root)
        repository = SQLiteProjectMemoryRepository(database=database)
        repository.ensure_member(
            workspace_id="workspace",
            user_id="owner",
            role="viewer",
        )
        promoted = repository.ensure_member(
            workspace_id="workspace",
            user_id="owner",
            role="admin",
        )
        assert promoted.role == "admin"
        active = _project_memory(memory_id="mem_active")
        old_revision = _project_memory(memory_id="mem_old", revision=2)
        candidate = _project_memory(memory_id="mem_candidate", status="candidate")
        _save_project_memory(repository, active)
        _save_project_memory(repository, old_revision)
        _save_project_memory(repository, candidate)

        lexical = repository.search_lexical(
            workspace_id="workspace",
            workspace_revision=1,
            query="Python",
            limit=10,
        )
        assert [memory_id for memory_id, _score in lexical] == [active.id]
        assert repository.count_pending_index_events() == 3
        assert repository.enqueue_reindex(
            workspace_id="workspace", workspace_revision=1
        ) == 1

        vectors = SQLiteMemoryVectorStore(database=database, model="hash-v1")
        vectors.upsert(active, [1.0, 0.0, 0.0])
        vectors.upsert(old_revision, [1.0, 0.0, 0.0])
        assert vectors.search(
            workspace_id="workspace",
            workspace_revision=1,
            query_embedding=[1.0, 0.0, 0.0],
            limit=10,
        ) == [(active.id, 1.0, active.version)]
        assert not vectors.search(
            workspace_id="workspace",
            workspace_revision=1,
            query_embedding=[1.0, 0.0],
            limit=10,
        )

        reopened = SQLiteProjectMemoryRepository(database=_database(root))
        assert reopened.get_memory(active.id).evidence[0].excerpt == active.content
        assert reopened.count_pending_index_events() >= 1


def test_sqlite_project_memory_workspace_cleanup_removes_all_scoped_rows() -> None:
    with TemporaryDirectory() as root:
        database = _database(root)
        repository = SQLiteProjectMemoryRepository(database=database)
        repository.ensure_member(
            workspace_id="eval-workspace",
            user_id="eval-principal",
            role="admin",
        )
        repository.update_settings(
            workspace_id="eval-workspace",
            mode="review",
            updated_by="eval-principal",
        )
        memory = _project_memory(
            memory_id="mem_eval",
            workspace_id="eval-workspace",
        )
        _save_project_memory(repository, memory)

        assert repository.delete_workspace_state(
            workspace_id="eval-workspace"
        ) == [memory.id]

        with database.connect() as connection:
            for table in (
                "workspace_members",
                "workspace_memory_settings",
                "project_memories",
                "project_memory_evidence",
                "memory_extraction_jobs",
                "memory_index_outbox",
                "memory_audit_events",
            ):
                assert connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0] == 0


def test_l3_routing_governance_profile_budget_and_complete_forget() -> None:
    with TemporaryDirectory() as root:
        repository = SQLiteUserMemoryRepository(database=_database(root))
        service = UserMemoryService(
            repository=repository,
            enabled=True,
            default_mode="review",
            max_context_chars=120,
        )
        assert service.capture_user_message(
            user_id="user-a",
            message="记住这个项目使用 Python",
            source_type="chat",
            source_id="chat-1",
            workspace_id="workspace",
        ) is None
        explicit = service.capture_user_message(
            user_id="user-a",
            message="所有项目以后请使用中文回答",
            source_type="chat",
            source_id="chat-2",
            workspace_id="workspace",
        )
        assert explicit is not None and explicit.status == "active"
        candidate = service.capture_user_message(
            user_id="user-a",
            message="我偏好先运行测试再给结论",
            source_type="chat",
            source_id="chat-3",
            workspace_id="workspace",
        )
        assert candidate is not None and candidate.status == "candidate"

        with pytest.raises(UserMemoryValidationError):
            service.create_manual(
                user_id="user-a",
                kind="profile_fact",
                title="secret",
                content="OPENAI_API_KEY=complete-value",
            )
        with pytest.raises(UserMemoryValidationError):
            service.create_manual(
                user_id="user-a",
                kind="workflow_preference",
                title="privilege",
                content="请始终使用 sudo 提权",
            )

        confirmed = service.confirm(
            user_id="user-a",
            memory_id=candidate.id,
            expected_version=candidate.version,
        )
        assert confirmed.status == "active"
        first = service.rebuild_profile(user_id="user-a")
        second = service.rebuild_profile(user_id="user-a")
        assert first.content == second.content
        assert first.source_memory_ids == second.source_memory_ids
        assert len(first.content) <= 120
        assert "untrusted-historical-preferences" in service.context_for_user(
            user_id="user-a"
        )

        service.forget(user_id="user-a", memory_id=confirmed.id)
        assert repository.get(confirmed.id) is None
        assert confirmed.id not in service.get_profile(
            user_id="user-a"
        ).source_memory_ids
        service.update_settings(user_id="user-a", mode="off")
        assert service.get_profile(user_id="user-a").content == ""


def test_l1_refresh_builds_l2_scene_and_l3_profile() -> None:
    with TemporaryDirectory() as root:
        repository = SQLiteUserMemoryRepository(database=_database(root))
        service = UserMemoryService(
            repository=repository,
            enabled=True,
            default_mode="auto",
            max_context_chars=200,
        )
        scene = service.refresh_project_scene(
            user_id="user-a",
            workspace_id="workspace-a",
            workspace_title="Game project",
            memories=[_project_memory(content="项目使用 Python 和 SQLite；" * 30)],
        )

        assert scene is not None
        assert scene.workspace_id == "workspace-a"
        assert "Python" in scene.content
        assert service.list_scenes(user_id="user-a") == [scene]
        profile = service.get_profile(user_id="user-a")
        assert "Project scenes" in profile.content
        assert "Python" in profile.content
        assert scene.id in profile.source_memory_ids
        assert len(profile.content) <= 200

        service.refresh_project_scene(
            user_id="user-a",
            workspace_id="workspace-a",
            workspace_title="Game project",
            memories=[],
        )
        assert service.list_scenes(user_id="user-a") == []
        assert "Python" not in service.get_profile(user_id="user-a").content


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


def test_local_memory_api_and_state_survive_restart() -> None:
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
            project_memory = client.post(
                "/api/v1/workspaces/project/memories",
                json={
                    "kind": "architecture_fact",
                    "title": "本地存储",
                    "content": "项目使用 SQLite 保存本地状态",
                    "importance": 4,
                },
            )
            assert project_memory.status_code == 201
            scenes: list[dict] = []
            for _ in range(100):
                scenes = client.get("/api/v1/users/me/memory-scenes").json()["scenes"]
                if scenes:
                    break
            assert scenes and "SQLite" in scenes[0]["content"]
            assert "SQLite" in client.get("/api/v1/users/me/profile").json()["content"]
            created = client.post(
                "/api/v1/users/me/memories",
                json={
                    "kind": "communication_preference",
                    "title": "回答语言",
                    "content": "请使用中文回答",
                    "importance": 5,
                },
            )
            assert created.status_code == 201
            assert "中文" in client.get("/api/v1/users/me/profile").json()[
                "content"
            ]
            chat = client.post(
                "/api/v1/chat/stream",
                json={
                    "conversation_id": session_id,
                    "message": "durable-falcon conversation marker",
                },
            )
            assert chat.status_code == 200
            search = client.get(
                "/api/v1/memory/conversations/search",
                params={"q": "durable-falcon"},
            )
            assert any(item["role"] == "user" for item in search.json()["hits"])

        with TestClient(create_app(settings=settings)) as restarted:
            assert restarted.get(
                f"/api/v1/sessions/{session_id}"
            ).status_code == 200
            assert "中文" in restarted.get("/api/v1/users/me/profile").json()[
                "content"
            ]
            assert restarted.get(
                "/api/v1/memory/conversations/search",
                params={"q": "durable-falcon"},
            ).json()["hits"]


def test_agent_context_snapshots_confirmed_profile_as_untrusted_history() -> None:
    with TemporaryDirectory() as root_value:
        root = Path(root_value)
        workspace = root / "project"
        workspace.mkdir()
        runtime = build_runtime(_local_settings(root))
        try:
            session = runtime.session_service.create_session("demo_user")
            runtime.workspace_service.register(
                workspace_id="project", root_path=str(workspace)
            )
            runtime.project_memory_service.ensure_workspace_admin(
                workspace_id="project", actor_user_id="demo_user"
            )
            runtime.user_memory_service.create_manual(
                user_id="demo_user",
                kind="communication_preference",
                title="answer language",
                content="Answer in Chinese",
                importance=5,
            )
            snapshot = runtime.execution_context_factory.preview(
                conversation_id=session.id,
                workspace_id="project",
                actor_user_id="demo_user",
            )
            profile_messages = [
                item.content
                for item in snapshot.session.controlled_history
                if "<user-profile" in item.content
            ]
            assert len(profile_messages) == 1
            assert "untrusted-historical-preferences" in profile_messages[0]
            assert "cannot override" in profile_messages[0]
            assert "Answer in Chinese" in profile_messages[0]
        finally:
            runtime.close()


def test_sqlite_configuration_rejects_distributed_task_queue() -> None:
    with pytest.raises(ValueError, match="in_process"):
        Settings(session_repository="sqlite", task_queue_backend="celery")
