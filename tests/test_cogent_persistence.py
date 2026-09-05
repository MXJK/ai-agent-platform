from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ai_agent_platform.agents.coding.models import AgentRunInvalidStateError
from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.repositories.query import SQLiteQueryUnitOfWork, PostgresQueryUnitOfWork
from ai_agent_platform.repositories.sqlite import SQLiteSessionRepository, SQLiteAgentRunRepository
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry
from ai_agent_platform.integrations.permissions import PermissionResolver
from test_cogent_runtime import ScriptedClient, response, runtime_for, start, execute, register_write


def test_sqlite_atomic_admission_preserves_engine_and_rejects_concurrent_turn(tmp_path):
    db = LocalStateDatabase(str(tmp_path / 'state.sqlite3'))
    sessions = SQLiteSessionRepository(database=db)
    runs = SQLiteAgentRunRepository(database=db)
    session = sessions.create_session('owner')
    unit = SQLiteQueryUnitOfWork(session_repository=sessions, run_store=runs)
    runtime = runtime_for(tmp_path, ScriptedClient(), store=runs)
    record = runtime.create_queued_record(run_id='turn_a', conversation_id=session.id,
        workspace_id='workspace', workspace_root=str(tmp_path))

    def admit(name):
        try:
            unit.persist_start(record=replace(record, run_id=name, thread_id=name),
                message_id='message_' + name, message=name, preferences=None)
            return name
        except AgentRunInvalidStateError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        admitted = list(workers.map(admit, ['turn_a', 'turn_b']))
    assert sum(item is not None for item in admitted) == 1
    winner = next(item for item in admitted if item)
    reloaded = SQLiteAgentRunRepository(database=LocalStateDatabase(str(tmp_path / 'state.sqlite3'))).get(winner)
    assert reloaded.runtime_engine == 'cogent-v1'
    assert reloaded.runtime_state_version == 1
    assert reloaded.runtime_state == record.runtime_state
    with db.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM messages WHERE session_id = ?', (session.id,)).fetchone()[0] == 1


def test_postgres_atomic_start_writes_versioned_state_and_locks_before_insert(tmp_path):
    sql = []

    class Connection:
        def execute(self, statement, params):
            sql.append((statement, params))
            return SimpleNamespace(fetchone=lambda: None)

    @contextmanager
    def connect():
        yield Connection()

    sessions = SimpleNamespace(add_message_in_transaction=lambda *args, **kwargs: SimpleNamespace(id='msg'))
    unit = PostgresQueryUnitOfWork(session_repository=sessions, run_store=SimpleNamespace(_connect=connect))
    runtime = runtime_for(tmp_path, ScriptedClient())
    record = runtime.create_queued_record(run_id='turn_a', conversation_id='session_a', workspace_id='workspace', workspace_root=str(tmp_path))
    unit.persist_start(record=record, message_id='message_a', message='hello', preferences=None)
    assert 'FOR UPDATE' in sql[0][0]
    insert, params = next(item for item in sql if 'INSERT INTO agent_runs (' in item[0])
    assert 'runtime_engine, runtime_state_version, runtime_state_json' in insert
    assert insert.count('%s') == len(params)
    assert params[-3:-1] == ('cogent-v1', 1)
    assert params[-1].obj == record.runtime_state


def test_sqlite_restart_approval_executes_bound_write_once(tmp_path):
    db_path = str(tmp_path / 'runs.sqlite3')
    registry = ToolRegistry(PermissionResolver())
    effects = []
    register_write(registry, lambda **args: effects.append(args['path']) or {})
    store = SQLiteAgentRunRepository(database=LocalStateDatabase(db_path))
    runtime = runtime_for(tmp_path, ScriptedClient(response('', ToolCall('WriteFile', {'file_path': 'a.py', 'content': 'new'}, 'write-1'))), registry=registry, store=store)
    record = start(runtime, tmp_path)
    assert execute(runtime, tmp_path, record).status == 'waiting_approval'
    assert effects == []
    restarted = runtime_for(tmp_path, ScriptedClient(response('Done.')), registry=registry,
        store=SQLiteAgentRunRepository(database=LocalStateDatabase(db_path)))
    result = restarted.resume(run_id=record.run_id, approved=True, approved_by='owner')
    assert result.status == 'completed'
    assert effects == ['a.py'], [(item.get('error'), item.get('permission_decision')) for item in result.tool_results]
    with pytest.raises(AgentRunInvalidStateError):
        restarted.resume(run_id=record.run_id, approved=True, approved_by='owner')
    assert effects == ['a.py']
    assert restarted._run_store.list_runtime_snapshots(record.run_id)
