"""Regression coverage for real approval feedback and suspended tool batches."""
from copy import deepcopy
from dataclasses import replace
import asyncio

import pytest

from ai_agent_platform.cogent.client import RegistryClient
from ai_agent_platform.cogent.context.platform import canonical_messages
from ai_agent_platform.cogent.conversation import ConversationManager
from ai_agent_platform.cogent.serialization import build_registry_messages
from ai_agent_platform.cogent.state import CogentState
from ai_agent_platform.cogent.tool_transcript import ToolMessagePairingError, ordered_tool_messages
from ai_agent_platform.cogent.tools import CogentToolAdapter
from ai_agent_platform.integrations.permissions import PermissionResolver
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry
from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.repositories.sqlite import SQLiteAgentRunRepository
from test_cogent_runtime import ScriptedClient, response, runtime_for, start, execute, register_read, register_write, SimulatedCrash


FEEDBACK = '用户已在对话中确认执行计划'


def assert_contiguous_results(messages):
    """Independent protocol oracle: every pending ID must precede any new turn."""
    pending = set()
    for message in messages:
        if pending:
            assert message['role'] == 'tool', message['role']
            assert message['call_id'] in pending
            pending.remove(message['call_id'])
        else:
            assert message['role'] != 'tool'
            calls = message.get('tool_calls') or []
            ids = [call['call_id'] for call in calls]
            assert len(ids) == len(set(ids))
            pending = set(ids)
    assert not pending


@pytest.mark.parametrize('action', ['approval', 'pause', 'steer'])
def test_feedback_and_mid_batch_controls_survive_restart_in_order(tmp_path, action):
    database = str(tmp_path / 'runs.sqlite')
    registry = ToolRegistry(PermissionResolver())
    effects = []
    def write(**args):
        effects.append(args['path'])
        if len(effects) == 1 and action != 'approval':
            runtime.request_control(run_id=record.run_id, action=action,
                                    message='Check both files' if action == 'steer' else '')
        return {'written': args['path']}
    register_write(registry, write)
    calls = [ToolCall('WriteFile', {'file_path': f'{i}.py', 'content': 'new'}, f'w{i}') for i in (1, 2)]
    runtime = runtime_for(tmp_path, ScriptedClient(response('', *calls)), registry=registry,
                          store=SQLiteAgentRunRepository(database=LocalStateDatabase(database)))
    record = start(runtime, tmp_path)
    assert execute(runtime, tmp_path, record).status == 'waiting_approval'
    original = runtime._persist
    def crash_after_resume(record, state, **kwargs):
        saved = original(record, state, **kwargs)
        if kwargs['boundary'] == 'resume':
            raise SimulatedCrash()
        return saved
    runtime._persist = crash_after_resume
    with pytest.raises(SimulatedCrash):
        runtime.resume(run_id=record.run_id, approved=True, approved_by='owner', feedback=FEEDBACK)
    model = ScriptedClient(response('Done.'), response('Follow-up.'))
    runtime = runtime_for(tmp_path, model, registry=registry,
                          store=SQLiteAgentRunRepository(database=LocalStateDatabase(database)))
    result = runtime.recover(record.run_id)
    if action == 'pause':
        assert result.status == 'paused'
        assert effects == ['1.py']
        result = runtime.resume(run_id=record.run_id, approved=True, approved_by='owner', feedback='Continue carefully')
    assert result.status == 'completed'
    assert effects == ['1.py', '2.py']
    transcript = model.requests[0]
    assert_contiguous_results(transcript)
    assert sum(m.get('content') == FEEDBACK for m in transcript) == 1
    if action != 'approval':
        assert transcript[-1]['content'] == ('Continue carefully' if action == 'pause' else 'Check both files')
    assert runtime.get_run(record.run_id).runtime_state['deferred_user_messages'] == []
    second = start(runtime, tmp_path)
    assert execute(runtime, tmp_path, second).status == 'completed'
    assert_contiguous_results(model.requests[1])
    assert effects == ['1.py', '2.py']


def test_input_siblings_then_approval_preserve_every_result(tmp_path):
    registry = ToolRegistry(PermissionResolver())
    registry.register('agent.request_user_input', lambda **kwargs: {})
    effects = []
    register_read(registry, effects)
    register_write(registry, lambda **args: effects.append('write') or {})
    question = lambda id: ToolCall('AskUserQuestion', {'questions': [{'id': id, 'question': 'Choose?'}]}, id)
    model = ScriptedClient(response('', question('q1'), ToolCall('ReadFile', {'file_path': 'a'}, 'r'),
                                    question('q2'), ToolCall('WriteFile', {'file_path': 'b', 'content': 'new'}, 'w')),
                           response('Done.'))
    runtime = runtime_for(tmp_path, model, registry=registry)
    record = start(runtime, tmp_path)
    assert execute(runtime, tmp_path, record).status == 'waiting_input'
    runtime.request_control(run_id=record.run_id, action='steer', message='Keep this constraint')
    assert runtime.resume(run_id=record.run_id, approved=True, approved_by='owner', input_response={'q1': 'yes'}).status == 'waiting_input'
    assert runtime.get_run(record.run_id).pending_approval['call_id'] == 'q2'
    assert runtime.resume(run_id=record.run_id, approved=True, approved_by='owner', input_response={'q2': 'yes'}).status == 'waiting_approval'
    assert effects == []
    assert runtime.resume(run_id=record.run_id, approved=True, approved_by='owner', feedback=FEEDBACK).status == 'completed'
    assert_contiguous_results(model.requests[-1])
    assert [m['call_id'] for m in model.requests[-1] if m['role'] == 'tool'] == ['q1', 'q2', 'r', 'w']
    assert effects == ['read', 'write']


def batch():
    return [{'role': 'assistant', 'content': '', 'provider': 'deepseek',
             'provider_items': [{'role': 'assistant', 'reasoning_content': 'opaque', 'tool_calls': [
                 {'id': 'a', 'type': 'function', 'function': {'name': 'ReadFile', 'arguments': '{}'}},
                 {'id': 'b', 'type': 'function', 'function': {'name': 'Glob', 'arguments': '{}'}}]}],
             'tool_calls': [{'call_id': 'a', 'name': 'ReadFile', 'arguments': {}},
                            {'call_id': 'b', 'name': 'Glob', 'arguments': {}}]},
            {'role': 'tool', 'call_id': 'a', 'content': {'ok': False, 'error': 'read failed'}},
            {'role': 'tool', 'call_id': 'b', 'content': {'ok': True, 'files': []}}]


def test_recover_old_interleaved_feedback_without_reexecuting_tools(tmp_path):
    messages = batch()
    messages.insert(1, {'role': 'user', 'content': FEEDBACK})
    original = deepcopy(messages)
    repaired = ordered_tool_messages(messages, restore_delayed_results=True)
    assert messages == original
    assert_contiguous_results(repaired)
    assert repaired[0] == original[0]
    assert repaired[-1]['content'] == FEEDBACK
    model = ScriptedClient(response('Done.'))
    runtime = runtime_for(tmp_path, model)
    record = start(runtime, tmp_path)
    state = CogentState(messages=[{'role': 'system', 'content': 'system'}, *messages])
    runtime._persist(replace(record, status='running'), state, boundary='model_request')
    assert runtime.recover(record.run_id).status == 'completed'
    assert_contiguous_results(model.requests[-1])
    assert model.requests[-1][1]['provider_items'] == original[0]['provider_items']


@pytest.mark.parametrize('defect', ['missing', 'duplicate_result', 'orphan', 'duplicate_call', 'empty_id', 'interleaved'])
def test_invalid_transcripts_never_reach_provider(defect):
    messages = batch()
    if defect == 'missing': messages.pop()
    if defect == 'duplicate_result': messages.append(deepcopy(messages[-1]))
    if defect == 'orphan': messages = messages[1:]
    if defect == 'duplicate_call': messages[0]['tool_calls'][1]['call_id'] = 'a'
    if defect == 'empty_id': messages[0]['tool_calls'][1]['call_id'] = ''
    if defect == 'interleaved': messages.insert(1, {'role': 'user', 'content': FEEDBACK})
    model = ScriptedClient(response('Should not be called'))
    async def collect():
        return [event async for event in RegistryClient(model).stream(ConversationManager(canonical_messages(messages)))]
    with pytest.raises(ToolMessagePairingError):
        asyncio.run(collect())
    assert model.requests == []
    if defect != 'interleaved':
        with pytest.raises(ToolMessagePairingError):
            ordered_tool_messages(messages, restore_delayed_results=True)


def test_duplicate_model_call_ids_cannot_execute_tools(tmp_path):
    registry = ToolRegistry(PermissionResolver())
    effects = []
    register_read(registry, effects)
    runtime = runtime_for(tmp_path, ScriptedClient(response('',
        ToolCall('ReadFile', {'file_path': 'a'}, 'same'), ToolCall('ReadFile', {'file_path': 'b'}, 'same'))), registry=registry)
    with pytest.raises(ToolMessagePairingError):
        execute(runtime, tmp_path, start(runtime, tmp_path))
    assert effects == []


def test_bash_exposes_configured_allowlist_and_inspection_tools(tmp_path):
    from ai_agent_platform.tools.sandbox import register_sandbox_tools
    registry = ToolRegistry(PermissionResolver())
    register_sandbox_tools(registry, allowed_commands=('python3', 'pytest'))
    spec = next(s for s in CogentToolAdapter(registry).list_specs() if s.name == 'Bash')
    assert 'python3' in spec.description and 'pytest' in spec.description
    assert 'Glob' in spec.description and 'repo.list_files' not in spec.description
    assert 'shell pipelines' in spec.description
