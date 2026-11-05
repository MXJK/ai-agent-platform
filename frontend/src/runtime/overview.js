import { state } from './state.js';
import { $, escapeHtml, formatDate } from './format.js';
import { humanizeStatus } from './feedback.js';

export function setChatStatus(label, status = "neutral") {
  const node = $("chat-meta");
  node.className = `status-pill ${status}`;
  node.innerHTML = `<span class="status-dot" aria-hidden="true"></span><span>${escapeHtml(label)}</span>`;
}

export function pushRequestLog(entry) {
  state.requestLog.unshift({ at: new Date().toISOString(), ...entry });
  state.requestLog = state.requestLog.slice(0, 50);
  renderRequestLog();
  renderOverview();
}

export function renderRequestLog() {
  const list = $("request-log");
  list.innerHTML = "";
  if (state.requestLog.length === 0) {
    list.innerHTML = '<div class="empty-state">暂无请求</div>';
    return;
  }
  for (const item of state.requestLog) {
    const row = document.createElement("div");
    row.className = `request-item ${item.ok ? "ok" : "error"}`;
    row.innerHTML = `
      <span class="request-method">${escapeHtml(item.method)}</span>
      <span class="request-path">${escapeHtml(item.path)}</span>
      <span class="request-time">${escapeHtml(formatDate(item.at))} · ${escapeHtml(item.ms)}ms</span>
      <span class="request-status">${escapeHtml(item.status)}</span>
    `;
    list.appendChild(row);
  }
}

export function renderOverview() {
  $("metric-api").textContent = humanizeStatus(state.healthStatus);
  $("metric-sessions").textContent = String(state.sessions.length);
  $("metric-run").textContent = state.latestRunId
    ? humanizeStatus(state.latestRunStatus)
    : "暂无";
  const latest = state.requestLog[0];
  $("metric-request").textContent = latest ? `${latest.status} · ${latest.ms}ms` : "暂无";
}
