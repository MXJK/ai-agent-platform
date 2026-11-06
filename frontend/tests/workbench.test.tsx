import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Workbench } from '../src/app/Workbench';
import { navigation, views } from '../src/app/navigation';
import { init } from '../src/runtime/bootstrap.js';
import { disposeResources } from '../src/lib/resources';
import { clearSlots } from '../src/components/ReactSlot';

afterEach(() => { disposeResources(); clearSlots(); vi.unstubAllGlobals(); });

it('keeps all nine routes and preserves drafts when switching panels', () => {
  const { container } = render(<Workbench />);
  const input = container.querySelector<HTMLTextAreaElement>('#chat-message-input')!;
  fireEvent.change(input, { target: { value: '保留这段草稿' } });
  for (const view of views) {
    act(() => navigation.set(view));
    const visible = [...container.querySelectorAll<HTMLElement>('[data-view-panel]')].filter(panel => !panel.hidden);
    expect(visible.map(panel => panel.dataset.viewPanel)).toEqual([view]);
    expect(container.querySelector('#chat-message-input')).toBe(input);
  }
  expect(input.value).toBe('保留这段草稿');
  act(() => navigation.set('chat'));
});

it('boots real controller modules against the React DOM and wires navigation once', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.scrollTo = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    let body: unknown = {};
    if (url.includes('/model-registry')) body = { connections: [], models: [], routing_policies: ['smart'] };
    if (url.includes('/mcp/servers')) body = { servers: [], config_writable: false, runtime_enabled: false };
    if (url.includes('/skills')) body = { skills: [], diagnostics: [], writable: false };
    return new Response(JSON.stringify(body));
  }));
  const { container } = render(<Workbench />);
  await act(async () => { await init(); });
  expect(container.querySelector('#chat-view')?.hasAttribute('hidden')).toBe(false);
  const modelButton = container.querySelector<HTMLButtonElement>('.primary-nav [data-view="models"]')!;
  fireEvent.click(modelButton);
  await waitFor(() => expect(container.querySelector('#models-view')?.hasAttribute('hidden')).toBe(false));
  await waitFor(() => expect(container.querySelectorAll('.provider-card').length).toBe(7));
  expect(screen.getByText('保存 Provider 后，打开“注册模型”添加第一个模型。')).toBeTruthy();
});
