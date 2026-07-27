const API_BASE = "/api/v1";
const UI_STORAGE_KEY = "ai-agent-platform-ui-v2";
const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "waiting_approval"]);

const state = {
  conversationId: "",
  latestRunId: "",
  latestRunStatus: "",
  healthStatus: "checking",
  sessions: [],
  requestLog: [],
  workspaces: [],
  knowledgeBases: [],
  currentView: "chat",
  composerMode: "chat",
  chatController: null,
  agentPollGeneration: 0,
};

const $ = (id) => document.getElementById(id);

function iconMarkup(name) {
  return `<svg class="app-icon" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;
}

function preferredScrollBehavior() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

function jsonPretty(value) {
  return JSON.stringify(value, null, 2);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function inlineMarkdown(value) {
  return value
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

function renderMarkdown(value) {
  const escaped = escapeHtml(value).replace(/\r\n/g, "\n");
  const codeBlocks = [];
  const withTokens = escaped.replace(/```([\w.+-]*)\n?([\s\S]*?)```/g, (_, language, code) => {
    const token = `@@CODE_BLOCK_${codeBlocks.length}@@`;
    codeBlocks.push(
      `<pre><code${language ? ` data-language="${escapeHtml(language)}"` : ""}>${code.trim()}</code></pre>`,
    );
    return `\n${token}\n`;
  });

  const lines = withTokens.split("\n");
  const output = [];
  let listType = "";

  const closeList = () => {
    if (listType) {
      output.push(`</${listType}>`);
      listType = "";
    }
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const codeMatch = line.trim().match(/^@@CODE_BLOCK_(\d+)@@$/);
    if (codeMatch) {
      closeList();
      output.push(codeBlocks[Number(codeMatch[1])] || "");
      continue;
    }
    if (!line.trim()) {
      closeList();
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      output.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const nextType = unordered ? "ul" : "ol";
      if (listType !== nextType) {
        closeList();
        listType = nextType;
        output.push(`<${listType}>`);
      }
      output.push(`<li>${inlineMarkdown((unordered || ordered)[1])}</li>`);
      continue;
    }
    closeList();
    output.push(`<p>${inlineMarkdown(line)}</p>`);
  }
  closeList();
  return output.join("");
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
  const thinkingLevel = $("thinking-level-input").value.trim();
  return {
    ...(provider ? { provider } : {}),
    ...(model ? { model } : {}),
    ...(thinkingLevel ? { thinking_level: thinkingLevel } : {}),
  };
}

function formatDate(value) {
  if (!value) {
    return "未知时间";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(value) {
  const milliseconds = Number(value || 0);
  if (milliseconds < 1000) {
    return `${milliseconds} ms`;
  }
  return `${(milliseconds / 1000).toFixed(milliseconds < 10000 ? 1 : 0)} s`;
}

function humanizeStatus(value) {
  const labels = {
    ok: "服务正常",
    offline: "连接失败",
    queued: "排队中",
    running: "运行中",
    waiting_approval: "等待审批",
    completed: "已完成",
    completed_with_errors: "完成，有警告",
    failed: "失败",
    pending: "等待中",
  };
  return labels[value] || value || "未知";
}

function statusClass(value) {
  if (value === "completed_with_errors") {
    return "warning";
  }
  if (["completed", "failed", "waiting_approval", "running", "queued"].includes(value)) {
    return value;
  }
  return "neutral";
}

function truncate(value, maxLength = 120) {
  const text = String(value ?? "");
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function humanizeError(error) {
  if (!error) {
    return "发生未知错误";
  }
  if (error.name === "AbortError") {
    return "操作已停止";
  }
  return error.message || String(error);
}

function showToast(message, type = "success", timeout = 4200) {
  const region = $("toast-region");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${escapeHtml(message)}</span><button type="button" aria-label="关闭通知">×</button>`;
  const close = () => toast.remove();
  toast.querySelector("button").addEventListener("click", close);
  region.appendChild(toast);
  window.setTimeout(close, timeout);
}

function setRaw(value) {
  $("raw-output").textContent = typeof value === "string" ? value : jsonPretty(value);
}

function setTrace(items) {
  const list = $("trace-list");
  list.innerHTML = "";
  if (!items || items.length === 0) {
    list.innerHTML = '<div class="empty-state">开始一次任务后，这里会显示执行轨迹。</div>';
    return;
  }
  for (const [index, item] of items.entries()) {
    const node = document.createElement("div");
    node.className = "trace-item";
    node.innerHTML = `
      <strong>${escapeHtml(item.step ?? index + 1)} · ${escapeHtml(item.node ?? "step")}</strong>
      <p>${escapeHtml(item.summary ?? "")}</p>
    `;
    list.appendChild(node);
  }
}

function setLastRequestId(value) {
  $("last-request-id").textContent = `Request ID：${value || "—"}`;
}

function loadUiPreferences() {
  try {
    return JSON.parse(localStorage.getItem(UI_STORAGE_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

function saveUiPreferences() {
  try {
    localStorage.setItem(
      UI_STORAGE_KEY,
      JSON.stringify({
        view: state.currentView,
        composerMode: state.composerMode,
        inspectorHidden: document.body.classList.contains("inspector-hidden"),
      }),
    );
  } catch {
    // Device-local preferences are optional; the product remains usable without them.
  }
}

function switchView(viewName, updateHash = true) {
  const panel = document.querySelector(`[data-view-panel="${viewName}"]`);
  if (!panel) {
    return;
  }
  state.currentView = viewName;
  document.querySelectorAll("[data-view-panel]").forEach((item) => {
    const active = item.dataset.viewPanel === viewName;
    item.classList.toggle("active", active);
    item.hidden = !active;
  });
  document.querySelectorAll("[data-view]").forEach((item) => {
    const active = item.dataset.view === viewName;
    item.classList.toggle("active", active);
    if (active) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  });
  if (updateHash) {
    history.replaceState(null, "", `#${viewName}`);
  }
  saveUiPreferences();
  $("main-workspace").focus({ preventScroll: true });
}

function setInspectorVisible(visible) {
  const panel = $("inspector-panel");
  document.body.classList.toggle("inspector-hidden", !visible);
  $("toggle-inspector-btn").setAttribute("aria-expanded", String(visible));
  $("toggle-inspector-btn").setAttribute("aria-label", visible ? "隐藏运行详情" : "显示运行详情");
  panel.setAttribute("aria-hidden", String(!visible));
  panel.inert = !visible;
  panel.hidden = !visible;
  saveUiPreferences();
}

function selectInspectorTab(name) {
  const isTrace = name === "trace";
  $("trace-panel").hidden = !isTrace;
  $("raw-panel").hidden = isTrace;
  $("trace-tab").classList.toggle("active", isTrace);
  $("raw-tab").classList.toggle("active", !isTrace);
  $("trace-tab").setAttribute("aria-selected", String(isTrace));
  $("raw-tab").setAttribute("aria-selected", String(!isTrace));
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

function updateContextSummary() {
  const userId = $("user-id-input").value.trim() || "demo_user";
  const provider = $("provider-input").value.trim();
  const model = $("model-input").value.trim();
  const thinkingLevel = $("thinking-level-input").value.trim();
  const workspaceId = $("workspace-id-input").value.trim() || "workspace_main";
  const modelLabel = `${model || provider || "默认配置"}${thinkingLevel ? ` · ${thinkingLevel}` : ""}`;

  $("context-user").textContent = userId;
  $("context-model").textContent = modelLabel;
  $("context-workspace").textContent = workspaceId;
  $("composer-context").textContent = modelLabel;
  $("composer-provider-input").value = provider;
  $("agent-workspace-badge").textContent = workspaceId;
  $("header-session-id").textContent = state.conversationId || "尚未创建";
}

function saveSettings() {
  state.conversationId = $("conversation-id-input").value.trim();
  updateContextSummary();
  closeSettings();
  showToast("工作区设置已更新");
}

function updateComposerMode(mode = $("composer-mode-input").value) {
  state.composerMode = mode === "agent" ? "agent" : "chat";
  $("composer-mode-input").value = state.composerMode;
  const isAgent = state.composerMode === "agent";
  $("composer-mode-description").textContent = isAgent
    ? "读取工作区并运行工具；高风险操作等待审批。"
    : "流式回答，不执行代码工具。";
  $("chat-message-input").placeholder = isAgent
    ? "描述代码任务，Enter 交给代码 Agent，Shift + Enter 换行…"
    : "输入消息，Enter 发送，Shift + Enter 换行…";
  $("send-chat-btn").innerHTML = isAgent
    ? `交给 Agent ${iconMarkup("arrow-right")}`
    : `发送 ${iconMarkup("arrow-up")}`;
  saveUiPreferences();
}

function setChatStatus(label, status = "neutral") {
  const node = $("chat-meta");
  node.className = `status-pill ${status}`;
  node.innerHTML = `<span class="status-dot" aria-hidden="true"></span><span>${escapeHtml(label)}</span>`;
}

function pushRequestLog(entry) {
  state.requestLog.unshift({ at: new Date().toISOString(), ...entry });
  state.requestLog = state.requestLog.slice(0, 50);
  renderRequestLog();
  renderOverview();
}

function renderRequestLog() {
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

function renderOverview() {
  $("metric-api").textContent = humanizeStatus(state.healthStatus);
  $("metric-sessions").textContent = String(state.sessions.length);
  $("metric-run").textContent = state.latestRunId
    ? humanizeStatus(state.latestRunStatus)
    : "暂无";
  const latest = state.requestLog[0];
  $("metric-request").textContent = latest ? `${latest.status} · ${latest.ms}ms` : "暂无";
}

function parseErrorDetail(body, fallback) {
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

async function fetchJson(path, options = {}) {
  const method = options.method || "GET";
  const startedAt = performance.now();
  let status = "ERR";
  let requestId = "";
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
    });
    status = response.status;
    requestId = response.headers.get("X-Request-ID") || "";
    setLastRequestId(requestId);
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
      throw new Error(`${response.status} ${parseErrorDetail(body, response.statusText)}`);
    }
    pushRequestLog({
      method,
      path,
      status,
      requestId,
      ok: true,
      ms: Math.round(performance.now() - startedAt),
    });
    return body;
  } catch (error) {
    pushRequestLog({
      method,
      path,
      status,
      requestId,
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
    pill.className = "status-pill ok";
    pill.innerHTML = '<span class="status-dot" aria-hidden="true"></span><span>服务正常</span>';
    setRaw(body);
  } catch (error) {
    state.healthStatus = "offline";
    pill.className = "status-pill error";
    pill.innerHTML = '<span class="status-dot" aria-hidden="true"></span><span>连接失败</span>';
  } finally {
    renderOverview();
  }
}

async function ensureSession() {
  const existing = $("conversation-id-input").value.trim();
  if (existing) {
    state.conversationId = existing;
    updateContextSummary();
    return existing;
  }
  return createSession();
}

function resetChatView() {
  $("chat-output").innerHTML = `
    <div class="welcome-state">
      <div class="welcome-mark" aria-hidden="true"><strong>A</strong><span>READY</span></div>
      <h2>从一个具体问题开始</h2>
      <p>解释代码、分析架构，或把需要执行的任务交给 Agent。</p>
      <div class="prompt-grid" aria-label="推荐问题">
        <button type="button" class="prompt-card" data-prompt="解释这个项目的核心架构和请求调用链"><span>理解项目</span><strong>解释核心架构和请求调用链</strong></button>
        <button type="button" class="prompt-card" data-prompt="帮我分析 SSE 流式输出的实现与异常处理"><span>分析实现</span><strong>检查 SSE 流式输出</strong></button>
        <button type="button" class="prompt-card" data-prompt="为这个项目设计一套可靠的测试策略"><span>规划质量</span><strong>设计可靠的测试策略</strong></button>
      </div>
    </div>
  `;
  setChatStatus("等待输入");
}

async function createSession() {
  $("session-status").textContent = "正在创建会话…";
  try {
    const body = await fetchJson("/sessions", {
      method: "POST",
      body: JSON.stringify({ user_id: $("user-id-input").value.trim() || "demo_user" }),
    });
    state.conversationId = body.id;
    $("conversation-id-input").value = body.id;
    $("session-status").textContent = "会话已就绪";
    resetChatView();
    updateContextSummary();
    setRaw(body);
    await listSessions(false);
    showToast("新会话已创建");
    return body.id;
  } catch (error) {
    $("session-status").textContent = "创建会话失败";
    showToast(humanizeError(error), "error");
    throw error;
  }
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
  $("sessions-count").textContent = String(state.sessions.length);
  list.innerHTML = "";
  if (state.sessions.length === 0) {
    list.innerHTML = '<div class="empty-state">暂无会话，创建一个会话开始工作。</div>';
    return;
  }
  for (const session of state.sessions) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `session-item ${session.id === state.conversationId ? "active" : ""}`;
    item.dataset.sessionId = session.id;
    item.innerHTML = `
      <strong>${escapeHtml(session.id)}</strong>
      <span>${escapeHtml(session.user_id)} · ${escapeHtml(formatDate(session.created_at))}</span>
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
  $("session-status").textContent = "会话已加载";
  renderSessionSummary(summary);
  renderMessages(messages.messages || []);
  renderChatHistory(messages.messages || []);
  renderSessions();
  updateContextSummary();
  if (showRaw) {
    setRaw({ session, summary, messages });
  }
  return session;
}

async function loadSessionSummary() {
  try {
    const conversationId = await ensureSession();
    const body = await fetchJson(`/sessions/${encodeURIComponent(conversationId)}/summary`);
    renderSessionSummary(body);
    setRaw(body);
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function refreshMessages(showRaw = true) {
  const conversationId = await ensureSession();
  const body = await fetchJson(`/sessions/${encodeURIComponent(conversationId)}/messages`);
  renderMessages(body.messages || []);
  renderChatHistory(body.messages || []);
  if (showRaw) {
    setRaw(body);
  }
  return body;
}

function renderSessionSummary(summary) {
  $("session-summary").innerHTML = `
    <div class="summary-strip">
      <strong>${escapeHtml(summary.session_id)}</strong>
      <span>${escapeHtml(summary.message_count)} 条消息</span>
    </div>
    ${summary.last_message ? `<p class="context-note">最近：${escapeHtml(truncate(summary.last_message, 180))}</p>` : ""}
  `;
}

function renderMessages(messages) {
  const list = $("messages-list");
  list.innerHTML = "";
  if (messages.length === 0) {
    list.innerHTML = '<div class="empty-state">这个会话还没有消息。</div>';
    return;
  }
  for (const message of messages) {
    const item = document.createElement("article");
    item.className = `message-item role-${message.role}`;
    item.innerHTML = `
      <div class="message-meta"><strong>${escapeHtml(message.role)}</strong><span>${escapeHtml(formatDate(message.created_at))}</span></div>
      <p>${escapeHtml(message.content)}</p>
    `;
    list.appendChild(item);
  }
}

function renderChatHistory(messages) {
  const chatMessages = messages.filter((message) => ["user", "assistant"].includes(message.role));
  if (chatMessages.length === 0) {
    resetChatView();
    return;
  }
  const output = $("chat-output");
  output.innerHTML = "";
  for (const message of chatMessages) {
    appendChatMessage(message.role, message.content, message.created_at);
  }
}

async function addMessage() {
  const content = $("message-content-input").value.trim();
  if (!content) {
    showToast("请输入消息内容", "warning");
    return;
  }
  try {
    const conversationId = await ensureSession();
    const body = await fetchJson(`/sessions/${encodeURIComponent(conversationId)}/messages`, {
      method: "POST",
      body: JSON.stringify({
        role: $("message-role-input").value,
        content,
        run_agent: $("message-run-agent-input").checked,
      }),
    });
    renderMessages(body.messages || []);
    setRaw(body);
    await loadSessionSummary();
    showToast("测试消息已添加");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

function appendChatMessage(role, content = "", createdAt = null) {
  const output = $("chat-output");
  const welcome = output.querySelector(".welcome-state");
  if (welcome) {
    output.innerHTML = "";
  }
  const item = document.createElement("article");
  item.className = `chat-message ${role}`;
  const roleLabel = role === "user" ? "你" : "AI 助手";
  const avatar = role === "user" ? "你" : "A";
  item.innerHTML = `
    <div class="chat-avatar" aria-hidden="true">${avatar}</div>
    <div class="chat-bubble">
      <div class="message-label"><strong>${roleLabel}</strong><span>${escapeHtml(createdAt ? formatDate(createdAt) : "刚刚")}</span></div>
      <div class="message-content rich-output">${content ? renderMarkdown(content) : '<span class="typing-indicator" aria-label="正在生成"><span></span><span></span><span></span></span>'}</div>
    </div>
  `;
  output.appendChild(item);
  item.scrollIntoView({ behavior: preferredScrollBehavior(), block: "nearest" });
  return item.querySelector(".message-content");
}

function stopChat() {
  if (state.chatController) {
    state.chatController.abort();
  }
}

async function submitComposerMessage() {
  if (state.composerMode === "agent") {
    await runAgentFromComposer();
    return;
  }
  await streamChat();
}

async function runAgentFromComposer() {
  const input = $("chat-message-input");
  const message = input.value.trim();
  if (!message) {
    input.setAttribute("aria-invalid", "true");
    showToast("请输入一条消息", "warning");
    input.focus();
    return;
  }

  const sendButton = $("send-chat-btn");
  const modeInput = $("composer-mode-input");
  sendButton.disabled = true;
  sendButton.setAttribute("aria-busy", "true");
  modeInput.disabled = true;
  $("agent-message-input").value = message;
  $("focus-files-input").value = "";
  input.value = "";
  switchView("agent");
  showToast("任务已交给代码 Agent，可在这里查看进度和审批");
  try {
    await runAgent();
  } finally {
    sendButton.disabled = false;
    sendButton.removeAttribute("aria-busy");
    modeInput.disabled = false;
  }
}

async function streamChat() {
  const input = $("chat-message-input");
  const message = input.value.trim();
  if (!message) {
    input.setAttribute("aria-invalid", "true");
    showToast("请输入一条消息", "warning");
    input.focus();
    return;
  }

  const sendButton = $("send-chat-btn");
  const stopButton = $("stop-chat-btn");
  const modeInput = $("composer-mode-input");
  sendButton.disabled = true;
  sendButton.setAttribute("aria-busy", "true");
  modeInput.disabled = true;
  stopButton.classList.remove("hidden");
  $("chat-output").setAttribute("aria-busy", "true");
  setChatStatus("正在准备", "running");
  setTrace([]);
  state.chatController = new AbortController();
  let assistantContent = null;
  let answer = "";

  try {
    const conversationId = await ensureSession();
    appendChatMessage("user", message);
    assistantContent = appendChatMessage("assistant");
    input.value = "";
    const payload = { conversation_id: conversationId, message, ...optionalModelFields() };
    const events = [];
    await postSse(
      "/chat/stream",
      payload,
      (eventName, data) => {
        events.push({ event: eventName, data });
        if (events.length <= 200) {
          setRaw(events);
        }
        if (eventName === "meta") {
          const thinking = data.thinking_level ? ` · ${data.thinking_level}` : "";
          setChatStatus(`${data.provider} · ${data.model}${thinking}`, "running");
        } else if (eventName === "delta") {
          answer += data.text || "";
          assistantContent.innerHTML = renderMarkdown(answer);
        } else if (eventName === "usage") {
          const thoughts = data.thoughts_tokens
            ? ` · ${data.thoughts_tokens} thinking`
            : "";
          setChatStatus(`${data.total_tokens || 0} tokens${thoughts}`, "running");
        } else if (eventName === "done") {
          setChatStatus(`已完成 · ${formatDuration(data.elapsed_ms)}`, "completed");
        } else if (eventName === "error") {
          const streamError = new Error(data.message || data.code || "模型响应失败");
          streamError.code = data.code || "llm_provider_error";
          streamError.finishReason = data.finish_reason || "";
          streamError.preservePartial = Boolean(data.partial_response && answer);
          throw streamError;
        }
      },
      state.chatController.signal,
    );
    if (!answer) {
      assistantContent.innerHTML = "<p>模型没有返回文本内容。</p>";
    }
    await refreshMessages(false);
  } catch (error) {
    if (error.name === "AbortError") {
      if (assistantContent) {
        assistantContent.innerHTML = `${assistantContent.innerHTML}<p><em>生成已由你停止。</em></p>`;
      }
      setChatStatus("已停止", "neutral");
    } else {
      if (assistantContent) {
        const detail = error.code === "max_output_tokens"
          ? "回答达到输出额度上限，已保留生成的部分内容。可提高额度或降低 Gemini 思考等级后重试。"
          : humanizeError(error);
        assistantContent.innerHTML = error.preservePartial
          ? `${renderMarkdown(answer)}<p><em>${escapeHtml(detail)}</em></p>`
          : `<p>${escapeHtml(detail)}</p>`;
      }
      setChatStatus(
        error.code === "max_output_tokens" ? "输出已截断" : "生成失败",
        error.code === "max_output_tokens" ? "warning" : "failed",
      );
      showToast(
        error.code === "max_output_tokens" ? "回答达到输出额度上限" : humanizeError(error),
        error.code === "max_output_tokens" ? "warning" : "error",
      );
    }
  } finally {
    state.chatController = null;
    sendButton.disabled = false;
    sendButton.removeAttribute("aria-busy");
    modeInput.disabled = false;
    stopButton.classList.add("hidden");
    $("chat-output").removeAttribute("aria-busy");
    input.focus();
  }
}

async function postSse(path, payload, onEvent, signal) {
  const startedAt = performance.now();
  let status = "ERR";
  let requestId = "";
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
  try {
    return { event, data: JSON.parse(rawData) };
  } catch {
    return { event, data: { text: rawData } };
  }
}

function setAgentStatus(status, runId = "") {
  const node = $("agent-status");
  node.className = `status-pill ${statusClass(status)}`;
  const label = `${humanizeStatus(status)}${runId ? ` · ${truncate(runId, 18)}` : ""}`;
  node.innerHTML = `<span class="status-dot" aria-hidden="true"></span><span>${escapeHtml(label)}</span>`;
}

async function runAgent() {
  const message = $("agent-message-input").value.trim();
  if (!message) {
    $("agent-message-input").setAttribute("aria-invalid", "true");
    showToast("请先描述 Agent 任务", "warning");
    return;
  }
  const button = $("run-agent-btn");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  $("agent-answer").setAttribute("aria-busy", "true");
  setAgentStatus("running");
  $("agent-answer").className = "rich-output empty-output";
  $("agent-answer").textContent = "Agent 正在理解任务并规划下一步…";
  $("agent-events").innerHTML = '<div class="empty-state">正在等待第一个运行事件…</div>';
  $("approval-card").classList.add("hidden");
  try {
    const conversationId = await ensureSession();
    const payload = {
      conversation_id: conversationId,
      message,
      workspace_id: $("workspace-id-input").value.trim() || "workspace_main",
      focus_files: csvValues($("focus-files-input").value),
    };
    const body = await fetchJson("/agent/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderAgentRun(body);
    await pollRunUntilTerminal();
  } catch (error) {
    setAgentStatus("failed");
    $("agent-answer").className = "rich-output";
    $("agent-answer").innerHTML = `<p>${escapeHtml(humanizeError(error))}</p>`;
    showToast(humanizeError(error), "error");
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    $("agent-answer").removeAttribute("aria-busy");
  }
}

function renderAgentRun(body) {
  const result = body.result || {};
  state.latestRunId = body.run_id || result.run_id || "";
  state.latestRunStatus = body.status || result.status || "";
  setAgentStatus(state.latestRunStatus, state.latestRunId);

  const answer = result.answer || body.error || "";
  const answerNode = $("agent-answer");
  if (answer) {
    answerNode.className = "rich-output";
    answerNode.innerHTML = renderMarkdown(answer);
  } else if (body.pending_approval) {
    answerNode.className = "rich-output empty-output";
    answerNode.textContent = "执行计划已生成，请完成下方审批。";
  } else {
    answerNode.className = "rich-output empty-output";
    answerNode.textContent = "Agent 正在运行，结果会在完成后显示。";
  }

  renderApproval(body.pending_approval);
  renderAgentMetrics(result.metrics);
  renderArtifacts(result.artifacts || []);
  setTrace(body.trace || result.trace || []);
  setRaw(body);
  renderOverview();
}

function renderApproval(approval) {
  const card = $("approval-card");
  if (!approval) {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");
  $("approval-reason").textContent = approval.reason || "一个或多个工具需要在执行前确认。";
  const tools = $("approval-tools");
  tools.innerHTML = "";
  const requiredByName = new Map(
    (approval.approval_required_tools || []).map((item) => [item.name, item]),
  );
  const calls = approval.tool_calls || (approval.planned_tools || []).map((name) => ({ name, arguments: {} }));
  for (const call of calls) {
    const risk = requiredByName.get(call.name) || {};
    const item = document.createElement("details");
    item.className = "approval-tool";
    item.innerHTML = `
      <summary><strong>${escapeHtml(call.name)}</strong><span>${escapeHtml(risk.permission_level || "计划工具")}</span></summary>
      <p>${escapeHtml(risk.risk_summary || "只读或低风险工具；请确认参数符合预期。")}</p>
      <pre>${escapeHtml(jsonPretty(risk.arguments_summary || call.arguments || {}))}</pre>
    `;
    tools.appendChild(item);
  }
  card.scrollIntoView({ behavior: preferredScrollBehavior(), block: "center" });
}

function renderAgentMetrics(metrics) {
  const values = metrics
    ? [
        ["耗时", formatDuration(metrics.elapsed_ms)],
        ["节点", metrics.node_count],
        ["工具调用", `${metrics.successful_tool_call_count}/${metrics.tool_call_count}`],
        ["变更文件", metrics.changed_file_count],
      ]
    : [["耗时", "—"], ["节点", "—"], ["工具调用", "—"], ["变更文件", "—"]];
  $("agent-metrics").innerHTML = values
    .map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`)
    .join("");
}

function renderArtifacts(artifacts) {
  const list = $("agent-artifacts");
  list.innerHTML = "";
  for (const [index, artifact] of artifacts.entries()) {
    const item = document.createElement("article");
    item.className = "artifact-card";
    const title = artifact.type || artifact.name || `Artifact ${index + 1}`;
    const content = artifact.content || artifact.diff || artifact.output || artifact;
    item.innerHTML = `<strong>${escapeHtml(title)}</strong><pre>${escapeHtml(typeof content === "string" ? content : jsonPretty(content))}</pre>`;
    list.appendChild(item);
  }
}

async function refreshRun() {
  if (!state.latestRunId) {
    showToast("还没有可刷新的 Agent 运行", "warning");
    return null;
  }
  const body = await fetchJson(`/agent/runs/${encodeURIComponent(state.latestRunId)}`);
  renderAgentRun(body);
  return body;
}

async function refreshEvents(showRaw = true) {
  if (!state.latestRunId) {
    $("agent-events").innerHTML = '<div class="empty-state">暂无运行事件</div>';
    return null;
  }
  const body = await fetchJson(`/agent/runs/${encodeURIComponent(state.latestRunId)}/events`);
  renderAgentEvents(body.events || []);
  if (showRaw) {
    setRaw(body);
  }
  return body;
}

function renderAgentEvents(events) {
  const list = $("agent-events");
  list.innerHTML = "";
  if (events.length === 0) {
    list.innerHTML = '<div class="empty-state">暂无运行事件</div>';
    return;
  }
  for (const event of events) {
    const item = document.createElement("div");
    const eventStatus = event.status || "pending";
    item.className = `timeline-item ${statusClass(eventStatus)}`;
    item.innerHTML = `
      <div class="timeline-heading">
        <strong>${escapeHtml(event.sequence)} · ${escapeHtml(event.node || event.type)}</strong>
        <span>${escapeHtml(humanizeStatus(eventStatus))}</span>
      </div>
      <p>${escapeHtml(event.summary || humanizeStatus(event.status))}</p>
    `;
    list.appendChild(item);
  }
}

async function resumeRun(approved) {
  if (!state.latestRunId) {
    return;
  }
  const approveButton = $("approve-run-btn");
  const rejectButton = $("reject-run-btn");
  approveButton.disabled = true;
  rejectButton.disabled = true;
  approveButton.setAttribute("aria-busy", "true");
  setAgentStatus("running", state.latestRunId);
  try {
    const feedback = $("approval-feedback-input").value.trim();
    const body = await fetchJson(`/agent/runs/${encodeURIComponent(state.latestRunId)}/resume`, {
      method: "POST",
      body: JSON.stringify({
        approved,
        feedback: feedback || (approved ? "用户已在产品界面确认执行计划" : "用户拒绝执行计划"),
      }),
    });
    renderAgentRun(body);
    showToast(approved ? "执行计划已批准" : "执行计划已拒绝", approved ? "success" : "warning");
    await pollRunUntilTerminal();
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    approveButton.disabled = false;
    rejectButton.disabled = false;
    approveButton.removeAttribute("aria-busy");
  }
}

async function pollRunUntilTerminal() {
  const generation = ++state.agentPollGeneration;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (
      generation !== state.agentPollGeneration ||
      !state.latestRunId ||
      TERMINAL_RUN_STATUSES.has(state.latestRunStatus)
    ) {
      break;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 750));
    await refreshRun();
    if (attempt % 2 === 0) {
      await refreshEvents(false);
    }
  }
  await refreshEvents(false);
  try {
    await refreshMessages(false);
  } catch (error) {
    showToast(`Agent 已结束，但共享会话刷新失败：${humanizeError(error)}`, "warning");
  }
  if (!TERMINAL_RUN_STATUSES.has(state.latestRunStatus)) {
    showToast("Agent 仍在后台运行，可稍后刷新状态", "warning");
  }
}

async function ingestDocument() {
  const input = $("document-files-input");
  const files = Array.from(input.files || []);
  if (!files.length) {
    showToast("请先选择需要录入的文件", "warning");
    return;
  }

  const kbId = $("kb-id-input").value.trim();
  if (!kbId) {
    showToast("请先创建并选择知识库", "warning");
    return;
  }

  const ingested = [];
  const failures = [];
  $("ingest-doc-btn").disabled = true;
  try {
    for (const [index, file] of files.entries()) {
      $("rag-status").textContent = `正在录入 ${index + 1}/${files.length}…`;
      const form = new FormData();
      form.append("file", file, file.name);
      try {
        const body = await fetchJson(
          `/knowledge-bases/${encodeURIComponent(kbId)}/documents`,
          { method: "POST", body: form },
        );
        ingested.push(body);
      } catch (error) {
        failures.push({ filename: file.name, error: humanizeError(error) });
      }
    }
    await listKnowledgeBases();
    $("kb-id-input").value = kbId;
    const chunkCount = ingested.reduce((total, item) => total + item.chunk_count, 0);
    $("rag-status").textContent = failures.length
      ? `已录入 ${ingested.length}，失败 ${failures.length}`
      : `已录入 ${ingested.length} 个文件`;
    setRaw({ documents: ingested, failures });
    input.value = "";
    renderSelectedDocumentFiles();
    if (failures.length) {
      showToast(
        `${ingested.length} 个文件录入成功，${failures.length} 个失败；详情见原始数据`,
        ingested.length ? "warning" : "error",
      );
    } else {
      showToast(`已录入 ${ingested.length} 个文件，共 ${chunkCount} 个片段`);
    }
  } finally {
    $("ingest-doc-btn").disabled = false;
  }
}

function renderSelectedDocumentFiles() {
  const files = Array.from($("document-files-input").files || []);
  $("selected-document-files").textContent = files.length
    ? `已选择 ${files.length} 个文件：${files.map((file) => file.name).join("、")}`
    : "尚未选择文件";
}

async function searchRag() {
  const question = $("rag-question-input").value.trim();
  if (!question) {
    showToast("请输入搜索词", "warning");
    return;
  }
  $("rag-status").textContent = "正在检索…";
  try {
    const kbId = $("kb-id-input").value.trim();
    if (!kbId) {
      throw new Error("请先选择知识库");
    }
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(kbId)}/search`, {
      method: "POST",
      body: JSON.stringify({
        query: question,
        limit: numberValue("rag-limit-input", 5),
        recall_limit: numberValue("rag-recall-limit-input", 10),
      }),
    });
    const results = body.results || [];
    $("rag-status").textContent = `找到 ${results.length} 条结果`;
    $("rag-answer").className = "rich-output";
    $("rag-answer").innerHTML = results.length
      ? `<h3>检索完成</h3><p>在知识库 <code>${escapeHtml(kbId)}</code> 中找到 ${results.length} 条相关内容。</p>`
      : "<p>没有找到相关内容。可以调整问题或增加召回数量后重试。</p>";
    renderCitations(results);
    setRaw(body);
  } catch (error) {
    $("rag-status").textContent = "检索失败";
    showToast(humanizeError(error), "error");
  }
}

async function askRag() {
  const question = $("rag-question-input").value.trim();
  if (!question) {
    showToast("请输入问题", "warning");
    return;
  }
  $("rag-status").textContent = "正在生成…";
  $("rag-answer").className = "rich-output empty-output";
  $("rag-answer").textContent = "正在检索相关内容并组织回答…";
  try {
    const kbId = $("kb-id-input").value.trim();
    if (!kbId) {
      throw new Error("请先选择知识库");
    }
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(kbId)}/ask`, {
      method: "POST",
      body: JSON.stringify({
        question,
        limit: numberValue("rag-limit-input", 5),
        recall_limit: numberValue("rag-recall-limit-input", 10),
        ...optionalModelFields(),
      }),
    });
    $("rag-status").textContent = `${body.citations.length} 条引用`;
    $("rag-answer").className = "rich-output";
    $("rag-answer").innerHTML = renderMarkdown(body.answer || "模型没有返回回答。");
    renderCitations(body.citations || []);
    setRaw(body);
  } catch (error) {
    $("rag-status").textContent = "生成失败";
    $("rag-answer").className = "rich-output";
    $("rag-answer").innerHTML = `<p>${escapeHtml(humanizeError(error))}</p>`;
    showToast(humanizeError(error), "error");
  }
}

function renderCitations(citations) {
  const list = $("rag-citations");
  list.innerHTML = "";
  for (const [index, citation] of citations.entries()) {
    const score = Number(citation.score || 0).toFixed(3);
    const lines = citation.start_line || citation.end_line
      ? ` · 行 ${citation.start_line || "?"}–${citation.end_line || "?"}`
      : "";
    const item = document.createElement("article");
    item.className = "citation-card";
    item.innerHTML = `
      <header><strong>[${index + 1}] ${escapeHtml(citation.filename)} · #${escapeHtml(citation.chunk_index)}${escapeHtml(lines)}</strong><span class="score-pill">${escapeHtml(score)}</span></header>
      <p>${escapeHtml(citation.text)}</p>
    `;
    list.appendChild(item);
  }
}

function knowledgeBasePayload() {
  return {
    name: $("kb-name-input").value.trim(),
    description: $("kb-description-input").value.trim(),
    tags: $("kb-tags-input").value
      .split(/[,，]/)
      .map((item) => item.trim())
      .filter(Boolean),
  };
}

function selectKnowledgeBase(knowledgeBase) {
  if (!knowledgeBase) {
    return;
  }
  $("kb-catalog-id-input").value = knowledgeBase.id;
  $("kb-name-input").value = knowledgeBase.name;
  $("kb-description-input").value = knowledgeBase.description || "";
  $("kb-tags-input").value = (knowledgeBase.tags || []).join(", ");
  $("kb-id-input").value = knowledgeBase.id;
}

function renderKnowledgeBases() {
  const list = $("knowledge-base-list");
  const select = $("kb-id-input");
  const selectedId = select.value;
  select.innerHTML = '<option value="">请选择知识库</option>';
  for (const knowledgeBase of state.knowledgeBases) {
    const option = document.createElement("option");
    option.value = knowledgeBase.id;
    option.textContent = `${knowledgeBase.name} (${knowledgeBase.id})`;
    select.appendChild(option);
  }
  if (state.knowledgeBases.some((item) => item.id === selectedId)) {
    select.value = selectedId;
  } else if (state.knowledgeBases.length) {
    select.value = state.knowledgeBases[0].id;
  }
  if (!state.knowledgeBases.length) {
    list.innerHTML = '<div class="empty-state">暂无知识库，请先创建目录。</div>';
    return;
  }
  list.innerHTML = state.knowledgeBases
    .map(
      (item) => `
        <button class="session-row" type="button" data-knowledge-base-id="${escapeHtml(item.id)}">
          <span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.id)} · ${escapeHtml(item.document_count)} 个文档</small></span>
          <small>${escapeHtml(truncate(item.description || (item.tags || []).join(", ") || "暂无描述", 80))}</small>
        </button>
      `,
    )
    .join("");
}

async function listKnowledgeBases() {
  const body = await fetchJson("/knowledge-bases");
  state.knowledgeBases = body.knowledge_bases || [];
  renderKnowledgeBases();
  return state.knowledgeBases;
}

async function createKnowledgeBase() {
  const id = $("kb-catalog-id-input").value.trim();
  const payload = knowledgeBasePayload();
  if (!id || !payload.name) {
    showToast("知识库 ID 和名称不能为空", "warning");
    return;
  }
  try {
    const body = await fetchJson("/knowledge-bases", {
      method: "POST",
      body: JSON.stringify({ id, ...payload }),
    });
    await listKnowledgeBases();
    selectKnowledgeBase(body);
    setRaw(body);
    showToast("知识库已创建");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function updateKnowledgeBase() {
  const id = $("kb-catalog-id-input").value.trim();
  const payload = knowledgeBasePayload();
  if (!id || !payload.name) {
    showToast("请选择知识库并填写名称", "warning");
    return;
  }
  try {
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    await listKnowledgeBases();
    selectKnowledgeBase(body);
    setRaw(body);
    showToast("知识库元数据已更新");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function deleteKnowledgeBase() {
  const id = $("kb-catalog-id-input").value.trim();
  if (!id) {
    showToast("请选择要删除的知识库", "warning");
    return;
  }
  if (!window.confirm(`删除知识库 ${id}？其中的文档、分块和向量也会被删除。`)) {
    return;
  }
  try {
    await fetchJson(`/knowledge-bases/${encodeURIComponent(id)}`, { method: "DELETE" });
    $("kb-catalog-id-input").value = "";
    $("kb-name-input").value = "";
    $("kb-description-input").value = "";
    $("kb-tags-input").value = "";
    await listKnowledgeBases();
    showToast("知识库已删除");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function registerWorkspace() {
  const workspaceId = $("workspace-id-input").value.trim();
  const rootPath = $("workspace-root-input").value.trim();
  if (!workspaceId) {
    showToast("请填写工作区 ID", "warning");
    return;
  }
  if (!rootPath) {
    showToast("请填写工作区根路径", "warning");
    $("workspace-root-input").focus();
    return;
  }
  $("register-workspace-btn").disabled = true;
  try {
    const body = await fetchJson(`/workspaces/${encodeURIComponent(workspaceId)}`, {
      method: "PUT",
      body: JSON.stringify({ root_path: rootPath }),
    });
    setRaw(body);
    await listWorkspaces();
    $("workspace-select").value = workspaceId;
    showToast(`工作区 ${workspaceId} 已注册`, "success");
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    $("register-workspace-btn").disabled = false;
  }
}

async function listWorkspaces() {
  const body = await fetchJson("/workspaces");
  state.workspaces = body.workspaces || [];
  const select = $("workspace-select");
  const selectedId = $("workspace-id-input").value.trim();
  select.innerHTML = '<option value="">选择工作区</option>';
  for (const workspace of state.workspaces) {
    const option = document.createElement("option");
    option.value = workspace.id;
    option.textContent = `${workspace.id} · ${workspace.root_path}`;
    option.dataset.rootPath = workspace.root_path;
    select.appendChild(option);
  }
  select.value = state.workspaces.some((item) => item.id === selectedId)
    ? selectedId
    : "";
}

function bindEvents() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });

  document.addEventListener("click", (event) => {
    const prompt = event.target.closest("[data-prompt]");
    if (!prompt) {
      return;
    }
    $("chat-message-input").value = prompt.dataset.prompt;
    $("chat-message-input").focus();
  });

  $("open-settings-btn").addEventListener("click", openSettings);
  $("sidebar-settings-btn").addEventListener("click", openSettings);
  $("save-settings-btn").addEventListener("click", saveSettings);
  $("close-settings-btn").addEventListener("click", closeSettings);
  $("settings-dialog").addEventListener("click", (event) => {
    if (event.target === $("settings-dialog")) {
      closeSettings();
    }
  });

  $("toggle-inspector-btn").addEventListener("click", () => {
    setInspectorVisible(document.body.classList.contains("inspector-hidden"));
  });
  $("close-inspector-btn").addEventListener("click", () => setInspectorVisible(false));
  $("trace-tab").addEventListener("click", () => selectInspectorTab("trace"));
  $("raw-tab").addEventListener("click", () => selectInspectorTab("raw"));

  $("create-session-btn").addEventListener("click", createSession);
  $("sessions-create-btn").addEventListener("click", createSession);
  $("load-session-btn").addEventListener("click", async () => {
    try {
      await loadSession();
      closeSettings();
      showToast("会话已加载");
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });
  $("list-sessions-btn").addEventListener("click", async () => {
    try {
      await listSessions();
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });
  $("summary-session-btn").addEventListener("click", loadSessionSummary);
  $("refresh-messages-btn").addEventListener("click", async () => {
    try {
      await refreshMessages();
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });
  $("add-message-btn").addEventListener("click", addMessage);
  $("sessions-list").addEventListener("click", async (event) => {
    const row = event.target.closest("[data-session-id]");
    if (!row) {
      return;
    }
    state.conversationId = row.dataset.sessionId;
    $("conversation-id-input").value = state.conversationId;
    try {
      await loadSession();
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });

  $("send-chat-btn").addEventListener("click", submitComposerMessage);
  $("stop-chat-btn").addEventListener("click", stopChat);
  $("composer-mode-input").addEventListener("change", (event) => {
    updateComposerMode(event.target.value);
  });
  $("composer-provider-input").addEventListener("change", (event) => {
    $("provider-input").value = event.target.value;
    updateContextSummary();
  });
  $("composer-attachment-btn").addEventListener("click", () => {
    switchView("rag");
    $("document-files-input").click();
  });
  $("chat-message-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submitComposerMessage();
    }
  });
  ["chat-message-input", "agent-message-input", "rag-question-input"].forEach((id) => {
    $(id).addEventListener("input", () => $(id).removeAttribute("aria-invalid"));
  });

  $("run-agent-btn").addEventListener("click", runAgent);
  $("refresh-run-btn").addEventListener("click", async () => {
    try {
      await refreshRun();
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });
  $("refresh-events-btn").addEventListener("click", async () => {
    try {
      await refreshEvents();
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });
  $("approve-run-btn").addEventListener("click", () => resumeRun(true));
  $("reject-run-btn").addEventListener("click", () => resumeRun(false));

  $("ingest-doc-btn").addEventListener("click", ingestDocument);
  $("document-files-input").addEventListener("change", renderSelectedDocumentFiles);
  $("refresh-knowledge-bases-btn").addEventListener("click", () => {
    listKnowledgeBases().catch((error) => showToast(humanizeError(error), "error"));
  });
  $("create-knowledge-base-btn").addEventListener("click", createKnowledgeBase);
  $("update-knowledge-base-btn").addEventListener("click", updateKnowledgeBase);
  $("delete-knowledge-base-btn").addEventListener("click", deleteKnowledgeBase);
  $("knowledge-base-list").addEventListener("click", (event) => {
    const row = event.target.closest("[data-knowledge-base-id]");
    if (!row) {
      return;
    }
    selectKnowledgeBase(
      state.knowledgeBases.find((item) => item.id === row.dataset.knowledgeBaseId),
    );
  });
  $("kb-id-input").addEventListener("change", (event) => {
    selectKnowledgeBase(
      state.knowledgeBases.find((item) => item.id === event.target.value),
    );
  });
  $("search-rag-btn").addEventListener("click", searchRag);
  $("ask-rag-btn").addEventListener("click", askRag);
  $("register-workspace-btn").addEventListener("click", registerWorkspace);
  $("refresh-workspaces-btn").addEventListener("click", () => {
    listWorkspaces().catch((error) => showToast(humanizeError(error), "error"));
  });
  $("workspace-select").addEventListener("change", (event) => {
    const workspace = state.workspaces.find((item) => item.id === event.target.value);
    if (!workspace) {
      return;
    }
    $("workspace-id-input").value = workspace.id;
    $("workspace-root-input").value = workspace.root_path;
    updateContextSummary();
  });

  $("refresh-overview-btn").addEventListener("click", async () => {
    await Promise.allSettled([checkHealth(), listSessions(false)]);
  });
  $("clear-log-btn").addEventListener("click", () => {
    state.requestLog = [];
    renderRequestLog();
    renderOverview();
  });
  $("clear-detail-btn").addEventListener("click", () => {
    setTrace([]);
    setRaw("等待响应…");
    setLastRequestId("");
  });

  $("conversation-id-input").addEventListener("input", (event) => {
    state.conversationId = event.target.value.trim();
    updateContextSummary();
  });
  ["user-id-input", "provider-input", "model-input", "thinking-level-input", "workspace-id-input", "workspace-root-input"].forEach((id) => {
    $(id).addEventListener("input", updateContextSummary);
    $(id).addEventListener("change", updateContextSummary);
  });

  window.addEventListener("hashchange", () => {
    const view = location.hash.replace("#", "");
    if (document.querySelector(`[data-view-panel="${view}"]`)) {
      switchView(view, false);
    }
  });
}

async function init() {
  bindEvents();
  const preferences = loadUiPreferences();
  const requestedView = location.hash.replace("#", "");
  const preferredView = document.querySelector(`[data-view-panel="${preferences.view}"]`)
    ? preferences.view
    : "chat";
  const initialView = document.querySelector(`[data-view-panel="${requestedView}"]`)
    ? requestedView
    : preferredView;
  state.composerMode = preferences.composerMode === "agent" ? "agent" : "chat";
  updateComposerMode(state.composerMode);
  switchView(initialView, !location.hash);
  setInspectorVisible(!preferences.inspectorHidden && window.innerWidth > 1120);
  selectInspectorTab("trace");
  setTrace([]);
  renderRequestLog();
  renderSessions();
  renderOverview();
  updateContextSummary();
  await Promise.allSettled([
    checkHealth(),
    listSessions(false),
    listWorkspaces(),
    listKnowledgeBases(),
  ]);
}

init();
