import { createApiClient } from '../lib/api';

export const apiClient = createApiClient({
 userId: () => $("user-id-input")?.value || "demo_user",
 onRequest: (entry) => { setLastRequestId(entry.requestId); if (document.getElementById("request-log")) pushRequestLog(entry); },
});
import { API_BASE } from './config.js';
import { state } from './state.js';
import { $ } from './format.js';
import { setLastRequestId, setRaw } from './feedback.js';
import { pushRequestLog, renderOverview } from './overview.js';

export function parseErrorDetail(body, fallback) {
  if (!body) {
    return fallback;
  }
  if (typeof body === "string") {
    return body;
  }
  if (typeof body.detail === "string") {
    return body.detail;
  }
  if (Array.isArray(body.detail)) {
    return body.detail
      .map((item) => `${(item.loc || []).slice(1).join(".") || "request"}: ${item.msg || "invalid"}`)
      .join("；");
  }
  return fallback;
}

export function fetchJson(path, options = {}) {
 return apiClient.request(path, options);
}

export async function checkHealth() {
  const pill = $("health-pill");
  try {
    const body = await fetchJson("/health");
    state.healthStatus = body.status;
    state.sessionStorageMode = body.session_storage || "unknown";
    const persistent = body.persistent_sessions !== false;
    pill.className = "status-pill ok";
    pill.innerHTML = `<span class="status-dot" aria-hidden="true"></span><span>${
      "服务已连接"
    }</span>`;
    pill.title = persistent ? "会话持久化已启用" : "当前为临时存储模式，重启会丢失会话";
    setRaw(body);
  } catch (error) {
    state.healthStatus = "offline";
    pill.className = "status-pill error";
    pill.innerHTML = '<span class="status-dot" aria-hidden="true"></span><span>连接失败</span>';
  } finally {
    renderOverview();
  }
}
