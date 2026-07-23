const API_BASE = "/api/v1";

const state = {
  conversationId: "",
  latestRunId: "",
  latestRunStatus: "",
  latestApprovalInterruptId: "",
  healthStatus: "checking",
  sessions: [],
  requestLog: [],
  latestRepositoryIndex: null,
  approvalResolutionInFlight: false,
  inspectorOpen: true,
  chatAbortController: null,
  chatText: "",
};

const $ = (id) => document.getElementById(id);

const VIEW_METADATA = {
  chat: {
    title: "Chat",
    description: "与模型进行流式对话，并沿用当前 Session 上下文。",
  },
  agent: {
    title: "Agent",
    description: "运行仓库感知的代码任务，查看审批、事件和执行结果。",
  },
  rag: {
    title: "Knowledge",
    description: "导入知识文档，并执行检索增强搜索与问答。",
  },
  repository: {
    title: "Repository",
    description: "查看当前仓库索引任务的进度、结果和失败信息。",
  },
  sessions: {
    title: "Sessions",
    description: "浏览会话历史、摘要和已保存的消息上下文。",
  },
  overview: {
    title: "Operations",
    description: "检查服务健康、请求日志和核心 API 运行情况。",
  },
};

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

function renderInlineMarkdown(value) {
  return String(value ?? "")
    .split(/(`[^`\n]+`)/g)
    .map((segment) => {
      if (segment.startsWith("`") && segment.endsWith("`")) {
        return `<code>${escapeHtml(segment.slice(1, -1))}</code>`;
      }
      return escapeHtml(segment).replace(
        /\*\*([^*\n]+)\*\*/g,
        "<strong>$1</strong>"
      );
    })
    .join("");
}

function renderSafeMarkdown(value) {
  const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let paragraph = [];
  let listType = "";
  let listItems = [];
  let codeLanguage = "";
  let codeLines = [];
  let inCodeFence = false;

  const flushParagraph = () => {
    if (paragraph.length === 0) {
      return;
    }
    blocks.push(`<p>${paragraph.map(renderInlineMarkdown).join("<br>")}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (listItems.length === 0) {
      return;
    }
    blocks.push(
      `<${listType}>${listItems
        .map((item) => `<li>${renderInlineMarkdown(item)}</li>`)
        .join("")}</${listType}>`
    );
    listType = "";
    listItems = [];
  };
  const flushCode = () => {
    const language = codeLanguage
      ? ` class="language-${escapeHtml(codeLanguage)}"`
      : "";
    blocks.push(
      `<pre class="markdown-code"><code${language}>${escapeHtml(
        codeLines.join("\n")
      )}</code></pre>`
    );
    codeLanguage = "";
    codeLines = [];
  };

  for (const line of lines) {
    if (inCodeFence) {
      if (/^```/.test(line)) {
        flushCode();
        inCodeFence = false;
      } else {
        codeLines.push(line);
      }
      continue;
    }

    const fence = line.match(/^```([A-Za-z0-9_-]*)\s*$/);
    if (fence) {
      flushParagraph();
      flushList();
      codeLanguage = fence[1] || "";
      inCodeFence = true;
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length + 2;
      blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const unordered = line.match(/^[-*]\s+(.+)$/);
    const ordered = line.match(/^\d+\.\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      const nextType = unordered ? "ul" : "ol";
      if (listType && listType !== nextType) {
        flushList();
      }
      listType = nextType;
      listItems.push((unordered || ordered)[1]);
      continue;
    }

    const quote = line.match(/^>\s?(.+)$/);
    if (quote) {
      flushParagraph();
      flushList();
      blocks.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }
    flushList();
    paragraph.push(line);
  }

  if (inCodeFence) {
    flushCode();
  }
  flushParagraph();
  flushList();
  return blocks.join("");
}

function renderChatOutput(value) {
  state.chatText = String(value ?? "");
  const output = $("chat-output");
  output.innerHTML = renderSafeMarkdown(state.chatText);
  output.scrollTop = output.scrollHeight;
}

function showToast(message, tone = "info") {
  const region = $("toast-region");
  const toast = document.createElement("div");
  toast.className = `toast toast-${tone}`;
  toast.setAttribute("role", tone === "error" ? "alert" : "status");

  const text = document.createElement("span");
  text.textContent = message;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast-close";
  close.setAttribute("aria-label", "Dismiss notification");
  close.textContent = "×";
  close.addEventListener("click", () => toast.remove());

  toast.append(text, close);
  region.appendChild(toast);
  window.setTimeout(() => toast.remove(), tone === "error" ? 7000 : 4200);
}

function reportError(context, error) {
  const detail = error?.message || String(error || "Unknown error");
  showToast(`${context}: ${detail}`, "error");
  setRaw({ error: detail, context });
}

async function runUiAction(action, errorContext, successMessage = "") {
  try {
    const result = await action();
    if (successMessage) {
      showToast(successMessage, "success");
    }
    return result;
  } catch (error) {
    reportError(errorContext, error);
    return null;
  }
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

function renderContextSummary() {
  const conversationId =
    state.conversationId || $("conversation-id-input").value.trim();
  const provider = $("provider-input").value.trim() || "Default";
  const model = $("model-input").value.trim();
  $("context-session").textContent = conversationId || "Not selected";
  $("context-provider").textContent = model ? `${provider} / ${model}` : provider;
  $("context-repository").textContent =
    $("repository-id-input").value.trim() || "repo_main";
}

function openSettings() {
  const dialog = $("settings-dialog");
  if (!dialog.open) {
    dialog.showModal();
  }
}

function closeSettings() {
  const dialog = $("settings-dialog");
  if (dialog.open) {
    dialog.close();
  }
}

function setInspectorOpen(open) {
  state.inspectorOpen = open;
  $("detail-panel").classList.toggle("is-collapsed", !open);
  $("app-shell").classList.toggle("inspector-collapsed", !open);
  $("toggle-inspector-btn").setAttribute("aria-expanded", String(open));
}

function setChatStreaming(streaming) {
  $("send-chat-btn").disabled = streaming;
  $("stop-chat-btn").hidden = !streaming;
  $("stop-chat-btn").disabled = !streaming;
  $("clear-chat-btn").disabled = streaming;
  $("chat-output").setAttribute("aria-busy", String(streaming));
}

function stopChat() {
  if (!state.chatAbortController) {
    return;
  }
  $("chat-meta").textContent = "Stopping...";
  $("stop-chat-btn").disabled = true;
  state.chatAbortController.abort();
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
    row.className = `data-item ${
      item.cancelled ? "cancelled" : item.ok ? "ok" : "error"
    }`;
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
  renderContextSummary();
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
  $("session-status").textContent = "Loading session...";
  try {
    const session = await fetchJson(`/sessions/${encodeURIComponent(conversationId)}`);
    const [summary, messages] = await Promise.all([
      fetchJson(`/sessions/${encodeURIComponent(conversationId)}/summary`),
      fetchJson(`/sessions/${encodeURIComponent(conversationId)}/messages`),
    ]);
    state.conversationId = session.id;
    renderContextSummary();
    $("session-status").textContent = `Loaded: ${session.id}`;
    renderSessionSummary(summary);
    renderMessages(messages.messages || []);
    if (showRaw) {
      setRaw({ session, summary, messages });
    }
  } catch (error) {
    const notFound = String(error.message || "").startsWith("404 ");
    $("session-status").textContent = notFound ? "Session not found" : "Load session failed";
    $("session-summary").innerHTML = '<div class="empty-state">No active session data</div>';
    renderMessages([]);
    if (showRaw) {
      setRaw({ error: error.message, conversation_id: conversationId });
      showToast(notFound ? "Session not found" : "Could not load session", "error");
    }
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
    reportError("Could not add message", error);
  }
}

async function streamChat() {
  const meta = $("chat-meta");
  const message = $("chat-message-input").value.trim();
  if (!message) {
    meta.textContent = "Enter a message";
    renderChatOutput("Message cannot be empty.");
    showToast("Enter a message before sending.", "error");
    return;
  }
  const controller = new AbortController();
  const events = [];
  let streamError = null;
  state.chatAbortController = controller;
  renderChatOutput("");
  setChatStreaming(true);
  meta.textContent = "Streaming...";
  setTrace([]);
  try {
    const conversationId = await ensureSession();
    const payload = {
      conversation_id: conversationId,
      message,
      ...optionalModelFields(),
    };
    await postSse(
      "/chat/stream",
      payload,
      (eventName, data) => {
        events.push({ event: eventName, data });
        setRaw(events);
        if (eventName === "meta") {
          meta.textContent = `${data.provider} / ${data.model}`;
        } else if (eventName === "delta") {
          renderChatOutput(`${state.chatText}${data.text || ""}`);
        } else if (eventName === "usage") {
          meta.textContent = `tokens ${data.total_tokens}`;
        } else if (eventName === "done") {
          meta.textContent = `done in ${data.elapsed_ms} ms`;
        } else if (eventName === "error") {
          streamError = data;
          meta.textContent = data.code || "error";
          renderChatOutput(`${state.chatText}\n\n> Error: ${data.message}`);
        }
      },
      { signal: controller.signal }
    );
    await refreshMessages();
    if (streamError) {
      showToast(streamError.message || "Chat stream failed.", "error");
    } else {
      showToast("Response completed.", "success");
    }
  } catch (error) {
    if (controller.signal.aborted || error.name === "AbortError") {
      meta.textContent = "Stopped";
      setRaw({ status: "stopped", events });
      showToast("Response stopped.", "info");
    } else {
      meta.textContent = "Error";
      renderChatOutput(error.message);
      reportError("Chat failed", error);
    }
  } finally {
    if (state.chatAbortController === controller) {
      state.chatAbortController = null;
    }
    setChatStreaming(false);
  }
}

async function postSse(path, payload, onEvent, { signal } = {}) {
  const startedAt = performance.now();
  let status = "ERR";
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
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
    const cancelled = Boolean(signal?.aborted || error.name === "AbortError");
    pushRequestLog({
      method: "POST",
      path,
      status: cancelled ? "ABORTED" : status,
      ok: false,
      cancelled,
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
    reportError("Agent run failed", error);
  } finally {
    button.disabled = false;
  }
}

function renderAgentRun(body) {
  const result = body.result || {};
  const pendingApproval = body.pending_approval || null;
  const approvalTools = pendingApproval?.approval_required_tools || [];
  state.latestRunId = body.run_id || "";
  state.latestRunStatus = body.status || "";
  state.latestApprovalInterruptId = pendingApproval?.interrupt_id || "";
  $("agent-status").textContent = `${body.status || "unknown"} ${body.run_id || ""}`;
  $("agent-answer").textContent =
    result.answer ||
    body.error ||
    (pendingApproval
      ? `Waiting approval for: ${approvalTools.map((item) => item.name).join(", ") || "review"}`
      : "No answer yet");
  const approvalActionsDisabled =
    body.status !== "waiting_approval" || state.approvalResolutionInFlight;
  $("approve-run-btn").disabled = approvalActionsDisabled;
  $("reject-run-btn").disabled = approvalActionsDisabled;
  renderAgentApproval(pendingApproval);
  setTrace(body.trace || result.trace || []);
  setRaw(body);
  renderOverview();
}

function renderAgentApproval(pendingApproval) {
  const panel = $("agent-approval-panel");
  const list = $("agent-approval-tools");
  const tools = pendingApproval?.approval_required_tools || [];
  panel.hidden = tools.length === 0;
  list.innerHTML = "";
  for (const tool of tools) {
    const item = document.createElement("article");
    item.className = "approval-tool-card";
    item.innerHTML = `
      <strong>${escapeHtml(tool.name || "tool")}</strong>
      <span>${escapeHtml(tool.permission_level || "unknown")} · ${escapeHtml(tool.provider || "unknown")}</span>
      <p>${escapeHtml(tool.risk_summary || "Review the requested tool before continuing.")}</p>
      <pre class="approval-arguments">${escapeHtml(jsonPretty(tool.arguments_summary || {}))}</pre>
    `;
    list.appendChild(item);
  }
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
  const approvalInterruptId = state.latestApprovalInterruptId;
  state.approvalResolutionInFlight = true;
  $("approve-run-btn").disabled = true;
  $("reject-run-btn").disabled = true;
  $("agent-status").textContent = approved ? "Applying approval..." : "Applying rejection...";
  try {
    const body = await fetchJson(`/agent/runs/${encodeURIComponent(state.latestRunId)}/resume`, {
      method: "POST",
      body: JSON.stringify({
        approved,
        feedback: approved ? "前端调试台批准执行" : "前端调试台拒绝执行",
      }),
    });
    renderAgentRun(body);
    await pollRunUntilTerminal({ ignoredWaitingApprovalId: approvalInterruptId });
  } catch (error) {
    $("agent-status").textContent = "Approval update failed";
    reportError("Approval update failed", error);
  } finally {
    state.approvalResolutionInFlight = false;
    if (state.latestRunId) {
      await refreshRun();
    }
  }
}

async function pollRunUntilTerminal({ ignoredWaitingApprovalId = "" } = {}) {
  const terminalStatuses = new Set(["completed", "failed"]);
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const waitingForNewApproval =
      state.latestRunStatus === "waiting_approval" &&
      (!ignoredWaitingApprovalId ||
        state.latestApprovalInterruptId !== ignoredWaitingApprovalId);
    if (
      !state.latestRunId ||
      terminalStatuses.has(state.latestRunStatus) ||
      waitingForNewApproval
    ) {
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
    reportError("Document import failed", error);
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
    reportError("Knowledge search failed", error);
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
    reportError("Knowledge answer failed", error);
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
  closeSettings();
  switchTab("repository");
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
    reportError("Repository indexing failed", error);
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
  const metadata = VIEW_METADATA[tabName] || VIEW_METADATA.chat;
  document.querySelectorAll(".tab").forEach((tab) => {
    const active = tab.dataset.tab === tabName;
    tab.classList.toggle("active", active);
    if (active) {
      tab.setAttribute("aria-current", "page");
    } else {
      tab.removeAttribute("aria-current");
    }
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `${tabName}-tab`);
  });
  $("workspace-title").textContent = metadata.title;
  $("workspace-description").textContent = metadata.description;
}

function bindEvents() {
  $("refresh-health-btn").addEventListener("click", checkHealth);
  $("refresh-overview-btn").addEventListener("click", async () => {
    await checkHealth();
    await listSessions(false);
  });
  $("create-session-btn").addEventListener("click", () => {
    runUiAction(createSession, "Could not create session", "Session created.");
  });
  $("load-session-btn").addEventListener("click", loadSession);
  $("list-sessions-btn").addEventListener("click", async () => {
    const result = await runUiAction(listSessions, "Could not list sessions");
    if (!result) {
      return;
    }
    closeSettings();
    switchTab("sessions");
  });
  $("summary-session-btn").addEventListener("click", () => {
    runUiAction(loadSessionSummary, "Could not load session summary");
  });
  $("refresh-messages-btn").addEventListener("click", () => {
    runUiAction(refreshMessages, "Could not refresh messages");
  });
  $("add-message-btn").addEventListener("click", addMessage);
  $("send-chat-btn").addEventListener("click", streamChat);
  $("stop-chat-btn").addEventListener("click", stopChat);
  $("run-agent-btn").addEventListener("click", runAgent);
  $("refresh-run-btn").addEventListener("click", () => {
    runUiAction(refreshRun, "Could not refresh agent run");
  });
  $("refresh-events-btn").addEventListener("click", () => {
    runUiAction(refreshEvents, "Could not refresh agent events");
  });
  $("approve-run-btn").addEventListener("click", () => resumeRun(true));
  $("reject-run-btn").addEventListener("click", () => resumeRun(false));
  $("ingest-doc-btn").addEventListener("click", ingestDocument);
  $("search-rag-btn").addEventListener("click", searchRag);
  $("ask-rag-btn").addEventListener("click", askRag);
  $("index-repo-btn").addEventListener("click", indexRepository);
  $("clear-chat-btn").addEventListener("click", () => {
    renderChatOutput("");
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
  $("settings-btn").addEventListener("click", openSettings);
  $("close-settings-btn").addEventListener("click", closeSettings);
  $("done-settings-btn").addEventListener("click", closeSettings);
  $("settings-dialog").addEventListener("click", (event) => {
    if (event.target === $("settings-dialog")) {
      closeSettings();
    }
  });
  $("toggle-inspector-btn").addEventListener("click", () => {
    setInspectorOpen(!state.inspectorOpen);
  });
  $("close-inspector-btn").addEventListener("click", () => {
    setInspectorOpen(false);
  });
  $("conversation-id-input").addEventListener("input", (event) => {
    state.conversationId = event.target.value.trim();
    renderContextSummary();
  });
  $("provider-input").addEventListener("change", renderContextSummary);
  $("model-input").addEventListener("input", renderContextSummary);
  $("repository-id-input").addEventListener("input", renderContextSummary);
  $("chat-message-input").addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      if (!state.chatAbortController) {
        streamChat();
      }
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.chatAbortController) {
      event.preventDefault();
      stopChat();
    }
  });
  $("sessions-list").addEventListener("click", async (event) => {
    const row = event.target.closest("[data-session-id]");
    if (!row) {
      return;
    }
    state.conversationId = row.dataset.sessionId;
    $("conversation-id-input").value = state.conversationId;
    renderContextSummary();
    await loadSession();
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });
}

function init() {
  bindEvents();
  switchTab("chat");
  renderContextSummary();
  setInspectorOpen(window.matchMedia("(min-width: 1281px)").matches);
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
