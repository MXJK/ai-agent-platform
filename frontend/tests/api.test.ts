import { describe, expect, it, vi } from 'vitest';
import { ApiError, createApiClient } from '../src/lib/api';

describe('API client', () => {
  it('preserves identity, multipart boundaries and request telemetry', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response('{"id":"doc-1"}', { status: 201, headers: { 'X-Request-ID': 'req-1' } }));
    const onRequest = vi.fn();
    const client = createApiClient({ userId: () => ' alice ', fetch: transport, onRequest });
    const form = new FormData();
    form.append('file', new Blob(['hello']), 'hello.txt');
    expect(await client.request('/documents', { method: 'POST', body: form })).toEqual({ id: 'doc-1' });
    const init = transport.mock.calls[0][1]!;
    expect(new Headers(init.headers).get('X-User-ID')).toBe('alice');
    expect(new Headers(init.headers).has('Content-Type')).toBe(false);
    expect(onRequest).toHaveBeenCalledWith(expect.objectContaining({ status: 201, requestId: 'req-1', ok: true }));
  });

  it('retains status and validation payload for form errors', async () => {
    const body = { detail: [{ loc: ['body', 'name'], msg: 'required' }] };
    const client = createApiClient({ userId: () => '', fetch: vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 422 })) });
    await expect(client.request('/skills')).rejects.toMatchObject({ name: 'ApiError', status: 422, body, message: '422 name: required' });
  });

  it('cancels pending requests and rejects late transport responses', async () => {
    let resolve!: (response: Response) => void;
    const transport = vi.fn<typeof fetch>().mockImplementation(() => new Promise(done => { resolve = done; }));
    const client = createApiClient({ userId: () => 'user', fetch: transport });
    const pending = client.request('/sessions');
    const rejection = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    client.abortAll();
    expect(transport.mock.calls[0][1]?.signal?.aborted).toBe(true);
    resolve(new Response('{}'));
    await rejection;
  });
});
