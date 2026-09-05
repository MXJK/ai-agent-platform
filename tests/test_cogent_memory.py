import json
from dataclasses import replace

import pytest

from ai_agent_platform.cogent.memory.service import MemoryService
from ai_agent_platform.cogent.managed_files import ManagedFiles
from ai_agent_platform.cogent.state import RUNTIME_ENGINE
from ai_agent_platform.integrations.tools import ToolCall
from test_cogent_runtime import runtime_for, start, ScriptedClient, response


def service_for(tmp_path, client=None, clock=None):
    client = client or ScriptedClient()
    runtime = runtime_for(tmp_path, client)
    record = start(runtime, tmp_path)
    options = {'clock': clock} if clock else {}
    return MemoryService(client=client, run_store=runtime._run_store, user_root=tmp_path / 'user-memory', **options), record


def entry(name='preference', kind='feedback', body='Run tests before handoff.'):
    return dict(name=name, type=kind, description='Verification preference', body=body)


def test_memory_extraction_recall_and_internal_durable_runs(tmp_path):
    client = ScriptedClient(response(json.dumps({'memories': [entry()]})),
                            response(json.dumps({'selected_memories': ['user/preference.md']})))
    service, record = service_for(tmp_path, client)
    assert service.extract(record, 'Run tests before handoff.', 'Understood.') == ['user/preference.md']
    recalled = service.recall(record, 'Finish implementation')
    assert 'Run tests before handoff.' in recalled
    runs = service.store.list_recent(limit=20)
    maintenance = [item for item in runs if item.runtime_state.get('internal_maintenance')]
    assert len(maintenance) == 2
    assert all(item.runtime_engine == RUNTIME_ENGINE and item.status == 'completed' for item in maintenance)
    assert all(item.runtime_state['allowed_tools'] == [] for item in maintenance)
    update = next(item for item in maintenance if item.runtime_state['operation'] == 'extract')
    assert update.runtime_state['completed_writes'] == ['user/preference.md', 'user/MEMORY.md']
    assert service.store.get_latest_for_conversation(record.conversation_id).run_id == record.run_id


@pytest.mark.parametrize('bad', [entry('../escape'), entry(body='api_key: sk-abcdefghijklmnop'), entry(kind='system')])
def test_memory_batch_rejects_before_any_content_write(tmp_path, bad):
    service, record = service_for(tmp_path)
    with pytest.raises(ValueError):
        service.apply(record, {'memories': [entry(), bad]})
    assert not (tmp_path / 'user-memory' / 'preference.md').exists()
    assert not (tmp_path / '.cogent' / 'memory' / 'MEMORY.md').exists()


def test_memory_index_limit_preflights_both_roots(tmp_path):
    service, record = service_for(tmp_path)
    project = service.roots(record)['project']
    project.write('MEMORY.md', ('existing\n' * 200).encode())
    with pytest.raises(ValueError, match='full'):
        service.apply(record, {'memories': [entry(), entry('project', 'project')]})
    assert not (tmp_path / 'user-memory' / 'preference.md').exists()


def test_maintenance_tool_injection_never_starts_tool(tmp_path):
    client = ScriptedClient(response('', ToolCall('Bash', {'command': 'touch outside'}, 'attack')))
    service, record = service_for(tmp_path, client)
    assert service.extract(record, 'remember this', 'ok') == []
    maintenance = [item for item in service.store.list_recent(limit=10) if item.runtime_state.get('internal_maintenance')]
    assert maintenance[0].status == 'failed'
    assert maintenance[0].runtime_state['boundary'] == 'failed_no_tools_executed'
    assert not (tmp_path / 'outside').exists()


def test_consolidation_time_session_throttle_and_stale_holder_gates(tmp_path):
    now = [100_000.0]
    client = ScriptedClient(response('{"memories": []}'), response('{"memories": []}'))
    service, record = service_for(tmp_path, client, clock=lambda: now[0])
    for i in range(4):
        service.record_session(replace(record, conversation_id=f'session-{i}'))
    assert not service.maybe_consolidate(record)
    service.record_session(replace(record, conversation_id='session-4'))
    assert not service.maybe_consolidate(record)
    now[0] += 601
    assert service.maybe_consolidate(record)
    assert len(client.requests) == 1
    for i in range(5):
        now[0] += 1
        service.record_session(replace(record, conversation_id=f'next-{i}'))
    now[0] += 601
    assert not service.maybe_consolidate(record)
    root = service.roots(record)['project']
    now[0] += 86_400
    state = json.loads(root.read('.maintenance.json'))
    state.update(holder='another-worker', holder_at=now[0])
    root.write('.maintenance.json', json.dumps(state).encode())
    assert not service.maybe_consolidate(record)
    now[0] += 3601
    assert service.maybe_consolidate(record)
    assert len(client.requests) == 2


def test_memory_write_recovery_deduplicates_written_files(tmp_path, monkeypatch):
    service, record = service_for(tmp_path, ScriptedClient(response(json.dumps({'memories': [entry()]}))))
    original = ManagedFiles.write
    class Crash(BaseException):
        pass
    def interrupted(files, path, data):
        original(files, path, data)
        if path == 'preference.md':
            raise Crash()
    monkeypatch.setattr(ManagedFiles, 'write', interrupted)
    with pytest.raises(Crash):
        service.extract(record, 'Run tests before handoff', 'Understood')
    monkeypatch.setattr(ManagedFiles, 'write', original)
    service.recover_pending(record)
    maintenance = next(item for item in service.store.list_recent(limit=20) if item.runtime_state.get('internal_maintenance'))
    assert maintenance.status == 'completed'
    assert maintenance.runtime_state['boundary'] == 'recovered_writes_completed'
    assert (tmp_path / 'user-memory' / 'MEMORY.md').read_text().count('preference.md') == 1


def test_memory_recovery_preflights_all_files_before_overwrite(tmp_path, monkeypatch):
    service, record = service_for(tmp_path, ScriptedClient(response(json.dumps({'memories': [entry(), entry('second')]}))))
    original = ManagedFiles.write
    class Crash(BaseException):
        pass
    def interrupted(files, path, data):
        if path == 'preference.md':
            raise Crash()
        original(files, path, data)
    monkeypatch.setattr(ManagedFiles, 'write', interrupted)
    with pytest.raises(Crash):
        service.extract(record, 'Remember preferences', 'Understood')
    monkeypatch.setattr(ManagedFiles, 'write', original)
    service.roots(record)['user'].write('second.md', b'user-written correction')
    service.recover_pending(record)
    assert not (tmp_path / 'user-memory' / 'preference.md').exists()
    assert (tmp_path / 'user-memory' / 'second.md').read_bytes() == b'user-written correction'
    maintenance = next(item for item in service.store.list_recent(limit=20) if item.runtime_state.get('internal_maintenance'))
    assert maintenance.status == 'blocked'


def test_validated_response_recovers_without_another_model_call(tmp_path, monkeypatch):
    service, record = service_for(tmp_path, ScriptedClient(response(json.dumps({'memories': [entry()]}))))
    original = service.store.save
    class Crash(BaseException):
        pass
    def interrupt(updated):
        original(updated)
        if updated.runtime_state.get('boundary') == 'response_validated':
            raise Crash()
    monkeypatch.setattr(service.store, 'save', interrupt)
    with pytest.raises(Crash):
        service.extract(record, 'remember preference', 'understood')
    monkeypatch.setattr(service.store, 'save', original)
    service.recover_pending(record)
    assert service.roots(record)['user'].read('preference.md')
    assert len(service.client.requests) == 1


def test_live_maintenance_request_is_not_taken_over(tmp_path):
    from threading import Event, Thread
    entered, release = Event(), Event()
    def model(*args):
        entered.set()
        assert release.wait(5)
        return response(json.dumps({'memories': [entry()]}))
    service, record = service_for(tmp_path, ScriptedClient(model))
    worker = Thread(target=lambda: service.extract(record, 'remember', 'ok'))
    worker.start()
    assert entered.wait(5)
    service.recover_pending(record)
    active = next(r for r in service.store.list_recent(limit=20) if r.runtime_state.get('internal_maintenance'))
    assert active.status == 'running'
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert service.roots(record)['user'].read('preference.md')


def test_user_memory_roots_are_identity_scoped(tmp_path):
    from types import SimpleNamespace
    service, record = service_for(tmp_path)
    first = replace(record, context_snapshot=SimpleNamespace(identity=SimpleNamespace(actor_user_id='alice')))
    second = replace(record, context_snapshot=SimpleNamespace(identity=SimpleNamespace(actor_user_id='bob')))
    service.roots(first)['user'].write('alice.md', b'private preference')
    assert service.roots(second)['user'].read('alice.md') is None
    assert service.roots(first)['project'].root == service.roots(second)['project'].root


def test_consolidation_prunes_stale_index_links_and_duplicates(tmp_path):
    service, record = service_for(tmp_path, ScriptedClient(response(json.dumps({'memories': [entry()]}))))
    root = service.roots(record)['user']
    root.write('MEMORY.md', b'- [gone](gone.md)\n- [gone](gone.md)\n')
    payload = service._request(record, 'consolidate', 'consolidate', 'topic metadata')
    service.apply(record, payload)
    index = root.read('MEMORY.md').decode()
    assert 'gone.md' not in index
    assert index.count('preference.md') == 1
