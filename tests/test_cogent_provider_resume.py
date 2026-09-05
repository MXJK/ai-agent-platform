from fastapi.testclient import TestClient

from cogent_strict_provider import StrictDeepSeekTransport, strict_app
from test_cogent_api import ready, finish


def test_real_deepseek_adapter_api_approval_sse_and_followup(tmp_path, monkeypatch):
    transport = StrictDeepSeekTransport()
    transport.install(monkeypatch)
    root = tmp_path / 'workspace'
    app = strict_app(root)
    with TestClient(app) as client:
        session = ready(client, root)
        request = {'conversation_id': session, 'workspace_id': 'workspace', 'message': '检查这个项目完整吗？',
                   'provider': 'deepseek', 'model': 'deepseek-v4-flash', 'mode': 'manual', 'sandbox_enabled': False}
        created = client.post('/api/v1/agent/runs', json=request)
        created.raise_for_status()
        run = created.json()['run_id']
        waiting = finish(client, run)
        assert waiting['status'] == 'waiting_approval', waiting
        assert len(transport.requests) == 1
        resumed = client.post(f'/api/v1/agent/runs/{run}/resume', json={
            'approved': True, 'feedback': '用户已在对话中确认执行计划'})
        resumed.raise_for_status()
        completed = finish(client, run)
        assert completed['status'] == 'completed', completed
        assert len(transport.requests) == 3
        assert transport.rejections == []
        results = completed['result']['tool_results']
        assert [r['name'] for r in results] == ['Bash', 'Glob', 'ReadFile']
        assert all(r['ok'] for r in results), results
        assert 'Isolated Cogent QA' in str(results[-1]['result'])
        replay = transport.requests[1]['messages']
        position = next(i for i, m in enumerate(replay) if m.get('tool_calls'))
        assert [m['role'] for m in replay[position:position + 4]] == ['assistant', 'tool', 'tool', 'user']
        assert 'Inspect the available' in replay[position]['reasoning_content']
        sse = client.get(f'/api/v1/agent/runs/{run}/events/stream')
        sse.raise_for_status()
        assert 'run_completed' in sse.text and 'tool_result' in sse.text
        second = client.post('/api/v1/agent/runs', json={**request, 'message': '继续说明检查结果'})
        second.raise_for_status()
        assert finish(client, second.json()['run_id'])['status'] == 'completed'
        assert len(transport.requests) == 4 and not transport.rejections
