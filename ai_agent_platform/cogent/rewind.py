from __future__ import annotations

from dataclasses import replace
import json
import shlex
from uuid import uuid4

from ai_agent_platform.agents.coding.models import AgentToolExecution
from ai_agent_platform.integrations.permissions import canonical_arguments_hash
from ai_agent_platform.integrations.tools import ToolCall
from .filehistory import FileHistory
from .state import RUNTIME_ENGINE
from .tools.base import Tool


class RewindCoordinator:
    def __init__(self, runtime):
        self.runtime = runtime

    def prepare(self, record, state, adapter, context, arguments):
        history = FileHistory(record.workspace_root, record.conversation_id)
        tokens = shlex.split(arguments)
        if not tokens:
            targets = [f'{item.run_id} · conversation' for item in self.runtime.list_recent_runs(limit=1000)
                       if item.conversation_id == record.conversation_id and item.workspace_root == record.workspace_root
                       and item.runtime_engine == RUNTIME_ENGINE and item.status == 'completed']
            text = '\n'.join([*targets, *(f'{item.id} · cursor {item.message_index} · {len(item.backups)} files' for item in history.get_snapshots())])
            return self._complete(record, state, adapter, context, text or '当前没有可回退的文件快照。')
        if len(tokens) > 2 or (len(tokens) == 2 and tokens[1] not in {'all', 'conversation', 'files'}):
            raise ValueError('Use /rewind <snapshot-id> [all|conversation|files]')
        snapshot_id, mode = tokens[0], tokens[1] if len(tokens) == 2 else 'all'
        snapshot = next((item for item in history.get_snapshots() if item.id == snapshot_id), None)
        if mode == 'conversation' and snapshot is None:
            source = self.runtime.get_run(snapshot_id)
            if source.status != 'completed':
                raise ValueError('Conversation rewind requires a completed Run')
            preview = {'snapshot_id': snapshot_id, 'run_id': source.run_id,
                       'checkpoint_id': source.checkpoint_id,
                       'message_index': len(source.runtime_state.get('messages') or []),
                       'expected_hashes': {}, 'target_hashes': {}, 'patch': ''}
        elif snapshot is None:
            raise ValueError('Unknown or expired rewind snapshot')
        elif mode == 'conversation':
            preview = {'snapshot_id': snapshot_id, 'run_id': snapshot.run_id, 'checkpoint_id': snapshot.checkpoint_id,
                       'message_index': snapshot.message_index, 'expected_hashes': {}, 'target_hashes': {}, 'patch': ''}
        else:
            current = self.runtime._execution_workspace_runtime.history_files(context)
            preview = history.preview(snapshot_id, current)
        conversation = self._conversation(record, preview) if mode != 'files' else []
        arguments = {'snapshot_id': snapshot_id, 'mode': mode, 'preview_hash': canonical_arguments_hash(preview),
                     'conversation_hash': canonical_arguments_hash({'messages': conversation})}
        call = ToolCall('CogentRewind', arguments, 'rewind_' + uuid4().hex[:12])
        self._preflight(record, state, adapter, context, preview, call)
        pending = {'type': 'rewind', 'tool_calls': [self.runtime._call_dict(call)],
            'rewind_preview': preview, 'mode': mode,
            'approval_required_tools': [{'run_id': record.run_id, 'call_id': call.call_id, 'name': call.name,
                'arguments_hash': canonical_arguments_hash(arguments), 'permission_level': 'read_only' if mode == 'conversation' else 'write_safe',
                'reason': 'Review this diff and conversation boundary before rewinding.', 'provider': 'cogent'}]}
        self.runtime._emit(record.run_id, 'permission_required', 'waiting_approval', 'Rewind requires explicit confirmation.', pending)
        waiting = replace(record, status='waiting_approval', pending_approval=pending)
        waiting = self.runtime._persist(replace(waiting, result=self.runtime._result(waiting, state, answer='')), state, boundary='rewind_prepared')
        return self.runtime._result(waiting, state, answer='')

    def _conversation(self, record, preview):
        source = self.runtime.get_run(preview['run_id'])
        if source.runtime_engine != RUNTIME_ENGINE or source.conversation_id != record.conversation_id or source.workspace_root != record.workspace_root:
            raise PermissionError('Rewind target is outside this Cogent conversation')
        snapshots = self.runtime._run_store.list_runtime_snapshots(source.run_id, limit=100)
        snapshot = next((item for item in snapshots if item.snapshot_id == preview.get('checkpoint_id')), None)
        if snapshot is None:
            raise ValueError('Conversation snapshot has expired; files-only rewind remains available')
        from .conversation_pairing import ensure_tool_pairing
        from .context.platform import canonical_messages
        from .serialization import build_registry_messages
        messages = list(snapshot.state.get('messages') or [])[:int(preview['message_index'])]
        return build_registry_messages(ensure_tool_pairing(canonical_messages(messages)))

    def _preflight(self, record, state, adapter, context, preview, call):
        for path in preview['target_hashes']:
            permission = self.runtime._checker(record, state).check(Tool('WriteFile', 'write'), {'file_path': path})
            if permission.effect == 'deny':
                raise PermissionError(permission.reason)
        if preview['patch']:
            actual = ToolCall('sandbox.apply_patch', {'patch': preview['patch']}, call.call_id)
            decision = adapter._tools.resolve_permission(actual, context, phase='plan')
            if decision.effect == 'deny':
                raise PermissionError(decision.reason)
            return actual
        return None

    def resume(self, record, state, adapter, context, *, approved, approved_by):
        pending = record.pending_approval or {}
        call = ToolCall(**pending['tool_calls'][0])
        self.runtime.validate_pending_approval(record, approved_by=approved_by)
        if not approved:
            return self._complete(record, state, adapter, context, 'Rewind was rejected; files and conversation are unchanged.', status='blocked')
        preview = pending['rewind_preview']
        if canonical_arguments_hash(preview) != call.arguments.get('preview_hash') or pending['mode'] != call.arguments.get('mode'):
            raise PermissionError('Rewind preview changed after approval')
        conversation = self._conversation(record, preview) if pending['mode'] != 'files' else []
        if canonical_arguments_hash({'messages': conversation}) != call.arguments.get('conversation_hash'):
            raise PermissionError('Rewind conversation changed after approval')
        actual = self._preflight(record, state, adapter, context, preview, call)
        store = self.runtime._run_store
        digest = canonical_arguments_hash(call.arguments)
        cached = store.get_tool_execution(record.run_id, call.call_id)
        if cached and (cached.name != call.name or cached.arguments_hash != digest):
            raise PermissionError('Rewind call binding changed')
        if cached and cached.response is None:
            return self._complete(record, state, adapter, context, 'Rewind execution has an uncertain prior result; no write was repeated.', status='blocked')
        if not cached:
            history = FileHistory(record.workspace_root, record.conversation_id)
            current = {}
            if pending['mode'] != 'conversation':
                current = self.runtime._execution_workspace_runtime.history_files(context)
                fresh = history.preview(preview['snapshot_id'], current)
                if canonical_arguments_hash(fresh) != call.arguments['preview_hash']:
                    raise PermissionError('Rewind files changed after approval')
            execution_context = context
            if actual:
                approval = adapter._tools.issue_approval(actual, context, approved_by=approved_by)
                execution_context = context.with_approvals((approval,))
                decision = adapter._tools.resolve_permission(actual, execution_context, phase='execute')
                if decision.effect != 'allow':
                    raise PermissionError(decision.reason)
                history.begin(record.run_id + ':' + call.call_id, run_id=record.run_id,
                    message_index=max(1, len(state.messages) - 1), before=current, checkpoint_id=record.checkpoint_id)
            store.save_tool_execution(AgentToolExecution(record.run_id, call.call_id, call.name, digest, 'started'))
            if actual:
                self.runtime._emit(record.run_id, 'tool_started', 'running', 'Applying approved rewind in the execution workspace.',
                                   {'name': 'Rewind', 'call_id': call.call_id})
                result = adapter._tools.execute(actual, context=execution_context)
                if not result.ok:
                    store.save_tool_execution(AgentToolExecution(record.run_id, call.call_id, call.name, digest, 'failed', result.to_response()))
                    return self._complete(record, state, adapter, context, result.error or 'Rewind failed.', status='blocked')
                history.finish(record.run_id + ':' + call.call_id, after=self.runtime._execution_workspace_runtime.history_files(context))
            store.save_tool_execution(AgentToolExecution(record.run_id, call.call_id, call.name, digest, 'completed', {'ok': True}))
        elif cached.status != 'completed':
            return self._complete(record, state, adapter, context, 'The previous rewind failed; no write was repeated.', status='blocked')
        if pending['mode'] != 'files':
            state.messages = [{'role': 'system', 'content': state.system_prompt}, *conversation[1:]]
            state.usage_anchor = {}
        boundary = {'type': 'rewind', 'source_snapshot': preview['snapshot_id'], 'mode': pending['mode'], 'history_deleted': False}
        if boundary not in state.compact_boundaries:
            state.compact_boundaries.append(boundary)
        state.consumed_approvals.append(digest)
        self.runtime._emit(record.run_id, 'rewind_completed', 'completed', 'Rewind boundary appended; previous history remains available.', boundary)
        return self._complete(record, state, adapter, context, '回退已完成。原有对话保留；文件变更继续遵循当前 Workspace / ChangeSet 模式。')

    def _complete(self, record, state, adapter, context, answer, status='completed'):
        self.runtime._emit(record.run_id, 'answer_delta', status, answer, {'text': answer})
        return self.runtime._complete(replace(record, control_action=None), state, status=status, answer=answer,
            terminal_reason='rewind_completed' if status == 'completed' else 'rewind_rejected', tool_access=adapter._tools, context=context)
