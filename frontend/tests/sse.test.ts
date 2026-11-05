import { expect, it } from 'vitest';
import { consumeSse } from '../src/lib/sse';

it('decodes UTF-8 and CRLF boundaries split across arbitrary chunks', async () => {
  const bytes = new TextEncoder().encode(': heartbeat\r\n\r\nevent: delta\r\ndata: {"text":"你好"}\r\n\r\ndata: final');
  const body = new ReadableStream<Uint8Array>({ start(controller) {
    for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
    controller.close();
  } });
  const events: unknown[] = [];
  await consumeSse(body, (event, data) => events.push([event, data]));
  expect(events).toEqual([['delta', { text: '你好' }], ['message', { text: 'final' }]]);
  expect(body.locked).toBe(false);
});

it('aborts a stalled reader without dispatching stale events', async () => {
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({ cancel() { cancelled = true; } });
  const controller = new AbortController();
  const pending = consumeSse(body, () => { throw new Error('unexpected event'); }, controller.signal);
  const rejection = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  controller.abort();
  await rejection;
  expect(cancelled).toBe(true);
  expect(body.locked).toBe(false);
});
