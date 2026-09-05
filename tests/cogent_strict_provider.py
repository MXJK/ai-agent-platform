"""Local HTTP fixture exercising real DeepSeek payload/stream adapters."""
import json
from pathlib import Path

import httpx

from ai_agent_platform.core import Settings
from ai_agent_platform.main import create_app


class StrictDeepSeekTransport:
    def __init__(self):
        self.requests = []
        self.rejections = []

    def respond(self, request):
        assert request.url.host == 'api.deepseek.com', request.url.host
        body = json.loads(request.content)
        self.requests.append(body)
        pending = set()
        try:
            for message in body['messages']:
                if pending:
                    assert message['role'] == 'tool', 'insufficient tool messages following tool_calls message'
                    assert message['tool_call_id'] in pending, 'unexpected tool_call_id'
                    pending.remove(message['tool_call_id'])
                else:
                    assert message['role'] != 'tool', 'orphan tool result'
                    calls = message.get('tool_calls') or []
                    ids = [call['id'] for call in calls]
                    assert len(ids) == len(set(ids)), 'duplicate tool_call_id'
                    pending = set(ids)
            assert not pending, 'missing tool results'
        except AssertionError as error:
            self.rejections.append(str(error))
            return httpx.Response(400, json={'error': {'type': 'invalid_request_error', 'message': str(error)}})
        results = [m for m in body['messages'] if m['role'] == 'tool']
        if not results:
            calls = [('inspect-command', 'Bash', {'command': 'python3 --version'}),
                     ('inspect-glob', 'Glob', {'pattern': '**/*.md'})]
            delta = {'reasoning_content': 'Inspect the available project files.', 'tool_calls': [
                {'index': i, 'id': id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}
                for i, (id, name, args) in enumerate(calls)]}
            finish = 'tool_calls'
        elif not any(m['tool_call_id'] == 'inspect-read' for m in results):
            delta = {'reasoning_content': 'Read the project documentation.', 'tool_calls': [
                {'index': 0, 'id': 'inspect-read', 'type': 'function', 'function': {'name': 'ReadFile', 'arguments': '{"file_path":"README.md"}'}}]}
            finish = 'tool_calls'
        else:
            delta = {'content': '项目检查完成：已读取 README，工具结果已核对。'}
            finish = 'stop'
        events = [{'model': body['model'], 'choices': [{'index': 0, 'delta': delta}]},
                  {'model': body['model'], 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]},
                  {'choices': [], 'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}]
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              content=''.join('data: ' + json.dumps(e, ensure_ascii=False) + '\n\n' for e in events) + 'data: [DONE]\n\n')

    def install(self, monkeypatch):
        original = httpx.Client
        def client(*args, **kwargs):
            if 'transport' not in kwargs:
                kwargs['transport'] = httpx.MockTransport(self.respond)
            return original(*args, **kwargs)
        monkeypatch.setattr(httpx, 'Client', client)


def strict_app(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'README.md').write_text('# Isolated Cogent QA\nA small project used for read-only validation.\n')
    app = create_app(settings=Settings(
        runtime_profile='custom', llm_provider='deepseek', llm_model='deepseek-v4-flash',
        llm_max_retries=0, auth_mode='disabled', model_secret_backend='memory',
        model_registry_store='memory', session_repository='sqlite', agent_run_store='sqlite',
        local_state_path=str(root.parent / 'state.sqlite3'), change_set_store='memory',
        document_store='memory', workspace_store='memory', task_queue_backend='in_process',
        project_memory_enabled=False, user_memory_enabled=False,
        rag_reranker_provider='none', rag_vector_store='memory',
        workspace_allowed_roots=(str(root),), native_directory_picker_mode='disabled',
        skills_directory_path=str(root.parent / 'skills'),
    ))
    app.state.query_service._runtime._memory_service = None
    app.state.model_registry.upsert_connection(provider='deepseek', display_name='Local protocol fixture',
                                              api_key='test-placeholder', enabled=True)
    return app
