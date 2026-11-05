import { fireEvent, render } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ProviderList, SkillList, McpList } from '../src/features/registry-lists';

it('keeps unsaved provider credentials and toggles through telemetry refreshes', () => {
  const first = { provider: 'openai', enabled: true, status: 'available', credential_configured: true, model_count: 1 };
  const { container, rerender } = render(<ProviderList connections={[first]} />);
  const card = container.querySelector('[data-provider="openai"]')!;
  const input = card.querySelector<HTMLInputElement>('[data-provider-key]')!;
  const enabled = card.querySelector<HTMLInputElement>('[data-provider-enabled]')!;
  fireEvent.change(input, { target: { value: 'test-only-draft' } });
  fireEvent.click(enabled);
  rerender(<ProviderList connections={[{ ...first, model_count: 2 }]} />);
  expect(input.value).toBe('test-only-draft');
  expect(enabled.checked).toBe(false);
  expect(card.textContent).not.toContain('test-only-draft');
});

it('escapes registry text and keeps built-in Skills read only', () => {
  const { container } = render(<SkillList skills={[{ name: '<script>alert(1)</script>', description: '<img src=x>', source: 'builtin', enabled: true, editable: false }]} />);
  expect(container.querySelector('script')).toBeNull();
  expect(container.querySelector('img')).toBeNull();
  expect(container.querySelector('button')).toBeNull();
  expect(container.textContent).toContain('<script>alert(1)</script>');
});

it('disables MCP testing when its runtime is disabled', () => {
  const { container } = render(<McpList servers={[{ name: 'demo', endpoint: 'local', state: 'disabled', transport: 'stdio', enabled: true, required: false }]} runtimeEnabled={false} />);
  expect(container.querySelector<HTMLButtonElement>('[data-mcp-action="test"]')?.disabled).toBe(true);
});
