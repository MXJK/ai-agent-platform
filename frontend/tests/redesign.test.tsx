import { act, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Workbench } from '../src/app/Workbench';
import { ThemeControl, applyTheme, bindTheme } from '../src/app/theme';
import { clearInspector, inspector, inspectorSelection, syncRunInspector } from '../src/lib/inspector';
import { state } from '../src/runtime/state.js';
import { setInspectorVisible, setPanelWidth, switchView } from '../src/runtime/navigation.js';
import { bindMemoryDraftProtection, clearMemorySelection, selectProjectMemory } from '../src/runtime/memory.js';
import { disposeResources } from '../src/lib/resources';
import { navigation } from '../src/app/navigation';

afterEach(() => { disposeResources(); clearInspector(); vi.restoreAllMocks(); act(() => navigation.set('chat')); document.body.className = ''; Object.assign(state, { latestRunBody: null, currentChangeSet: null, conversationId: '', latestRunId: '', latestRunConversationId: '', pendingSessionId: '', currentView: 'chat' }); });

it('clears details for a pending or different session, retains a user-selected tab through stream updates', () => {
  render(<Workbench />);
  Object.assign(state, { conversationId: 'a', latestRunConversationId: 'a', latestRunId: 'r1', latestRunBody: { status: 'running', stream_events: [{ type: 'tool_started', node: 'read' }] }, currentChangeSet: { run_id: 'r1', conversation_id: 'a', patch: '+safe' } });
  act(() => { inspectorSelection.set({ tab: 'trace', path: '' }); syncRunInspector(); });
  expect(inspector.getSnapshot().changes.patch).toBe('+safe');
  Object.assign(state, { latestRunBody: { status: 'completed' } });
  act(syncRunInspector);
  expect(inspector.getSnapshot().body.stream_events).toHaveLength(1);
  expect(inspectorSelection.getSnapshot().tab).toBe('trace');
  Object.assign(state, { pendingSessionId: 'b' });
  act(syncRunInspector);
  expect(inspector.getSnapshot().body).toBeNull();
  expect(inspector.getSnapshot().changes).toBeNull();
  Object.assign(state, { pendingSessionId: '', conversationId: 'b' });
  act(syncRunInspector);
  expect(inspector.getSnapshot().run).toBe('');
});

it('keeps a composer and provider draft mounted across theme and section changes', () => {
  const { container } = render(<Workbench />);
  const input = container.querySelector<HTMLTextAreaElement>('#chat-message-input')!;
  fireEvent.change(input, { target: { value: '主题切换时保留' } });
  fireEvent.change(container.querySelector('[aria-label="外观主题"]')!, { target: { value: 'light' } });
  expect(document.documentElement.dataset.theme).toBe('light');
  fireEvent.click(container.querySelector('#models-tab-1')!);
  fireEvent.click(container.querySelector('#models-tab-0')!);
  expect(container.querySelector('#chat-message-input')).toBe(input);
  expect(input.value).toBe('主题切换时保留');
});

it('follows OS changes only in system mode and still works when persistence is unavailable', () => {
  const media = new EventTarget() as EventTarget & { matches: boolean };
  media.matches = false;
  vi.stubGlobal('matchMedia', () => media);
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage disabled'); });
  const { container } = render(<ThemeControl />);
  bindTheme();
  const select = container.querySelector('select')!;
  fireEvent.change(select, { target: { value: 'system' } });
  media.matches = true;
  act(() => media.dispatchEvent(new Event('change')));
  expect(document.documentElement.dataset.theme).toBe('dark');
  fireEvent.change(select, { target: { value: 'light' } });
  act(() => media.dispatchEvent(new Event('change')));
  expect(document.documentElement.dataset.theme).toBe('light');
  applyTheme('dark');
  expect(document.documentElement.style.colorScheme).toBe('dark');
  vi.unstubAllGlobals();
});

it('constrains details so the center stays at least 560px and preserves the panel preference on management pages', () => {
  render(<Workbench />);
  vi.stubGlobal('innerWidth', 1200);
  setPanelWidth('sidebar', 260);
  expect(setPanelWidth('inspector', 480)).toBe(380);
  act(() => setInspectorVisible(true));
  state.currentView = 'sessions';
  act(() => setInspectorVisible(state.inspectorPreferred, { persist: false }));
  expect(document.querySelector<HTMLElement>('#inspector-panel')!.hidden).toBe(true);
  expect(state.inspectorPreferred).toBe(true);
  state.currentView = 'chat';
  act(() => setInspectorVisible(state.inspectorPreferred, { persist: false }));
  expect(document.querySelector<HTMLElement>('#inspector-panel')!.hidden).toBe(false);
  vi.unstubAllGlobals();
});

it('protects an unsaved memory draft when selecting another object', () => {
  render(<Workbench />);
  bindMemoryDraftProtection();
  const input = document.querySelector<HTMLTextAreaElement>('#memory-content-input')!;
  fireEvent.input(input, { target: { value: '未保存的决策' } });
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  clearMemorySelection();
  selectProjectMemory({ id: 'another', name: 'another', type: 'project', description: '', text: 'other', sha256: 'abc', mtime_ms: 0 });
  expect(input.value).toBe('未保存的决策');
  expect(document.querySelector('#memory-selection-label')!.textContent).toBe('未保存');
});

it('provides keyboard tab navigation without remounting the details', () => {
  const { container } = render(<Workbench />);
  act(() => inspectorSelection.set({ tab: 'changes', path: '' }));
  const tab = container.querySelector('#inspector-tab-changes')!;
  fireEvent.keyDown(tab, { key: 'ArrowRight' });
  expect(inspectorSelection.getSnapshot().tab).toBe('trace');
  expect(document.activeElement?.id).toBe('inspector-tab-trace');
  expect(container.querySelector('#inspector-tab-trace')?.getAttribute('aria-selected')).toBe('true');
});

it('keeps approvals actionable across themes and retains feedback after a failed submission', async () => {
  const { renderInlineAgentCheckpoint, handleInlineAgentAction } = await import('../src/runtime/checkpoints.js');
  const { container } = render(<Workbench />);
  Element.prototype.scrollIntoView = vi.fn();
  const output = container.querySelector('#chat-output')!;
  output.innerHTML = '<article class="chat-message assistant"><div class="chat-bubble"><div class="message-content"></div></div></article>';
  const content = output.querySelector('.message-content')!;
  const body = { run_id: 'approval-run', conversation_id: 'approval-session', status: 'waiting_approval', pending_approval: { reason: 'Confirm file write', tool_calls: [{ name: 'sandbox.write_file', arguments: { path: 'example.txt', content: 'test' } }] } };
  Object.assign(state, { conversationId: 'approval-session', latestRunId: 'approval-run', latestRunConversationId: 'approval-session' });
  renderInlineAgentCheckpoint(content, body);
  const card = output.querySelector<HTMLElement>('.inline-agent-checkpoint')!;
  const feedback = card.querySelector<HTMLTextAreaElement>('textarea')!;
  feedback.value = '保留补充要求';
  applyTheme('light'); applyTheme('dark');
  expect(card.querySelector('[data-inline-agent-action="approve"]')).toBeTruthy();
  expect(card.querySelector('[data-inline-agent-action="reject"]')).toBeTruthy();
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('test connection failure')));
  await handleInlineAgentAction(content, body, 'approve', card);
  expect(feedback.value).toBe('保留补充要求');
  expect(feedback.disabled).toBe(false);
  expect(card.querySelector<HTMLElement>('[role="alert"]')!.hidden).toBe(false);
  vi.unstubAllGlobals();
});

it('preserves question choices, free input and required-answer validation', async () => {
  const { renderInlineAgentCheckpoint, collectInlineQuestionAnswers, inlineQuestionAnswersComplete } = await import('../src/runtime/checkpoints.js');
  const { container } = render(<Workbench />);
  Element.prototype.scrollIntoView = vi.fn();
  const output = container.querySelector('#chat-output')!;
  output.innerHTML = '<div class="chat-bubble"><div class="message-content"></div></div>';
  renderInlineAgentCheckpoint(output.querySelector('.message-content'), { run_id: 'question', status: 'waiting_input', pending_approval: { questions: [{ id: 'direction', header: '选择方向', question: '先做哪一步？', options: [{ label: '补充测试' }, { label: '修复实现' }] }] } });
  const card = output.querySelector('.inline-agent-checkpoint')!;
  expect(inlineQuestionAnswersComplete(collectInlineQuestionAnswers(card))).toBe(false);
  fireEvent.click(card.querySelector('input')!);
  fireEvent.input(card.querySelector('textarea')!, { target: { value: '保留兼容性' } });
  expect(collectInlineQuestionAnswers(card)[0]).toMatchObject({ id: 'direction', selected: ['补充测试'], custom: '保留兼容性' });
  expect(card.querySelector<HTMLButtonElement>('[data-inline-agent-action="continue"]')!.disabled).toBe(false);
});

it('exposes distinct pause, resume and cancel controls and sends cancel to the existing Run API', async () => {
  const { updateComposerAvailability } = await import('../src/runtime/context.js');
  const { stopChat } = await import('../src/runtime/chat.js');
  render(<Workbench />);
  Object.assign(state, { conversationId: 'control-session', latestRunId: 'control-run', latestRunConversationId: 'control-session', latestRunStatus: 'running', currentSession: null, composerRunControlBusy: false, composerPauseRequestedRunId: '' });
  updateComposerAvailability();
  const control = document.querySelector<HTMLButtonElement>('#agent-run-control-btn')!;
  const cancel = document.querySelector<HTMLButtonElement>('#stop-chat-btn')!;
  expect(control.dataset.action).toBe('pause');
  expect(control.getAttribute('aria-label')).toBe('暂停 Agent');
  expect(cancel.classList.contains('hidden')).toBe(false);
  state.latestRunStatus = 'paused';
  updateComposerAvailability();
  expect(control.dataset.action).toBe('continue');
  const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: 'control-run', conversation_id: 'control-session', status: 'cancelled' })));
  vi.stubGlobal('fetch', request);
  await act(async () => { await stopChat(); });
  expect(request.mock.calls[0][0]).toContain('/agent/runs/control-run/cancel');
  expect(state.latestRunStatus).toBe('cancelled');
  expect(cancel.classList.contains('hidden')).toBe(true);
  vi.unstubAllGlobals();
});

it('drops the previous ChangeSet when a new Run becomes current', async () => {
  const { renderAgentRun } = await import('../src/runtime/runs.js');
  render(<Workbench />);
  Object.assign(state, { conversationId: 'same-session', latestRunId: 'old-run', latestRunConversationId: 'same-session', currentChangeSet: { run_id: 'old-run', conversation_id: 'same-session', patch: '+old' } });
  act(() => renderAgentRun({ run_id: 'new-run', conversation_id: 'same-session', status: 'running' }));
  expect(state.currentChangeSet).toBeNull();
  expect(inspector.getSnapshot().run).toBe('new-run');
  expect(inspector.getSnapshot().changes).toBeNull();
});
