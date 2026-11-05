export type RequestLog = {
  method: string;
  path: string;
  status: number | 'ERR';
  requestId: string;
  ok: boolean;
  ms: number;
};

export class ApiError extends Error {
  constructor(public status: number, public body: unknown, detail: string) {
    super(`${status} ${detail}`);
    this.name = 'ApiError';
  }
}

export function errorDetail(body: unknown, fallback: string): string {
  if (typeof body === 'string') return body || fallback;
  if (!body || typeof body !== 'object') return fallback;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(item =>
    `${(item.loc || []).slice(1).join('.') || 'request'}: ${item.msg || 'invalid'}`).join('；');
  if (detail && typeof detail === 'object') {
    const value = detail as { message?: string; error?: string; code?: string };
    return value.message || value.error || value.code || fallback;
  }
  return fallback;
}

export function createApiClient(options: {
  base?: string;
  userId: () => string;
  onRequest?: (entry: RequestLog) => void;
  fetch?: typeof fetch;
}) {
  const pending = new Set<AbortController>();
  return {
    async request<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
      const controller = new AbortController();
      const abort = () => controller.abort(init.signal?.reason);
      if (init.signal?.aborted) abort();
      else init.signal?.addEventListener('abort', abort, { once: true });
      pending.add(controller);
      const startedAt = performance.now();
      let status: number | 'ERR' = 'ERR';
      let requestId = '';
      let ok = false;
      try {
        const headers = new Headers(init.headers);
        headers.set('X-User-ID', options.userId().trim() || 'demo_user');
        if (!(init.body instanceof FormData) && !headers.has('Content-Type')) {
          headers.set('Content-Type', 'application/json');
        }
        const response = await (options.fetch || fetch)(`${options.base ?? '/api/v1'}${path}`, {
          ...init, headers, signal: controller.signal,
        });
        status = response.status;
        requestId = response.headers.get('X-Request-ID') || '';
        const text = await response.text();
        let body: unknown = null;
        if (text) {
          try { body = JSON.parse(text); } catch { body = text; }
        }
        if (!response.ok) throw new ApiError(status, body, errorDetail(body, response.statusText));
        // Some test transports and proxies can deliver after cancellation.
        controller.signal.throwIfAborted();
        ok = true;
        return body as T;
      } finally {
        pending.delete(controller);
        init.signal?.removeEventListener('abort', abort);
        options.onRequest?.({ method: init.method || 'GET', path, status, requestId, ok,
          ms: Math.round(performance.now() - startedAt) });
      }
    },
    abortAll() {
      pending.forEach(controller => controller.abort());
      pending.clear();
    },
  };
}
