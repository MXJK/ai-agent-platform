import { createStore } from './store';
import { state } from '../runtime/state.js';
import { humanizeStatus } from '../runtime/feedback.js';
import { setInspectorVisible } from '../runtime/navigation.js';

// A read-only projection of the existing controller state. No fetch or timers.
type Snapshot = { session: string; run: string; body: any; changes: any; workspace: string; model: string; permission: string; budget: string };
const empty: Snapshot = { session: '', run: '', body: null, changes: null, workspace: '', model: '', permission: '', budget: '' };
export const inspector = createStore<Snapshot>(empty);
export const inspectorSelection = createStore({ tab: 'changes', path: '' });
export function clearInspector() { inspector.set({ ...empty }); }
export function syncRunInspector() {
  const valid = !state.pendingSessionId && state.latestRunConversationId === state.conversationId;
  const run = valid ? state.latestRunId : '';
  const prior = inspector.getSnapshot();
  const body: any = valid ? state.latestRunBody : null;
  const changes: any = state.currentChangeSet;
  // Final GET snapshots may omit stream_events; retain the same run's event history.
  const events = body?.stream_events || (prior.run === run ? prior.body?.stream_events : undefined);
  inspector.set({
    session: state.conversationId, run, body: body ? { ...body, stream_events: events } : null,
    changes: changes?.run_id === run && changes?.conversation_id === state.conversationId ? changes : null,
    workspace: document.getElementById('composer-workspace-label')?.textContent || '未选择工作区',
    model: document.getElementById('composer-model-label')?.textContent || '默认配置',
    permission: (document.getElementById('permission-mode-input') as HTMLSelectElement | null)?.selectedOptions[0]?.textContent || '逐次确认',
    budget: document.getElementById('composer-context-budget')?.title || '暂无上下文用量数据',
  });
  const status = document.getElementById('workbench-run-status');
  if (status) { status.textContent = run ? humanizeStatus(state.latestRunStatus) : '等待输入'; status.title = run; }
}
export function openInspectorChanges(path = '') {
  inspectorSelection.set({ tab: 'changes', path });
  setInspectorVisible(true);
}
