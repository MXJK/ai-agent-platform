"""Behavioral acceptance for the Cogent bridge and restart boundaries."""
import asyncio
from dataclasses import replace
import json
import os
from types import SimpleNamespace

import pytest

from ai_agent_platform.cogent.client import RegistryClient
from ai_agent_platform.cogent.context.platform import canonical_messages
from ai_agent_platform.cogent.conversation import ConversationManager
from ai_agent_platform.cogent.serialization import build_registry_messages
from ai_agent_platform.cogent.leases import RunLeaseUnavailable
from ai_agent_platform.cogent.state import CogentState
from ai_agent_platform.cogent.mcp.loading_strategy import decide_mode, McpLoadingMode
from ai_agent_platform.cogent.tools import CogentToolAdapter
from ai_agent_platform.cogent.tools.base import StreamEnd
from ai_agent_platform.integrations.llm import LLMUsage, LLMProviderError, _openai_tool_input, _anthropic_tool_messages, _deepseek_tool_messages
from ai_agent_platform.integrations.permissions import PermissionResolver
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry
from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.repositories.sqlite import SQLiteAgentRunRepository
from test_cogent_runtime import ScriptedClient, response, runtime_for, start, execute, register_read, register_write, SimulatedCrash


@pytest.mark.parametrize('provider', ['openai', 'anthropic', 'google', 'deepseek', 'glm', 'minimax', 'doubao'])
def test_canonical_stream_preserves_roles_pairs_native_blocks_and_raw_usage(provider):
    native = [{'type': 'reasoning', 'encrypted_content': 'opaque-only', 'summary': []}]
    messages = [
        {'role': 'system', 'content': 'trusted'}, {'role': 'user', 'content': 'inspect'},
        {'role': 'assistant', 'content': 'reading', 'provider': provider, 'provider_items': native,
         'tool_calls': [{'call_id': 'read-a', 'name': 'ReadFile', 'arguments': {'file_path': 'a.py'}, 'source': 'model'}]},
        {'role': 'tool', 'call_id': 'read-a', 'name': 'ReadFile', 'content': {'ok': True, 'result': {'content': 'source'}}, 'is_error': False},
    ]
    usage = LLMUsage(21, 8, thoughts_tokens=3, cached_input_tokens=7, reported_total_tokens=37)
    model = ScriptedClient(replace(response('answer', usage=usage), provider=provider))
    assert build_registry_messages(canonical_messages(messages)) == messages
    async def collect():
        return [item async for item in RegistryClient(model).stream(ConversationManager(canonical_messages(messages)))]
    events = asyncio.run(collect())
    end = next(item for item in events if isinstance(item, StreamEnd))
    assert model.requests == [messages]
    assert end.decision.usage.total_tokens == 37
    assert end.decision.provider == provider


def test_cross_provider_serialization_keeps_pair_and_drops_foreign_private_data():
    messages = [{'role': 'assistant', 'provider': 'minimax', 'content': 'read',
                 'provider_items': [{'role': 'assistant', 'reasoning_details': [{'type': 'encrypted', 'data': 'opaque-private'}]}],
                 'tool_calls': [{'call_id': 'a', 'name': 'ReadFile', 'arguments': {'file_path': 'a.py'}}]},
                {'role': 'tool', 'call_id': 'a', 'name': 'ReadFile', 'content': {'ok': True}, 'is_error': False}]
    canonical = build_registry_messages(canonical_messages(messages))
    for serialized in [_openai_tool_input(canonical, {}), _anthropic_tool_messages(canonical, {}),
                       _deepseek_tool_messages(canonical, {}, provider='glm')]:
        assert 'opaque-private' not in json.dumps(serialized)
        assert 'a' in json.dumps(serialized)
        assert 'ReadFile' in json.dumps(serialized)


@pytest.mark.parametrize('boundary', ['model_request', 'model_response', 'tool_batch_prepared', 'tool_result:read-1', 'tool_batch_completed'])
def test_sqlite_crash_boundary_replays_complete_calls_once(tmp_path, boundary, monkeypatch):
    path = str(tmp_path / 'runs.db')
    effects = []
    registry = ToolRegistry(PermissionResolver())
    register_read(registry, effects)
    client = ScriptedClient(response('', ToolCall('ReadFile', {'file_path': 'a.py'}, 'read-1')), response('Done.'))
    runtime = runtime_for(tmp_path, client, registry=registry, store=SQLiteAgentRunRepository(database=LocalStateDatabase(path)))
    record = start(runtime, tmp_path)
    original = runtime._persist
    def crash(record, state, *, boundary: str):
        stored = original(record, state, boundary=boundary)
        if boundary == crash_at:
            raise SimulatedCrash()
        return stored
    crash_at = boundary
    monkeypatch.setattr(runtime, '_persist', crash)
    with pytest.raises(SimulatedCrash):
        execute(runtime, tmp_path, record)
    restarted = runtime_for(tmp_path, ScriptedClient(*client.steps), registry=registry,
                            store=SQLiteAgentRunRepository(database=LocalStateDatabase(path)))
    result = restarted.recover(record.run_id)
    assert result.status == 'completed'
    assert effects == ['read']
    messages = restarted.get_run(record.run_id).runtime_state['messages']
    assert len([m for m in messages if m.get('call_id') == 'read-1']) == 1
    snapshots = restarted._run_store.list_runtime_snapshots(record.run_id)
    assert snapshots[-1].state == restarted.get_run(record.run_id).runtime_state


def test_sqlite_snapshot_failure_rolls_back_state_and_events(tmp_path, monkeypatch):
    store = SQLiteAgentRunRepository(database=LocalStateDatabase(str(tmp_path / 'runs.db')))
    runtime = runtime_for(tmp_path, ScriptedClient(), store=store)
    record = start(runtime, tmp_path)
    events_before = store.list_events(record.run_id)
    def fail(*args):
        raise RuntimeError('snapshot disk fault')
    monkeypatch.setattr(store, '_save_snapshot_in_transaction', fail)
    with pytest.raises(RuntimeError, match='disk fault'):
        runtime._persist(replace(record, status='running'), CogentState(), boundary='fault')
    assert store.get(record.run_id).status == 'queued'
    assert store.get(record.run_id).checkpoint_id is None
    assert store.list_events(record.run_id) == events_before


def test_sqlite_different_repository_instances_cannot_execute_same_run(tmp_path):
    path = str(tmp_path / 'runs.db')
    first = SQLiteAgentRunRepository(database=LocalStateDatabase(path))
    second = SQLiteAgentRunRepository(database=LocalStateDatabase(path))
    with first.run_lease('same'):
        with pytest.raises(RunLeaseUnavailable):
            with second.run_lease('same'):
                pytest.fail('duplicate ownership')
        with second.run_lease('different'):
            pass
    with second.run_lease('same'):
        pass


def test_input_resume_reaches_next_model_request_without_reasking(tmp_path):
    registry = ToolRegistry(PermissionResolver())
    registry.register('agent.request_user_input', lambda **kwargs: {})
    runtime = runtime_for(tmp_path, ScriptedClient(
        response('', ToolCall('AskUserQuestion', {'questions': [{'id': 'name', 'question': 'Name?'}]}, 'question-1')),
        response('Hello.'),
    ), registry=registry)
    record = start(runtime, tmp_path)
    assert execute(runtime, tmp_path, record).status == 'waiting_input'
    assert runtime.get_run(record.run_id).result.tool_calls
    result = runtime.resume(run_id=record.run_id, approved=True, input_response={'name': 'Sam'}, approved_by='owner')
    assert result.status == 'completed' and result.answer == 'Hello.'
    assert len(result.tool_results) == 1


def test_context_overflow_without_compactable_history_is_partial(tmp_path):
    def overflow(*args):
        raise LLMProviderError('context full', code='context_length_exceeded')
    runtime = runtime_for(tmp_path, ScriptedClient(overflow))
    result = execute(runtime, tmp_path, start(runtime, tmp_path))
    assert result.status == 'partial'
    assert result.terminal_reason == 'context_overflow'


def test_native_mcp_selection_requires_supported_provider_and_model(monkeypatch):
    monkeypatch.setenv('COGENT_MCP_LOADING', 'native')
    assert decide_mode('', 128000, 999999, provider='anthropic', model='claude-sonnet-4-6') == McpLoadingMode.NATIVE
    for provider, model in [('deepseek', 'deepseek-chat'), ('anthropic', 'claude-3-7-sonnet')]:
        assert decide_mode('', 128000, 999999, provider=provider, model=model) == McpLoadingMode.DISPATCH


def test_mcp_search_result_loaded_set_survives_restart(tmp_path):
    from ai_agent_platform.integrations.tool_pool import ToolPoolBuilder
    registry = ToolRegistry(PermissionResolver())
    registry.register('mcp.notes.lookup', lambda query: {'found': query}, provider='mcp',
                      input_schema={'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']})
    runtime = runtime_for(tmp_path, ScriptedClient(response('', ToolCall('ToolSearch', {'query': 'notes'}, 'search-1')),
        response('', ToolCall('mcp__notes__lookup', {'query': 'hello'}, 'mcp-1')), response('Done.')), registry=registry)
    record = start(runtime, tmp_path)
    result = execute(runtime, tmp_path, record)
    assert result.status == 'completed'
    state = CogentState.from_mapping(runtime.get_run(record.run_id).runtime_state)
    assert state.loaded_mcp_tools == ['mcp__notes__lookup']
    adapter = runtime._adapter(record, state, runtime._restore_tools(record, None))
    assert 'mcp__notes__lookup' in {spec.name for spec in adapter.list_specs()}


@pytest.fixture
def postgres_store():
    url = os.getenv('COGENT_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('set COGENT_TEST_POSTGRES_URL to an isolated migrated test database')
    from ai_agent_platform.repositories.postgres import PostgresAgentRunRepository
    return PostgresAgentRunRepository(database_url=url)


@pytest.mark.parametrize('boundary', ['model_request', 'model_response', 'tool_batch_prepared', 'tool_result:read-1', 'tool_batch_completed'])
def test_postgres_restart_matrix(tmp_path, postgres_store, monkeypatch, boundary):
    registry = ToolRegistry(PermissionResolver())
    effects = []
    register_read(registry, effects)
    client = ScriptedClient(response('', ToolCall('ReadFile', {'file_path': 'a.py'}, 'read-1')), response('Done.'))
    runtime = runtime_for(tmp_path, client, registry=registry, store=postgres_store)
    record = start(runtime, tmp_path)
    original = runtime._persist
    def crash(record, state, **kwargs):
        stored = original(record, state, **kwargs)
        if kwargs['boundary'] == boundary:
            raise SimulatedCrash()
        return stored
    monkeypatch.setattr(runtime, '_persist', crash)
    with pytest.raises(SimulatedCrash):
        execute(runtime, tmp_path, record)
    from ai_agent_platform.repositories.postgres import PostgresAgentRunRepository
    reopened = PostgresAgentRunRepository(database_url=postgres_store._database_url)
    restarted = runtime_for(tmp_path, ScriptedClient(*client.steps), registry=registry, store=reopened)
    assert restarted.recover(record.run_id).status == 'completed'
    assert effects == ['read']
    assert reopened.get(record.run_id).runtime_state == reopened.list_runtime_snapshots(record.run_id)[-1].state


def test_postgres_atomic_rollback_and_exclusive_ownership(tmp_path, postgres_store, monkeypatch):
    runtime = runtime_for(tmp_path, ScriptedClient(), store=postgres_store)
    record = start(runtime, tmp_path)
    def fault(*args):
        raise RuntimeError('snapshot failure')
    monkeypatch.setattr(postgres_store, '_save_snapshot_in_transaction', fault)
    with pytest.raises(RuntimeError, match='snapshot failure'):
        runtime._persist(replace(record, status='running'), CogentState(), boundary='fault')
    assert postgres_store.get(record.run_id).status == 'queued'
    assert not postgres_store.list_runtime_snapshots(record.run_id)
    with postgres_store.run_lease(record.run_id):
        with pytest.raises(RunLeaseUnavailable):
            with postgres_store.run_lease(record.run_id):
                pytest.fail('second worker acquired owned run')
    with postgres_store.run_lease(record.run_id):
        pass


def test_postgres_approved_write_restart_does_not_repeat(tmp_path, postgres_store, monkeypatch):
    registry = ToolRegistry(PermissionResolver())
    effects = []
    register_write(registry, lambda **kwargs: effects.append('write') or {})
    runtime = runtime_for(tmp_path, ScriptedClient(response('', ToolCall('WriteFile', {'file_path': 'a.py', 'content': 'new'}, 'write-1'))),
                          registry=registry, store=postgres_store)
    record = start(runtime, tmp_path)
    assert execute(runtime, tmp_path, record).status == 'waiting_approval'
    original = runtime._persist
    def crash(record, state, **kwargs):
        stored = original(record, state, **kwargs)
        if kwargs['boundary'] == 'tool_result:write-1':
            raise SimulatedCrash()
        return stored
    monkeypatch.setattr(runtime, '_persist', crash)
    with pytest.raises(SimulatedCrash):
        runtime.resume(run_id=record.run_id, approved=True, approved_by='owner')
    restarted = runtime_for(tmp_path, ScriptedClient(response('Done.')), registry=registry, store=postgres_store)
    assert restarted.recover(record.run_id).status == 'completed'
    assert effects == ['write']


def test_native_mcp_payload_and_thinking_fields_are_provider_owned(monkeypatch):
    from ai_agent_platform.core import Settings
    from ai_agent_platform.integrations.llm import LLMClient
    from ai_agent_platform.integrations.tools import ToolSpec
    from ai_agent_platform.integrations.public_reasoning import summary_text
    llm = LLMClient(Settings())
    monkeypatch.setattr(llm, '_api_key', lambda provider: 'test-placeholder')
    requests = []
    def transport(provider, url, **kwargs):
        requests.append(kwargs['payload'])
        return {'content': [{'type': 'tool_use', 'id': 'call-1', 'name': 'notes_lookup', 'input': {'query': 'hello'}}], 'stop_reason': 'tool_use'}
    monkeypatch.setattr(llm, '_native_tool_response', transport)
    specs = [ToolSpec('ToolSearch', 'Search', {'type': 'object'}, {}, 'cogent', native_type='tool_search_tool_regex_20251119'),
             ToolSpec('mcp__notes__lookup', 'Lookup notes', {'type': 'object'}, {}, 'mcp', defer_loading=True)]
    decision = llm._decide_anthropic_tools([{'role': 'user', 'content': 'find notes'}], specs,
                {'ToolSearch': 'ToolSearch', 'notes_lookup': 'mcp__notes__lookup'}, 'claude-sonnet-4-6', max_output_tokens=500)
    assert requests[0]['tools'][0] == {'type': 'tool_search_tool_regex_20251119', 'name': 'tool_search_tool_regex'}
    assert requests[0]['tools'][1]['defer_loading'] is True
    assert decision.tool_calls[0].name == 'mcp__notes__lookup'
    assert summary_text('anthropic', [{'type': 'thinking', 'thinking': 'public summary', 'signature': 'secret-signature'},
                                      {'type': 'redacted_thinking', 'data': 'private'}]) == 'public summary'
    assert summary_text('google', [{'parts': [{'thought': True, 'text': 'summary', 'thought_signature': 'private'}]}]) == 'summary'
    for provider in ['deepseek', 'glm', 'doubao', 'minimax']:
        assert summary_text(provider, [{'reasoning_content': 'summary', 'reasoning_details': [{'type': 'encrypted', 'data': 'private'}]}]) == 'summary'


@pytest.fixture(params=['sqlite', 'postgres'])
def durable_store(request, tmp_path):
    if request.param == 'sqlite':
        return SQLiteAgentRunRepository(database=LocalStateDatabase(str(tmp_path / 'durable.db')))
    return request.getfixturevalue('postgres_store')


@pytest.mark.parametrize('kind', ['approval', 'input'])
def test_resume_boundary_survives_restart(durable_store, tmp_path, monkeypatch, kind):
    registry = ToolRegistry(PermissionResolver())
    effects = []
    if kind == 'approval':
        register_write(registry, lambda **kwargs: effects.append('write') or {})
        call = ToolCall('WriteFile', {'file_path': 'a.py', 'content': 'after'}, 'call-1')
    else:
        registry.register('agent.request_user_input', lambda **kwargs: {})
        call = ToolCall('AskUserQuestion', {'questions': [{'id': 'name', 'question': 'Name?'}]}, 'call-1')
    runtime = runtime_for(tmp_path, ScriptedClient(response('', call)), registry=registry, store=durable_store)
    record = start(runtime, tmp_path)
    assert execute(runtime, tmp_path, record).status == ('waiting_approval' if kind == 'approval' else 'waiting_input')
    assert not effects
    original = runtime._persist
    def crash(record, state, **kwargs):
        saved = original(record, state, **kwargs)
        if kwargs['boundary'] == 'resume':
            raise SimulatedCrash()
        return saved
    monkeypatch.setattr(runtime, '_persist', crash)
    with pytest.raises(SimulatedCrash):
        runtime.resume(run_id=record.run_id, approved=True, approved_by='owner',
                       **({'input_response': {'name': 'Sam'}} if kind == 'input' else {'feedback': '用户已在对话中确认执行计划'}))
    restarted = runtime_for(tmp_path, ScriptedClient(response('Done.')), registry=registry, store=durable_store)
    result = restarted.recover(record.run_id)
    assert result.status == 'completed'
    assert len(result.tool_results) == 1
    assert effects == (['write'] if kind == 'approval' else [])
    state = restarted.get_run(record.run_id).runtime_state
    assert state['deferred_user_messages'] == []
    if kind == 'approval':
        assert [m['role'] for m in state['messages'][-4:]] == ['assistant', 'tool', 'user', 'assistant']
        assert state['messages'][-2]['content'] == '用户已在对话中确认执行计划'


def test_compaction_checkpoint_restart_retains_summary_and_recent_pairs(durable_store, tmp_path):
    runtime = runtime_for(tmp_path, ScriptedClient(response('<summary>Old facts preserved.</summary>')), store=durable_store)
    record = start(runtime, tmp_path)
    state = CogentState.from_mapping(record.runtime_state)
    state.messages = [{'role': 'system', 'content': 'System'}]
    for i in range(10):
        state.messages.extend([{'role': 'user', 'content': f'question {i}'}, {'role': 'assistant', 'content': f'answer {i}'}])
    runtime._compact(record, state, {'automatic': True})
    assert state.compact_boundaries[-1]['changed']
    runtime._persist(replace(record, status='running'), state, boundary='compact')
    model = ScriptedClient(response('Done.'))
    restarted = runtime_for(tmp_path, model, store=durable_store)
    assert restarted.recover(record.run_id).status == 'completed'
    assert any('Old facts preserved.' in item.get('content', '') for item in model.requests[-1])
    assert any(item.get('content') == 'answer 9' for item in model.requests[-1])


def test_worker_process_death_releases_lease(durable_store):
    import subprocess
    import sys
    from textwrap import dedent
    if hasattr(durable_store, '_database_url'):
        setup = 'from ai_agent_platform.repositories.postgres import PostgresAgentRunRepository\ns = PostgresAgentRunRepository(database_url=os.environ["COGENT_TEST_POSTGRES_URL"])'
    else:
        setup = 'from ai_agent_platform.local_state import LocalStateDatabase\nfrom ai_agent_platform.repositories.sqlite import SQLiteAgentRunRepository\ns = SQLiteAgentRunRepository(database=LocalStateDatabase(sys.argv[1]))'
    path = str(durable_store.database.path) if hasattr(durable_store, 'database') else ''
    code = 'import os,sys,time\n' + setup + '\nwith s.run_lease("dead-worker"):\n print("owned", flush=True)\n time.sleep(30)\n'
    child = subprocess.Popen([sys.executable, '-c', code, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'owned', child.stderr.read() if child.poll() is not None else ''
        with pytest.raises(RunLeaseUnavailable):
            with durable_store.run_lease('dead-worker'):
                pytest.fail('stole a live worker lease')
        child.kill()
        child.wait(timeout=5)
        with durable_store.run_lease('dead-worker'):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_legacy_history_projection_does_not_mutate_stored_record(tmp_path):
    runtime = runtime_for(tmp_path, ScriptedClient())
    record = start(runtime, tmp_path)
    legacy = replace(record, runtime_engine='langgraph-v1', status='waiting_approval')
    runtime._run_store.save(legacy)
    before = runtime._run_store.list_events(record.run_id)
    assert runtime.get_run(record.run_id).status == 'blocked'
    assert runtime._run_store.get(record.run_id).status == 'waiting_approval'
    assert runtime._run_store.list_events(record.run_id) == before


def test_edit_protected_file_does_not_read_its_content(tmp_path, monkeypatch):
    from pathlib import Path
    from ai_agent_platform.integrations.permissions import ToolUseContext
    registry = ToolRegistry(PermissionResolver())
    registry.register('sandbox.apply_patch', lambda **kwargs: {})
    adapter = CogentToolAdapter(registry)
    context = ToolUseContext(run_id='r', conversation_id='c', workspace_id='w', workspace_root=str(tmp_path))
    def unexpected_read(*args, **kwargs):
        raise AssertionError('protected content was read during preflight')
    monkeypatch.setattr(Path, 'read_text', unexpected_read)
    from ai_agent_platform.integrations.execution_workspace import ExecutionWorkspaceError
    for name in ['.env', '.cogent/permissions.yaml']:
        with pytest.raises((ValueError, PermissionError, ExecutionWorkspaceError)):
            adapter.prepare(ToolCall('EditFile', {'file_path': name, 'old_string': 'a', 'new_string': 'b'}, 'c'), context=context)


@pytest.mark.skipif(os.getenv('COGENT_TEST_OS_SANDBOX') != '1', reason='opt-in real OS sandbox execution')
def test_real_os_sandbox_limits_writes_reads_and_network(tmp_path):
    import subprocess
    import sys
    from ai_agent_platform.cogent.sandbox import create_sandbox, SandboxConfig
    sandbox = create_sandbox()
    assert sandbox and sandbox.available()
    writable = tmp_path / 'allowed'
    writable.mkdir()
    secret = tmp_path / 'secret'
    secret.write_text('private')
    config = SandboxConfig(allow_write=[str(writable)], deny_read=[str(secret)], network_enabled=False)
    code = '''import pathlib,socket,sys
root=pathlib.Path(sys.argv[1])
(root/'allowed'/'yes').write_text('ok')
for action in [lambda: (root/'outside').write_text('no'), lambda: (root/'secret').read_text(), lambda: socket.socket().bind(('127.0.0.1',0))]:
 try: action()
 except PermissionError: continue
 raise AssertionError('sandbox restriction was bypassed')
'''
    result = subprocess.run(sandbox.wrap_argv([sys.executable, '-c', code, str(tmp_path)], config), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (writable / 'yes').read_text() == 'ok'
    assert not (tmp_path / 'outside').exists()


def test_result_reports_elapsed_time_and_durable_activity_count(tmp_path):
    runtime = runtime_for(tmp_path, ScriptedClient())
    record = start(runtime, tmp_path)
    state = CogentState(started_at=1)
    runtime._emit(record.run_id, 'tool_started', 'running', 'Reading source', {})
    from datetime import datetime, timezone
    state.started_at = datetime.now(timezone.utc).timestamp() - 1
    result = runtime._result(record, state, answer='Done.')
    assert 900 <= result.metrics.elapsed_ms < 5000
    assert result.metrics.node_count == len(result.trace) > 0
