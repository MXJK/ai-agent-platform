from pathlib import Path

from fastapi.testclient import TestClient

from ai_agent_platform.cogent.filehistory import FileHistory
from ai_agent_platform.integrations.tools import ToolCall
from test_cogent_api import app_for, ready, finish
from test_cogent_runtime import ScriptedClient, response


def source_run(app, client, tmp_path):
    (tmp_path / 'a.py').write_text('before\n')
    session = ready(client, tmp_path)
    runtime = app.state.runtime.cogent_runtime
    runtime._memory_service = None
    runtime._llm = ScriptedClient(response('', ToolCall('WriteFile', {'file_path': 'a.py', 'content': 'after\n'}, 'edit-1')), response('Done.'))
    run = client.post('/api/v1/agent/runs', json={'conversation_id': session, 'workspace_id': 'workspace', 'message': 'Update a.py'}).json()['run_id']
    assert finish(client, run)['status'] == 'waiting_approval'
    client.post(f'/api/v1/agent/runs/{run}/resume', json={'approved': True}).raise_for_status()
    assert finish(client, run)['status'] == 'completed'
    snapshots = FileHistory(str(tmp_path), session).get_snapshots()
    assert len(snapshots) == 1
    assert (tmp_path / 'a.py').read_text() == 'before\n'
    # Simulate applying the first reviewed ChangeSet before asking for a rewind.
    (tmp_path / 'a.py').write_text('after\n')
    return session, snapshots[0]


def test_rewind_api_approval_preserves_patch_only_and_branches_conversation(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        session, snapshot = source_run(app, client, tmp_path)
        run = client.post('/api/v1/agent/runs', json={'conversation_id': session, 'workspace_id': 'workspace',
            'message': f'/rewind {snapshot.id} all'}).json()['run_id']
        waiting = finish(client, run)
        assert waiting['status'] == 'waiting_approval'
        assert '-after' in waiting['pending_approval']['rewind_preview']['patch']
        client.post(f'/api/v1/agent/runs/{run}/resume', json={'approved': True}).raise_for_status()
        final = finish(client, run)
        assert final['status'] == 'completed', final.get('error')
        assert final['result']['change_set_id']
        assert (tmp_path / 'a.py').read_text() == 'after\n'
        runtime = app.state.runtime.cogent_runtime
        state = runtime.get_run(run).runtime_state
        assert state['compact_boundaries'][-1]['type'] == 'rewind'
        assert not state['compact_boundaries'][-1]['history_deleted']
        assert client.post(f'/api/v1/agent/runs/{run}/resume', json={'approved': True}).status_code == 409


def test_rewind_conflict_after_preview_does_not_overwrite(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        session, snapshot = source_run(app, client, tmp_path)
        run = client.post('/api/v1/agent/runs', json={'conversation_id': session, 'workspace_id': 'workspace',
            'message': f'/rewind {snapshot.id} files'}).json()['run_id']
        assert finish(client, run)['status'] == 'waiting_approval'
        record = app.state.runtime.cogent_runtime.get_run(run)
        execution = Path(record.context_snapshot.execution_workspace.execution_root)
        (execution / 'a.py').write_text('concurrent user edit\n')
        client.post(f'/api/v1/agent/runs/{run}/resume', json={'approved': True}).raise_for_status()
        final = finish(client, run)
        assert final['status'] in {'failed', 'blocked'}
        if execution.exists():
            assert (execution / 'a.py').read_text() == 'concurrent user edit\n'
        assert (tmp_path / 'a.py').read_text() == 'after\n'
        events = client.get(f'/api/v1/agent/runs/{run}/events').json()['events']
        assert not any(item['type'] == 'tool_started' for item in events)


def test_conversation_only_rewind_uses_a_completed_run_without_file_history(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        session = ready(client, tmp_path)
        runtime = app.state.runtime.cogent_runtime
        runtime._memory_service = None
        runtime._llm = ScriptedClient(response('First answer.'), response('Second answer.'))
        ids = []
        for question in ['first question', 'second question']:
            run = client.post('/api/v1/agent/runs', json={'conversation_id': session, 'workspace_id': 'workspace', 'message': question}).json()['run_id']
            assert finish(client, run)['status'] == 'completed'
            ids.append(run)
        assert not FileHistory(str(tmp_path), session).get_snapshots()
        run = client.post('/api/v1/agent/runs', json={'conversation_id': session, 'workspace_id': 'workspace',
            'message': f'/rewind {ids[0]} conversation'}).json()['run_id']
        assert finish(client, run)['status'] == 'waiting_approval'
        client.post(f'/api/v1/agent/runs/{run}/resume', json={'approved': True}).raise_for_status()
        assert finish(client, run)['status'] == 'completed'
        messages = runtime.get_run(run).runtime_state['messages']
        assert any(m.get('content') == 'First answer.' for m in messages)
        assert not any(m.get('content') == 'Second answer.' for m in messages)
        assert runtime.get_run(ids[1]).result.answer == 'Second answer.'
