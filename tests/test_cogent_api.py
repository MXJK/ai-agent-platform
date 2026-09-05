import time
from dataclasses import replace

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.main import create_app


def app_for(root):
    return create_app(settings=Settings(
        runtime_profile='custom', llm_provider='fake', auth_mode='disabled',
        model_secret_backend='memory', model_registry_store='memory',
        session_repository='memory', agent_run_store='memory', change_set_store='memory',
        document_store='memory', workspace_store='memory', task_queue_backend='in_process',
        project_memory_enabled=False, user_memory_enabled=False,
        rag_reranker_provider='none', rag_vector_store='memory',
        workspace_allowed_roots=(str(root),), native_directory_picker_mode='disabled',
        skills_directory_path=str(root / 'user-skills'),
    ))


def ready(client, root):
    session = client.post('/api/v1/sessions', json={'user_id': 'tester'}).json()['id']
    client.put('/api/v1/workspaces/workspace', json={'root_path': str(root)}).raise_for_status()
    return session


def finish(client, run_id):
    for _ in range(100):
        body = client.get(f'/api/v1/agent/runs/{run_id}').json()
        if body['status'] not in {'queued', 'running'}:
            return body
        time.sleep(0.01)
    raise AssertionError('Cogent Run did not reach a terminal or suspended state')


def test_default_api_uses_cogent_and_fast_chat_is_removed(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        session = ready(client, tmp_path)
        assert client.post('/api/v1/chat/stream', json={}).status_code == 404
        response = client.post('/api/v1/agent/runs', json={
            'conversation_id': session, 'workspace_id': 'workspace', 'message': 'Hello Cogent',
            'permission_mode': 'plan', 'sandbox_enabled': True,
        })
        response.raise_for_status()
        body = finish(client, response.json()['run_id'])
        assert body['runtime_engine'] == 'cogent-v1'
        assert body['status'] == 'completed'
        assert body['result']['metrics']['input_tokens'] > 0
        assert not {'context_route', 'selected_knowledge_base_ids', 'context_sources'} & body['result'].keys()
        events = client.get(f"/api/v1/agent/runs/{body['run_id']}/events").json()['events']
        assert {'answer_delta', 'usage', 'turn_completed'} <= {item['type'] for item in events}
        assert not any(item['type'] in {'memory_context', 'rag_context'} for item in events)
        for field in ('knowledge_base_ids', 'evaluation_knowledge_base_ids', 'context_route'):
            rejected = client.post('/api/v1/agent/runs', json={
                'conversation_id': session, 'workspace_id': 'workspace', 'message': 'Hello', field: [],
            })
            assert rejected.status_code == 422


def test_legacy_run_remains_readable_and_cannot_resume(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        session = ready(client, tmp_path)
        runtime = app.state.query_service._runtime
        record = runtime.create_queued_run(conversation_id=session, workspace_id='workspace', workspace_root=str(tmp_path))
        runtime._run_store.save(replace(record, runtime_engine='langgraph-v1', runtime_state={}))
        body = client.get(f'/api/v1/agent/runs/{record.run_id}').json()
        assert body['status'] == 'blocked'
        assert body['legacy_read_only']
        assert body['error'] == 'legacy_runtime_retired'
        denied = client.post(f'/api/v1/agent/runs/{record.run_id}/resume', json={'approved': True})
        assert denied.status_code == 409
        assert denied.json()['detail'] == 'legacy_run_read_only'
