import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from ai_agent_platform.cogent.hooks import HookConfigError, HookContext, HookEngine, load_hooks, load_hooks_file
from ai_agent_platform.cogent.hooks import engine as engine_module
from ai_agent_platform.cogent.hooks.executors import execute_action
from ai_agent_platform.cogent.runtime import CogentRuntime
from ai_agent_platform.cogent.state import CogentState
from ai_agent_platform.core.config import Settings
from ai_agent_platform.core.config_resolver import ConfigResolver, ConfigSecurityError
from ai_agent_platform.integrations.permissions import PermissionResolver
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry
from ai_agent_platform.integrations.tool_pool import ToolPoolBuilder
from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.repositories.sqlite import SQLiteAgentRunRepository
from test_cogent_runtime import ScriptedClient, SimulatedCrash, response, start, execute, register_read, register_write


def hook(event, message="hook message", **kwargs):
    return {"id": event, "event": event, "action": {"type": "prompt", "message": message}, **kwargs}


def runtime(root, client, raw, *, registry=None, store=None):
    from ai_agent_platform.agents.coding.store import InMemoryAgentRunStore
    registry = registry or ToolRegistry(PermissionResolver())
    return CogentRuntime(llm_client=client, run_store=store or InMemoryAgentRunStore(),
                         tool_registry=registry, tool_pool_builder=ToolPoolBuilder(registry), hooks=load_hooks(raw))


@pytest.mark.parametrize("raw", [False, {}, [None], [hook("startup")],
    [hook("pre_send", once="false")], [hook("pre_send", reject=True)],
    [hook("pre_tool_use", **{"async": True})], [hook("pre_send"), hook("pre_send")],
    [hook("pre_send", **{"if": "missing == a"})],
    [hook("pre_send", **{"if": "args.x =~ /[/"})],
    [hook("pre_send", **{"if": 'tool == A && event == B || tool == C'})],
    [hook("pre_send", unknown=True)],
    [{"event": "pre_send", "action": {"type": "agent", "prompt": "noop"}}],
    [{"event": "pre_send", "action": {"type": "command", "command": "true", "timeout": True}}],
    [{"event": "pre_send", "action": {"type": "http", "url": "$MESSAGE"}}],
])
def test_invalid_configuration_is_rejected(raw):
    with pytest.raises(HookConfigError):
        load_hooks(raw)


def test_conditions_and_single_pass_expansion_preserve_false_and_zero():
    ctx = HookContext(event_name="pre_tool_use", tool_name="WriteFile",
                      tool_args={"count": 0, "flag": False, "path": "src/a.py"}, message="$ERROR", error="secret")
    parsed = load_hooks([hook("pre_tool_use", **{"if": 'tool == WriteFile && args.path ~= "src/*.py"'})])[0]
    assert parsed.condition.evaluate(ctx)
    assert ctx.get_field("args.count") == "0"
    assert ctx.get_field("args.flag") == "False"
    assert ctx.expand("$MESSAGE $TOOL_ARGS.count") == "$ERROR 0"


def test_configuration_is_process_owned_and_opt_in(tmp_path):
    source = tmp_path / "hooks.json"
    source.write_text(json.dumps({"hooks": [hook("pre_send")]}))
    assert len(load_hooks_file(str(source))) == 1
    assert load_hooks_file("") == []
    with pytest.raises(ValueError, match="absolute"):
        Settings(cogent_hooks_path="relative.json")
    with pytest.raises(ConfigSecurityError):
        ConfigResolver(project_config={"process_security": {"cogent_hooks_path": str(source)}}, env={}).resolve()
    for name in ("COGENT_HOOKS_PATH", "AI_AGENT_PLATFORM_COGENT_HOOKS_PATH"):
        resolved = ConfigResolver(env={name: str(source)}).resolve()
        assert resolved.settings.cogent_hooks_path == str(source)
        assert resolved.process_security.cogent_hooks_path == str(source)


def test_command_context_is_data_and_runs_in_workspace(tmp_path):
    marker = tmp_path / "injected"
    data = f'$(touch "{marker}"); `touch "{marker}"`; "quoted"'
    action = load_hooks([{"event": "pre_send", "action": {
        "type": "command", "command": 'printf "%s\\n" "$TOOL_ARGS.content"; pwd; cat'}}])[0].action
    result = execute_action(action, HookContext(workspace_root=str(tmp_path), tool_args={"content": data}))
    assert result.success
    assert data in result.output and str(tmp_path) in result.output
    assert not marker.exists()
    assert json.loads(result.output.splitlines()[-1])["tool_args"]["content"] == data


def test_command_timeout_and_nonzero_are_failures(tmp_path):
    for command, timeout in [("sleep 5", 1), ("exit 4", 30)]:
        action = load_hooks([{"event": "pre_send", "action": {
            "type": "command", "command": command, "timeout": timeout}}])[0].action
        assert not execute_action(action, HookContext(workspace_root=str(tmp_path))).success


def test_http_action_sends_json_and_rejects_redirect(tmp_path):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(302 if self.path == '/redirect' else 200)
            self.send_header('Location', '/unexpected')
            self.end_headers()
            self.wfile.write(b'ok')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        action = load_hooks([{"event": "pre_send", "action": {"type": "http", "url": base + '/ok'}}])[0].action
        assert execute_action(action, HookContext(run_id='run-test')).success
        assert requests[0][1]['run_id'] == 'run-test'
        with pytest.raises(Exception):
            execute_action(replace(action, url=base + '/redirect'), HookContext())
        assert [path for path, _ in requests] == ['/ok', '/redirect']
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_runtime_lifecycle_prompt_consumption_and_trace(tmp_path):
    registry = ToolRegistry(PermissionResolver())
    reads = []
    register_read(registry, reads)
    client = ScriptedClient(response('', ToolCall('ReadFile', {'file_path': 'a.py'}, 'read-1')), response('Done'))
    raw = [hook('session_start', 'RUN_ONLY', once=True), hook('turn_start', 'TURN'),
           hook('pre_send', 'SEND'), hook('post_receive', 'RECEIVED'),
           hook('pre_tool_use', 'PRE'), hook('post_tool_use', 'POST'),
           hook('turn_end', 'END'), hook('session_end', 'FINISH')]
    app = runtime(tmp_path, client, raw, registry=registry)
    record = start(app, tmp_path)
    assert execute(app, tmp_path, record).status == 'completed'
    assert reads == ['read']
    assert 'RUN_ONLY' in client.requests[0][0]['content']
    assert 'RUN_ONLY' not in client.requests[1][0]['content']
    assert 'PRE' in client.requests[1][0]['content'] and 'POST' in client.requests[1][0]['content']
    events = [e.output['event'] for e in app.list_events(record.run_id) if e.type == 'hook_executed']
    assert events == ['session_start', 'turn_start', 'pre_send', 'post_receive', 'pre_tool_use',
                      'post_tool_use', 'turn_end', 'turn_start', 'pre_send', 'post_receive', 'turn_end', 'session_end']
    assert client.requests[-1][-1]['role'] == 'tool'


def test_rejected_tool_is_paired_and_never_executed_even_in_bypass_mode(tmp_path):
    registry = ToolRegistry(PermissionResolver())
    effects = []
    register_write(registry, lambda **args: effects.append(args) or {})
    client = ScriptedClient(response('', ToolCall('WriteFile', {'file_path': 'a.py', 'content': 'x'}, 'write-1')), response('Blocked'))
    app = runtime(tmp_path, client, [hook('pre_tool_use', 'Do not write', reject=True)], registry=registry)
    record = start(app, tmp_path, permission_mode='bypassPermissions')
    result = execute(app, tmp_path, record)
    assert result.status == 'completed' and effects == []
    assert result.tool_results[0]['error_code'] == 'hook_rejected'
    assert client.requests[-1][-1]['call_id'] == 'write-1'
    assert client.requests[-1][-1]['is_error'] is True


def test_sqlite_approval_restart_does_not_replay_hooks(tmp_path):
    path = str(tmp_path / 'state.sqlite3')
    registry = ToolRegistry(PermissionResolver())
    writes = []
    register_write(registry, lambda **args: writes.append(args) or {})
    raw = [hook('pre_tool_use', 'PRE', once=True), hook('permission_request', 'APPROVAL'), hook('post_tool_use', 'POST')]
    app = runtime(tmp_path, ScriptedClient(response('', ToolCall('WriteFile', {'file_path': 'a.py', 'content': 'x'}, 'write-1'))),
                  raw, registry=registry, store=SQLiteAgentRunRepository(database=LocalStateDatabase(path)))
    record = start(app, tmp_path)
    assert execute(app, tmp_path, record).status == 'waiting_approval'
    client = ScriptedClient(response('Done'))
    restored = runtime(tmp_path, client, raw, registry=registry,
                       store=SQLiteAgentRunRepository(database=LocalStateDatabase(path)))
    assert restored.resume(run_id=record.run_id, approved=True, approved_by='owner').status == 'completed'
    assert len(writes) == 1
    assert client.requests[0][0]['content'].count('PRE') == 1
    assert [e.output['event'] for e in restored.list_events(record.run_id) if e.type == 'hook_executed'] == [
        'pre_tool_use', 'permission_request', 'post_tool_use']


def test_once_and_prompts_are_isolated_across_concurrent_runs(tmp_path):
    class Client:
        def decide_tools(self, messages, tools, **kwargs):
            assert messages[0]['content'].count('ONCE') == 1
            return response('Done')
    app = runtime(tmp_path, Client(), [hook('pre_send', 'ONCE $RUN_ID', once=True)])
    records = [app.create_queued_run(conversation_id=f'c{i}', workspace_id='w', workspace_root=str(tmp_path)) for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda record: execute(app, tmp_path, record), records))
    assert all(item.status == 'completed' for item in results)
    for record in records:
        state = app.get_run(record.run_id).runtime_state
        assert len(state['hook_state']['executions']) == 1
        assert record.run_id in state['system_prompt']
        assert next(r.run_id for r in records if r != record) not in state['system_prompt']


def test_uncertain_reject_hook_fails_closed_without_replay(tmp_path, monkeypatch):
    registry = ToolRegistry(PermissionResolver())
    writes = []
    register_write(registry, lambda **args: writes.append(args) or {})
    raw = [hook('pre_tool_use', 'deny', reject=True)]
    app = runtime(tmp_path, ScriptedClient(response('', ToolCall('WriteFile', {'file_path': 'a.py', 'content': 'x'}, 'write-1'))), raw, registry=registry)
    record = start(app, tmp_path, permission_mode='bypassPermissions')
    attempts = []
    def crash(action, ctx):
        attempts.append(ctx.event_name)
        raise SimulatedCrash()
    monkeypatch.setattr(engine_module, 'execute_action', crash)
    with pytest.raises(SimulatedCrash):
        execute(app, tmp_path, record)
    restored = runtime(tmp_path, ScriptedClient(response('Denied')), raw, registry=registry, store=app._run_store)
    result = restored.recover(record.run_id)
    assert result.status == 'completed' and writes == [] and attempts == ['pre_tool_use']
    assert result.tool_results[0]['error_code'] == 'hook_rejected'
    assert 'not replayed' in result.tool_results[0]['error']


def test_configuration_change_cannot_bypass_recovered_policy(tmp_path):
    registry = ToolRegistry(PermissionResolver())
    register_write(registry, lambda **args: {})
    app = runtime(tmp_path, ScriptedClient(response('', ToolCall('WriteFile', {'file_path': 'a.py', 'content': 'x'}, 'write-1'))),
                  [hook('pre_tool_use')], registry=registry)
    record = start(app, tmp_path)
    assert execute(app, tmp_path, record).status == 'waiting_approval'
    changed = runtime(tmp_path, ScriptedClient(), [], registry=registry, store=app._run_store)
    with pytest.raises(ValueError, match='configuration changed'):
        changed.resume(run_id=record.run_id, approved=True, approved_by='owner')


def test_failed_action_reports_failure_but_observation_does_not_abort(tmp_path, monkeypatch):
    def fail(action, ctx):
        raise RuntimeError('do not leak a secret URL')
    monkeypatch.setattr(engine_module, 'execute_action', fail)
    app = runtime(tmp_path, ScriptedClient(response('Done')), [hook('pre_send')])
    record = start(app, tmp_path)
    assert execute(app, tmp_path, record).status == 'completed'
    event = next(e for e in app.list_events(record.run_id) if e.type == 'hook_executed')
    assert event.output['success'] is False
    assert 'secret URL' not in event.output['output']


def test_error_and_session_end_are_durable_on_failure(tmp_path):
    app = runtime(tmp_path, ScriptedClient(), [hook('error', '$ERROR'), hook('session_end', 'END')])
    record = start(app, tmp_path)
    failed = app.mark_run_failed(run_id=record.run_id, error='provider failed')
    assert failed.status == 'failed'
    app.mark_run_failed(run_id=record.run_id, error='again')
    assert [e.output['event'] for e in app.list_events(record.run_id) if e.type == 'hook_executed'] == ['error', 'session_end']


def test_rejected_call_stays_rejected_when_sibling_needs_approval(tmp_path):
    registry = ToolRegistry(PermissionResolver())
    writes = []
    register_write(registry, lambda **args: writes.append(args['path']) or {})
    raw = [hook('pre_tool_use', 'protected', reject=True, **{'if': 'args.file_path == protected.py'})]
    app = runtime(tmp_path, ScriptedClient(response('',
        ToolCall('WriteFile', {'file_path': 'protected.py', 'content': 'x'}, 'blocked'),
        ToolCall('WriteFile', {'file_path': 'allowed.py', 'content': 'x'}, 'allowed')), response('Done')),
        raw, registry=registry)
    record = start(app, tmp_path)
    assert execute(app, tmp_path, record).status == 'waiting_approval'
    result = app.resume(run_id=record.run_id, approved=True, approved_by='owner')
    assert result.status == 'completed' and writes == ['allowed.py']
    assert result.tool_results[0]['error_code'] == 'hook_rejected'
    transcript = app._llm.requests[-1]
    assert [message['call_id'] for message in transcript if message['role'] == 'tool'] == ['blocked', 'allowed']


def test_hooks_do_not_bypass_existing_tool_permissions(tmp_path):
    registry = ToolRegistry(PermissionResolver())
    writes = []
    register_write(registry, lambda **args: writes.append(args) or {})
    app = runtime(tmp_path, ScriptedClient(response('', ToolCall('WriteFile', {'file_path': 'a.py', 'content': 'x'}, 'write-1')),
                                           response('No write')),
                  [hook('pre_tool_use', 'allow')], registry=registry)
    record = start(app, tmp_path, permission_mode='plan')
    result = execute(app, tmp_path, record)
    assert result.status == 'waiting_approval' and writes == []
    assert app.get_run(record.run_id).pending_approval['approval_required_tools'][0]['name'] == 'WriteFile'


def test_prompt_survives_crash_before_model_response_without_duplication(tmp_path):
    def crash(*args):
        raise SimulatedCrash()
    raw = [hook('pre_send', 'UNIQUE_PROMPT')]
    app = runtime(tmp_path, ScriptedClient(crash), raw)
    record = start(app, tmp_path)
    with pytest.raises(SimulatedCrash):
        execute(app, tmp_path, record)
    client = ScriptedClient(response('Done'))
    restarted = runtime(tmp_path, client, raw, store=app._run_store)
    assert restarted.recover(record.run_id).status == 'completed'
    assert client.requests[0][0]['content'].count('UNIQUE_PROMPT') == 1
    assert len([e for e in restarted.list_events(record.run_id) if e.type == 'hook_executed']) == 1


def test_completed_hook_trace_is_repaired_after_notification_crash(tmp_path, monkeypatch):
    raw = [hook('pre_send')]
    app = runtime(tmp_path, ScriptedClient(), raw)
    record = start(app, tmp_path)
    def crash(*args):
        raise SimulatedCrash()
    monkeypatch.setattr(app, '_notify_hook', crash)
    with pytest.raises(SimulatedCrash):
        execute(app, tmp_path, record)
    client = ScriptedClient(response('Done'))
    restarted = runtime(tmp_path, client, raw, store=app._run_store)
    assert restarted.recover(record.run_id).status == 'completed'
    assert len([e for e in restarted.list_events(record.run_id) if e.type == 'hook_executed']) == 1


def test_parallel_tool_results_are_collected_before_next_prompt(tmp_path):
    registry = ToolRegistry(PermissionResolver())
    reads = []
    register_read(registry, reads)
    client = ScriptedClient(response('', *[ToolCall('ReadFile', {'file_path': f'{i}.py'}, f'read-{i}') for i in range(4)]), response('Done'))
    app = runtime(tmp_path, client, [hook('post_tool_use', 'RESULT $TOOL_ARGS.file_path')], registry=registry)
    record = start(app, tmp_path)
    assert execute(app, tmp_path, record).status == 'completed'
    assert len(reads) == 4
    assert len([e for e in app.list_events(record.run_id) if e.type == 'hook_executed']) == 4
    assert all(f'RESULT {i}.py' in client.requests[-1][0]['content'] for i in range(4))


def test_compact_and_command_hooks_are_wired_through_api(tmp_path):
    from fastapi.testclient import TestClient
    from ai_agent_platform.main import create_app
    from test_cogent_api import ready, finish
    source = tmp_path / 'hooks.json'
    source.write_text(json.dumps([hook('command_execute', 'COMMAND'), hook('compact', 'COMPACT'), hook('session_end', 'END')]))
    settings = Settings(llm_provider='fake', auth_mode='disabled',
        model_secret_backend='memory', workspace_allowed_roots=(str(tmp_path),),
        native_directory_picker_mode='disabled', cogent_hooks_path=str(source),
        cogent_user_memory_root=str(tmp_path / 'memory'), skills_directory_path=str(tmp_path / 'skills'))
    with TestClient(create_app(settings=settings)) as client:
        session = ready(client, tmp_path)
        result = client.post('/api/v1/agent/runs', json={'conversation_id': session,
            'workspace_id': 'workspace', 'message': '/compact'})
        result.raise_for_status()
        body = finish(client, result.json()['run_id'])
        assert body['status'] == 'completed'
        events = client.get(f"/api/v1/agent/runs/{body['run_id']}/events").json()['events']
        assert [e['output']['event'] for e in events if e['type'] == 'hook_executed'] == ['command_execute', 'compact', 'session_end']


def test_user_input_result_triggers_post_tool_hook(tmp_path):
    client = ScriptedClient(response('', ToolCall('AskUserQuestion', {'questions': [{'question': 'Which?'}]}, 'ask-1')), response('Done'))
    registry = ToolRegistry(PermissionResolver())
    registry.register('agent.request_user_input', lambda **args: {})
    app = runtime(tmp_path, client, [hook('post_tool_use', 'INPUT_RECEIVED', **{'if': 'tool == AskUserQuestion'})], registry=registry)
    record = start(app, tmp_path)
    assert execute(app, tmp_path, record).status == 'waiting_input'
    assert app.resume(run_id=record.run_id, approved=True, approved_by='owner', input_response={'answer': 'yes'}).status == 'completed'
    assert 'INPUT_RECEIVED' in client.requests[-1][0]['content']
    events = [e.output for e in app.list_events(record.run_id) if e.type == 'hook_executed']
    assert len(events) == 1 and events[0]['event'] == 'post_tool_use'


def test_command_dispatch_recovers_after_hook_crash(tmp_path, monkeypatch):
    raw = [hook('command_execute')]
    app = runtime(tmp_path, ScriptedClient(), raw)
    record = start(app, tmp_path)
    state = CogentState.from_mapping(record.runtime_state)
    state.messages = [{'role': 'system', 'content': ''}, {'role': 'user', 'content': '/help'}]
    record = app._persist(replace(record, status='running'), state, boundary='run_started')
    adapter = app._adapter(record, state, app._restore_tools(record, None))
    context = app._base_tool_context(record, None, actor_user_id='owner')
    def crash(action, ctx):
        raise SimulatedCrash()
    monkeypatch.setattr(engine_module, 'execute_action', crash)
    with pytest.raises(SimulatedCrash):
        app._run_command(record, state, adapter, context, {'name': 'help', 'arguments': ''})
    restored = runtime(tmp_path, ScriptedClient(), raw, store=app._run_store)
    result = restored.recover(record.run_id)
    assert result.status == 'completed' and '/help' in result.answer
    assert len(restored._llm.requests) == 0
