import json

import pytest

from ai_agent_platform.cogent.tool_results import ToolResultFiles
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry
from test_cogent_runtime import ScriptedClient, execute, response, runtime_for, start


def test_result_files_are_durable_scoped_and_hash_verified(tmp_path):
    refs = {}
    store = ToolResultFiles(str(tmp_path), refs)
    preview = store.persist('run_fixture', {'call_id': 'large', 'text': 'one\ntwo'})
    assert preview['path'].startswith('.cogent/sessions/run_fixture/tool-results/')
    restored = ToolResultFiles(str(tmp_path), json.loads(json.dumps(refs)))
    assert 'two' in restored.read(preview['path'])['content']
    with pytest.raises(PermissionError):
        ToolResultFiles(str(tmp_path), {}).read(preview['path'])
    (tmp_path / preview['path']).write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        restored.read(preview['path'])


def test_result_storage_never_follows_symlinked_parent(tmp_path):
    other = tmp_path / 'outside'
    other.mkdir()
    (tmp_path / '.cogent').symlink_to(other, target_is_directory=True)
    with pytest.raises(OSError):
        ToolResultFiles(str(tmp_path), {}).persist('run_fixture', {'call_id': 'x'})
    assert not list(other.iterdir())


def test_later_run_can_read_prior_result_without_repository_handler(tmp_path):
    registry = ToolRegistry()
    effects = []
    registry.register('repo.read_file', lambda **args: effects.append(args) or {'content': 'x' * 100_000}, max_output_chars=120_000)
    client = ScriptedClient(response('', ToolCall('ReadFile', {'file_path': 'a.py'}, 'read-large')), response('First answer.'))
    runtime = runtime_for(tmp_path, client, registry=registry)
    first = start(runtime, tmp_path)
    execute(runtime, tmp_path, first)
    state = runtime.get_run(first.run_id).runtime_state
    path = next(iter(state['tool_result_files']))
    assert (tmp_path / path).is_file()
    # Build a fresh runtime to exercise the persisted conversation reference, not an adapter cache.
    next_client = ScriptedClient(response('', ToolCall('ReadFile', {'file_path': path, 'limit': 3}, 'read-back')), response('Restored.'))
    resumed = runtime_for(tmp_path, next_client, registry=registry, store=runtime._run_store)
    second = start(resumed, tmp_path)
    assert execute(resumed, tmp_path, second).answer == 'Restored.'
    assert len(effects) == 1
    assert next_client.requests[-1][-1]['name'] == 'ReadFile'


def test_provider_summary_stream_is_visible_once_and_private_fields_never_leak(tmp_path):
    from ai_agent_platform.integrations.native_streaming import NativeStreamAccumulator

    runtime = None
    record = None

    def stream(messages, tools, kwargs):
        acc = NativeStreamAccumulator('openai', kwargs['on_delta'])
        acc.parse('', {'type': 'response.reasoning_summary_text.delta', 'delta': 'Public summary',
            'signature': 'DO_NOT_SHOW', 'obfuscation': 'DO_NOT_SHOW'})
        from time import monotonic, sleep
        deadline = monotonic() + 2
        while not any(e.type == 'thinking_delta' for e in runtime.list_events(record.run_id)) and monotonic() < deadline:
            sleep(0.005)
        assert any(e.type == 'thinking_delta' for e in runtime.list_events(record.run_id))
        acc.parse('', {'type': 'response.reasoning_text.delta', 'delta': 'PRIVATE_REASONING'})
        return response('Done.', provider_items=[{'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 'Public summary'}], 'encrypted_content': 'DO_NOT_SHOW'}])

    runtime = runtime_for(tmp_path, ScriptedClient(stream))
    record = start(runtime, tmp_path)
    execute(runtime, tmp_path, record)
    events = runtime.list_events(record.run_id)
    assert len([e for e in events if e.type == 'thinking_delta']) == 1
    assert len([e for e in events if e.type == 'thinking_completed']) == 1
    assert 'DO_NOT_SHOW' not in str(events)
    assert 'PRIVATE_REASONING' not in str(events)
