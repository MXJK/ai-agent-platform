const API_BASE = "/api/v1";

const state = {
  conversationId: "",
  latestRunId: "",
  latestRunStatus: "",
  healthStatus: "checking",
  sessions: [],
  requestLog: [],
  latestRepositoryIndex: null,
};

const $ = (id) => document.getElementById(id);

function jsonPretty(value) {
  return JSON.stringify(value, null, 2);
}

function setRaw(value) {
  $("raw-output").textContent =
    typeof value === "string" ? value : jsonPretty(value);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function csvValues(value) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function numberValue(id, fallback) {
  const value = Number.parseInt($(id).value, 10);
  return Number.isFinite(value) ? value : fallback;
}

function optionalModelFields() {
  const provider = $("provider-input").value.trim();
  const model = $("model-input").value.trim();
  return {
    ...(provider ? { provider } : {}),
    ...(model ? { model } : {}),
  };
}

function formatDate(value) {
  if (!value) {
    return "unknown";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function pushRequestLog(entry) {
  state.requestLog.unshift({
    at: new Date().toISOString(),
    ...entry,
  });
  state.requestLog = state.requestLog.slice(0, 40);
  renderRequestLog();
  renderOverview();
}

function renderOverview() {
  $("metric-api").textContent = state.healthStatus;
  $("metric-sessions").textContent = String(state.sessions.length);
  $("metric-run").textContent = state.latestRunId
    ? `${state.latestRunStatus || "unknown"}`
    : "none";
  const latest = state.requestLog[0];
  $("metric-request").textContent = latest
    ? `${latest.status} ${latest.ms}ms`
    : "none";
}

function renderRequestLog() {
  const node = $("request-log");
  node.innerHTML = "";
  if (state.requestLog.length === 0) {
    node.innerHTML = '<div class="empty-state">No requests yet</div>';
    return;
  }
  for (const item of state.requestLog) {
    const row = document.createElement("div");
    row.className = `data-item ${item.ok ? "ok" : "error"}`;
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(item.method)} ${escapeHtml(item.path)}</strong>
        <span>${escapeHtml(formatDate(item.at))}</span>
      </div>
      <code>${escapeHtml(item.status)} ${escapeHtml(item.ms)}ms</code>
    `;
    node.appendChild(row);
  }
}

function setTrace(items) {
  const traceList = $("trace-list");
  traceList.innerHTML = "";
  if (!items || items.length === 0) {
    traceList.innerHTML = '<div class="empty-state">No trace yet</div>';
    return;
  }
  for (const item of items) {
    const node = document.createElement("div");
    node.className = "trace-item";
    node.innerHTML = `
      <strong>${escapeHtml(item.step ?? "")}. ${escapeHtml(item.node ?? "step")}</strong>
      <span>${escapeHtml(item.summary ?? "")}</span>
    `;
    traceList.appendChild(node);
  }
}

async function fetchJson(path, options = {}) {
  const method = options.method || "GET";
  const startedAt = performance.now();
  let status = "ERR";
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });
    status = response.status;
    const text = await response.text();
    let body = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    if (!response.ok) {
      const detail = body && body.detail ? body.detail : response.statusText;
      throw new Error(`${response.status} ${detail}`);
    }
    pushRequestLog({
      method,
      path,
      status,
      ok: true,
      ms: Math.round(performance.now() - startedAt),
    });
    return body;
  } catch (error) {
    pushRequestLog({
      method,
      path,
      status,
      ok: false,
      ms: Math.round(performance.now() - startedAt),
    });
    throw error;
  }
}

async function checkHealth() {
  const pill = $("health-pill");
  try {
    const body = await fetchJson("/health");
    state.healthStatus = body.status;
    pill.textContent = body.status;
    pill.className = "pill ok";
    setRaw(body);
  } catch (error) {
    state.healthStatus = "offline";
    pill.textContent = "offline";
    pill.className = "pill error";
  } finally {
    renderOverview();
  }
}

async function ensureSession() {
  const existing = $("conversation-id-input").value.trim();
  if (existing) {
    state.conversationId = existing;
    return existing;
  }
  return createSession();
}

async function createSession() {
  $("session-status").textContent = "Creating session...";
  const body = await fetchJson("/sessions", {
    method: "POST",
    body: JSON.stringify({
      user_id: $("user-id-input").value.trim() || "demo_user",
    }),
  });
  state.conversationId = body.id;
  $("conversation-id-input").value = body.id;
  $("session-status").textContent = `Active: ${body.id}`;
  setRaw(body);
  await listSessions(false);
  await loadSession(false);
  return body.id;
}

async function listSessions(showRaw = true) {
  const body = await fetchJson("/sessions");
  state.sessions = body.sessions || [];
  renderSessions();
  renderOverview();
  if (showRaw) {
    setRaw(body);
  }
  return body;
}

function renderSessions() {
  const list = $("sessions-list");
  list.innerHTML = "";
  if (state.sessions.length === 0) {
    list.innerHTML = '<div class="empty-state">No sessions</div>';
    return;
  }
  for (const session of state.sessions) {
    const item = document.createElement("button");
    item.className = "data-item selectable";
    item.dataset.sessionId = session.id;
    item.innerHTML = `
      <div>
        <strong>${escapeHtml(session.id)}</strong>
        <span>${escapeHtml(session.user_id)} · ${escapeHtml(formatDate(session.created_at))}</span>
      </div>
    `;
    list.appendChild(item);
  }
}

async function loadSession(showRaw = true) {
  const conversationId = await ensureSession();
  const [session, summary, messages] = await Promise.all([
    fetchJson(`/sessions/${encodeURIComponent(conversationId)}`),
    fetchJson(`/sessions/${encodeURIComponent(conversationId)}/summary`),
    fetchJson(`/sessions/${encodeURIComponent(conversationId)}/messages`),
  ]);
  $("session-status").textContent = `Loaded: ${session.id}`;
  renderSessionSummary(summary);
  renderMessages(messages.messages || []);
  if (showRaw) {
    setRaw({ session, summary, messages });
  }
}

async function loadSessionSummary() {
  const conversationId = await ensureSession();
  const body = await fetchJson(`/sessions/${encodeURIComponent(conversationId)}/summary`);
  renderSessionSummary(body);
  setRaw(body);
}

async function refreshMessages() {
  const conversationId = await ensureSession();
  const body = await fetchJson(`/sessions/${encodeURIComponent(conversationId)}/messages`);
  renderMessages(body.messages || []);
  setRaw(body);
}

function renderSessionSummary(summary) {
  $("session-summary").innerHTML = `
    <div class="summary-row"><span>Session</span><strong>${escapeHtml(summary.session_id)}</strong></div>
    <div class="summary-row"><span>Messages</span><strong>${escapeHtml(summary.message_count)}</strong></div>
    <div class="summary-row"><span>Last</span><strong>${escapeHtml(summary.last_message || "none")}</strong></div>
  `;
}

function renderMessages(messages) {
  const list = $("messages-list");
  list.innerHTML = "";
  if (messages.length === 0) {
    list.innerHTML = '<div class="empty-state">No messages</div>';
    return;
  }
  for (const message of messages) {
    const item = document.createElement("div");
    item.className = `message-item role-${message.role}`;
    item.innerHTML = `
      <div class="message-meta">
        <strong>${escapeHtml(message.role)}</strong>
        <span>${escapeHtml(formatDate(message.created_at))}</span>
      </div>
      <p>${escapeHtml(message.content)}</p>
    `;
    list.appendChild(item);
  }
}

async function addMessage() {
  const conversationId = await ensureSession();
  $("session-status").textContent = "Adding message...";
  try {
    const body = await fetchJson(`/sessions/${encodeURIComponent(conversationId)}/messages`, {
      method: "POST",
      body: JSON.stringify({
        role: $("message-role-input").value,
        content: $("message-content-input").value.trim(),
        run_agent: $("message-run-agent-input").checked,
      }),
    });
    $("session-status").textContent = `messages ${body.messages.length}`;
    renderMessages(body.messages || []);
    setRaw(body);
    await loadSessionSummary();
  } catch (error) {
    $("session-status").textContent = "Add message failed";
    setRaw({ error: error.message });
  }
}

async function streamChat() {
  const button = $("send-chat-btn");
  const output = $("chat-output");
  const meta = $("chat-meta");
  button.disabled = true;
  output.textContent = "";
  meta.textContent = "Streaming...";
  setTrace([]);
  try {
    const conversationId = await ensureSession();
    const payload = {
      conversation_id: conversationId,
      message: $("chat-message-input").value.trim(),
      ...optionalModelFields(),
    };
    const events = [];
    await postSse("/chat/stream", payload, (eventName, data) => {
      events.push({ event: eventName, data });
      setRaw(events);
      if (eventName === "meta") {
        meta.textContent = `${data.provider} / ${data.model}`;
      } else if (eventName === "delta") {
        output.textContent += data.text || "";
      } else if (eventName === "usage") {
        meta.textContent = `tokens ${data.total_tokens}`;
      } else if (eventName === "done") {
        meta.textContent = `done in ${data.elapsed_ms} ms`;
      } else if (eventName === "error") {
        meta.textContent = data.code || "error";
        output.textContent += `\n[error] ${data.message}`;
      }
    });
    await refreshMessages();
  } catch (error) {
    meta.textContent = "Error";
    output.textContent = error.message;
    setRaw({ error: error.message });
  } finally {
    button.disabled = false;
  }
}

async function postSse(path, payload, onEvent) {
  const startedAt = performance.now();
  let status = "ERR";
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    status = response.status;
    if (!response.ok || !response.body) {
      const text = await response.text();
      throw new Error(`${response.status} ${text || response.statusText}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const parsed = parseSseBlock(part);
        if (parsed) {
          onEvent(parsed.event, parsed.data);
        }
      }
    }
    if (buffer.trim()) {
      const parsed = parseSseBlock(buffer);
      if (parsed) {
        onEvent(parsed.event, parsed.data);
      }
    }
    pushRequestLog({
      method: "POST",
      path,
      status,
      ok: true,
      ms: Math.round(performance.now() - startedAt),
    });
  } catch (error) {
    pushRequestLog({
      method: "POST",
      path,
      status,
      ok: false,
      ms: Math.round(performance.now() - startedAt),
    });
    throw error;
  }
}

function parseSseBlock(block) {
  const lines = block.split("\n");
  let event = "message";
  const dataLines = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  const rawData = dataLines.join("\n");
  let data = rawData;
  try {
    data = JSON.parse(rawData);
  } catch {
    data = { text: rawData };
  }
  return { event, data };
}

async function runAgent() {
  const button = $("run-agent-btn");
  const status = $("agent-status");
  const answer = $("agent-answer");
  button.disabled = true;
  status.textContent = "Running...";
  answer.textContent = "";
  $("agent-events").innerHTML = "";
  try {
    const conversationId = await ensureSession();
    const payload = {
      conversation_id: conversationId,
      message: $("agent-message-input").value.trim(),
      repository_id: $("repository-id-input").value.trim() || "repo_main",
      focus_files: csvValues($("focus-files-input").value),
    };
    const body = await fetchJson("/agent/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderAgentRun(body);
    await pollRunUntilTerminal();
  } catch (error) {
    status.textContent = "Error";
    answer.textContent = error.message;
    setRaw({ error: error.message });
  } finally {
    button.disabled = false;
  }
}

function renderAgentRun(body) {
  const result = body.result || {};
  state.latestRunId = body.run_id || "";
  state.latestRunStatus = body.status || "";
  $("agent-status").textContent = `${body.status || "unknown"} ${body.run_id || ""}`;
  $("agent-answer").textContent =
    result.answer ||
    body.error ||
    (body.pending_approval
      ? `Waiting approval for: ${(body.pending_approval.planned_tools || []).join(", ")}`
      : "No answer yet");
  $("approve-run-btn").disabled = body.status !== "waiting_approval";
  $("reject-run-btn").disabled = body.status !== "waiting_approval";
  setTrace(body.trace || result.trace || []);
  setRaw(body);
  renderOverview();
}

async function refreshRun() {
  if (!state.latestRunId) {
    $("agent-status").textContent = "No run";
    return;
  }
  const body = await fetchJson(`/agent/runs/${encodeURIComponent(state.latestRunId)}`);
  renderAgentRun(body);
}

async function refreshEvents() {
  if (!state.latestRunId) {
    $("agent-events").innerHTML = '<div class="empty-state">No run selected</div>';
    return;
  }
  const body = await fetchJson(
    `/agent/runs/${encodeURIComponent(state.latestRunId)}/events`
  );
  renderAgentEvents(body.events || []);
  setRaw(body);
}

function renderAgentEvents(events) {
  const list = $("agent-events");
  list.innerHTML = "";
  if (events.length === 0) {
    list.innerHTML = '<div class="empty-state">No events</div>';
    return;
  }
  for (const event of events) {
    const item = document.createElement("div");
    item.className = "data-item";
    item.innerHTML = `
      <div>
        <strong>${escapeHtml(event.sequence)} · ${escapeHtml(event.type)}</strong>
        <span>${escapeHtml(event.status)} · ${escapeHtml(event.node || "run")}</span>
        <p>${escapeHtml(event.summary)}</p>
      </div>
    `;
    list.appendChild(item);
  }
}

async function resumeRun(approved) {
  if (!state.latestRunId) {
    return;
  }
  $("agent-status").textContent = approved ? "Approving..." : "Rejecting...";
  const body = await fetchJson(`/agent/runs/${encodeURIComponent(state.latestRunId)}/resume`, {
    method: "POST",
    body: JSON.stringify({
      approved,
      feedback: approved ? "前端调试台批准执行" : "前端调试台拒绝执行",
    }),
  });
  renderAgentRun(body);
  await pollRunUntilTerminal();
}

async function pollRunUntilTerminal() {
  const terminalStatuses = new Set(["completed", "failed", "waiting_approval"]);
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (!state.latestRunId || terminalStatuses.has(state.latestRunStatus)) {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
    await refreshRun();
  }
  await refreshEvents();
}

async function ingestDocument() {
  const status = $("rag-status");
  status.textContent = "Ingesting...";
  try {
    const kbId = $("kb-id-input").value.trim() || "demo_kb";
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(kbId)}/documents`, {
      method: "POST",
      body: JSON.stringify({
        filename: $("document-filename-input").value.trim() || "notes.md",
        content: $("document-content-input").value,
      }),
    });
    status.textContent = `ingested ${body.chunk_count} chunks`;
    setRaw(body);
  } catch (error) {
    status.textContent = "Error";
    $("rag-answer").textContent = error.message;
    setRaw({ error: error.message });
  }
}

async function searchRag() {
  const status = $("rag-status");
  const answer = $("rag-answer");
  status.textContent = "Searching...";
  answer.textContent = "";
  try {
    const kbId = $("kb-id-input").value.trim() || "demo_kb";
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(kbId)}/search`, {
      method: "POST",
      body: JSON.stringify({
        query: $("rag-question-input").value.trim(),
        limit: numberValue("rag-limit-input", 5),
        recall_limit: numberValue("rag-recall-limit-input", 10),
      }),
    });
    status.textContent = `results ${body.results.length}`;
    answer.textContent = renderRagResults(body.results || []);
    setRaw(body);
  } catch (error) {
    status.textContent = "Error";
    answer.textContent = error.message;
    setRaw({ error: error.message });
  }
}

async function askRag() {
  const status = $("rag-status");
  const answer = $("rag-answer");
  status.textContent = "Asking...";
  answer.textContent = "";
  try {
    const kbId = $("kb-id-input").value.trim() || "demo_kb";
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(kbId)}/ask`, {
      method: "POST",
      body: JSON.stringify({
        question: $("rag-question-input").value.trim(),
        limit: numberValue("rag-limit-input", 5),
        recall_limit: numberValue("rag-recall-limit-input", 10),
        ...optionalModelFields(),
      }),
    });
    status.textContent = `citations ${body.citations.length}`;
    answer.textContent = renderRagAnswer(body);
    setRaw(body);
  } catch (error) {
    status.textContent = "Error";
    answer.textContent = error.message;
    setRaw({ error: error.message });
  }
}

function renderRagResults(results) {
  if (results.length === 0) {
    return "No results";
  }
  return results
    .map((item, index) => {
      const score = Number(item.score || 0).toFixed(3);
      const lines =
        item.start_line || item.end_line
          ? ` lines ${item.start_line || "?"}-${item.end_line || "?"}`
          : "";
      return `[${index + 1}] ${item.filename} #${item.chunk_index} score=${score}${lines}\n${item.text}`;
    })
    .join("\n\n");
}

function renderRagAnswer(body) {
  return `${body.answer}\n\nCitations\n${renderRagResults(body.citations || [])}`;
}

async function indexRepository() {
  const status = $("session-status");
  const repositoryId = $("repository-id-input").value.trim() || "repo_main";
  const rootPath = $("repository-root-input").value.trim();
  if (!rootPath) {
    status.textContent = "Fill repository root path first";
    switchTab("repository");
    return;
  }
  status.textContent = "Indexing repository...";
  try {
    const submitted = await fetchJson(
      `/repositories/${encodeURIComponent(repositoryId)}/index`,
      {
        method: "POST",
        body: JSON.stringify({
          root_path: rootPath,
          include_patterns: csvValues($("include-patterns-input").value),
          exclude_patterns: csvValues($("exclude-patterns-input").value),
          max_file_size: numberValue("max-file-size-input", 200000),
        }),
      }
    );
    renderRepositoryResult(submitted);
    status.textContent = `Index job ${submitted.job_id} queued`;
    const body = await waitForRepositoryIndex(repositoryId, submitted.job_id);
    state.latestRepositoryIndex = body;
    status.textContent = `indexed ${body.indexed_files}, skipped ${body.skipped_files}`;
    renderRepositoryResult(body);
    setRaw(body);
    switchTab("repository");
  } catch (error) {
    status.textContent = "Index failed";
    $("repository-result").innerHTML = `<div class="empty-state error-text">${escapeHtml(error.message)}</div>`;
    setRaw({ error: error.message });
    switchTab("repository");
  }
}

async function waitForRepositoryIndex(repositoryId, jobId) {
  const terminalStatuses = new Set([
    "completed",
    "completed_with_errors",
    "failed",
  ]);
  for (let attempt = 0; attempt < 240; attempt += 1) {
    const body = await fetchJson(
      `/repositories/${encodeURIComponent(repositoryId)}/index-jobs/${encodeURIComponent(jobId)}`
    );
    renderRepositoryResult(body);
    $("session-status").textContent =
      `Index ${body.status}: scanned ${body.scanned_files}, indexed ${body.indexed_files}`;
    if (terminalStatuses.has(body.status)) {
      return body;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`index job ${jobId} did not finish before the polling timeout`);
}

function renderRepositoryResult(body) {
  $("repository-result").innerHTML = `
    <div class="summary-row"><span>Job</span><strong>${escapeHtml(body.job_id)}</strong></div>
    <div class="summary-row"><span>Status</span><strong>${escapeHtml(body.status)}</strong></div>
    <div class="summary-row"><span>Scanned</span><strong>${escapeHtml(body.scanned_files)}</strong></div>
    <div class="summary-row"><span>Indexed</span><strong>${escapeHtml(body.indexed_files)}</strong></div>
    <div class="summary-row"><span>Skipped</span><strong>${escapeHtml(body.skipped_files)}</strong></div>
    <div class="summary-row"><span>Failed</span><strong>${escapeHtml(body.failed_files)}</strong></div>
  `;
  renderPathList(
    "indexed-paths",
    body.indexed_paths || [],
    "Path details are not persisted"
  );
  renderPathList(
    "skipped-paths",
    [...(body.skipped_paths || []), ...(body.failed_paths || [])],
    "No skipped or failed paths"
  );
}

function renderPathList(id, paths, emptyText) {
  const list = $(id);
  list.innerHTML = "";
  if (paths.length === 0) {
    list.innerHTML = `<div class="empty-state">${escapeHtml(emptyText)}</div>`;
    return;
  }
  for (const path of paths.slice(0, 120)) {
    const item = document.createElement("div");
    item.className = "data-item";
    item.innerHTML = `<code>${escapeHtml(path)}</code>`;
    list.appendChild(item);
  }
}

function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `${tabName}-tab`);
  });
}

function bindEvents() {
  $("refresh-health-btn").addEventListener("click", checkHealth);
  $("refresh-overview-btn").addEventListener("click", async () => {
    await checkHealth();
    await listSessions(false);
  });
  $("create-session-btn").addEventListener("click", createSession);
  $("load-session-btn").addEventListener("click", loadSession);
  $("list-sessions-btn").addEventListener("click", async () => {
    await listSessions();
    switchTab("sessions");
  });
  $("summary-session-btn").addEventListener("click", loadSessionSummary);
  $("refresh-messages-btn").addEventListener("click", refreshMessages);
  $("add-message-btn").addEventListener("click", addMessage);
  $("send-chat-btn").addEventListener("click", streamChat);
  $("run-agent-btn").addEventListener("click", runAgent);
  $("refresh-run-btn").addEventListener("click", refreshRun);
  $("refresh-events-btn").addEventListener("click", refreshEvents);
  $("approve-run-btn").addEventListener("click", () => resumeRun(true));
  $("reject-run-btn").addEventListener("click", () => resumeRun(false));
  $("ingest-doc-btn").addEventListener("click", ingestDocument);
  $("search-rag-btn").addEventListener("click", searchRag);
  $("ask-rag-btn").addEventListener("click", askRag);
  $("index-repo-btn").addEventListener("click", indexRepository);
  $("clear-chat-btn").addEventListener("click", () => {
    $("chat-output").textContent = "";
    $("chat-meta").textContent = "Idle";
  });
  $("clear-log-btn").addEventListener("click", () => {
    state.requestLog = [];
    renderRequestLog();
    renderOverview();
  });
  $("clear-detail-btn").addEventListener("click", () => {
    setTrace([]);
    setRaw("");
  });
  $("conversation-id-input").addEventListener("input", (event) => {
    state.conversationId = event.target.value.trim();
  });
  $("sessions-list").addEventListener("click", async (event) => {
    const row = event.target.closest("[data-session-id]");
    if (!row) {
      return;
    }
    state.conversationId = row.dataset.sessionId;
    $("conversation-id-input").value = state.conversationId;
    await loadSession();
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });
}

function init() {
  bindEvents();
  setTrace([]);
  renderRequestLog();
  renderSessions();
  renderOverview();
  checkHealth();
  listSessions(false).catch(() => {
    renderOverview();
  });
}

init();
