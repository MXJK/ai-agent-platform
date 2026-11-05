import { listen, managedSetTimeout } from '../lib/resources';
import { FINAL_RUN_STATUSES } from './config.js';
import { state } from './state.js';
import { $, escapeHtml, formatDuration, formatTokenCount, iconMarkup, jsonPretty } from './format.js';
import { humanizeAgentNode } from './execution.js';
import { humanizeError, humanizeStatus, statusClass } from './feedback.js';
import { fetchJson } from './http.js';

export function auditRunMatchesStatus(run, filter) {
  if (filter === "all") return true;
  if (filter === "active") {
    return ["queued", "running", "waiting_approval", "waiting_input", "paused"].includes(run.status);
  }
  if (filter === "failed") return ["failed", "blocked", "partial"].includes(run.status);
  return run.status === filter;
}

export function renderAuditRuns() {
  const list = $("trace-run-list");
  const query = $("trace-run-search").value.trim().toLowerCase();
  const statusFilter = $("trace-run-status-filter").value;
  const runs = state.auditRuns.filter((run) => {
    const haystack = [run.run_id, run.conversation_id, run.workspace_id, run.latest_node]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return (!query || haystack.includes(query)) && auditRunMatchesStatus(run, statusFilter);
  });
  $("trace-run-count").textContent = `${runs.length} / ${state.auditRuns.length} 次运行`;
  list.setAttribute("aria-busy", "false");
  list.innerHTML = "";
  if (!state.auditRuns.length) {
    list.innerHTML = '<div class="trace-run-empty"><strong>还没有 Agent Run</strong><p>从对话工作台发起一次 Agent 任务后，审计记录会出现在这里。</p></div>';
    return;
  }
  if (!runs.length) {
    list.innerHTML = '<div class="trace-run-empty"><strong>没有匹配的 Run</strong><p>调整搜索词或状态筛选后重试。</p></div>';
    return;
  }
  for (const run of runs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `trace-run-item${run.run_id === state.auditRunId ? " active" : ""}`;
    button.dataset.auditRunId = run.run_id;
    button.setAttribute("aria-current", run.run_id === state.auditRunId ? "true" : "false");
    button.innerHTML = `
      <span class="trace-run-item-heading">
        <strong>${escapeHtml(run.run_id)}</strong>
        <span class="status-pill ${statusClass(run.status)}"><span class="status-dot" aria-hidden="true"></span>${escapeHtml(humanizeStatus(run.status))}</span>
      </span>
      <span class="trace-run-item-node">${escapeHtml(humanizeAgentNode(run.latest_node))}</span>
      <span class="trace-run-item-meta">
        <span>${escapeHtml(run.conversation_id)}</span>
        <span>${formatTokenCount(run.trace_count)} 步 · ${formatTokenCount(run.tool_call_count)} 工具</span>
      </span>
    `;
    list.appendChild(button);
  }
}

export function auditEventMatches(event, filter) {
  if (filter === "all") return true;
  const type = String(event.type || "");
  if (filter === "error") {
    return type.includes("error") || type.includes("failed") || event.output?.ok === false;
  }
  if (filter === "tool") return type.startsWith("tool_");
  if (filter === "approval") return type.startsWith("approval_");
  if (filter === "state") {
    return type.startsWith("run_") || type.startsWith("control_") || type === "input_required";
  }
  return false;
}

export function auditEventCategory(event) {
  const type = String(event.type || "");
  if (type.includes("error") || type.includes("failed") || event.output?.ok === false) return "error";
  if (type.startsWith("tool_")) return "tool";
  if (type.startsWith("approval_")) return "approval";
  if (type.startsWith("run_") || type.startsWith("control_") || type === "input_required") return "state";
  return "node";
}

export function auditEventTitle(event) {
  const output = event.output || {};
  const labels = {
    run_queued: "Run 已进入队列",
    run_started: "Run 开始执行",
    run_resume_requested: "Run 请求恢复",
    run_paused: "Run 已暂停",
    run_completed: "Run 已完成",
    run_partial: "Run 部分完成",
    run_blocked: "Run 受阻",
    run_cancelled: "Run 已取消",
    run_failed: "Run 执行失败",
    input_required: "等待用户输入",
    node_started: `开始阶段 · ${humanizeAgentNode(event.node)}`,
    node_completed: humanizeAgentNode(event.node),
    reasoning_summary: "阶段思路摘要",
    approval_required: "请求工具审批",
    approval_decided: output.approved ? "审批已通过" : "审批已拒绝",
    tool_selected: `选择工具 · ${output.name || "未知工具"}`,
    tool_started: `开始工具 · ${output.name || "未知工具"}`,
    tool_result: `工具完成 · ${output.name || "未知工具"}`,
    tool_error: `工具失败 · ${output.name || "未知工具"}`,
    answer_delta: "回答增量",
    answer_reset: "重置临时回答",
    answer_completed: "回答生成完成",
  };
  return labels[event.type] || event.summary || event.type || "审计事件";
}

export function auditEventCategoryLabel(event) {
  return {
    state: "状态转移",
    node: "执行节点",
    tool: event.type === "tool_selected" ? "工具选择" : "工具结果",
    approval: "审批",
    error: "错误",
  }[auditEventCategory(event)] || "事件";
}

export function buildAuditEvents(run, storedEvents) {
  const events = [...(storedEvents || [])];
  let sequence = events.reduce((maximum, event) => Math.max(maximum, Number(event.sequence || 0)), 0);
  const toolCalls = new Set(events.filter((event) => event.type === "tool_selected").map((event) => event.output?.call_id));
  const toolResults = new Set(events.filter((event) => ["tool_result", "tool_error"].includes(event.type)).map((event) => event.output?.call_id));
  for (const call of run.result?.tool_calls || []) {
    if (toolCalls.has(call.call_id)) continue;
    events.push({
      sequence: ++sequence,
      type: "tool_selected",
      status: "running",
      node: null,
      summary: `Tool selected: ${call.name}.`,
      output: call,
      reconstructed: true,
    });
  }
  for (const result of run.result?.tool_results || []) {
    if (toolResults.has(result.call_id)) continue;
    events.push({
      sequence: ++sequence,
      type: result.ok ? "tool_result" : "tool_error",
      status: "running",
      node: null,
      summary: result.ok ? `Tool completed: ${result.name}.` : `Tool failed: ${result.name}.`,
      output: result,
      reconstructed: true,
    });
  }
  if (run.pending_approval && !events.some((event) => event.type === "approval_required")) {
    events.push({
      sequence: ++sequence,
      type: "approval_required",
      status: run.status,
      node: run.latest_node,
      summary: "Agent run is waiting for approval.",
      output: run.pending_approval,
      reconstructed: true,
    });
  }
  if (run.error && !events.some((event) => event.type === "run_failed")) {
    events.push({
      sequence: ++sequence,
      type: "run_failed",
      status: run.status,
      node: run.latest_node,
      summary: "Agent run failed.",
      output: { error: run.error, errors: run.errors || [] },
      reconstructed: true,
    });
  }
  return events.sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0));
}

export function auditEventSummary(event) {
  const output = event.output || {};
  if (event.type === "tool_selected") {
    return [output.call_id, output.source].filter(Boolean).join(" · ") || "已记录工具与参数";
  }
  if (["tool_result", "tool_error"].includes(event.type)) {
    const duration = output.duration_ms !== undefined ? formatDuration(output.duration_ms) : "";
    return [output.call_id, duration, output.cached ? "缓存命中" : ""].filter(Boolean).join(" · ") || event.summary;
  }
  if (event.type === "approval_decided") {
    return [output.actor_user_id ? `操作者 ${output.actor_user_id}` : "", output.feedback || "无补充意见"]
      .filter(Boolean)
      .join(" · ");
  }
  return event.summary || humanizeAgentNode(event.node);
}

export const AUDIT_PAGE_SIZE = 100;

export function renderAuditTimeline() {
  const list = $("trace-audit-timeline");
  const visible = state.auditEvents.filter((event) => auditEventMatches(event, state.auditCategory));
  const pages = Math.max(1, Math.ceil(visible.length / AUDIT_PAGE_SIZE));
  state.auditPage = Math.min(state.auditPage, pages - 1);
  const start = state.auditPage * AUDIT_PAGE_SIZE;
  $("trace-audit-visible-count").textContent = `${visible.length} 条`;
  $("trace-audit-page-label").textContent = `${state.auditPage + 1} / ${pages} 页`;
  $("trace-audit-prev").disabled = state.auditPage === 0;
  $("trace-audit-next").disabled = state.auditPage >= pages - 1;
  const existing = new Map([...list.children].map((item) => [item.auditKey, item]));
  const items = [];
  if (!visible.length) {
    list.innerHTML = '<li class="trace-audit-placeholder">当前筛选下没有审计事件。</li>';
    return;
  }
  for (const event of visible.slice(start, start + AUDIT_PAGE_SIZE)) {
    const key = `${state.auditRunId}:${event.reconstructed ? "reconstructed" : "stored"}:${event.sequence}`;
    const previous = existing.get(key);
    if (previous && (previous.auditEvent === event || (event.reconstructed && jsonPretty(previous.auditEvent) === jsonPretty(event)))) {
      items.push(previous);
      continue;
    }
    const category = auditEventCategory(event);
    const item = document.createElement("li");
    item.className = `trace-audit-event category-${category}${event.type === "tool_error" ? " event-failed" : ""}`;
    item.auditKey = key;
    item.auditEvent = event;
    const payload = event.output || {};
    const hasPayload = Object.keys(payload).length > 0;
    item.innerHTML = `
      <span class="trace-audit-marker" aria-hidden="true"></span>
      <article>
        <header>
          <div>
            <span class="trace-audit-event-kind">${escapeHtml(auditEventCategoryLabel(event))}</span>
            <h3>${escapeHtml(auditEventTitle(event))}</h3>
          </div>
          <span class="trace-audit-sequence">#${escapeHtml(event.sequence)}</span>
        </header>
        <p>${escapeHtml(auditEventSummary(event))}</p>
        <div class="trace-audit-event-meta">
          <span>${escapeHtml(humanizeStatus(event.status))}</span>
          ${event.node ? `<span>${escapeHtml(event.node)}</span>` : ""}
          ${event.reconstructed ? "<span>由 Run 结果补录</span>" : ""}
        </div>
        ${hasPayload ? `
          <details class="trace-audit-payload">
            <summary>${event.type === "tool_selected" ? "查看精确参数" : "查看完整事件数据"}</summary>
            <pre><code></code></pre>
          </details>
        ` : ""}
      </article>
    `;
    const details = item.querySelector("details");
    if (details) {
      listen(details, "toggle", () => {
        if (!details.open || details.dataset.loaded) return;
        details.querySelector("code").textContent = jsonPretty(payload);
        details.dataset.loaded = "true";
      });
    }
    items.push(item);
  }
  // Keep unchanged rows attached so polling preserves focus and expanded payloads.
  for (const child of [...list.children]) {
    if (!items.includes(child)) child.remove();
  }
  items.forEach((item, index) => {
    if (list.children[index] !== item) list.insertBefore(item, list.children[index] || null);
  });
}

export function renderAuditDetail() {
  const run = state.auditRunBody;
  const empty = $("trace-audit-empty");
  const content = $("trace-audit-content");
  if (!run) {
    empty.hidden = false;
    content.hidden = true;
    return;
  }
  empty.hidden = true;
  content.hidden = false;
  $("trace-audit-run-id").textContent = run.run_id;
  const status = $("trace-audit-status");
  status.className = `status-pill ${statusClass(run.status)}`;
  status.innerHTML = `<span class="status-dot" aria-hidden="true"></span>${escapeHtml(humanizeStatus(run.status))}`;
  $("trace-audit-node").textContent = run.latest_node
    ? `当前节点：${humanizeAgentNode(run.latest_node)} · ${run.latest_node}`
    : "尚未进入执行节点";
  $("trace-audit-session").textContent = run.conversation_id;
  $("trace-audit-workspace").textContent = run.workspace_id;
  $("trace-audit-checkpoint").textContent = run.checkpoint_id || "尚无";
  $("trace-audit-cursor").textContent = state.auditEvents.length
    ? `#${state.auditCursor}`
    : "—";
  const toolEvents = state.auditEvents.filter((event) => event.type === "tool_selected");
  const approvals = state.auditEvents.filter((event) => event.type.startsWith("approval_"));
  const errors = state.auditEvents.filter((event) => auditEventMatches(event, "error"));
  $("trace-count-events").textContent = formatTokenCount(state.auditEvents.length);
  $("trace-count-tools").textContent = formatTokenCount(toolEvents.length);
  $("trace-count-approvals").textContent = formatTokenCount(approvals.length);
  $("trace-count-errors").textContent = formatTokenCount(errors.length);
  $("trace-audit-live").hidden = !["queued", "running", "waiting_approval", "waiting_input", "paused"].includes(run.status);
  renderAuditTimeline();
}

export function clearAuditPoll() {
  window.clearTimeout(state.auditPollTimer);
  state.auditPollTimer = null;
}

export function scheduleAuditPoll() {
  clearAuditPoll();
  if (state.currentView !== "trace-audit" || !state.auditRunBody) return;
  if (FINAL_RUN_STATUSES.has(state.auditRunBody.status)) return;
  state.auditPollTimer = managedSetTimeout(() => {
    loadAuditRun(state.auditRunId, { silent: true }).catch(() => {});
  }, 2500);
}

export async function loadAuditRun(runId, options = {}) {
  if (!runId) return;
  clearAuditPoll();
  const generation = ++state.auditRequestGeneration;
  const sameRun = state.auditRunBody?.run_id === runId;
  if (!sameRun) {
    state.auditStoredEvents = [];
    state.auditCursor = 0;
    state.auditPage = 0;
    state.auditRunBody = null;
    state.auditEvents = [];
  }
  state.auditRunId = runId;
  renderAuditRuns();
  if (!sameRun) {
    $("trace-audit-empty").hidden = true;
    $("trace-audit-content").hidden = false;
    $("trace-audit-timeline").innerHTML = '<li class="trace-audit-placeholder">正在装载审计事实…</li>';
  }
  try {
    const [run, eventBody] = await Promise.all([
      fetchJson(`/agent/runs/${encodeURIComponent(runId)}`),
      fetchJson(`/agent/runs/${encodeURIComponent(runId)}/events?after=${state.auditCursor}`),
    ]);
    if (generation !== state.auditRequestGeneration) return;
    state.auditRunBody = run;
    const known = new Set(state.auditStoredEvents.map((event) => event.sequence));
    for (const event of eventBody.events || []) {
      if (!known.has(event.sequence)) {
        state.auditStoredEvents.push(event);
        known.add(event.sequence);
      }
      state.auditCursor = Math.max(state.auditCursor, Number(event.sequence || 0));
    }
    state.auditEvents = buildAuditEvents(run, state.auditStoredEvents);
    state.auditRuns = state.auditRuns.map((item) => item.run_id === run.run_id
      ? {
          ...item,
          status: run.status,
          checkpoint_id: run.checkpoint_id,
          latest_node: run.latest_node,
          trace_count: run.trace?.length || 0,
          tool_call_count: run.result?.tool_calls?.length || item.tool_call_count || 0,
          error_count: (run.errors?.length || 0) + (run.error ? 1 : 0),
          has_pending_approval: Boolean(run.pending_approval),
        }
      : item);
    renderAuditRuns();
    renderAuditDetail();
    scheduleAuditPoll();
  } catch (error) {
    if (generation !== state.auditRequestGeneration) return;
    clearAuditPoll();
    if (!options.silent) {
      state.auditRunBody = null;
      state.auditEvents = [];
      $("trace-audit-empty").hidden = false;
      $("trace-audit-content").hidden = true;
      $("trace-audit-empty").innerHTML = `
        ${iconMarkup("shield")}
        <h2>Trace 加载失败</h2>
        <p>${escapeHtml(humanizeError(error))}。请刷新后重试。</p>
      `;
    }
    throw error;
  }
}

export async function loadAuditRuns() {
  const button = $("refresh-trace-audit-btn");
  button.disabled = true;
  $("trace-run-list").setAttribute("aria-busy", "true");
  try {
    const body = await fetchJson("/agent/runs?limit=50");
    state.auditRuns = body.runs || [];
    if (!state.auditRuns.some((run) => run.run_id === state.auditRunId)) {
      state.auditRunId = state.auditRuns[0]?.run_id || "";
      state.auditRunBody = null;
      state.auditEvents = [];
    }
    renderAuditRuns();
    if (state.auditRunId) await loadAuditRun(state.auditRunId);
    else renderAuditDetail();
  } catch (error) {
    $("trace-run-list").setAttribute("aria-busy", "false");
    $("trace-run-list").innerHTML = `<div class="trace-run-empty error"><strong>Run 列表加载失败</strong><p>${escapeHtml(humanizeError(error))}</p></div>`;
    throw error;
  } finally {
    button.disabled = false;
  }
}
