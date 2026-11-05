import { consumeSse } from '../lib/sse';
import { API_BASE } from './config.js';
import { $ } from './format.js';
import { setLastRequestId } from './feedback.js';
import { pushRequestLog } from './overview.js';
import { parseErrorDetail } from './http.js';

export async function postSse(path, payload, onEvent, signal) {
  const startedAt = performance.now();
  let status = "ERR";
  let requestId = "";
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-ID": $("user-id-input").value.trim() || "demo_user",
      },
      body: JSON.stringify(payload),
      signal,
    });
    status = response.status;
    requestId = response.headers.get("X-Request-ID") || "";
    setLastRequestId(requestId);
    if (!response.ok || !response.body) {
      const text = await response.text();
      let detail = text;
      try {
        detail = parseErrorDetail(JSON.parse(text), response.statusText);
      } catch {
        // Keep the response body as the most useful diagnostic.
      }
      throw new Error(`${response.status} ${detail || response.statusText}`);
    }

    await consumeSse(response.body, onEvent, signal);
    pushRequestLog({
      method: "POST",
      path,
      status,
      requestId,
      ok: true,
      ms: Math.round(performance.now() - startedAt),
    });
  } catch (error) {
    pushRequestLog({
      method: "POST",
      path,
      status,
      requestId,
      ok: false,
      ms: Math.round(performance.now() - startedAt),
    });
    throw error;
  }
}

export { parseSseBlock } from '../lib/sse';
