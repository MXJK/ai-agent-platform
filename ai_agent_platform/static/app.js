const API_BASE = "/api/v1";
const UI_STORAGE_KEY = "ai-agent-platform-ui-v2";
const FINAL_RUN_STATUSES = new Set(["completed", "partial", "blocked", "cancelled", "failed"]);
const SUSPENDED_RUN_STATUSES = new Set(["waiting_approval", "waiting_input", "paused"]);
const TERMINAL_RUN_STATUSES = new Set([...FINAL_RUN_STATUSES, ...SUSPENDED_RUN_STATUSES]);
const TRACE_STEP_REVEAL_DELAY_MS = 16;
const MAX_TRACE_REPLAY_MS = 1200;
const responseTimers = new WeakMap();

const state = {
  conversationId: "",
  latestRunId: "",
  latestRunStatus: "",
  latestRunConversationId: "",
  healthStatus: "checking",
  sessionStorageMode: "unknown",
  sessions: [],
  recentSessions: [],
  sessionsNextCursor: null,
  sessionsArchived: false,
  sessionsQuery: "",
  currentSession: null,
  preferences: null,
  sessionTokenUsage: {},
  requestLog: [],
  workspaces: [],
  workspacesLoaded: false,
  activeWorkspaceId: "",
  defaultWorkspaceId: "",
  workspaceTokenUsage: {},
  workspaceTokenUsageErrors: {},
  workspaceDirectoryPath: null,
  workspaceDirectoryParentPath: null,
  workspaceRelinkId: null,
  knowledgeBases: [],
  preferredKnowledgeBaseId: "",
  knowledgeDocuments: [],
  knowledgeDocumentTotal: 0,
  knowledgeDocumentPage: 1,
  knowledgeDocumentPageSize: 20,
  selectedDocumentIds: new Set(),
  activeKnowledgeDocument: null,
  activeKnowledgeTab: "documents",
  documentRequestController: null,
  documentRequestGeneration: 0,
  documentDrawerReturnFocus: null,
  rerankEnabled: false,
  rerankerCapabilities: {
    available: false,
    provider: null,
    model: null,
    default_enabled: false,
    status: "checking",
  },
  projectMemories: [],
  selectedMemoryId: "",
  modelRegistry: {
    connections: [],
    models: [],
    routing_policies: ["smart", "quality", "cost", "latency"],
  },
  modelDiscovery: {
    provider: "",
    models: [],
    loading: false,
  },
  modelPreference: {
    mode: "auto",
    routing_policy: "smart",
    preferred_model_id: null,
    fallback_enabled: true,
  },
  currentView: "chat",
  composerMode: "chat",
  chatController: null,
  agentPollGeneration: 0,
  changeSetRequestGeneration: 0,
  currentChangeSet: null,
  ragRequestController: null,
  ragRequestGeneration: 0,
};

const $ = (id) => document.getElementById(id);

function iconMarkup(name) {
  return `<svg class="app-icon" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;
}

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function preferredScrollBehavior() {
  return prefersReducedMotion() ? "auto" : "smooth";
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
  const thinkingLevel = $("thinking-level-input").value.trim();
  return {
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

function formatTokenCount(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function humanizeAgentNode(value) {
  const labels = {
    setup_workspace: "准备工作区",
    load_project_instructions: "加载项目指令",
    classify_request: "识别任务意图",
    decide_context_source: "选择上下文来源",
    retrieve_knowledge: "检索知识库",
    retrieve_project_memory: "检索项目记忆",
    plan_exploration: "规划仓库探索",
    execute_exploration: "执行仓库探索",
    assess_context: "评估上下文",
    merge_evidence: "合并证据",
    plan_tools: "规划工具调用",
    review_tool_plan: "等待工具审批",
    inspect_repository: "检查代码仓库",
    execute_changes: "执行代码修改",
    validate_changes: "验证代码修改",
    review_repair_plan: "等待修复审批",
    collect_artifacts: "汇总变更产物",
    compose_answer: "生成最终回答",
    compose_error_answer: "生成错误说明",
    model_request: "请求模型",
    stream_response: "流式生成回答",
  };
  return labels[value] || value || "准备执行";
}

function traceToolNames(trace) {
  const names = [];
  for (const step of trace || []) {
    const output = step.output || {};
    for (const key of [
      "called_tools",
      "planned_tools",
      "repair_planned_tools",
      "approval_required_tools",
    ]) {
      const values = Array.isArray(output[key]) ? output[key] : [];
      for (const value of values) {
        const name = typeof value === "string" ? value : value?.name;
        if (name && !names.includes(name)) {
          names.push(name);
        }
      }
    }
  }
  return names;
}

function ensureExecutionProcess(contentNode) {
  const bubble = contentNode.closest(".chat-bubble");
  let details = bubble.querySelector(".execution-process");
  if (!details) {
    details = document.createElement("details");
    details.className = "execution-process";
    details.open = true;
    details.innerHTML = `
      <summary>
        <span class="execution-summary-main">
          <span class="execution-indicator" aria-hidden="true"></span>
          <strong>正在思考</strong>
          <span class="execution-summary-text">准备执行…</span>
        </span>
        <span class="execution-duration">0 ms</span>
      </summary>
      <div class="execution-body">
        <ol class="execution-steps"></ol>
        <div class="execution-tools" hidden></div>
      </div>
    `;
    bubble.insertBefore(details, contentNode);
  }
  return details;
}

function renderExecutionProcess(
  contentNode,
  {
    trace = [],
    status = "running",
    elapsedMs = 0,
    fallbackNode = "",
    fallbackSummary = "",
  } = {},
) {
  const details = ensureExecutionProcess(contentNode);
  const terminal = TERMINAL_RUN_STATUSES.has(status) || status === "done";
  const tools = traceToolNames(trace);
  const steps = trace.length
    ? trace
    : [{
        step: 1,
        node: fallbackNode || "model_request",
        summary: fallbackSummary || "正在建立请求并等待响应。",
        output: {},
      }];
  details.classList.toggle("complete", terminal);
  details.open = !terminal;
  details.querySelector(".execution-summary-main strong").textContent = terminal
    ? "思考过程"
    : "正在思考";
  details.querySelector(".execution-summary-text").textContent = terminal
    ? `${steps.length} 个阶段${tools.length ? ` · ${tools.length} 个工具` : ""}`
    : humanizeAgentNode(steps.at(-1)?.node);
  details.querySelector(".execution-duration").textContent = formatDuration(elapsedMs);
  details.querySelector(".execution-steps").innerHTML = steps
    .map((step, index) => `
      <li>
        <span class="execution-step-index">${escapeHtml(step.step ?? index + 1)}</span>
        <div>
          <strong>${escapeHtml(humanizeAgentNode(step.node))}</strong>
          <p>${escapeHtml(step.summary || "")}</p>
        </div>
      </li>
    `)
    .join("");
  const toolList = details.querySelector(".execution-tools");
  toolList.hidden = tools.length === 0;
  toolList.innerHTML = tools.length
    ? `<span>工具</span>${tools.map((name) => `<code>${escapeHtml(name)}</code>`).join("")}`
    : "";
  return details;
}

function startResponseTimer(contentNode, startedAt) {
  stopResponseTimer(contentNode);
  const timer = window.setInterval(() => {
    const duration = contentNode.closest(".chat-bubble")
      ?.querySelector(".execution-duration");
    if (duration) {
      duration.textContent = formatDuration(performance.now() - startedAt);
    }
  }, 200);
  responseTimers.set(contentNode, timer);
}

function stopResponseTimer(contentNode) {
  const timer = responseTimers.get(contentNode);
  if (timer) {
    window.clearInterval(timer);
    responseTimers.delete(contentNode);
  }
}

function renderResponseMetrics(contentNode, metrics) {
  const bubble = contentNode.closest(".chat-bubble");
  let footer = bubble.querySelector(".response-metrics");
  if (!footer) {
    footer = document.createElement("div");
    footer.className = "response-metrics";
    bubble.appendChild(footer);
  }
  const values = [];
  if (metrics.elapsed_ms !== undefined) {
    values.push(["耗时", formatDuration(metrics.elapsed_ms)]);
  }
  if (metrics.input_tokens !== undefined) {
    values.push(["输入", `${formatTokenCount(metrics.input_tokens)} tokens`]);
  }
  if (metrics.output_tokens !== undefined) {
    values.push(["输出", `${formatTokenCount(metrics.output_tokens)} tokens`]);
  }
  if (metrics.thoughts_tokens) {
    values.push(["思考", `${formatTokenCount(metrics.thoughts_tokens)} tokens`]);
  }
  if (metrics.total_tokens !== undefined) {
    values.push(["合计", `${formatTokenCount(metrics.total_tokens)} tokens`]);
  }
  if (metrics.node_count !== undefined) {
    values.push(["阶段", formatTokenCount(metrics.node_count)]);
  }
  if (metrics.tool_call_count !== undefined) {
    values.push(["工具", formatTokenCount(metrics.tool_call_count)]);
  }
  footer.innerHTML = values
    .map(([label, value]) => `<span><small>${escapeHtml(label)}</small>${escapeHtml(value)}</span>`)
    .join("");
  footer.hidden = values.length === 0;
}

function humanizeStatus(value) {
  const labels = {
    ok: "服务正常",
    offline: "连接失败",
    queued: "排队中",
    running: "运行中",
    waiting_approval: "等待审批",
    waiting_input: "等待输入",
    paused: "已暂停",
    completed: "已完成",
    partial: "部分完成",
    blocked: "受阻",
    cancelled: "已取消",
    completed_with_errors: "完成，有警告",
    failed: "失败",
    pending: "等待中",
    ready: "待审阅",
    applying: "应用中",
    applied: "已应用",
    rejected: "已拒绝",
    conflicted: "存在冲突",
  };
  return labels[value] || value || "未知";
}

function humanizeApprovalReason(value) {
  if (value === "one or more planned tools require human approval before execution") {
    return "以下操作可能产生变更，需要你确认后才能执行。";
  }
  return value || "以下操作需要在执行前获得你的确认。";
}

function humanizePermissionLevel(value) {
  const labels = {
    read_only: "只读",
    write_safe: "沙箱内可写",
    external_side_effect: "外部副作用",
  };
  return labels[value] || value || "需确认";
}

function statusClass(value) {
  if (value === "completed_with_errors") {
    return "warning";
  }
  if (value === "ready") return "pending";
  if (value === "applying") return "running";
  if (value === "applied") return "completed";
  if (value === "rejected") return "cancelled";
  if (value === "conflicted") return "warning";
  if (["completed", "partial", "blocked", "cancelled", "failed", "waiting_approval", "waiting_input", "paused", "running", "queued"].includes(value)) {
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
        inspectorHidden: document.body.classList.contains("inspector-hidden"),
        rerankEnabled: state.rerankEnabled,
        knowledgeBaseId: $("kb-id-input")?.value || "",
        knowledgeTab: state.activeKnowledgeTab,
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
  if (viewName === "memory") {
    refreshProjectMemory();
  }
  if (viewName === "models") {
    loadModelRegistry().catch((error) => showToast(humanizeError(error), "error"));
  }
}

function setInspectorVisible(visible) {
  const panel = $("inspector-panel");
  document.body.classList.toggle("inspector-hidden", !visible);
  $("toggle-inspector-btn").setAttribute("aria-expanded", String(visible));
  $("toggle-inspector-btn").setAttribute(
    "aria-label",
    visible ? "隐藏会话与运行详情" : "显示会话与运行详情",
  );
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
  const defaultToggle = $("workspace-default-toggle");
  defaultToggle.checked = !state.currentSession;
  defaultToggle.disabled = !state.currentSession;
  $("workspace-default-help").textContent = state.currentSession
    ? "不勾选时，只切换当前会话。"
    : "当前没有会话，所选文件夹将成为新会话的默认工作区。";
  renderWorkspaceManager();
  if (!dialog.open) {
    dialog.showModal();
  }
  listWorkspaces().catch((error) => showToast(humanizeError(error), "error"));
}

function closeSettings() {
  const dialog = $("settings-dialog");
  if (dialog.open) {
    dialog.close();
  }
}

function closeWorkspacePicker() {
  const dialog = $("workspace-picker-dialog");
  if (dialog.open) {
    dialog.close();
  }
  state.workspaceRelinkId = null;
}

function updateContextSummary() {
  const workspace = currentWorkspace();
  const workspaceId = workspace?.id || "";
  const workspaceLabel = workspace ? workspaceName(workspace) : "未选择";
  const workspaceReady = workspaceIsReady(workspace);
  const workspaceRoot = workspace?.root_path || "Agent 运行前必须选择一个可用工作区";
  const roleLabel = workspaceRoleLabel(workspace?.role);
  const modelLabel = currentModelSelectionLabel();

  $("context-model").textContent = modelLabel;
  $("context-workspace").textContent = workspaceLabel;
  $("composer-context").textContent = modelLabel;
  $("agent-workspace-badge").textContent = workspaceReady
    ? `${workspaceLabel} · 可用`
    : workspace
      ? `${workspaceLabel} · 不可用`
      : "未选择工作区";
  $("agent-workspace-context-id").textContent = workspaceLabel;
  $("agent-workspace-root").textContent = workspaceRoot;
  $("agent-workspace-role").textContent = workspace
    ? `${roleLabel} · ${workspaceReady ? "可运行" : "路径不可用"}`
    : "未连接";
  $("agent-workspace-context").className = `agent-workspace-context ${
    workspaceReady ? "is-ready" : workspace ? "is-unavailable" : "is-missing"
  }`;
  $("header-session-id").textContent = state.currentSession?.title
    || state.conversationId
    || "尚未创建";
}

function currentWorkspace() {
  return state.workspaces.find((item) => item.id === state.activeWorkspaceId) || null;
}

function workspaceIsReady(workspace) {
  return Boolean(
    workspace
    && workspace.status !== "unavailable"
    && workspace.available !== false,
  );
}

function workspaceRoleLabel(role) {
  return {
    admin: "管理员",
    editor: "可编辑",
    viewer: "只读",
  }[role] || "本地管理";
}

function setActiveWorkspace(workspaceId) {
  const normalizedId = workspaceId || "";
  state.activeWorkspaceId = normalizedId && (
    !state.workspacesLoaded
    || state.workspaces.some((item) => item.id === normalizedId)
  ) ? normalizedId : "";
  $("workspace-id-input").value = state.activeWorkspaceId;
  $("workspace-root-input").value = currentWorkspace()?.root_path || "";
  updateContextSummary();
  updateComposerAvailability();
  renderWorkspaceManager();
  renderWorkspaceCatalog();
}

function applyConfigurationToInputs(source, defaults = false) {
  const value = (name) => source?.[defaults ? `default_${name}` : name] ?? "";
  $("provider-input").value = value("provider");
  $("model-input").value = value("model");
  $("thinking-level-input").value = value("thinking_level");
  setActiveWorkspace(value("workspace_id"));
  updateComposerMode(value("composer_mode") || "chat");
  renderWorkspaceManager();
  updateContextSummary();
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
  updateComposerAvailability();
}

async function persistComposerMode(mode) {
  updateComposerMode(mode);
  try {
    if (state.currentSession) {
      state.currentSession = await fetchJson(
        `/sessions/${encodeURIComponent(state.currentSession.id)}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            configuration: { composer_mode: state.composerMode },
            save_configuration_as_default: true,
          }),
        },
      );
      replaceSessionInLists(state.currentSession);
    } else {
      state.preferences = await fetchJson("/users/me/preferences", {
        method: "PATCH",
        body: JSON.stringify({ default_composer_mode: state.composerMode }),
      });
    }
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

function updateComposerAvailability() {
  const archived = Boolean(state.currentSession?.archived_at);
  const streaming = Boolean(state.chatController);
  const agentNeedsWorkspace = state.composerMode === "agent"
    && !workspaceIsReady(currentWorkspace());
  $("archived-session-notice").hidden = !archived;
  $("chat-message-input").disabled = archived;
  $("composer-mode-input").disabled = archived || streaming;
  $("send-chat-btn").disabled = archived || streaming || agentNeedsWorkspace;
  $("run-agent-btn").disabled = archived || !workspaceIsReady(currentWorkspace());
  if (archived) {
    setChatStatus("已归档 · 恢复后可继续", "warning");
  }
  renderSessionModelControls();
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
  headers["X-User-ID"] = $("user-id-input")?.value.trim() || "demo_user";
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
      const error = new Error(`${response.status} ${parseErrorDetail(body, response.statusText)}`);
      error.status = response.status;
      error.body = body;
      throw error;
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
    state.sessionStorageMode = body.session_storage || "unknown";
    const persistent = body.persistent_sessions !== false;
    pill.className = `status-pill ${persistent ? "ok" : "warning"}`;
    pill.innerHTML = `<span class="status-dot" aria-hidden="true"></span><span>${
      persistent ? "服务正常" : "临时模式 · 重启会丢失"
    }</span>`;
    setRaw(body);
  } catch (error) {
    state.healthStatus = "offline";
    pill.className = "status-pill error";
    pill.innerHTML = '<span class="status-dot" aria-hidden="true"></span><span>连接失败</span>';
  } finally {
    renderOverview();
  }
}

const MODEL_PROVIDERS = [
  ["openai", "OpenAI"],
  ["deepseek", "DeepSeek"],
  ["anthropic", "Anthropic"],
  ["google", "Google"],
];

const ROUTING_POLICY_LABELS = {
  smart: "智能 · 按任务难度",
  quality: "质量优先",
  cost: "成本优先",
  latency: "速度优先",
};

function modelStatusLabel(status) {
  return {
    available: "可用",
    degraded: "不稳定",
    unavailable: "不可用",
    disabled: "已停用",
    unknown: "待观测",
  }[status] || status || "待观测";
}

function modelStatusClass(status) {
  if (status === "available") return "ok";
  if (status === "degraded" || status === "unknown") return "warning";
  if (status === "unavailable") return "error";
  return "neutral";
}

function registeredModel(modelId) {
  return state.modelRegistry.models.find((item) => item.id === modelId) || null;
}

function modelLatency(model) {
  const routing = model.routing_metadata || {};
  const value = routing.routing_latency_ms;
  if (value != null && Number.isFinite(Number(value))) {
    return {
      milliseconds: Math.max(0, Math.round(Number(value))),
      source: routing.latency_source === "observed_p50" ? "实测 P50" : "冷启动先验",
    };
  }
  return { milliseconds: null, source: "暂无数据" };
}

function latencyTier(milliseconds) {
  if (milliseconds == null) return { key: "unknown", label: "待观测", symbol: "⚪" };
  if (milliseconds <= 1000) return { key: "fast", label: "快", symbol: "🟢" };
  if (milliseconds <= 3000) return { key: "moderate", label: "一般", symbol: "🟡" };
  return { key: "slow", label: "慢", symbol: "🔴" };
}

function formatLatencyMs(milliseconds) {
  return milliseconds == null
    ? "— ms"
    : `${new Intl.NumberFormat("zh-CN").format(milliseconds)} ms`;
}

function modelsByLatency(models) {
  return [...models].sort((left, right) => {
    if (left.enabled !== right.enabled) return left.enabled ? -1 : 1;
    const leftLatency = modelLatency(left).milliseconds ?? Number.POSITIVE_INFINITY;
    const rightLatency = modelLatency(right).milliseconds ?? Number.POSITIVE_INFINITY;
    return leftLatency - rightLatency
      || left.display_name.localeCompare(right.display_name, "zh-CN");
  });
}

function currentModelSelectionLabel() {
  const preference = state.modelPreference;
  if (preference.mode === "manual") {
    const model = registeredModel(preference.preferred_model_id);
    if (!model) return "手动模型未配置";
    const latency = modelLatency(model);
    return `${model.display_name} · ${formatLatencyMs(latency.milliseconds)} · ${modelStatusLabel(model.status)}`;
  }
  return `自动 · ${ROUTING_POLICY_LABELS[preference.routing_policy] || preference.routing_policy}`;
}

function modelOptionLabel(model) {
  const latency = modelLatency(model);
  const tier = latencyTier(latency.milliseconds);
  return `${tier.symbol} ${model.display_name} · ${model.provider} · ${formatLatencyMs(latency.milliseconds)} · ${latency.source}`;
}

function modelPickerOptionMarkup(model, selectedId) {
  const latency = modelLatency(model);
  const tier = latencyTier(latency.milliseconds);
  const selected = model.id === selectedId;
  return `
    <button
      class="model-picker-option${selected ? " selected" : ""}"
      type="button"
      role="option"
      aria-selected="${selected}"
      aria-disabled="${!model.enabled}"
      data-model-option="${escapeHtml(model.id)}"
      ${model.enabled ? "" : "disabled"}
    >
      <span class="latency-dot ${tier.key}" aria-label="延迟${escapeHtml(tier.label)}"></span>
      <span class="model-picker-copy">
        <strong>${escapeHtml(model.display_name)}</strong>
        <small>${escapeHtml(model.provider)} · ${escapeHtml(model.model)}</small>
      </span>
      <span class="model-picker-metric">
        <strong>${escapeHtml(formatLatencyMs(latency.milliseconds))}</strong>
        <small>${escapeHtml(latency.source)} · ${escapeHtml(modelStatusLabel(model.status))}</small>
      </span>
      <span class="model-picker-check" aria-hidden="true">✓</span>
    </button>`;
}

function renderManualModelPicker(models, selectedId, disabled) {
  const trigger = $("model-picker-trigger");
  const menu = $("model-picker-menu");
  const selected = registeredModel(selectedId);
  trigger.hidden = false;
  trigger.disabled = disabled || !models.length;
  trigger.setAttribute("aria-expanded", "false");
  menu.hidden = true;
  $("model-picker-options").innerHTML = models.length
    ? models.map((model) => modelPickerOptionMarkup(model, selectedId)).join("")
    : '<div class="model-picker-empty">请先在模型管理中注册并启用模型</div>';

  if (!selected) {
    $("model-picker-trigger-content").innerHTML = `
      <span class="latency-dot unknown"></span>
      <span class="model-picker-copy"><strong>选择首选模型</strong><small>暂无可用模型</small></span>`;
    return;
  }
  const latency = modelLatency(selected);
  const tier = latencyTier(latency.milliseconds);
  $("model-picker-trigger-content").innerHTML = `
    <span class="latency-dot ${tier.key}"></span>
    <span class="model-picker-copy">
      <strong>${escapeHtml(selected.display_name)}</strong>
      <small>${escapeHtml(selected.provider)} · ${escapeHtml(modelStatusLabel(selected.status))}</small>
    </span>
    <span class="model-picker-metric">
      <strong>${escapeHtml(formatLatencyMs(latency.milliseconds))}</strong>
      <small>${escapeHtml(latency.source)}</small>
    </span>`;
}

function closeModelPicker({ restoreFocus = false } = {}) {
  const trigger = $("model-picker-trigger");
  $("model-picker-menu").hidden = true;
  trigger.setAttribute("aria-expanded", "false");
  if (restoreFocus) trigger.focus();
}

function toggleModelPicker() {
  const trigger = $("model-picker-trigger");
  if (trigger.disabled) return;
  const menu = $("model-picker-menu");
  const opening = menu.hidden;
  menu.hidden = !opening;
  trigger.setAttribute("aria-expanded", String(opening));
  if (opening) {
    menu.querySelector('[aria-selected="true"]')?.focus();
  }
}

function renderSessionModelControls() {
  const preference = state.modelPreference;
  const automatic = preference.mode !== "manual";
  const select = $("session-model-select");
  const disabled = !state.conversationId || Boolean(state.currentSession?.archived_at);
  const choiceControl = select.closest(".model-choice-control");
  choiceControl.classList.toggle("manual", !automatic);
  choiceControl.closest(".composer-mode-bar")?.classList.toggle(
    "manual-model-mode",
    !automatic,
  );
  $("auto-model-toggle").checked = automatic;
  $("session-model-label").textContent = automatic ? "路由策略" : "首选模型";
  $("model-fallback-control").hidden = automatic;
  $("model-fallback-toggle").checked = preference.fallback_enabled !== false;
  if (automatic) {
    closeModelPicker();
    $("model-picker-trigger").hidden = true;
    select.hidden = false;
    select.innerHTML = (state.modelRegistry.routing_policies || Object.keys(ROUTING_POLICY_LABELS))
      .map((policy) => `<option value="${escapeHtml(policy)}">${escapeHtml(ROUTING_POLICY_LABELS[policy] || policy)}</option>`)
      .join("");
    select.value = preference.routing_policy || "smart";
    $("provider-input").value = "";
    $("model-input").value = "";
  } else {
    const models = modelsByLatency(state.modelRegistry.models);
    const selectableModels = models.filter((item) => item.enabled);
    select.hidden = true;
    select.innerHTML = models.length
      ? models.map((model) => `<option value="${escapeHtml(model.id)}" ${model.enabled ? "" : "disabled"}>${escapeHtml(modelOptionLabel(model))}</option>`).join("")
      : '<option value="">请先在模型管理中注册模型</option>';
    const selectedId = preference.preferred_model_id || selectableModels[0]?.id || "";
    select.value = selectedId;
    if (selectedId && !preference.preferred_model_id) {
      preference.preferred_model_id = selectedId;
    }
    const selected = registeredModel(selectedId);
    $("provider-input").value = selected?.provider || "";
    $("model-input").value = selected?.model || "";
    renderManualModelPicker(models, selectedId, disabled);
  }
  $("auto-model-toggle").disabled = disabled;
  select.disabled = disabled;
  $("model-fallback-toggle").disabled = disabled;
  updateContextSummary();
}

async function loadModelRegistry(showRaw = false) {
  const body = await fetchJson("/model-registry");
  state.modelRegistry = body;
  renderProviderConnections();
  renderRegisteredModels();
  renderSessionModelControls();
  if (showRaw) setRaw(body);
  return body;
}

async function loadModelPreference(sessionId = state.conversationId) {
  if (!sessionId) {
    renderSessionModelControls();
    return state.modelPreference;
  }
  const preference = await fetchJson(
    `/sessions/${encodeURIComponent(sessionId)}/model-preference`,
  );
  if (
    preference.mode === "auto"
    && state.currentSession?.provider
    && state.currentSession?.model
  ) {
    const legacy = state.modelRegistry.models.find(
      (item) => item.provider === state.currentSession.provider
        && item.model === state.currentSession.model,
    );
    if (legacy) {
      preference.mode = "manual";
      preference.preferred_model_id = legacy.id;
      preference.fallback_enabled = true;
    }
  }
  state.modelPreference = preference;
  renderSessionModelControls();
  return preference;
}

async function saveModelPreference() {
  const conversationId = await ensureSession();
  const automatic = $("auto-model-toggle").checked;
  const payload = automatic
    ? {
        mode: "auto",
        routing_policy: $("session-model-select").value || "smart",
        preferred_model_id: null,
        fallback_enabled: true,
      }
    : {
        mode: "manual",
        routing_policy: state.modelPreference.routing_policy || "smart",
        preferred_model_id: $("session-model-select").value || null,
        fallback_enabled: $("model-fallback-toggle").checked,
      };
  if (!automatic && !payload.preferred_model_id) {
    showToast("请先在模型管理中注册并启用一个模型", "warning");
    throw new Error("manual mode requires a registered model");
  }
  state.modelPreference = await fetchJson(
    `/sessions/${encodeURIComponent(conversationId)}/model-preference`,
    { method: "PUT", body: JSON.stringify(payload) },
  );
  const selected = registeredModel(state.modelPreference.preferred_model_id);
  if (state.currentSession) {
    state.currentSession.provider = selected?.provider || null;
    state.currentSession.model = selected?.model || null;
  }
  renderSessionModelControls();
  showToast(automatic ? "已启用自动选模" : `首选模型已切换为 ${selected?.display_name || "手动模型"}`);
}

function renderProviderConnections() {
  const grid = $("provider-connection-grid");
  grid.innerHTML = MODEL_PROVIDERS.map(([provider, displayName]) => {
    const connection = state.modelRegistry.connections.find((item) => item.provider === provider);
    const status = connection?.status || "unavailable";
    const configured = connection?.credential_configured === true;
    return `
      <article class="provider-card" data-provider="${provider}">
        <div class="provider-card-heading">
          <div><strong>${displayName}</strong><small>${provider}</small></div>
          <span class="status-pill ${modelStatusClass(status)}"><span class="status-dot"></span>${escapeHtml(modelStatusLabel(status))}</span>
        </div>
        <label>API Key
          <input data-provider-key type="password" autocomplete="new-password" placeholder="${configured ? "已安全保存；输入新值可替换" : "输入 API Key"}" />
        </label>
        <label class="check-field"><input data-provider-enabled type="checkbox" ${connection?.enabled !== false ? "checked" : ""} /> 启用连接</label>
        <div class="button-row">
          <button class="button secondary" type="button" data-provider-action="save">${configured ? "更新配置" : "保存配置"}</button>
          <button class="button ghost" type="button" data-provider-action="test" ${connection ? "" : "disabled"}>测试连接</button>
        </div>
        <p>${configured ? "凭证已配置" : "尚未配置凭证"} · ${connection?.model_count || 0} 个模型</p>
      </article>`;
  }).join("");
}

async function handleProviderAction(button) {
  const card = button.closest("[data-provider]");
  const provider = card.dataset.provider;
  const action = button.dataset.providerAction;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    if (action === "save") {
      const apiKey = card.querySelector("[data-provider-key]").value.trim();
      const displayName = MODEL_PROVIDERS.find(([id]) => id === provider)?.[1] || provider;
      await fetchJson(`/model-registry/connections/${encodeURIComponent(provider)}`, {
        method: "PUT",
        body: JSON.stringify({
          display_name: displayName,
          ...(apiKey ? { api_key: apiKey } : {}),
          enabled: card.querySelector("[data-provider-enabled]").checked,
        }),
      });
      showToast(`${displayName} 配置已保存`);
    } else {
      const result = await fetchJson(
        `/model-registry/connections/${encodeURIComponent(provider)}/test`,
        { method: "POST" },
      );
      showToast(`${provider} 连接成功 · ${formatDuration(result.elapsed_ms)}`);
    }
    await loadModelRegistry();
    if (action === "save") {
      $("registered-model-provider-input").value = provider;
      await discoverProviderModels(provider, { showToast: false });
    }
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

function modelTierLabel(value) {
  return {
    advanced: "高质量",
    balanced: "均衡",
    efficient: "高效",
    high: "高成本",
    standard: "标准成本",
    low: "低成本",
  }[value] || value || "后端默认";
}

function selectedDiscoveredModel() {
  const modelId = $("discovered-model-select").value;
  return state.modelDiscovery.models.find((item) => item.model === modelId) || null;
}

function renderDiscoveredModels() {
  const select = $("discovered-model-select");
  const summary = $("discovered-model-summary");
  const models = state.modelDiscovery.models || [];
  const selectable = models.filter((item) => !item.already_registered);
  select.disabled = state.modelDiscovery.loading || !selectable.length;
  select.innerHTML = state.modelDiscovery.loading
    ? '<option value="">正在发现模型…</option>'
    : models.length
      ? models.map((item) => `<option value="${escapeHtml(item.model)}" ${item.already_registered ? "disabled" : ""}>${escapeHtml(item.display_name)} · ${escapeHtml(item.model)}${item.already_registered ? " · 已注册" : ""}</option>`).join("")
      : '<option value="">暂无已发现模型，可使用下方手动兜底</option>';
  if (selectable.length) select.value = selectable[0].model;
  const selected = selectedDiscoveredModel();
  if (!selected) {
    summary.textContent = state.modelDiscovery.loading
      ? "正在读取 Provider 当前账号的模型目录…"
      : "后端会自动维护显示名称、能力、上下文和路由参数。";
    return;
  }
  const capabilities = [
    selected.capabilities?.tool_calling ? "工具调用" : null,
    selected.capabilities?.structured_output ? "结构化输出" : null,
  ].filter(Boolean).join("、") || "基础文本生成";
  summary.innerHTML = `<strong>${escapeHtml(selected.display_name)}</strong><span>${Number(selected.context_window_tokens).toLocaleString()} ctx · ${escapeHtml(capabilities)} · ${escapeHtml(modelTierLabel(selected.quality_tier))} · ${escapeHtml(modelTierLabel(selected.cost_tier))}</span><small>Provider 元数据 + 后端路由画像；延迟将在真实请求后自动学习。</small>`;
}

async function discoverProviderModels(
  provider = $("registered-model-provider-input").value,
  { showToast: shouldToast = true } = {},
) {
  state.modelDiscovery = { provider, models: [], loading: true };
  renderDiscoveredModels();
  try {
    const result = await fetchJson(
      `/model-registry/connections/${encodeURIComponent(provider)}/available-models`,
    );
    state.modelDiscovery = { provider, models: result.models || [], loading: false };
    renderDiscoveredModels();
    if (shouldToast) showToast(`已发现 ${result.models?.length || 0} 个可用模型`);
    return result;
  } catch (error) {
    state.modelDiscovery = { provider, models: [], loading: false };
    renderDiscoveredModels();
    throw error;
  }
}

function resetRegisteredModelForm() {
  $("manual-model-id-input").value = "";
  $("registered-model-enabled-input").checked = true;
  $("registered-model-auto-input").checked = true;
}

async function saveRegisteredModel() {
  const provider = $("registered-model-provider-input").value;
  const manualModel = $("manual-model-id-input").value.trim();
  const discovered = selectedDiscoveredModel();
  const model = manualModel || (discovered?.already_registered ? "" : discovered?.model || "");
  if (!model) {
    showToast("请先发现并选择模型，或填写模型 ID", "warning");
    return;
  }
  const payload = {
    provider,
    model,
    enabled: $("registered-model-enabled-input").checked,
    auto_eligible: $("registered-model-auto-input").checked,
  };
  const button = $("save-registered-model-btn");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await fetchJson("/model-registry/models", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    resetRegisteredModelForm();
    await loadModelRegistry();
    await discoverProviderModels(provider, { showToast: false });
    showToast("模型已注册，路由元数据由后端维护");
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

async function updateRegisteredModel(modelId, changes) {
  const model = registeredModel(modelId);
  if (!model) return;
  try {
    await fetchJson(`/model-registry/models/${encodeURIComponent(modelId)}`, {
      method: "PUT",
      body: JSON.stringify({
        enabled: changes.enabled ?? model.enabled,
        auto_eligible: changes.auto_eligible ?? model.auto_eligible,
      }),
    });
    await loadModelRegistry();
    showToast("模型状态已更新");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function deleteRegisteredModel(modelId) {
  const model = registeredModel(modelId);
  if (!model || !window.confirm(`确认删除模型“${model.display_name}”？`)) return;
  try {
    await fetchJson(`/model-registry/models/${encodeURIComponent(modelId)}`, { method: "DELETE" });
    await loadModelRegistry();
    showToast("模型已删除");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

function renderRegisteredModels() {
  const list = $("registered-model-list");
  const models = state.modelRegistry.models || [];
  $("registered-model-count").textContent = String(models.length);
  if (!models.length) {
    list.innerHTML = '<div class="empty-state">保存 Provider 后，在左侧注册第一个模型。</div>';
    return;
  }
  list.innerHTML = models.map((model) => {
    const telemetry = model.telemetry || {};
    const routing = model.routing_metadata || {};
    const latency = routing.latency_source === "observed_p50"
      ? `实测 P50 ${formatDuration(routing.routing_latency_ms)} · P95 ${formatDuration(telemetry.total_latency_p95_ms)}`
      : `冷启动先验 ${formatDuration(routing.routing_latency_ms || model.configured_latency_ms)}`;
    return `
      <article class="registered-model-card" data-model-id="${escapeHtml(model.id)}">
        <div class="registered-model-heading">
          <div><strong>${escapeHtml(model.display_name)}</strong><small>${escapeHtml(model.provider)} · ${escapeHtml(model.model)}</small></div>
          <span class="status-pill ${modelStatusClass(model.status)}"><span class="status-dot"></span>${escapeHtml(modelStatusLabel(model.status))}</span>
        </div>
        <div class="model-stat-row"><span>${escapeHtml(latency)}</span><span>成功 ${telemetry.success_rate == null ? "—" : `${Math.round(telemetry.success_rate * 100)}%`}</span><span>${model.context_window_tokens.toLocaleString()} ctx</span></div>
        <p>${escapeHtml(modelTierLabel(routing.quality_tier))} · ${escapeHtml(modelTierLabel(routing.cost_tier))} · ${model.auto_eligible ? "可自动选择" : "仅手动选择"}${model.enabled ? "" : " · 已停用"}${telemetry.last_error ? ` · ${escapeHtml(truncate(telemetry.last_error, 80))}` : ""}</p>
        <div class="button-row"><button class="button ghost" type="button" data-model-action="toggle-enabled">${model.enabled ? "停用" : "启用"}</button><button class="button ghost" type="button" data-model-action="toggle-auto">${model.auto_eligible ? "仅手动" : "加入自动"}</button><button class="button ghost" type="button" data-model-action="delete">删除</button></div>
      </article>`;
  }).join("");
}

async function ensureSession() {
  if (state.currentSession?.archived_at) {
    throw new Error("已归档会话必须恢复后才能继续");
  }
  if (state.currentSession?.id) {
    return state.currentSession.id;
  }
  const requested = $("conversation-id-input").value.trim();
  if (requested) {
    const session = await loadSession(false, requested);
    return session.id;
  }
  return createSession();
}

function resetChatView() {
  $("chat-output").innerHTML = `
    <div class="welcome-state">
      <div class="welcome-signal" aria-hidden="true">
        <span class="active"><i></i><b>ASK</b></span>
        <span><i></i><b>PLAN</b></span>
        <span><i></i><b>ACT</b></span>
        <span><i></i><b>VERIFY</b></span>
      </div>
      <div class="welcome-content">
        <div class="welcome-mark" aria-hidden="true"><strong>AGENT</strong><span>READY</span></div>
        <h2>从一个具体问题开始</h2>
        <p>先理解上下文，再选择快速回答或代码 Agent；涉及写入时会暂停等待审批。</p>
        <div class="prompt-grid" aria-label="推荐问题">
          <button type="button" class="prompt-card" data-prompt="解释这个项目的核心架构和请求调用链"><span>理解项目</span><strong>解释核心架构和请求调用链</strong></button>
          <button type="button" class="prompt-card" data-prompt="帮我分析 SSE 流式输出的实现与异常处理"><span>分析实现</span><strong>检查 SSE 流式输出</strong></button>
          <button type="button" class="prompt-card" data-prompt="为这个项目设计一套可靠的测试策略"><span>规划质量</span><strong>设计可靠的测试策略</strong></button>
        </div>
      </div>
    </div>
  `;
  setChatStatus("等待输入");
}

function resetLatestAgentRunState() {
  state.agentPollGeneration += 1;
  state.latestRunId = "";
  state.latestRunStatus = "";
  state.latestRunConversationId = "";
  const statusNode = $("agent-status");
  statusNode.className = "status-pill neutral";
  statusNode.innerHTML = '<span class="status-dot" aria-hidden="true"></span><span>尚未运行</span>';
  $("approval-card").classList.add("hidden");
  $("agent-control-bar").classList.add("hidden");
  $("agent-answer").className = "rich-output empty-output";
  $("agent-answer").textContent = "运行 Agent 后，结果会显示在这里。";
  $("agent-events").innerHTML = '<div class="empty-state">暂无运行事件</div>';
  renderAgentMetrics(null);
  renderArtifacts([]);
  resetChangeSetCard();
}

function normalizedMessageText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function runAnswerAlreadyPersisted(body, messages) {
  const answer = normalizedMessageText(body?.result?.answer);
  return Boolean(answer) && messages.some(
    (message) => message.role === "assistant"
      && normalizedMessageText(message.content) === answer,
  );
}

async function restoreLatestAgentRun(conversationId, messages = []) {
  let body;
  try {
    body = await fetchJson(
      `/sessions/${encodeURIComponent(conversationId)}/agent/runs/latest`,
    );
  } catch (error) {
    if (error.status === 404) return null;
    throw error;
  }
  if (conversationId !== state.conversationId) return null;

  renderAgentRun(body, { scrollApproval: false });
  setChatStatusFromRun(body);
  const runId = agentRunId(body);
  const status = agentRunStatus(body);
  const answerPersisted = runAnswerAlreadyPersisted(body, messages);
  const shouldRestoreMessage = !answerPersisted;
  if (!shouldRestoreMessage) return body;

  let contentNode = chatContentForRun(runId);
  if (!contentNode) {
    contentNode = appendChatMessage("assistant", "", null, { runId });
    const timestamp = contentNode.closest(".chat-bubble")?.querySelector(".message-label span");
    if (timestamp) timestamp.textContent = "已恢复运行";
  }
  const startedAt = performance.now() - (body?.result?.metrics?.elapsed_ms || 0);
  renderAgentChatResponse(contentNode, body, startedAt);
  if (!["queued", "running"].includes(status)) return body;

  const presenter = createAgentProgressPresenter(contentNode, startedAt, {
    initialTrace: agentRunTrace(body),
  });
  startResponseTimer(contentNode, startedAt);
  watchRunUntilTerminal({
    runId,
    conversationId,
    preserveChat: true,
    onProgress: (latestBody) => presenter.update(latestBody),
  }).then(async (finalBody) => {
    if (finalBody && conversationId === state.conversationId) {
      await presenter.update(finalBody);
      setChatStatusFromRun(finalBody);
    }
    stopResponseTimer(contentNode);
  }).catch((error) => {
    stopResponseTimer(contentNode);
    showToast(`Agent 运行恢复失败：${humanizeError(error)}`, "error");
  });
  return body;
}

async function createSession() {
  $("session-status").textContent = "正在创建会话…";
  try {
    const body = await fetchJson("/sessions", {
      method: "POST",
      body: JSON.stringify({ user_id: $("user-id-input").value.trim() || "demo_user" }),
    });
    state.currentSession = body;
    state.conversationId = body.id;
    resetLatestAgentRunState();
    $("conversation-id-input").value = body.id;
    $("session-status").textContent = "会话已就绪";
    resetChatView();
    applyConfigurationToInputs(body);
    await loadModelPreference(body.id);
    updateSessionUrl(body.id);
    updateComposerAvailability();
    updateContextSummary();
    setRaw(body);
    await Promise.allSettled([
      listSessions(false, { archived: false, query: "" }),
      loadPreferences(),
    ]);
    switchView("chat");
    showToast("新会话已创建");
    return body.id;
  } catch (error) {
    $("session-status").textContent = "创建会话失败";
    showToast(humanizeError(error), "error");
    throw error;
  }
}

async function loadPreferences() {
  state.preferences = await fetchJson("/users/me/preferences");
  state.defaultWorkspaceId = state.preferences.default_workspace_id || "";
  if (!state.currentSession) {
    applyConfigurationToInputs(state.preferences, true);
  }
  return state.preferences;
}

async function listSessions(showRaw = true, options = {}) {
  const append = options.append === true;
  const archived = options.archived ?? state.sessionsArchived;
  const query = options.query ?? state.sessionsQuery;
  const params = new URLSearchParams({
    archived: String(archived),
    limit: "30",
  });
  if (query) {
    params.set("q", query);
  }
  if (append && state.sessionsNextCursor) {
    params.set("cursor", state.sessionsNextCursor);
  }
  const body = await fetchJson(`/sessions?${params}`);
  state.sessionsArchived = archived;
  state.sessionsQuery = query;
  state.sessions = append
    ? [...state.sessions, ...(body.sessions || [])]
    : body.sessions || [];
  state.sessionsNextCursor = body.next_cursor || null;
  if (!archived && !query && !append) {
    state.recentSessions = state.sessions.slice(0, 12);
  }
  $("session-search-input").value = query;
  $("session-state-filter").value = archived ? "archived" : "active";
  renderSessions();
  renderRecentSessions();
  renderOverview();
  if (showRaw) {
    setRaw(body);
  }
  return body;
}

async function refreshRecentSessions() {
  const body = await fetchJson("/sessions?archived=false&limit=12");
  state.recentSessions = body.sessions || [];
  renderRecentSessions();
  return body;
}

async function loadSessionTokenUsage(sessionIds = state.sessions.map((item) => item.id)) {
  const entries = await Promise.all(
    sessionIds.map(async (sessionId) => [
      sessionId,
      await fetchJson(`/sessions/${encodeURIComponent(sessionId)}/token-usage`),
    ]),
  );
  for (const [sessionId, usage] of entries) {
    state.sessionTokenUsage[sessionId] = usage;
  }
  return entries;
}

function renderSessions() {
  const list = $("sessions-list");
  $("sessions-count").textContent = String(state.sessions.length);
  $("load-more-sessions-btn").hidden = !state.sessionsNextCursor;
  list.innerHTML = "";
  if (state.sessions.length === 0) {
    list.innerHTML = '<div class="empty-state">暂无会话，创建一个会话开始工作。</div>';
    return;
  }
  for (const session of state.sessions) {
    const item = document.createElement("article");
    item.className = `session-item ${session.id === state.conversationId ? "active" : ""}`;
    item.dataset.sessionId = session.id;
    item.innerHTML = `
      <button class="session-open" type="button" data-session-action="open">
        <strong>${escapeHtml(session.title || "新会话")}</strong>
        <span>${escapeHtml(formatDate(session.updated_at))} · ${escapeHtml(session.message_count)} 条消息</span>
        <span class="session-message-preview">${escapeHtml(session.last_message_preview || "暂无消息")}</span>
      </button>
      <div class="session-actions">
        <button class="text-button" type="button" data-session-action="rename">重命名</button>
        <button class="text-button" type="button" data-session-action="${session.archived_at ? "restore" : "archive"}">${session.archived_at ? "恢复" : "归档"}</button>
      </div>
    `;
    list.appendChild(item);
  }
}

function recentSessionGroup(value) {
  const updated = new Date(value);
  const today = new Date();
  const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const startUpdated = new Date(updated.getFullYear(), updated.getMonth(), updated.getDate());
  const days = Math.floor((startToday - startUpdated) / 86400000);
  if (days <= 0) {
    return "今天";
  }
  return days < 7 ? "过去 7 天" : "更早";
}

function renderRecentSessions() {
  const list = $("recent-sessions-list");
  list.innerHTML = "";
  if (!state.recentSessions.length) {
    list.innerHTML = '<div class="empty-state compact">暂无会话</div>';
    return;
  }
  const groups = new Map();
  for (const session of state.recentSessions) {
    const label = recentSessionGroup(session.updated_at);
    groups.set(label, [...(groups.get(label) || []), session]);
  }
  for (const [label, sessions] of groups) {
    const section = document.createElement("section");
    section.className = "recent-session-group";
    section.innerHTML = `<h3>${escapeHtml(label)}</h3>`;
    for (const session of sessions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `recent-session-item ${session.id === state.conversationId ? "active" : ""}`;
      button.dataset.sessionId = session.id;
      button.innerHTML = `
        <strong>${escapeHtml(session.title || "新会话")}</strong>
        <small>${escapeHtml(formatDate(session.updated_at))}</small>
      `;
      section.appendChild(button);
    }
    list.appendChild(section);
  }
}

function updateSessionUrl(sessionId) {
  const url = new URL(window.location.href);
  if (sessionId) {
    url.searchParams.set("session", sessionId);
  } else {
    url.searchParams.delete("session");
  }
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function replaceSessionInLists(session) {
  state.sessions = state.sessions.map((item) => item.id === session.id ? session : item);
  state.recentSessions = state.recentSessions.map((item) => item.id === session.id ? session : item);
  renderSessions();
  renderRecentSessions();
}

function canSwitchSession() {
  if (!state.chatController) {
    return true;
  }
  showToast("请先完成或停止当前流式回答，再切换会话", "warning");
  return false;
}

async function loadSession(showRaw = true, requestedSessionId = null, options = {}) {
  if (!canSwitchSession()) {
    return null;
  }
  const conversationId = requestedSessionId
    || $("conversation-id-input").value.trim()
    || state.conversationId;
  if (!conversationId) {
    throw new Error("请输入要加载的会话 ID");
  }
  const [session, summary, messages, usage] = await Promise.all([
    fetchJson(`/sessions/${encodeURIComponent(conversationId)}`),
    fetchJson(`/sessions/${encodeURIComponent(conversationId)}/summary`),
    fetchJson(`/sessions/${encodeURIComponent(conversationId)}/messages`),
    fetchJson(`/sessions/${encodeURIComponent(conversationId)}/token-usage`),
  ]);
  if (options.requireActive && (session.archived_at || session.message_count === 0)) {
    throw new Error(session.archived_at ? "会话已归档" : "空会话不参与启动恢复");
  }
  if (!session.archived_at && session.message_count > 0) {
    state.preferences = await fetchJson("/users/me/preferences", {
      method: "PATCH",
      body: JSON.stringify({ last_active_session_id: session.id }),
    });
  }
  resetLatestAgentRunState();
  state.currentSession = session;
  state.conversationId = session.id;
  $("conversation-id-input").value = session.id;
  state.sessionTokenUsage[conversationId] = usage;
  $("session-status").textContent = session.archived_at ? "正在查看已归档会话" : "会话已加载";
  renderSessionSummary(summary, usage);
  renderMessages(messages.messages || []);
  renderChatHistory(messages.messages || []);
  applyConfigurationToInputs(session);
  await loadModelPreference(session.id);
  updateSessionUrl(session.id);
  updateComposerAvailability();
  replaceSessionInLists(session);
  renderSessions();
  updateContextSummary();
  if (options.navigate !== false) {
    switchView("chat");
  }
  try {
    await restoreLatestAgentRun(session.id, messages.messages || []);
  } catch (error) {
    showToast(`Agent 状态恢复失败：${humanizeError(error)}`, "warning");
  }
  if (showRaw) {
    setRaw({ session, summary, messages, usage });
  }
  return session;
}

async function updateSessionMetadata(sessionId, payload) {
  const session = await fetchJson(`/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  if (state.currentSession?.id === session.id) {
    state.currentSession = session;
    applyConfigurationToInputs(session);
    updateComposerAvailability();
  }
  replaceSessionInLists(session);
  await Promise.allSettled([
    listSessions(false),
    refreshRecentSessions(),
  ]);
  return session;
}

async function refreshCurrentSessionMetadata() {
  if (!state.currentSession?.id) {
    return null;
  }
  const session = await fetchJson(
    `/sessions/${encodeURIComponent(state.currentSession.id)}`,
  );
  state.currentSession = session;
  replaceSessionInLists(session);
  updateContextSummary();
  return session;
}

async function handleSessionAction(sessionId, action) {
  if (action === "open") {
    await loadSession(true, sessionId);
    return;
  }
  if (action === "rename") {
    const existing = [...state.sessions, ...state.recentSessions]
      .find((item) => item.id === sessionId);
    const title = window.prompt("输入新的会话标题", existing?.title || "");
    if (title === null || !title.trim()) {
      return;
    }
    await updateSessionMetadata(sessionId, { title: title.trim() });
    showToast("会话已重命名");
    return;
  }
  const archived = action === "archive";
  await updateSessionMetadata(sessionId, { archived });
  showToast(archived ? "会话已归档" : "会话已恢复");
}

async function loadSessionSummary() {
  try {
    const conversationId = await ensureSession();
    const [summary, usage] = await Promise.all([
      fetchJson(`/sessions/${encodeURIComponent(conversationId)}/summary`),
      fetchJson(`/sessions/${encodeURIComponent(conversationId)}/token-usage`),
    ]);
    state.sessionTokenUsage[conversationId] = usage;
    renderSessionSummary(summary, usage);
    renderSessions();
    setRaw({ summary, usage });
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function refreshMessages(showRaw = true, renderChat = true) {
  const conversationId = await ensureSession();
  const body = await fetchJson(`/sessions/${encodeURIComponent(conversationId)}/messages`);
  renderMessages(body.messages || []);
  if (renderChat) {
    renderChatHistory(body.messages || []);
  }
  if (showRaw) {
    setRaw(body);
  }
  return body;
}

function renderSessionSummary(
  summary,
  usage = state.sessionTokenUsage[summary.session_id],
) {
  const workspaces = usage?.workspaces || [];
  const operations = usage?.operations || [];
  const sessionBudget = usage?.budget?.session;
  const latestPromptRecord = [...(usage?.records || [])]
    .reverse()
    .find((record) => record.operation !== "embedding");
  $("session-summary").innerHTML = `
    <div class="summary-strip">
      <strong>${escapeHtml(summary.session_id)}</strong>
      <span>${escapeHtml(summary.message_count)} 条消息</span>
    </div>
    ${
      usage
        ? `
          <div class="token-metric-grid" aria-label="会话 Token 用量">
            <div><span>累计总量</span><strong>${escapeHtml(formatTokenCount(usage.total_tokens))}</strong></div>
            <div><span>输入</span><strong>${escapeHtml(formatTokenCount(usage.input_tokens))}</strong></div>
            <div><span>输出</span><strong>${escapeHtml(formatTokenCount(usage.output_tokens))}</strong></div>
            <div><span>思考</span><strong>${escapeHtml(formatTokenCount(usage.thoughts_tokens))}</strong></div>
          </div>
          <div class="context-token-card">
            <div>
              <span>当前会话上下文 · 估算</span>
              <strong>≈ ${escapeHtml(formatTokenCount(usage.context?.estimated_tokens || 0))} tokens</strong>
            </div>
            <small>${escapeHtml(usage.context?.message_count || 0)} 条注入消息${
              usage.context?.includes_summary ? " · 包含滚动摘要" : ""
            } · 不含下一条用户输入、系统提示和工作区检索内容</small>
            <small>${
              latestPromptRecord
                ? `最近最终 Prompt <strong>${escapeHtml(formatTokenCount(latestPromptRecord.input_tokens))}</strong> tokens · ${escapeHtml(formatInputCountMethod(latestPromptRecord.input_count_method))}`
                : "尚无已发送的最终 Prompt"
            }</small>
          </div>
          <div class="token-budget-card ${sessionBudget?.exceeded ? "is-exceeded" : ""}">
            <div>
              <span>会话 Token 预算 · ${escapeHtml(usage.budget?.action || "reject")}</span>
              <strong>${
                sessionBudget?.limit
                  ? `${escapeHtml(formatTokenCount(sessionBudget.used))} / ${escapeHtml(formatTokenCount(sessionBudget.limit))}`
                  : "未启用"
              }</strong>
            </div>
            <small>${
              sessionBudget?.remaining == null
                ? "设置 SESSION_TOKEN_BUDGET 后启用"
                : `剩余 ${escapeHtml(formatTokenCount(sessionBudget.remaining))} tokens`
            }</small>
          </div>
          <div class="token-operation-breakdown">
            <span>调用类型</span>
            ${
              operations.length
                ? operations
                    .map(
                      (item) =>
                        `<small><strong>${escapeHtml(formatUsageOperation(item.operation))}</strong>${escapeHtml(formatTokenCount(item.total_tokens))}</small>`,
                    )
                    .join("")
                : "<small>暂无调用记录</small>"
            }
          </div>
          <div class="token-workspace-breakdown">
            <span>Workspace 分布</span>
            ${
              workspaces.length
                ? workspaces
                    .map(
                      (item) =>
                        `<small><strong>${escapeHtml(item.workspace_id || "未归属")}</strong>${escapeHtml(formatTokenCount(item.total_tokens))} tokens</small>`,
                    )
                    .join("")
                : "<small>尚无 Token 用量记录</small>"
            }
          </div>
        `
        : ""
    }
    ${summary.last_message ? `<p class="context-note">最近：${escapeHtml(truncate(summary.last_message, 180))}</p>` : ""}
    ${
      summary.compressed_summary
        ? `
          <div class="citation-card">
            <strong>滚动摘要 · v${escapeHtml(summary.summary_version)}</strong>
            <small>已压缩 ${escapeHtml(summary.summarized_message_count)} 条旧消息 · ${escapeHtml(formatDate(summary.summary_updated_at))}</small>
            <p>${escapeHtml(summary.compressed_summary)}</p>
          </div>
        `
        : '<p class="context-note">会话尚未达到滚动压缩阈值。</p>'
    }
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
    await Promise.allSettled([
      refreshCurrentSessionMetadata(),
      refreshRecentSessions(),
    ]);
    showToast("测试消息已添加");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

function appendChatMessage(role, content = "", createdAt = null, { runId = "" } = {}) {
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
  if (runId) {
    item.dataset.agentRunId = runId;
  }
  output.appendChild(item);
  item.scrollIntoView({ behavior: preferredScrollBehavior(), block: "nearest" });
  return item.querySelector(".message-content");
}

function agentRunId(body) {
  return body?.run_id || body?.result?.run_id || "";
}

function agentRunStatus(body) {
  return body?.status || body?.result?.status || "";
}

function agentRunConversationId(body) {
  return body?.conversation_id || body?.result?.conversation_id || "";
}

function agentRunTrace(body) {
  return body?.trace || body?.result?.trace || [];
}

function chatContentForRun(runId) {
  const message = [...document.querySelectorAll("[data-agent-run-id]")]
    .find((item) => item.dataset.agentRunId === runId);
  return message?.querySelector(".message-content") || null;
}

function setInlineCheckpointBusy(card, busy) {
  card.classList.toggle("is-busy", busy);
  card.setAttribute("aria-busy", String(busy));
  card.querySelectorAll("button, textarea").forEach((control) => {
    control.disabled = busy;
  });
}

function inlineApprovalTools(approval) {
  const calls = approval?.tool_calls
    || (approval?.planned_tools || []).map((name) => ({ name, arguments: {} }));
  const callByName = new Map(calls.map((call) => [call.name, call]));
  const required = approval?.approval_required_tools || [];
  if (required.length) {
    return required.map((risk) => ({
      ...callByName.get(risk.name),
      ...risk,
      arguments: risk.arguments_summary || callByName.get(risk.name)?.arguments || {},
    }));
  }
  return calls;
}

function renderInlineAgentCheckpoint(contentNode, body) {
  const bubble = contentNode.closest(".chat-bubble");
  if (!bubble) return;
  const status = agentRunStatus(body);
  let card = bubble.querySelector(".inline-agent-checkpoint");
  const shouldFocusCheckpoint = !card || card.dataset.status !== status;
  if (!SUSPENDED_RUN_STATUSES.has(status)) {
    if (!card?.dataset.decision) {
      card?.remove();
      return;
    }
    const decision = card.dataset.decision;
    const previousStatus = card.dataset.status;
    const rejected = decision === "reject";
    const resolvedTitle = rejected
      ? "已拒绝执行"
      : previousStatus === "waiting_input"
        ? "已提交补充信息"
        : previousStatus === "paused"
          ? "已继续运行"
          : "已确认执行计划";
    const finished = FINAL_RUN_STATUSES.has(status);
    card.className = `inline-agent-checkpoint resolved ${rejected ? "rejected" : "approved"}`;
    card.removeAttribute("aria-busy");
    card.innerHTML = `
      <div class="inline-checkpoint-resolved-mark" aria-hidden="true">${rejected ? "×" : "✓"}</div>
      <div>
        <strong>${resolvedTitle}</strong>
        <p>${finished ? `Run ${humanizeStatus(status)}` : "Agent 正在按你的决定继续处理。"}</p>
      </div>
    `;
    return;
  }

  if (!card) {
    card = document.createElement("section");
    contentNode.insertAdjacentElement("afterend", card);
  }
  delete card.dataset.decision;
  card.className = `inline-agent-checkpoint ${statusClass(status)}`;
  card.dataset.runId = agentRunId(body);
  card.dataset.status = status;
  card.setAttribute("role", "group");

  const pending = body?.pending_approval || body?.result?.pending_approval || {};
  if (status === "waiting_approval") {
    const tools = inlineApprovalTools(pending);
    card.setAttribute("aria-label", "Agent 执行审批");
    card.innerHTML = `
      <div class="inline-checkpoint-heading">
        <span class="inline-checkpoint-mark" aria-hidden="true">!</span>
        <div>
          <span>执行检查点</span>
          <h3>需要你确认后继续</h3>
        </div>
        <code>${escapeHtml(agentRunId(body))}</code>
      </div>
      <p class="inline-checkpoint-reason">${escapeHtml(humanizeApprovalReason(pending.reason))}</p>
      <div class="inline-checkpoint-tools">
        ${tools.map((tool) => `
          <details class="approval-tool">
            <summary><strong>${escapeHtml(tool.name || "待审批工具")}</strong><span>${escapeHtml(humanizePermissionLevel(tool.permission_level))}</span></summary>
            <p>${escapeHtml(tool.risk_summary || "请确认此操作及参数符合你的预期。")}</p>
            <pre>${escapeHtml(jsonPretty(tool.arguments || {}))}</pre>
          </details>
        `).join("") || '<p class="inline-checkpoint-empty">执行计划需要你的确认。</p>'}
      </div>
      <label class="inline-checkpoint-feedback">
        <span>补充要求 <small>可选</small></span>
        <textarea rows="2" maxlength="4000" placeholder="例如：不要安装新依赖，优先使用原生浏览器 API"></textarea>
      </label>
      <p class="inline-checkpoint-error" role="alert" hidden></p>
      <div class="inline-checkpoint-actions">
        <button class="button danger" type="button" data-inline-agent-action="reject">拒绝执行</button>
        <button class="button primary" type="button" data-inline-agent-action="approve">确认并继续</button>
      </div>
    `;
  } else {
    const needsInput = status === "waiting_input";
    const question = pending.question
      || (needsInput ? "Agent 需要你补充信息后才能继续。" : "Agent 已在安全边界暂停。你可以补充新的方向后继续。")
    card.setAttribute("aria-label", needsInput ? "Agent 等待输入" : "Agent 已暂停");
    card.innerHTML = `
      <div class="inline-checkpoint-heading">
        <span class="inline-checkpoint-mark" aria-hidden="true">${needsInput ? "?" : "Ⅱ"}</span>
        <div>
          <span>${needsInput ? "需要你的输入" : "安全暂停"}</span>
          <h3>${escapeHtml(question)}</h3>
        </div>
        <code>${escapeHtml(agentRunId(body))}</code>
      </div>
      <label class="inline-checkpoint-feedback">
        <span>${needsInput ? "回复 Agent" : "继续时的补充要求"}</span>
        <textarea rows="3" maxlength="4000" placeholder="${escapeHtml(question)}"></textarea>
      </label>
      <p class="inline-checkpoint-error" role="alert" hidden></p>
      <div class="inline-checkpoint-actions">
        <button class="button primary" type="button" data-inline-agent-action="continue">继续运行</button>
      </div>
    `;
  }

  card.querySelectorAll("[data-inline-agent-action]").forEach((button) => {
    button.addEventListener("click", () => {
      handleInlineAgentAction(contentNode, body, button.dataset.inlineAgentAction, card);
    });
  });
  if (shouldFocusCheckpoint) {
    window.requestAnimationFrame(() => {
      card.scrollIntoView({ behavior: preferredScrollBehavior(), block: "center" });
    });
  }
}

function setChatStatusFromRun(body) {
  const status = agentRunStatus(body);
  if (status === "completed") {
    setChatStatus("Agent 已完成", status);
  } else if (status === "waiting_approval") {
    setChatStatus("Agent 等待你的确认", status);
  } else if (status === "waiting_input") {
    setChatStatus("Agent 等待你的输入", status);
  } else if (status === "paused") {
    setChatStatus("Agent 已暂停", status);
  } else if (["queued", "running"].includes(status)) {
    setChatStatus("Agent 运行中", status);
  } else {
    setChatStatus(`Agent ${humanizeStatus(status)}`, statusClass(status));
  }
}

async function handleInlineAgentAction(contentNode, body, action, card) {
  const runId = agentRunId(body);
  const conversationId = agentRunConversationId(body);
  if (!runId || conversationId !== state.conversationId) {
    showToast("这条审批不属于当前会话，请重新打开对应会话", "warning");
    return;
  }
  const feedback = card.querySelector("textarea")?.value.trim() || "";
  const errorNode = card.querySelector(".inline-checkpoint-error");
  setInlineCheckpointBusy(card, true);
  if (errorNode) errorNode.hidden = true;
  try {
    const isApproval = action === "approve" || action === "reject";
    const nextBody = await fetchJson(
      isApproval
        ? `/agent/runs/${encodeURIComponent(runId)}/resume`
        : `/agent/runs/${encodeURIComponent(runId)}/continue`,
      {
        method: "POST",
        body: JSON.stringify(isApproval
          ? {
              approved: action === "approve",
              feedback: feedback || (action === "approve"
                ? "用户已在对话中确认执行计划"
                : "用户已在对话中拒绝执行计划"),
            }
          : { message: feedback }),
      },
    );
    if (
      conversationId !== state.conversationId
      || runId !== state.latestRunId
    ) {
      return;
    }
    card.dataset.decision = action;
    renderAgentRun(nextBody, { scrollApproval: false });
    const startedAt = performance.now() - (body?.result?.metrics?.elapsed_ms || 0);
    const presenter = createAgentProgressPresenter(contentNode, startedAt, {
      initialTrace: agentRunTrace(body),
    });
    startResponseTimer(contentNode, startedAt);
    await presenter.update(nextBody);
    const finalBody = await watchRunUntilTerminal({
      runId,
      conversationId,
      preserveChat: true,
      onProgress: (latestBody) => presenter.update(latestBody),
    });
    if (
      conversationId !== state.conversationId
      || runId !== state.latestRunId
    ) {
      stopResponseTimer(contentNode);
      return;
    }
    if (finalBody) await presenter.update(finalBody);
    setChatStatusFromRun(finalBody || nextBody);
    if (TERMINAL_RUN_STATUSES.has(state.latestRunStatus)) {
      stopResponseTimer(contentNode);
    }
    await Promise.allSettled([
      refreshCurrentSessionMetadata(),
      refreshRecentSessions(),
    ]);
  } catch (error) {
    setInlineCheckpointBusy(card, false);
    if (errorNode) {
      errorNode.textContent = humanizeError(error);
      errorNode.hidden = false;
    }
    if (
      conversationId === state.conversationId
      && runId === state.latestRunId
    ) {
      showToast(humanizeError(error), "error");
    }
  }
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

function renderAgentChatResponse(
  contentNode,
  body,
  startedAt,
  { visibleTrace = null, holdAnswer = false } = {},
) {
  const result = body?.result || {};
  const actualStatus = body?.status || result.status || "running";
  const status = holdAnswer ? "running" : actualStatus;
  const trace = visibleTrace || body?.trace || result.trace || [];
  const elapsedMs = result.metrics?.elapsed_ms ?? Math.round(performance.now() - startedAt);
  renderExecutionProcess(contentNode, {
    trace,
    status,
    elapsedMs,
    fallbackNode: body?.latest_node || "setup_workspace",
    fallbackSummary: actualStatus === "queued"
      ? "Agent 任务已进入执行队列。"
      : "Agent 正在运行 LangGraph 工作流。",
  });

  if (!holdAnswer && ["completed", "partial", "blocked", "cancelled"].includes(actualStatus)) {
    contentNode.innerHTML = result.answer
      ? renderMarkdown(result.answer)
      : "<p>Agent 已完成，但没有返回文本内容。</p>";
  } else if (!holdAnswer && actualStatus === "waiting_approval") {
    contentNode.innerHTML = "<p>Agent 已在执行前暂停。请检查下方计划并决定是否继续。</p>";
  } else if (!holdAnswer && ["paused", "waiting_input"].includes(actualStatus)) {
    contentNode.innerHTML = "<p>Agent 已在安全边界暂停，请在下方补充信息或继续。</p>";
  } else if (!holdAnswer && actualStatus === "failed") {
    contentNode.innerHTML = `<p>${escapeHtml(body.error || "Agent 运行失败，请查看运行详情。")}</p>`;
  } else {
    const currentNode = trace.at(-1)?.node || body?.latest_node;
    contentNode.innerHTML = `<p class="response-placeholder">${escapeHtml(
      currentNode
        ? `${humanizeAgentNode(currentNode)}…`
        : "Agent 正在理解任务并规划下一步…",
    )}</p>`;
  }

  if (result.metrics) {
    renderResponseMetrics(contentNode, {
      elapsed_ms: result.metrics.elapsed_ms,
      node_count: result.metrics.node_count,
      tool_call_count: result.metrics.tool_call_count,
      input_tokens: result.metrics.input_tokens,
      output_tokens: result.metrics.output_tokens,
      thoughts_tokens: result.metrics.thoughts_tokens,
      total_tokens: result.metrics.total_tokens,
    });
  }
  if (!holdAnswer) {
    renderInlineAgentCheckpoint(contentNode, body);
  }
}

function createAgentProgressPresenter(
  contentNode,
  startedAt,
  { initialTrace = [] } = {},
) {
  const visibleTrace = [...initialTrace];
  let pending = Promise.resolve();

  return {
    update(body) {
      pending = pending.then(async () => {
        const result = body?.result || {};
        const fullTrace = body?.trace || result.trace || [];
        const newSteps = fullTrace.slice(visibleTrace.length);
        const revealDelayMs = prefersReducedMotion()
          ? 0
          : Math.min(
              TRACE_STEP_REVEAL_DELAY_MS,
              Math.floor(MAX_TRACE_REPLAY_MS / Math.max(1, newSteps.length)),
            );
        for (const step of newSteps) {
          visibleTrace.push(step);
          renderAgentChatResponse(contentNode, body, startedAt, {
            visibleTrace,
            holdAnswer: true,
          });
          if (revealDelayMs > 0) {
            await new Promise((resolve) => window.setTimeout(resolve, revealDelayMs));
          }
        }
        renderAgentChatResponse(contentNode, body, startedAt, { visibleTrace });
      });
      return pending;
    },
  };
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
  let submitted = false;
  let assistantContent = null;
  let progressPresenter = null;
  const startedAt = performance.now();
  setChatStatus("正在提交给 Agent", "running");
  try {
    const run = await runAgent({
      onSubmitted: (body) => {
        submitted = true;
        appendChatMessage("user", message);
        assistantContent = appendChatMessage("assistant", "", null, {
          runId: agentRunId(body),
        });
        progressPresenter = createAgentProgressPresenter(assistantContent, startedAt);
        progressPresenter.update(body);
        startResponseTimer(assistantContent, startedAt);
        setChatStatus("Agent 运行中", "running");
        showToast("任务已交给代码 Agent；运行详情可前往代码 Agent 页面查看");
      },
      onProgress: async (body) => {
        if (progressPresenter) {
          await progressPresenter.update(body);
        }
      },
    });
    if (run && agentRunConversationId(run) !== state.conversationId) {
      if (assistantContent) stopResponseTimer(assistantContent);
      return;
    }
    if (!run && !submitted) {
      input.value = message;
      setChatStatus("Agent 提交失败", "failed");
      return;
    }
    if (progressPresenter && run) {
      await progressPresenter.update(run);
    }
    setChatStatusFromRun({ status: state.latestRunStatus });
    if (state.latestRunStatus === "waiting_approval") {
      showToast("Agent 需要你的确认，已在对话中显示执行检查点", "warning");
    } else if (["waiting_input", "paused"].includes(state.latestRunStatus)) {
      showToast("Agent 已在对话中等待你的补充", "warning");
    }
  } finally {
    if (assistantContent && TERMINAL_RUN_STATUSES.has(state.latestRunStatus)) {
      stopResponseTimer(assistantContent);
    }
    sendButton.disabled = false;
    sendButton.removeAttribute("aria-busy");
    modeInput.disabled = false;
    updateComposerAvailability();
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
  let latestUsage = null;
  const startedAt = performance.now();
  const chatTrace = [];

  try {
    const conversationId = await ensureSession();
    appendChatMessage("user", message);
    assistantContent = appendChatMessage("assistant");
    renderExecutionProcess(assistantContent, {
      trace: chatTrace,
      status: "running",
      fallbackNode: "model_request",
      fallbackSummary: "正在建立模型请求并等待首个响应。",
    });
    startResponseTimer(assistantContent, startedAt);
    input.value = "";
    const payload = {
      conversation_id: conversationId,
      message,
      ...(state.activeWorkspaceId
        ? { workspace_id: state.activeWorkspaceId }
        : {}),
      ...optionalModelFields(),
    };
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
          const budget =
            data.budget_decision === "downgraded" ? " · 已按预算降级" : "";
          const routeLabel = data.routing_pending
            ? `${data.routing_policy || "quality"} 路由${budget}`
            : `${data.provider} · ${data.model}${thinking}${budget}`;
          setChatStatus(routeLabel, "running");
          chatTrace.push({
            step: 1,
            node: "model_request",
            summary: data.routing_pending
              ? `正在按 ${data.routing_policy || "quality"} 策略筛选模型。`
              : `已请求 ${data.provider} / ${data.model}${thinking}。`,
            output: data,
          });
          renderExecutionProcess(assistantContent, {
            trace: chatTrace,
            status: "running",
            elapsedMs: performance.now() - startedAt,
          });
        } else if (eventName === "route") {
          const thinking = data.thinking_level ? ` · ${data.thinking_level}` : "";
          const failures = data.route_trace?.failures?.length || 0;
          const budget =
            data.budget_decision === "downgraded" ? " · 已按预算降级" : "";
          setChatStatus(
            `${data.provider} · ${data.model}${thinking}${budget}`,
            "running",
          );
          chatTrace.push({
            step: chatTrace.length + 1,
            node: "model_route",
            summary: data.budget_decision === "downgraded"
              ? `预算治理已降级并选择 ${data.provider} / ${data.model}。`
              : failures
              ? `已回退并选择 ${data.provider} / ${data.model}（${failures} 次前置失败）。`
              : `已选择 ${data.provider} / ${data.model}。`,
            output: data.route_trace || {},
          });
          renderExecutionProcess(assistantContent, {
            trace: chatTrace,
            status: "running",
            elapsedMs: performance.now() - startedAt,
          });
        } else if (eventName === "memory_context") {
          const count = (data.items || []).length;
          chatTrace.push({
            step: chatTrace.length + 1,
            node: "retrieve_project_memory",
            summary: `已加载 ${count} 条工作区项目记忆。`,
            output: data,
          });
          renderExecutionProcess(assistantContent, {
            trace: chatTrace,
            status: "running",
            elapsedMs: performance.now() - startedAt,
          });
        } else if (eventName === "delta") {
          answer += data.text || "";
          assistantContent.innerHTML = renderMarkdown(answer);
          if (!chatTrace.some((step) => step.node === "stream_response")) {
            chatTrace.push({
              step: chatTrace.length + 1,
              node: "stream_response",
              summary: "模型正在流式生成最终回答。",
              output: {},
            });
            renderExecutionProcess(assistantContent, {
              trace: chatTrace,
              status: "running",
              elapsedMs: performance.now() - startedAt,
            });
          }
        } else if (eventName === "usage") {
          latestUsage = data;
          const thoughts = data.thoughts_tokens
            ? ` · ${data.thoughts_tokens} thinking`
            : "";
          setChatStatus(`${data.total_tokens || 0} tokens${thoughts}`, "running");
          renderResponseMetrics(assistantContent, data);
        } else if (eventName === "done") {
          latestUsage = data;
          renderExecutionProcess(assistantContent, {
            trace: chatTrace,
            status: "done",
            elapsedMs: data.elapsed_ms,
          });
          renderResponseMetrics(assistantContent, data);
          setChatStatus(`已完成 · ${formatDuration(data.elapsed_ms)}`, "completed");
        } else if (eventName === "error") {
          latestUsage = data;
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
    await refreshMessages(false, false);
    await Promise.allSettled([
      refreshCurrentSessionMetadata(),
      refreshRecentSessions(),
    ]);
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
    if (assistantContent) {
      const elapsedMs = Math.round(performance.now() - startedAt);
      renderExecutionProcess(assistantContent, {
        trace: chatTrace,
        status: "failed",
        elapsedMs,
      });
      renderResponseMetrics(assistantContent, {
        elapsed_ms: elapsedMs,
        ...(latestUsage || {}),
      });
    }
  } finally {
    if (assistantContent) {
      stopResponseTimer(assistantContent);
    }
    state.chatController = null;
    sendButton.disabled = false;
    sendButton.removeAttribute("aria-busy");
    modeInput.disabled = false;
    stopButton.classList.add("hidden");
    $("chat-output").removeAttribute("aria-busy");
    await refreshTokenUsageData();
    updateComposerAvailability();
    if (!input.disabled) {
      input.focus();
    }
  }
}

async function postSse(path, payload, onEvent, signal) {
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

async function runAgent({ onSubmitted = null, onProgress = null } = {}) {
  const message = $("agent-message-input").value.trim();
  if (!message) {
    $("agent-message-input").setAttribute("aria-invalid", "true");
    showToast("请先描述 Agent 任务", "warning");
    return null;
  }
  const workspace = currentWorkspace();
  if (!workspaceIsReady(workspace)) {
    showToast(
      workspace ? "当前工作区路径不可用，请切换工作区" : "请先选择 Agent 工作区",
      "warning",
    );
    openSettings();
    return null;
  }
  let submittedRun = null;
  let conversationId = "";
  const button = $("run-agent-btn");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  $("agent-answer").setAttribute("aria-busy", "true");
  setAgentStatus("running");
  $("agent-answer").className = "rich-output empty-output";
  $("agent-answer").textContent = "Agent 正在理解任务并规划下一步…";
  $("agent-events").innerHTML = '<div class="empty-state">正在等待第一个运行事件…</div>';
  resetChangeSetCard();
  $("approval-card").classList.add("hidden");
  try {
    conversationId = await ensureSession();
    const payload = {
      conversation_id: conversationId,
      message,
      workspace_id: workspace.id,
      ...optionalModelFields(),
      focus_files: csvValues($("focus-files-input").value),
    };
    const body = await fetchJson("/agent/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    submittedRun = body;
    if (conversationId !== state.conversationId) {
      return body;
    }
    renderAgentRun(body);
    if (onSubmitted) {
      onSubmitted(body);
    }
    const finalBody = await watchRunUntilTerminal({
      onProgress,
      preserveChat: Boolean(onProgress),
    });
    await Promise.allSettled([
      refreshCurrentSessionMetadata(),
      refreshRecentSessions(),
    ]);
    return finalBody || body;
  } catch (error) {
    if (!conversationId || conversationId === state.conversationId) {
      setAgentStatus("failed");
      $("agent-answer").className = "rich-output";
      $("agent-answer").innerHTML = `<p>${escapeHtml(humanizeError(error))}</p>`;
      showToast(humanizeError(error), "error");
    }
    return submittedRun;
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    $("agent-answer").removeAttribute("aria-busy");
    updateComposerAvailability();
  }
}

function renderAgentRun(body, { scrollApproval = true } = {}) {
  const result = body.result || {};
  state.latestRunId = body.run_id || result.run_id || "";
  state.latestRunStatus = body.status || result.status || "";
  state.latestRunConversationId = agentRunConversationId(body);
  const actualStatus = state.latestRunStatus;
  setAgentStatus(state.latestRunStatus, state.latestRunId);

  const answer = result.answer || body.error || "";
  const answerNode = $("agent-answer");
  if (answer) {
    answerNode.className = "rich-output";
    answerNode.innerHTML = renderMarkdown(answer);
  } else if (actualStatus === "waiting_input" && body.pending_approval?.type === "input_required") {
    answerNode.className = "rich-output empty-output";
    answerNode.textContent = body.pending_approval.question || "Agent 需要你补充一项信息后才能继续。";
    $("agent-steering-input").placeholder = body.pending_approval.question || "补充信息后点击继续";
  } else if (actualStatus === "waiting_approval" && body.pending_approval) {
    answerNode.className = "rich-output empty-output";
    answerNode.textContent = "执行计划已生成，请完成下方审批。";
  } else {
    answerNode.className = "rich-output empty-output";
    answerNode.textContent = "Agent 正在运行，结果会在完成后显示。";
  }

  renderApproval(
    state.latestRunStatus === "waiting_approval" ? body.pending_approval : null,
    { scroll: scrollApproval },
  );
  renderAgentControls(state.latestRunStatus);
  renderAgentMetrics(result.metrics);
  renderArtifacts(result.artifacts || []);
  const changeSetId = result.change_set_id || body.change_set_id || "";
  if (changeSetId && FINAL_RUN_STATUSES.has(actualStatus)) {
    loadChangeSet(state.latestRunId).catch((error) => {
      showToast(`ChangeSet 加载失败：${humanizeError(error)}`, "error");
    });
  } else if (!changeSetId) {
    resetChangeSetCard();
  }
  setTrace(body.trace || result.trace || []);
  setRaw(body);
  renderOverview();
}

function renderAgentControls(status) {
  const bar = $("agent-control-bar");
  if (!state.latestRunId || FINAL_RUN_STATUSES.has(status)) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  const isRunning = ["queued", "running"].includes(status);
  const canContinue = ["paused", "waiting_input"].includes(status);
  $("pause-run-btn").classList.toggle("hidden", !isRunning);
  $("steer-run-btn").classList.toggle("hidden", !(isRunning || canContinue));
  $("continue-run-btn").classList.toggle("hidden", !canContinue);
  $("cancel-run-btn").classList.toggle("hidden", status === "cancelled");
}

function renderApproval(approval, { scroll = true } = {}) {
  const card = $("approval-card");
  if (!approval || approval.type === "input_required" || approval.type === "run_pause") {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");
  $("approval-reason").textContent = humanizeApprovalReason(approval.reason);
  const tools = $("approval-tools");
  tools.innerHTML = "";
  for (const call of inlineApprovalTools(approval)) {
    const item = document.createElement("details");
    item.className = "approval-tool";
    item.innerHTML = `
      <summary><strong>${escapeHtml(call.name)}</strong><span>${escapeHtml(humanizePermissionLevel(call.permission_level))}</span></summary>
      <p>${escapeHtml(call.risk_summary || "请确认此操作及参数符合你的预期。")}</p>
      <pre>${escapeHtml(jsonPretty(call.arguments || {}))}</pre>
    `;
    tools.appendChild(item);
  }
  if (scroll) {
    card.scrollIntoView({ behavior: preferredScrollBehavior(), block: "center" });
  }
}

function renderAgentMetrics(metrics) {
  const values = metrics
    ? [
        ["耗时", formatDuration(metrics.elapsed_ms)],
        ["节点", metrics.node_count],
        ["工具调用", `${metrics.successful_tool_call_count}/${metrics.tool_call_count}`],
        ["变更文件", metrics.changed_file_count],
        ["Token", formatTokenCount(metrics.total_tokens)],
        ["思考 Token", formatTokenCount(metrics.thoughts_tokens)],
      ]
    : [
        ["耗时", "—"],
        ["节点", "—"],
        ["工具调用", "—"],
        ["变更文件", "—"],
        ["Token", "—"],
        ["思考 Token", "—"],
      ];
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

function resetChangeSetCard() {
  state.changeSetRequestGeneration += 1;
  state.currentChangeSet = null;
  $("change-set-card").classList.add("hidden");
  $("change-set-patch").textContent = "";
}

async function loadChangeSet(runId) {
  const generation = ++state.changeSetRequestGeneration;
  const body = await fetchJson(`/agent/runs/${encodeURIComponent(runId)}/changes`);
  if (generation !== state.changeSetRequestGeneration || runId !== state.latestRunId) {
    return null;
  }
  state.currentChangeSet = body;
  renderChangeSet(body);
  return body;
}

function renderChangeSet(changeSet) {
  const card = $("change-set-card");
  card.classList.remove("hidden");
  $("change-set-status").textContent = humanizeStatus(changeSet.status);
  $("change-set-status").className = `meta-badge ${statusClass(changeSet.status)}`;
  $("change-set-summary").textContent = changeSet.apply_mode === "patch_only"
    ? "当前为 patch-only：补丁可审阅和拒绝，但不会写入真实工作区。"
    : "应用会再次校验补丁摘要、工作区登记版本与每个目标文件的基线哈希。";
  $("change-set-meta").innerHTML = [
    ["文件", (changeSet.changed_files || []).length],
    ["模式", changeSet.apply_mode],
    ["校验", changeSet.validation_status],
    ["SHA-256", truncate(changeSet.patch_sha256, 18)],
  ]
    .map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd title="${escapeHtml(value)}">${escapeHtml(value)}</dd></div>`)
    .join("");
  $("change-set-patch").textContent = changeSet.patch || "没有可显示的补丁";
  const errorNode = $("change-set-error");
  errorNode.hidden = !changeSet.error;
  errorNode.textContent = changeSet.error || "";
  const ready = changeSet.status === "ready";
  $("reject-change-set-btn").disabled = !ready;
  $("apply-change-set-btn").disabled = !ready || changeSet.apply_mode === "patch_only";
  $("apply-change-set-btn").textContent = changeSet.apply_mode === "patch_only"
    ? "真实写入未启用"
    : "确认并应用到工作区";
}

async function applyCurrentChangeSet() {
  const changeSet = state.currentChangeSet;
  if (!changeSet || changeSet.status !== "ready") return;
  const confirmed = window.confirm(
    `确认应用 ${changeSet.changed_files.length} 个文件？\n补丁摘要：${changeSet.patch_sha256}`,
  );
  if (!confirmed) return;
  const button = $("apply-change-set-btn");
  button.disabled = true;
  try {
    const updated = await fetchJson(
      `/agent/runs/${encodeURIComponent(changeSet.run_id)}/changes/apply`,
      {
        method: "POST",
        body: JSON.stringify({
          change_set_id: changeSet.id,
          patch_sha256: changeSet.patch_sha256,
        }),
      },
    );
    state.currentChangeSet = updated;
    renderChangeSet(updated);
    showToast("ChangeSet 已应用", "success");
  } catch (error) {
    await loadChangeSet(changeSet.run_id).catch(() => null);
    showToast(humanizeError(error), "error");
  }
}

async function rejectCurrentChangeSet() {
  const changeSet = state.currentChangeSet;
  if (!changeSet || changeSet.status !== "ready") return;
  const button = $("reject-change-set-btn");
  button.disabled = true;
  try {
    const updated = await fetchJson(
      `/agent/runs/${encodeURIComponent(changeSet.run_id)}/changes/reject`,
      {
        method: "POST",
        body: JSON.stringify({ change_set_id: changeSet.id }),
      },
    );
    state.currentChangeSet = updated;
    renderChangeSet(updated);
    showToast("ChangeSet 已拒绝", "success");
  } catch (error) {
    button.disabled = false;
    showToast(humanizeError(error), "error");
  }
}

async function refreshRun(
  runId = state.latestRunId,
  { conversationId = state.conversationId, render = true } = {},
) {
  if (!runId) {
    showToast("还没有可刷新的 Agent 运行", "warning");
    return null;
  }
  const body = await fetchJson(`/agent/runs/${encodeURIComponent(runId)}`);
  if (
    render
    && runId === state.latestRunId
    && conversationId === state.conversationId
  ) {
    renderAgentRun(body, { scrollApproval: state.currentView === "agent" });
  }
  return body;
}

async function refreshEvents(
  showRaw = true,
  runId = state.latestRunId,
  {
    render = true,
    conversationId = state.latestRunConversationId || state.conversationId,
  } = {},
) {
  if (!runId) {
    $("agent-events").innerHTML = '<div class="empty-state">暂无运行事件</div>';
    return null;
  }
  const body = await fetchJson(`/agent/runs/${encodeURIComponent(runId)}/events`);
  const isCurrentRun = runId === state.latestRunId
    && conversationId === state.conversationId;
  if (render && isCurrentRun) renderAgentEvents(body.events || []);
  if (showRaw && isCurrentRun) {
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

function agentProgressBodyFromEvents(events, runId = state.latestRunId) {
  const trace = events
    .filter((event) => event.type === "node_completed")
    .map((event, index) => ({
      step: index + 1,
      node: event.node || "step",
      summary: event.summary || "",
      output: event.output || {},
    }));
  const latestEvent = events.at(-1) || {};
  return {
    run_id: runId,
    status: latestEvent.status || state.latestRunStatus || "running",
    latest_node: latestEvent.node || trace.at(-1)?.node || null,
    trace,
  };
}

function renderStreamedAgentProgress(events, runId = state.latestRunId) {
  const body = agentProgressBodyFromEvents(events, runId);
  state.latestRunStatus = body.status;
  setAgentStatus(body.status, runId);
  renderAgentControls(body.status);
  renderAgentEvents(events);
  setTrace(body.trace);
  renderOverview();
  return body;
}

async function resumeRun(approved) {
  if (!state.latestRunId) {
    return;
  }
  const runId = state.latestRunId;
  const conversationId = state.latestRunConversationId || state.conversationId;
  const chatContent = chatContentForRun(runId);
  const approveButton = $("approve-run-btn");
  const rejectButton = $("reject-run-btn");
  approveButton.disabled = true;
  rejectButton.disabled = true;
  approveButton.setAttribute("aria-busy", "true");
  setAgentStatus("running", state.latestRunId);
  try {
    const feedback = $("approval-feedback-input").value.trim();
    const body = await fetchJson(`/agent/runs/${encodeURIComponent(runId)}/resume`, {
      method: "POST",
      body: JSON.stringify({
        approved,
        feedback: feedback || (approved ? "用户已在产品界面确认执行计划" : "用户拒绝执行计划"),
      }),
    });
    if (
      conversationId !== state.conversationId
      || runId !== state.latestRunId
    ) {
      return;
    }
    renderAgentRun(body);
    let presenter = null;
    if (chatContent) {
      const checkpoint = chatContent.closest(".chat-bubble")
        ?.querySelector(".inline-agent-checkpoint");
      if (checkpoint) checkpoint.dataset.decision = approved ? "approve" : "reject";
      const startedAt = performance.now() - (body?.result?.metrics?.elapsed_ms || 0);
      presenter = createAgentProgressPresenter(chatContent, startedAt, {
        initialTrace: agentRunTrace(body),
      });
      await presenter.update(body);
    }
    showToast(approved ? "执行计划已批准" : "执行计划已拒绝", approved ? "success" : "warning");
    const finalBody = await watchRunUntilTerminal({
      runId,
      conversationId,
      preserveChat: Boolean(chatContent),
      onProgress: presenter ? (latestBody) => presenter.update(latestBody) : null,
    });
    if (
      conversationId !== state.conversationId
      || runId !== state.latestRunId
    ) {
      return;
    }
    if (presenter && finalBody) await presenter.update(finalBody);
    if (finalBody) setChatStatusFromRun(finalBody);
  } catch (error) {
    if (
      conversationId === state.conversationId
      && runId === state.latestRunId
    ) {
      showToast(humanizeError(error), "error");
    }
  } finally {
    approveButton.disabled = false;
    rejectButton.disabled = false;
    approveButton.removeAttribute("aria-busy");
  }
}

async function controlRun(action) {
  if (!state.latestRunId) return;
  const runId = state.latestRunId;
  const conversationId = state.latestRunConversationId || state.conversationId;
  const input = $("agent-steering-input");
  const message = input.value.trim();
  if (action === "steer" && !message) {
    showToast("请先输入需要转向的方向", "warning");
    input.focus();
    return;
  }
  const endpoint = action === "continue" ? "continue" : action;
  try {
    const body = await fetchJson(
      `/agent/runs/${encodeURIComponent(runId)}/${endpoint}`,
      {
        method: "POST",
        body: JSON.stringify({ message }),
      },
    );
    if (
      conversationId !== state.conversationId
      || runId !== state.latestRunId
    ) {
      return;
    }
    renderAgentRun(body);
    if (["steer", "continue"].includes(action)) input.value = "";
    showToast({
      pause: "暂停请求已发送，将在安全边界生效",
      cancel: "取消请求已发送",
      steer: "转向信息已加入当前运行",
      continue: "Agent 已继续运行",
    }[action] || "运行状态已更新", action === "cancel" ? "warning" : "success");
    if (action === "continue") {
      const chatContent = chatContentForRun(runId);
      let presenter = null;
      if (chatContent) {
        const startedAt = performance.now() - (body?.result?.metrics?.elapsed_ms || 0);
        presenter = createAgentProgressPresenter(chatContent, startedAt, {
          initialTrace: agentRunTrace(body),
        });
        await presenter.update(body);
      }
      const finalBody = await watchRunUntilTerminal({
        runId,
        conversationId,
        preserveChat: Boolean(chatContent),
        onProgress: presenter ? (latestBody) => presenter.update(latestBody) : null,
      });
      if (
        conversationId !== state.conversationId
        || runId !== state.latestRunId
      ) {
        return;
      }
      if (presenter && finalBody) await presenter.update(finalBody);
      if (finalBody) setChatStatusFromRun(finalBody);
    }
  } catch (error) {
    if (
      conversationId === state.conversationId
      && runId === state.latestRunId
    ) {
      showToast(humanizeError(error), "error");
    }
  }
}

async function watchRunUntilTerminal(options = {}) {
  const runId = options.runId || state.latestRunId;
  const conversationId = options.conversationId || state.conversationId;
  const generation = ++state.agentPollGeneration;
  let cursor = 0;
  let latestBody = null;
  let progressUpdates = Promise.resolve();
  const streamedEvents = [];
  const publishProgress = (body) => {
    if (!options.onProgress || !body) return;
    progressUpdates = progressUpdates.then(() => options.onProgress(body));
  };
  try {
    const response = await fetch(
      `${API_BASE}/agent/runs/${encodeURIComponent(runId)}/events/stream?cursor=${cursor}`,
      {
        headers: { "X-User-ID": $("user-id-input")?.value.trim() || "demo_user" },
      },
    );
    if (!response.ok || !response.body) throw new Error(`event stream returned ${response.status}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let stopped = false;
    while (
      generation === state.agentPollGeneration
      && runId === state.latestRunId
      && conversationId === state.conversationId
    ) {
      const { value, done } = await reader.read();
      if (done) break;
      if (
        generation !== state.agentPollGeneration
        || runId !== state.latestRunId
        || conversationId !== state.conversationId
      ) {
        await reader.cancel();
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const dataLine = chunk.split("\n").find((line) => line.startsWith("data: "));
        if (!dataLine) continue;
        const event = JSON.parse(dataLine.slice(6));
        cursor = Math.max(cursor, Number(event.sequence) || 0);
        streamedEvents.push(event);
        const progressBody = renderStreamedAgentProgress(streamedEvents, runId);
        if (event.type === "node_completed") {
          publishProgress(progressBody);
        }
        if (TERMINAL_RUN_STATUSES.has(progressBody.status)) {
          latestBody = await refreshRun(runId, { conversationId });
          publishProgress(latestBody);
          stopped = true;
          break;
        }
      }
      if (stopped) break;
    }
    await progressUpdates;
  } catch (error) {
    console.warn("Agent event stream unavailable; falling back to polling", error);
  }
  if (
    generation !== state.agentPollGeneration
    || runId !== state.latestRunId
    || conversationId !== state.conversationId
  ) {
    return latestBody;
  }
  const polledBody = await pollRunUntilTerminal(
    { ...options, runId, conversationId },
    latestBody,
  );
  return polledBody || latestBody;
}

async function pollRunUntilTerminal(
  {
    onProgress = null,
    preserveChat = false,
    runId = state.latestRunId,
    conversationId = state.conversationId,
  } = {},
  initialBody = null,
) {
  const generation = ++state.agentPollGeneration;
  let latestBody = initialBody;
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (
      generation !== state.agentPollGeneration ||
      !runId ||
      runId !== state.latestRunId ||
      conversationId !== state.conversationId ||
      TERMINAL_RUN_STATUSES.has(state.latestRunStatus)
    ) {
      break;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 300));
    latestBody = await refreshRun(runId, { conversationId });
    if (onProgress) {
      await onProgress(latestBody);
    }
    if (attempt % 3 === 0) {
      await refreshEvents(false, runId);
    }
  }
  if (
    !latestBody
    && runId
    && runId === state.latestRunId
    && conversationId === state.conversationId
    && TERMINAL_RUN_STATUSES.has(state.latestRunStatus)
  ) {
    latestBody = await refreshRun(runId, { conversationId });
    if (onProgress) {
      await onProgress(latestBody);
    }
  }
  if (
    generation !== state.agentPollGeneration
    || runId !== state.latestRunId
    || conversationId !== state.conversationId
  ) {
    return latestBody;
  }
  await refreshEvents(false, runId);
  try {
    await refreshMessages(false, !preserveChat);
  } catch (error) {
    showToast(`Agent 已结束，但共享会话刷新失败：${humanizeError(error)}`, "warning");
  }
  await refreshTokenUsageData();
  if (!TERMINAL_RUN_STATUSES.has(state.latestRunStatus)) {
    showToast("Agent 仍在后台运行，可稍后刷新状态", "warning");
  }
  return latestBody;
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
  $("document-files-input").disabled = true;
  try {
    for (const [index, file] of files.entries()) {
      updateUploadQueueItem(index, "uploading", `正在上传 ${index + 1}/${files.length}`);
      $("rag-status").textContent = `正在上传 ${index + 1}/${files.length}…`;
      const form = new FormData();
      form.append("file", file, file.name);
      try {
        let body;
        try {
          body = await fetchJson(
            `/knowledge-bases/${encodeURIComponent(kbId)}/documents`,
            { method: "POST", body: form },
          );
        } catch (error) {
          const detail = error.body?.detail;
          if (
            error.status !== 409
            || detail?.code !== "document_filename_conflict"
          ) {
            throw error;
          }
          updateUploadQueueItem(index, "conflict", "发现同名文档，等待确认");
          const shouldReplace = window.confirm(
            `知识库中已有 ${file.name}。是否替换原文档并重建索引？`,
          );
          if (!shouldReplace) {
            failures.push({ filename: file.name, error: "用户取消替换" });
            updateUploadQueueItem(index, "conflict", "已保留原文档");
            continue;
          }
          const replacement = new FormData();
          replacement.append("file", file, file.name);
          body = await fetchJson(
            `/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(detail.existing_document_id)}/content`,
            { method: "PUT", body: replacement },
          );
        }
        ingested.push(body);
        updateUploadQueueItem(index, "success", `可用 · ${body.chunk_count} 个分块`);
      } catch (error) {
        failures.push({ filename: file.name, error: humanizeError(error) });
        updateUploadQueueItem(index, "failed", humanizeError(error));
      }
    }
    await Promise.all([listKnowledgeBases({ preserveSelection: kbId }), listKnowledgeDocuments()]);
    const chunkCount = ingested.reduce((total, item) => total + item.chunk_count, 0);
    $("rag-status").textContent = failures.length
      ? `已处理 ${ingested.length}，未完成 ${failures.length}`
      : `已建立 ${ingested.length} 个文档索引`;
    setRaw({ documents: ingested, failures });
    input.value = "";
    $("document-upload-actions").hidden = true;
    if (failures.length) {
      showToast(
        `${ingested.length} 个文件已完成，${failures.length} 个未完成`,
        ingested.length ? "warning" : "error",
      );
    } else {
      showToast(`已上传 ${ingested.length} 个文件，共 ${chunkCount} 个分块`);
    }
  } finally {
    $("ingest-doc-btn").disabled = false;
    $("document-files-input").disabled = false;
  }
}

function renderSelectedDocumentFiles() {
  const files = Array.from($("document-files-input").files || []);
  const queue = $("selected-document-files");
  queue.hidden = !files.length;
  $("document-upload-actions").hidden = !files.length;
  queue.innerHTML = files
    .map(
      (file, index) => `
        <div class="upload-queue-item" data-upload-index="${index}" data-state="pending">
          <strong>${escapeHtml(file.name)}</strong>
          <span>等待上传</span>
          <small>${escapeHtml(formatByteSize(file.size))}</small>
        </div>
      `,
    )
    .join("");
}

function updateUploadQueueItem(index, status, message) {
  const item = document.querySelector(`[data-upload-index="${index}"]`);
  if (!item) return;
  item.dataset.state = status;
  item.querySelector("span").textContent = message;
}

function formatByteSize(value) {
  if (value === null || value === undefined) return "未知大小";
  const bytes = Number(value);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function renderRerankControl() {
  const capabilities = state.rerankerCapabilities;
  const button = $("rag-rerank-toggle");
  const description = $("rag-rerank-description");
  const strategy = $("rag-strategy-summary");
  const available = Boolean(capabilities.available);
  const operable = available && capabilities.status !== "error";
  const pressed = operable && state.rerankEnabled;
  button.disabled = !operable || Boolean(state.ragRequestController);
  button.setAttribute("aria-pressed", String(pressed));
  button.textContent = `精排：${pressed ? "开启" : "关闭"}`;
  strategy.textContent = capabilities.status === "checking"
    ? "策略：检测中"
    : `策略：${pressed ? "CrossEncoder" : "RRF"}`;
  strategy.dataset.strategy = pressed ? "rerank" : "rrf";

  if (!available) {
    description.textContent = capabilities.status === "checking"
      ? "正在检查服务端精排能力…"
      : "服务端未配置精排模型，当前仅使用 RRF 排序。";
    return;
  }
  const model = capabilities.model || capabilities.provider || "已配置模型";
  const status = capabilities.status === "ready"
    ? "模型已加载"
    : capabilities.status === "error"
      ? "模型加载失败"
      : "首次开启时加载模型";
  description.textContent = `${model} · ${status}；精排会增加响应时间。`;
}

async function loadRagCapabilities() {
  try {
    const body = await fetchJson("/rag/capabilities");
    state.rerankerCapabilities = body.reranker || {
      available: false,
      status: "unavailable",
    };
  } catch {
    state.rerankerCapabilities = {
      available: false,
      status: "unavailable",
    };
  }
  renderRerankControl();
}

function toggleRerank() {
  if (!state.rerankerCapabilities.available) {
    return;
  }
  state.rerankEnabled = !state.rerankEnabled;
  renderRerankControl();
  saveUiPreferences();
}

function retrievalStatus(retrieval) {
  if (!retrieval?.rerank_applied) {
    return "RRF 排序";
  }
  state.rerankerCapabilities.status = "ready";
  renderRerankControl();
  const duration = retrieval.rerank_duration_ms;
  return duration === null || duration === undefined
    ? "CrossEncoder 精排"
    : `CrossEncoder 精排 ${formatDuration(duration)}`;
}

function setRagRequestBusy(mode, busy) {
  const searchButton = $("search-rag-btn");
  const askButton = $("ask-rag-btn");
  const controls = [
    $("rag-question-input"),
    $("kb-id-input"),
    $("rag-limit-input"),
    $("rag-recall-limit-input"),
  ];
  searchButton.disabled = busy;
  askButton.disabled = busy;
  searchButton.textContent = busy && mode === "search" ? "检索中…" : "仅检索";
  askButton.textContent = busy && mode === "ask" ? "生成中…" : "生成回答";
  searchButton.toggleAttribute("aria-busy", busy && mode === "search");
  askButton.toggleAttribute("aria-busy", busy && mode === "ask");
  $("rag-answer").toggleAttribute("aria-busy", busy);
  for (const control of controls) {
    control.disabled = busy;
  }
  renderRerankControl();
}

function beginRagRequest(mode) {
  if (state.ragRequestController) {
    state.ragRequestController.abort();
  }
  state.ragRequestGeneration += 1;
  state.ragRequestController = new AbortController();
  setRagRequestBusy(mode, true);
  return {
    controller: state.ragRequestController,
    generation: state.ragRequestGeneration,
  };
}

function isCurrentRagRequest(generation) {
  return generation === state.ragRequestGeneration;
}

function finishRagRequest(generation) {
  if (!isCurrentRagRequest(generation)) {
    return;
  }
  state.ragRequestController = null;
  setRagRequestBusy("", false);
}

function prepareRagOutput(message) {
  $("rag-answer").className = "rich-output empty-output";
  $("rag-answer").textContent = message;
  renderCitations([]);
}

async function searchRag() {
  const question = $("rag-question-input").value.trim();
  if (!question) {
    showToast("请输入搜索词", "warning");
    return;
  }
  const kbId = $("kb-id-input").value.trim();
  if (!kbId) {
    showToast("请先选择知识库", "warning");
    return;
  }
  const rerankEnabled = state.rerankEnabled;
  const request = beginRagRequest("search");
  $("rag-status").textContent = rerankEnabled ? "正在召回并精排…" : "正在检索…";
  prepareRagOutput(rerankEnabled ? "正在检索并精排相关内容…" : "正在检索相关内容…");
  try {
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(kbId)}/search`, {
      method: "POST",
      signal: request.controller.signal,
      body: JSON.stringify({
        query: question,
        limit: numberValue("rag-limit-input", 5),
        recall_limit: numberValue("rag-recall-limit-input", 20),
        rerank_enabled: rerankEnabled,
      }),
    });
    if (!isCurrentRagRequest(request.generation)) {
      return;
    }
    const results = body.results || [];
    $("rag-status").textContent = `找到 ${results.length} 条结果 · ${retrievalStatus(body.retrieval)}`;
    $("rag-answer").className = "rich-output";
    $("rag-answer").innerHTML = results.length
      ? `<h3>检索完成</h3><p>在知识库 <code>${escapeHtml(kbId)}</code> 中找到 ${results.length} 条相关内容。</p>`
      : "<p>没有找到相关内容。可以调整问题或增加召回数量后重试。</p>";
    renderCitations(results);
    setRaw(body);
  } catch (error) {
    if (error.name === "AbortError" || !isCurrentRagRequest(request.generation)) {
      return;
    }
    $("rag-status").textContent = "检索失败";
    $("rag-answer").className = "rich-output";
    $("rag-answer").innerHTML = `<p>${escapeHtml(humanizeError(error))}</p>`;
    showToast(humanizeError(error), "error");
  } finally {
    finishRagRequest(request.generation);
  }
}

async function askRag() {
  const question = $("rag-question-input").value.trim();
  if (!question) {
    showToast("请输入问题", "warning");
    return;
  }
  const kbId = $("kb-id-input").value.trim();
  if (!kbId) {
    showToast("请先选择知识库", "warning");
    return;
  }
  const rerankEnabled = state.rerankEnabled;
  const request = beginRagRequest("ask");
  $("rag-status").textContent = rerankEnabled ? "正在精排并生成…" : "正在生成…";
  prepareRagOutput("正在检索相关内容并组织回答…");
  try {
    const conversationId = await ensureSession();
    const workspaceId = activeWorkspaceId();
    const attributedWorkspaceId = state.workspaces.some(
      (workspace) => workspace.id === workspaceId,
    )
      ? workspaceId
      : null;
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(kbId)}/ask`, {
      method: "POST",
      signal: request.controller.signal,
      body: JSON.stringify({
        question,
        conversation_id: conversationId,
        workspace_id: attributedWorkspaceId,
        limit: numberValue("rag-limit-input", 5),
        recall_limit: numberValue("rag-recall-limit-input", 20),
        rerank_enabled: rerankEnabled,
        ...optionalModelFields(),
      }),
    });
    if (!isCurrentRagRequest(request.generation)) {
      return;
    }
    $("rag-status").textContent = `${body.citations.length} 条引用 · ${retrievalStatus(body.retrieval)}`;
    $("rag-answer").className = "rich-output";
    $("rag-answer").innerHTML = renderMarkdown(body.answer || "模型没有返回回答。");
    renderCitations(body.citations || []);
    setRaw(body);
  } catch (error) {
    if (error.name === "AbortError" || !isCurrentRagRequest(request.generation)) {
      return;
    }
    $("rag-status").textContent = "生成失败";
    $("rag-answer").className = "rich-output";
    $("rag-answer").innerHTML = `<p>${escapeHtml(humanizeError(error))}</p>`;
    showToast(humanizeError(error), "error");
  } finally {
    finishRagRequest(request.generation);
  }
}

function renderCitations(citations) {
  const list = $("rag-citations");
  list.innerHTML = "";
  for (const [index, citation] of citations.entries()) {
    const scores = [];
    if (citation.fusion_score !== null && citation.fusion_score !== undefined) {
      scores.push(`RRF ${Number(citation.fusion_score).toFixed(3)}`);
    }
    if (citation.rerank_score !== null && citation.rerank_score !== undefined) {
      scores.push(`精排 ${Number(citation.rerank_score).toFixed(3)}`);
    }
    if (!scores.length) {
      scores.push(`相关度 ${Number(citation.score ?? 0).toFixed(3)}`);
    }
    const lines = citation.start_line || citation.end_line
      ? ` · 行 ${citation.start_line || "?"}–${citation.end_line || "?"}`
      : "";
    const item = document.createElement("article");
    item.className = "citation-card";
    item.innerHTML = `
      <header>
        <strong>[${index + 1}] ${escapeHtml(citation.filename)} · #${escapeHtml(citation.chunk_index)}${escapeHtml(lines)}</strong>
        <span class="citation-scores">${scores.map((score) => `<span class="score-pill">${escapeHtml(score)}</span>`).join("")}</span>
      </header>
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

function currentKnowledgeBase() {
  const id = $("kb-id-input").value;
  return state.knowledgeBases.find((item) => item.id === id) || null;
}

function setKnowledgeTab(tabName, { focus = false } = {}) {
  const allowed = new Set(["documents", "ask", "settings"]);
  const tab = allowed.has(tabName) ? tabName : "documents";
  state.activeKnowledgeTab = tab;
  document.querySelectorAll("[data-rag-tab]").forEach((button) => {
    const active = button.dataset.ragTab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    if (active && focus) button.focus();
  });
  document.querySelectorAll("[data-rag-tab-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.ragTabPanel !== tab;
  });
  saveUiPreferences();
}

function renderKnowledgeContext() {
  const knowledgeBase = currentKnowledgeBase();
  const unavailable = !knowledgeBase;
  $("knowledge-documents-tab").disabled = unavailable;
  $("knowledge-ask-tab").disabled = unavailable;
  if (!knowledgeBase) {
    $("selected-kb-name").textContent = "请选择知识库";
    $("selected-kb-description").textContent = "选中后即可查看和管理文档。";
    $("selected-kb-tags").innerHTML = "";
    $("selected-kb-document-count").textContent = "—";
    $("selected-kb-health").className = "index-status neutral";
    $("selected-kb-health").innerHTML = '<i aria-hidden="true"></i>未选择';
    return;
  }
  $("selected-kb-name").textContent = knowledgeBase.name;
  $("selected-kb-description").textContent = knowledgeBase.description || "暂无描述";
  $("selected-kb-tags").innerHTML = (knowledgeBase.tags || [])
    .map((tag) => `<span class="tag-chip">${escapeHtml(tag)}</span>`)
    .join("");
  $("selected-kb-document-count").textContent = String(knowledgeBase.document_count || 0);
  const hasFailures = state.knowledgeDocuments.some(
    (document) => document.last_index_status === "failed",
  );
  const health = hasFailures ? "failed" : "active";
  $("selected-kb-health").className = `index-status ${health}`;
  $("selected-kb-health").innerHTML = `<i aria-hidden="true"></i>${hasFailures ? "存在索引异常" : "索引可用"}`;
}

function populateKnowledgeBaseForm(knowledgeBase) {
  $("kb-catalog-id-input").value = knowledgeBase.id;
  $("kb-catalog-id-input").disabled = true;
  $("kb-name-input").value = knowledgeBase.name;
  $("kb-description-input").value = knowledgeBase.description || "";
  $("kb-tags-input").value = (knowledgeBase.tags || []).join(", ");
  $("create-knowledge-base-btn").hidden = true;
  $("update-knowledge-base-btn").hidden = false;
  $("delete-knowledge-base-btn").hidden = false;
  $("knowledge-settings-title").textContent = "知识库设置";
}

function selectKnowledgeBase(knowledgeBase, { loadDocuments = true } = {}) {
  state.selectedDocumentIds.clear();
  state.knowledgeDocumentPage = 1;
  state.activeKnowledgeDocument = null;
  closeDocumentDrawer({ restoreFocus: false });
  if (!knowledgeBase) {
    $("kb-id-input").value = "";
    state.knowledgeDocuments = [];
    state.knowledgeDocumentTotal = 0;
    renderKnowledgeBases();
    renderKnowledgeContext();
    renderKnowledgeDocuments();
    return;
  }
  populateKnowledgeBaseForm(knowledgeBase);
  $("kb-id-input").value = knowledgeBase.id;
  renderKnowledgeBases();
  renderKnowledgeContext();
  saveUiPreferences();
  if (loadDocuments) {
    listKnowledgeDocuments().catch((error) => showToast(humanizeError(error), "error"));
  }
}

function renderKnowledgeBases() {
  const list = $("knowledge-base-list");
  const selectedId = $("kb-id-input").value;
  const mobileSelect = $("mobile-knowledge-base-select");
  mobileSelect.innerHTML = state.knowledgeBases.length
    ? state.knowledgeBases
      .map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === selectedId ? "selected" : ""}>${escapeHtml(item.name)} · ${escapeHtml(item.document_count)} 个文档</option>`)
      .join("")
    : '<option value="" selected disabled>暂无知识库</option>';
  mobileSelect.disabled = !state.knowledgeBases.length;
  const query = $("knowledge-base-search-input").value.trim().toLowerCase();
  const items = query
    ? state.knowledgeBases.filter((item) => (
      `${item.id} ${item.name} ${item.description || ""} ${(item.tags || []).join(" ")}`
        .toLowerCase()
        .includes(query)
    ))
    : state.knowledgeBases;
  if (!items.length) {
    list.innerHTML = `<div class="empty-state">${state.knowledgeBases.length ? "没有匹配的知识库" : "暂无知识库，可以从新建开始。"}</div>`;
    return;
  }
  list.innerHTML = items
    .map(
      (item) => `
        <button class="knowledge-base-row" type="button" role="option" aria-selected="${String(item.id === selectedId)}" data-knowledge-base-id="${escapeHtml(item.id)}">
          <strong>${escapeHtml(item.name)}</strong>
          <span class="kb-count">${escapeHtml(item.document_count)}</span>
          <small>${escapeHtml(item.id)} · ${escapeHtml(truncate((item.tags || []).join(" / ") || item.description || "暂无标签", 48))}</small>
        </button>
      `,
    )
    .join("");
}

async function listKnowledgeBases({ preserveSelection = "" } = {}) {
  const currentId = $("kb-id-input").value;
  const selectedId = preserveSelection || currentId || state.preferredKnowledgeBaseId;
  const body = await fetchJson("/knowledge-bases");
  state.knowledgeBases = body.knowledge_bases || [];
  renderKnowledgeBases();
  const selected = state.knowledgeBases.find((item) => item.id === selectedId)
    || state.knowledgeBases[0]
    || null;
  if (selected && selected.id === currentId) {
    populateKnowledgeBaseForm(selected);
    renderKnowledgeBases();
    renderKnowledgeContext();
    saveUiPreferences();
  } else {
    selectKnowledgeBase(selected);
  }
  return state.knowledgeBases;
}

function beginCreateKnowledgeBase() {
  selectKnowledgeBase(null, { loadDocuments: false });
  $("kb-catalog-id-input").disabled = false;
  $("kb-catalog-id-input").value = "";
  $("kb-name-input").value = "";
  $("kb-description-input").value = "";
  $("kb-tags-input").value = "";
  $("create-knowledge-base-btn").hidden = false;
  $("update-knowledge-base-btn").hidden = true;
  $("delete-knowledge-base-btn").hidden = true;
  $("knowledge-settings-title").textContent = "新建知识库";
  setKnowledgeTab("settings");
  $("kb-catalog-id-input").focus();
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
    state.knowledgeBases.push(body);
    selectKnowledgeBase(body);
    setKnowledgeTab("documents");
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
    state.knowledgeBases = state.knowledgeBases.map((item) => item.id === id ? body : item);
    selectKnowledgeBase(body, { loadDocuments: false });
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
  const knowledgeBase = currentKnowledgeBase();
  const confirmed = knowledgeBase?.document_count
    ? window.prompt(`删除后将移除 ${knowledgeBase.document_count} 个文档、分块和向量。输入 ${id} 确认：`) === id
    : window.confirm(`删除知识库 ${id}？`);
  if (!confirmed) return;
  try {
    await fetchJson(`/knowledge-bases/${encodeURIComponent(id)}`, { method: "DELETE" });
    $("kb-catalog-id-input").value = "";
    $("kb-name-input").value = "";
    $("kb-description-input").value = "";
    $("kb-tags-input").value = "";
    $("kb-id-input").value = "";
    await listKnowledgeBases();
    showToast("知识库已删除");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

function documentIndexPresentation(status, searchable = true) {
  if (status === "failed") {
    return {
      state: "failed",
      className: "failed",
      label: searchable ? "替换失败 · 旧版可用" : "索引失败",
    };
  }
  if (["pending", "parsing", "embedding", "vector_written"].includes(status)) {
    return { state: "processing", className: "processing", label: "建立索引中" };
  }
  return { state: "active", className: "active", label: "可用" };
}

function renderKnowledgeDocuments({ loading = false } = {}) {
  const body = $("knowledge-document-rows");
  const selectedAll = state.knowledgeDocuments.length > 0
    && state.knowledgeDocuments.every((item) => state.selectedDocumentIds.has(item.id));
  $("select-all-documents").checked = selectedAll;
  $("select-all-documents").disabled = !state.knowledgeDocuments.length;
  $("bulk-delete-documents-btn").disabled = !state.selectedDocumentIds.size;
  $("bulk-delete-documents-btn").textContent = state.selectedDocumentIds.size
    ? `删除已选 (${state.selectedDocumentIds.size})`
    : "删除已选";
  if (loading) {
    body.innerHTML = '<tr><td colspan="6"><div class="empty-state" aria-busy="true">正在加载文档…</div></td></tr>';
  } else if (!currentKnowledgeBase()) {
    body.innerHTML = '<tr><td colspan="6"><div class="empty-state">请先选择知识库</div></td></tr>';
  } else if (!state.knowledgeDocuments.length) {
    body.innerHTML = '<tr><td colspan="6"><div class="empty-state">这个知识库还没有文档。使用“批量上传”建立第一份索引。</div></td></tr>';
  } else {
    body.innerHTML = state.knowledgeDocuments.map((documentItem) => {
      const status = documentIndexPresentation(
        documentItem.last_index_status,
        documentItem.is_searchable,
      );
      const extension = documentItem.filename.includes(".")
        ? documentItem.filename.split(".").pop().toUpperCase()
        : "FILE";
      return `
        <tr class="document-row" data-document-id="${escapeHtml(documentItem.id)}" data-index-state="${status.state}">
          <td><input class="document-select" type="checkbox" aria-label="选择 ${escapeHtml(documentItem.title)}" ${state.selectedDocumentIds.has(documentItem.id) ? "checked" : ""} /></td>
          <td class="document-name-cell"><button class="document-open-button" type="button" data-open-document="${escapeHtml(documentItem.id)}"><strong>${escapeHtml(documentItem.title)}</strong><span>${escapeHtml(documentItem.filename)}</span></button>${documentItem.tags?.length ? `<small class="document-tag-summary">${escapeHtml(documentItem.tags.join(" / "))}</small>` : ""}</td>
          <td data-label="状态"><span class="index-status ${status.className}"><i aria-hidden="true"></i>${escapeHtml(status.label)}</span></td>
          <td data-label="类型"><span class="document-type">${escapeHtml(extension)} · ${escapeHtml(formatByteSize(documentItem.byte_size))}</span></td>
          <td data-label="分块"><span class="document-chunk-count">${escapeHtml(documentItem.chunk_count)}</span></td>
          <td data-label="更新"><span class="document-date">${escapeHtml(formatDate(documentItem.updated_at))}</span></td>
        </tr>`;
    }).join("");
  }
  const pageCount = Math.max(1, Math.ceil(state.knowledgeDocumentTotal / state.knowledgeDocumentPageSize));
  $("document-page-summary").textContent = state.knowledgeDocumentTotal
    ? `共 ${state.knowledgeDocumentTotal} 个文档 · 第 ${state.knowledgeDocumentPage}/${pageCount} 页`
    : "暂无文档";
  $("previous-document-page").disabled = state.knowledgeDocumentPage <= 1;
  $("next-document-page").disabled = state.knowledgeDocumentPage >= pageCount;
  renderKnowledgeContext();
}

async function listKnowledgeDocuments() {
  const knowledgeBaseId = $("kb-id-input").value;
  if (state.documentRequestController) state.documentRequestController.abort();
  if (!knowledgeBaseId) {
    state.knowledgeDocuments = [];
    state.knowledgeDocumentTotal = 0;
    renderKnowledgeDocuments();
    return;
  }
  state.documentRequestController = new AbortController();
  const generation = ++state.documentRequestGeneration;
  state.selectedDocumentIds.clear();
  renderKnowledgeDocuments({ loading: true });
  const params = new URLSearchParams({
    query: $("document-search-input").value.trim(),
    sort: $("document-sort-input").value,
    page: String(state.knowledgeDocumentPage),
    page_size: String(state.knowledgeDocumentPageSize),
  });
  if ($("document-status-filter").value) {
    params.set("status", $("document-status-filter").value);
  }
  try {
    const response = await fetchJson(
      `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents?${params}`,
      { signal: state.documentRequestController.signal },
    );
    if (generation !== state.documentRequestGeneration) return;
    state.knowledgeDocuments = response.items || [];
    state.knowledgeDocumentTotal = response.total || 0;
    $("rag-status").textContent = `已加载 ${state.knowledgeDocumentTotal} 个文档`;
    renderKnowledgeDocuments();
  } catch (error) {
    if (error.name === "AbortError" || generation !== state.documentRequestGeneration) return;
    state.knowledgeDocuments = [];
    state.knowledgeDocumentTotal = 0;
    renderKnowledgeDocuments();
    throw error;
  } finally {
    if (generation === state.documentRequestGeneration) {
      state.documentRequestController = null;
    }
  }
}

function renderDocumentDrawer(documentItem) {
  $("document-drawer-title").textContent = documentItem.title;
  $("document-title-input").value = documentItem.title;
  $("document-description-input").value = documentItem.description || "";
  $("document-tags-input").value = (documentItem.tags || []).join(", ");
  $("replace-document-file").value = "";
  const status = documentIndexPresentation(
    documentItem.last_index_status,
    documentItem.is_searchable,
  );
  $("document-metadata-list").innerHTML = [
    ["文件名", documentItem.filename],
    ["文档 ID", documentItem.id],
    ["索引状态", status.label],
    ["MIME", documentItem.media_type || "未知"],
    ["文件大小", formatByteSize(documentItem.byte_size)],
    ["分块数", String(documentItem.chunk_count)],
    ["内容哈希", documentItem.content_hash],
    ["创建时间", formatDate(documentItem.created_at)],
    ["索引时间", formatDate(documentItem.indexed_at)],
  ].map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  $("document-index-error").hidden = !documentItem.last_index_error;
  $("document-index-error").textContent = documentItem.last_index_error
    ? `最近索引失败：${documentItem.last_index_error}。当前仍使用上一版可用内容。`
    : "";
}

async function openDocumentDrawer(documentId, returnFocus = document.activeElement) {
  const knowledgeBaseId = $("kb-id-input").value;
  state.documentDrawerReturnFocus = returnFocus;
  const documentItem = await fetchJson(
    `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(documentId)}`,
  );
  state.activeKnowledgeDocument = documentItem;
  renderDocumentDrawer(documentItem);
  $("document-drawer").hidden = false;
  document.body.style.overflow = "hidden";
  $("close-document-drawer").focus();
}

function closeDocumentDrawer({ restoreFocus = true } = {}) {
  const drawer = $("document-drawer");
  if (!drawer || drawer.hidden) return;
  drawer.hidden = true;
  document.body.style.overflow = "";
  state.activeKnowledgeDocument = null;
  if (restoreFocus && state.documentDrawerReturnFocus?.isConnected) {
    state.documentDrawerReturnFocus.focus();
  }
}

async function saveKnowledgeDocument() {
  const documentItem = state.activeKnowledgeDocument;
  if (!documentItem) return;
  const payload = {
    title: $("document-title-input").value.trim(),
    description: $("document-description-input").value.trim(),
    tags: csvValues($("document-tags-input").value.replaceAll("，", ",")),
  };
  if (!payload.title) {
    showToast("文档标题不能为空", "warning");
    return;
  }
  $("save-document-btn").disabled = true;
  try {
    const updated = await fetchJson(
      `/knowledge-bases/${encodeURIComponent(documentItem.knowledge_base_id)}/documents/${encodeURIComponent(documentItem.id)}`,
      { method: "PATCH", body: JSON.stringify(payload) },
    );
    state.activeKnowledgeDocument = updated;
    renderDocumentDrawer(updated);
    await Promise.all([listKnowledgeDocuments(), listKnowledgeBases({ preserveSelection: documentItem.knowledge_base_id })]);
    showToast("文档元数据已保存");
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    $("save-document-btn").disabled = false;
  }
}

async function replaceKnowledgeDocument() {
  const documentItem = state.activeKnowledgeDocument;
  const file = $("replace-document-file").files?.[0];
  if (!documentItem || !file) {
    showToast("请先选择替换文件", "warning");
    return;
  }
  const form = new FormData();
  form.append("file", file, file.name);
  $("replace-document-btn").disabled = true;
  $("replace-document-btn").textContent = "重建索引中…";
  try {
    const response = await fetchJson(
      `/knowledge-bases/${encodeURIComponent(documentItem.knowledge_base_id)}/documents/${encodeURIComponent(documentItem.id)}/content`,
      { method: "PUT", body: form },
    );
    state.activeKnowledgeDocument = response.document;
    renderDocumentDrawer(response.document);
    await Promise.all([listKnowledgeDocuments(), listKnowledgeBases({ preserveSelection: documentItem.knowledge_base_id })]);
    showToast("文件已替换，新索引可用");
  } catch (error) {
    showToast(`替换失败，继续使用旧内容：${humanizeError(error)}`, "error");
    try {
      const latest = await fetchJson(
        `/knowledge-bases/${encodeURIComponent(documentItem.knowledge_base_id)}/documents/${encodeURIComponent(documentItem.id)}`,
      );
      state.activeKnowledgeDocument = latest;
      renderDocumentDrawer(latest);
    } catch {
      // Keep the existing drawer content if status refresh also fails.
    }
  } finally {
    $("replace-document-btn").disabled = false;
    $("replace-document-btn").textContent = "替换并重建索引";
  }
}

async function deleteKnowledgeDocument(documentItem = state.activeKnowledgeDocument) {
  if (!documentItem || !window.confirm(`删除文档“${documentItem.title}”？其分块和向量也会被移除。`)) return;
  try {
    await fetchJson(
      `/knowledge-bases/${encodeURIComponent(documentItem.knowledge_base_id)}/documents/${encodeURIComponent(documentItem.id)}`,
      { method: "DELETE" },
    );
    closeDocumentDrawer();
    await Promise.all([listKnowledgeDocuments(), listKnowledgeBases({ preserveSelection: documentItem.knowledge_base_id })]);
    showToast("文档已删除");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function bulkDeleteKnowledgeDocuments() {
  const ids = [...state.selectedDocumentIds];
  if (!ids.length) return;
  const names = state.knowledgeDocuments
    .filter((item) => state.selectedDocumentIds.has(item.id))
    .map((item) => item.title);
  if (!window.confirm(`删除已选 ${ids.length} 个文档？\n${names.join("、")}`)) return;
  const knowledgeBaseId = $("kb-id-input").value;
  $("bulk-delete-documents-btn").disabled = true;
  try {
    const response = await fetchJson(
      `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/bulk-delete`,
      { method: "POST", body: JSON.stringify({ document_ids: ids }) },
    );
    state.selectedDocumentIds.clear();
    await Promise.all([listKnowledgeDocuments(), listKnowledgeBases({ preserveSelection: knowledgeBaseId })]);
    if (response.failures?.length) {
      showToast(`已删除 ${response.deleted_ids.length} 个，${response.failures.length} 个失败`, "warning");
    } else {
      showToast(`已删除 ${response.deleted_ids.length} 个文档`);
    }
    setRaw(response);
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    renderKnowledgeDocuments();
  }
}

function activeWorkspaceId() {
  return state.activeWorkspaceId;
}

function requireActiveWorkspace() {
  const workspace = currentWorkspace();
  if (!workspace) {
    throw new Error("请先选择工作区");
  }
  if (!workspaceIsReady(workspace)) {
    throw new Error("当前工作区路径不可用，请切换或更新登记");
  }
  return workspace.id;
}

function memoryPayload() {
  return {
    kind: $("memory-kind-input").value,
    title: $("memory-title-input").value.trim(),
    content: $("memory-content-input").value.trim(),
    importance: numberValue("memory-importance-input", 3),
    expires_at: null,
  };
}

function selectedProjectMemory() {
  return state.projectMemories.find((item) => item.id === state.selectedMemoryId) || null;
}

function clearMemorySelection() {
  state.selectedMemoryId = "";
  $("memory-selection-label").textContent = "新记忆";
  $("memory-kind-input").value = "architecture_fact";
  $("memory-importance-input").value = "3";
  $("memory-title-input").value = "";
  $("memory-content-input").value = "";
  $("memory-evidence").innerHTML =
    '<div class="empty-state">选择记忆后查看置信度、冲突状态和来源证据。</div>';
  renderProjectMemories();
}

function selectProjectMemory(memory) {
  if (!memory) {
    clearMemorySelection();
    return;
  }
  state.selectedMemoryId = memory.id;
  $("memory-selection-label").textContent = `${memory.status} · v${memory.version}`;
  $("memory-kind-input").value = memory.kind;
  $("memory-importance-input").value = memory.importance;
  $("memory-title-input").value = memory.title;
  $("memory-content-input").value = memory.content;
  const evidence = memory.evidence || [];
  $("memory-evidence").innerHTML = `
    <div class="citation-card">
      <strong>置信度 ${(Number(memory.confidence || 0) * 100).toFixed(0)}% · ${escapeHtml(memory.conflict ? "存在冲突" : "无冲突")}</strong>
      <small>最后确认：${escapeHtml(formatDate(memory.last_confirmed_at))} · 访问 ${escapeHtml(memory.access_count || 0)} 次</small>
    </div>
    ${
      evidence.length
        ? evidence
            .map(
              (item) => `
                <div class="citation-card">
                  <strong>${escapeHtml(item.source_kind)} · ${escapeHtml(item.source_id)}</strong>
                  <small>${escapeHtml(item.path || item.excerpt || "已记录来源")}</small>
                </div>
              `,
            )
            .join("")
        : '<div class="empty-state">列表响应未携带证据；重新打开详情可查看完整来源。</div>'
    }
  `;
  renderProjectMemories();
}

function renderProjectMemories() {
  const list = $("memory-list");
  $("memory-count").textContent = state.projectMemories.length;
  if (!state.projectMemories.length) {
    list.innerHTML = '<div class="empty-state">当前 revision 暂无项目记忆。</div>';
    return;
  }
  list.innerHTML = state.projectMemories
    .map(
      (item) => `
        <button
          class="session-row${item.id === state.selectedMemoryId ? " active" : ""}"
          type="button"
          data-memory-id="${escapeHtml(item.id)}"
        >
          <span>
            <strong>${escapeHtml(item.title)}</strong>
            <small>${escapeHtml(item.kind)} · ${escapeHtml(item.status)} · v${escapeHtml(item.version)}</small>
          </span>
          <small>${escapeHtml(truncate(item.content, 100))}</small>
        </button>
      `,
    )
    .join("");
}

function renderMemoryJobs(jobs) {
  const list = $("memory-job-list");
  if (!jobs.length) {
    list.innerHTML = '<div class="empty-state">暂无提炼任务</div>';
    return;
  }
  list.innerHTML = jobs
    .map(
      (job) => `
        <div class="request-item ${job.status === "failed" ? "error" : "ok"}">
          <span><strong>${escapeHtml(job.source_type)}</strong><small>${escapeHtml(job.source_id)}</small></span>
          <span class="request-status">${escapeHtml(job.status)} · 尝试 ${escapeHtml(job.attempts)} · ${escapeHtml(job.candidate_count)} 条</span>
        </div>
      `,
    )
    .join("");
}

async function loadMemorySettings() {
  const workspaceId = requireActiveWorkspace();
  const body = await fetchJson(
    `/workspaces/${encodeURIComponent(workspaceId)}/memory-settings`,
  );
  $("memory-mode-input").value = body.mode;
  $("memory-status").textContent = `${workspaceId} · ${body.mode}`;
  return body;
}

async function listProjectMemories() {
  if (!workspaceIsReady(currentWorkspace())) {
    state.projectMemories = [];
    state.selectedMemoryId = "";
    renderProjectMemories();
    renderMemoryJobs([]);
    return [];
  }
  const workspaceId = requireActiveWorkspace();
  const params = new URLSearchParams();
  if ($("memory-status-filter").value) {
    params.set("status", $("memory-status-filter").value);
  }
  if ($("memory-kind-filter").value) {
    params.set("kind", $("memory-kind-filter").value);
  }
  const suffix = params.toString() ? `?${params}` : "";
  const [memoryBody, jobBody] = await Promise.all([
    fetchJson(`/workspaces/${encodeURIComponent(workspaceId)}/memories${suffix}`),
    fetchJson(`/workspaces/${encodeURIComponent(workspaceId)}/memory-jobs?limit=20`),
  ]);
  state.projectMemories = memoryBody.memories || [];
  if (!state.projectMemories.some((item) => item.id === state.selectedMemoryId)) {
    state.selectedMemoryId = "";
  }
  renderProjectMemories();
  renderMemoryJobs(jobBody.jobs || []);
  return state.projectMemories;
}

async function refreshProjectMemory() {
  if (!workspaceIsReady(currentWorkspace())) {
    state.projectMemories = [];
    state.selectedMemoryId = "";
    renderProjectMemories();
    renderMemoryJobs([]);
    $("memory-status").textContent = "请先选择可用工作区";
    return;
  }
  try {
    await Promise.all([loadMemorySettings(), listProjectMemories()]);
  } catch (error) {
    $("memory-status").textContent = "加载失败";
    showToast(humanizeError(error), "error");
  }
}

async function saveMemoryMode() {
  try {
    const workspaceId = requireActiveWorkspace();
    const body = await fetchJson(
      `/workspaces/${encodeURIComponent(workspaceId)}/memory-settings`,
      {
        method: "PATCH",
        body: JSON.stringify({ mode: $("memory-mode-input").value }),
      },
    );
    $("memory-status").textContent = `${workspaceId} · ${body.mode}`;
    showToast("项目记忆模式已更新");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function createProjectMemory() {
  const payload = memoryPayload();
  if (!payload.title || !payload.content) {
    showToast("记忆标题和内容不能为空", "warning");
    return;
  }
  try {
    const workspaceId = requireActiveWorkspace();
    const body = await fetchJson(
      `/workspaces/${encodeURIComponent(workspaceId)}/memories`,
      { method: "POST", body: JSON.stringify(payload) },
    );
    await listProjectMemories();
    selectProjectMemory(body);
    showToast("项目记忆已创建");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function updateProjectMemory() {
  const current = selectedProjectMemory();
  if (!current) {
    showToast("请先选择一条记忆", "warning");
    return;
  }
  try {
    const workspaceId = requireActiveWorkspace();
    const body = await fetchJson(
      `/workspaces/${encodeURIComponent(workspaceId)}/memories/${encodeURIComponent(current.id)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ ...memoryPayload(), version: current.version }),
      },
    );
    await listProjectMemories();
    selectProjectMemory(body);
    showToast("项目记忆已更新");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function transitionProjectMemory(action) {
  const current = selectedProjectMemory();
  if (!current) {
    showToast("请先选择一条记忆", "warning");
    return;
  }
  try {
    const workspaceId = requireActiveWorkspace();
    const body = await fetchJson(
      `/workspaces/${encodeURIComponent(workspaceId)}/memories/${encodeURIComponent(current.id)}/${action}`,
      {
        method: "POST",
        body: JSON.stringify({ version: current.version }),
      },
    );
    await listProjectMemories();
    selectProjectMemory(body);
    showToast(action === "confirm" ? "记忆已确认生效" : "记忆已拒绝");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function forgetProjectMemory() {
  const current = selectedProjectMemory();
  if (!current) {
    showToast("请先选择一条记忆", "warning");
    return;
  }
  if (!window.confirm("遗忘这条项目记忆？来源会话仍会保留。")) {
    return;
  }
  try {
    const workspaceId = requireActiveWorkspace();
    await fetchJson(
      `/workspaces/${encodeURIComponent(workspaceId)}/memories/${encodeURIComponent(current.id)}`,
      { method: "DELETE" },
    );
    clearMemorySelection();
    await listProjectMemories();
    showToast("项目记忆已遗忘");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function reindexProjectMemories() {
  try {
    const workspaceId = requireActiveWorkspace();
    const body = await fetchJson(
      `/workspaces/${encodeURIComponent(workspaceId)}/memories/reindex`,
      { method: "POST", body: "{}" },
    );
    showToast(`已提交 ${body.queued_count || 0} 条索引事件`);
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

function workspaceName(workspace) {
  return workspace.root_path.split(/[\\/]/).filter(Boolean).at(-1) || workspace.id;
}

function renderWorkspaceManager() {
  const list = $("workspace-manager-list");
  if (!list) {
    return;
  }
  const activeId = state.activeWorkspaceId;
  const defaultId = state.defaultWorkspaceId;
  list.innerHTML = "";
  if (!state.workspaces.length) {
    list.innerHTML = `
      <div class="workspace-manager-empty">
        ${iconMarkup("folder")}
        <strong>还没有工作区</strong>
        <p>添加一个代码文件夹，Agent 才能读取项目上下文。</p>
        <button class="button primary" type="button" data-workspace-add>添加第一个文件夹</button>
      </div>
    `;
    return;
  }
  for (const workspace of state.workspaces) {
    const ready = workspaceIsReady(workspace);
    const canUpdate = workspace.can_update !== false;
    const active = workspace.id === activeId;
    const isDefault = workspace.id === defaultId;
    const item = document.createElement("article");
    item.className = `workspace-manager-item${active ? " active" : ""}${ready ? "" : " unavailable"}`;
    item.setAttribute("role", "listitem");
    item.innerHTML = `
      <button
        class="workspace-manager-select"
        type="button"
        data-workspace-select="${escapeHtml(workspace.id)}"
        ${ready ? "" : "disabled"}
        aria-label="切换到 ${escapeHtml(workspaceName(workspace))}"
      >
        <span class="workspace-manager-icon" aria-hidden="true">${iconMarkup("folder")}</span>
        <span class="workspace-manager-copy">
          <strong>${escapeHtml(workspaceName(workspace))}</strong>
          <small title="${escapeHtml(workspace.root_path)}">${escapeHtml(workspace.root_path)}</small>
        </span>
        <span class="workspace-manager-status">
          ${active ? '<span class="workspace-badge current">当前</span>' : ""}
          ${isDefault ? '<span class="workspace-badge">新会话默认</span>' : ""}
          ${ready ? "" : '<span class="workspace-badge warning">无法访问</span>'}
        </span>
      </button>
      <div class="workspace-manager-actions">
        ${!ready && canUpdate ? `<button class="text-button" type="button" data-workspace-relink="${escapeHtml(workspace.id)}">重新选择位置</button>` : ""}
        ${canUpdate ? `<button class="text-button danger-text" type="button" data-workspace-remove="${escapeHtml(workspace.id)}">移除</button>` : ""}
      </div>
    `;
    list.appendChild(item);
  }
}

async function persistWorkspaceSelection(
  workspaceId,
  { setDefault = $("workspace-default-toggle").checked, announce = true } = {},
) {
  const workspace = state.workspaces.find((item) => item.id === workspaceId) || null;
  if (workspaceId && (!workspace || !workspaceIsReady(workspace))) {
    throw new Error("所选工作区当前无法访问，请重新选择文件夹位置");
  }
  const list = $("workspace-manager-list");
  list?.setAttribute("aria-busy", "true");
  try {
    if (state.currentSession) {
      state.currentSession = await fetchJson(
        `/sessions/${encodeURIComponent(state.currentSession.id)}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            configuration: { workspace_id: workspaceId || null },
          }),
        },
      );
      replaceSessionInLists(state.currentSession);
    }
    if (!state.currentSession || setDefault) {
      state.preferences = await fetchJson("/users/me/preferences", {
        method: "PATCH",
        body: JSON.stringify({ default_workspace_id: workspaceId || null }),
      });
      state.defaultWorkspaceId = state.preferences.default_workspace_id || "";
    }
    setActiveWorkspace(workspaceId || "");
    if (state.currentView === "memory") {
      refreshProjectMemory();
    }
    if (announce) {
      showToast(workspace ? `已切换到 ${workspaceName(workspace)}` : "已清除当前工作区");
    }
  } finally {
    list?.removeAttribute("aria-busy");
  }
}

async function registerWorkspace(workspaceId, rootPath) {
  const body = await fetchJson(`/workspaces/${encodeURIComponent(workspaceId)}`, {
    method: "PUT",
    body: JSON.stringify({ root_path: rootPath }),
  });
  setRaw(body);
  await listWorkspaces();
  return body;
}

async function removeWorkspace(workspaceId) {
  const workspace = state.workspaces.find((item) => item.id === workspaceId);
  if (!workspace) {
    return;
  }
  if (!window.confirm(
    `从工作区列表移除“${workspaceName(workspace)}”？\n\n电脑中的文件、历史会话和项目记忆都会保留。`,
  )) {
    return;
  }
  const wasActive = state.activeWorkspaceId === workspaceId;
  const wasDefault = state.defaultWorkspaceId === workspaceId;
  try {
    await fetchJson(`/workspaces/${encodeURIComponent(workspaceId)}`, {
      method: "DELETE",
    });
    if (wasActive) {
      await persistWorkspaceSelection("", { setDefault: wasDefault, announce: false });
    } else if (wasDefault) {
      state.preferences = await fetchJson("/users/me/preferences", {
        method: "PATCH",
        body: JSON.stringify({ default_workspace_id: null }),
      });
      state.defaultWorkspaceId = "";
    }
    delete state.workspaceTokenUsage[workspaceId];
    await listWorkspaces();
    showToast(`已移除 ${workspaceName(workspace)}；本地文件保持不变`);
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

function workspaceIdForPath(path) {
  const existing = state.workspaces.find((workspace) => workspace.root_path === path);
  if (existing) {
    return existing.id;
  }
  const leaf = path.split(/[\\/]/).filter(Boolean).at(-1) || "workspace";
  const normalized = leaf
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^[-_]+|[-_]+$/g, "")
    .slice(0, 110) || "workspace";
  let candidate = normalized;
  let suffix = 2;
  while (state.workspaces.some((workspace) => workspace.id === candidate)) {
    candidate = `${normalized}-${suffix}`;
    suffix += 1;
  }
  return candidate;
}

function renderWorkspaceDirectories(body) {
  state.workspaceDirectoryPath = body.current_path || null;
  state.workspaceDirectoryParentPath = body.parent_path || null;
  const list = $("workspace-directory-list");
  const currentPath = state.workspaceDirectoryPath;
  $("workspace-picker-current-path").textContent = currentPath || "允许的位置";
  $("workspace-picker-up-btn").disabled = !currentPath;
  $("workspace-picker-roots-btn").disabled = !currentPath;
  $("choose-workspace-directory-btn").disabled = !currentPath;
  list.innerHTML = "";

  if (!body.directories || body.directories.length === 0) {
    list.innerHTML = `<div class="empty-state">${
      currentPath ? "这个文件夹中没有可进入的子文件夹。" : "服务端没有配置可访问的文件夹。"
    }</div>`;
    return;
  }

  for (const directory of body.directories) {
    const item = document.createElement("button");
    item.className = "workspace-directory-item";
    item.type = "button";
    item.dataset.directoryPath = directory.path;
    item.innerHTML = `
      ${iconMarkup("folder")}
      <span class="workspace-directory-copy">
        <strong>${escapeHtml(directory.name)}</strong>
        <small>${escapeHtml(directory.path)}</small>
      </span>
      <span class="workspace-directory-arrow" aria-hidden="true">›</span>
    `;
    list.appendChild(item);
  }
}

async function browseWorkspaceDirectories(path = null) {
  const list = $("workspace-directory-list");
  const chooseButton = $("choose-workspace-directory-btn");
  list.setAttribute("aria-busy", "true");
  list.innerHTML = '<div class="empty-state">正在加载文件夹…</div>';
  chooseButton.disabled = true;
  try {
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    let body = await fetchJson(`/workspace-directories${query}`);
    if (!path && body.directories?.length === 1) {
      body = await fetchJson(
        `/workspace-directories?path=${encodeURIComponent(body.directories[0].path)}`,
      );
    }
    renderWorkspaceDirectories(body);
  } finally {
    list.removeAttribute("aria-busy");
  }
}

function configureWorkspacePicker(workspaceId = null) {
  state.workspaceRelinkId = typeof workspaceId === "string" ? workspaceId : null;
  const relinkWorkspace = state.workspaces.find(
    (item) => item.id === state.workspaceRelinkId,
  );
  $("workspace-picker-title").textContent = relinkWorkspace
    ? `重新选择 ${workspaceName(relinkWorkspace)} 的位置`
    : "添加工作区文件夹";
  $("workspace-picker-description").textContent = relinkWorkspace
    ? "选择新的文件夹位置；历史会话和项目记忆仍与这个工作区关联。"
    : "系统窗口不可用时，可在这里选择服务允许访问的项目文件夹。";
  $("choose-workspace-directory-btn").textContent = relinkWorkspace
    ? "使用此位置"
    : "添加此文件夹";
  const activeWorkspace = currentWorkspace();
  const currentValue = workspaceIsReady(relinkWorkspace)
    ? relinkWorkspace.root_path
    : workspaceIsReady(activeWorkspace)
      ? activeWorkspace.root_path
      : "";
  return { currentValue, relinkWorkspace };
}

async function openWorkspaceBrowserPicker(workspaceId = null) {
  const { currentValue } = configureWorkspacePicker(workspaceId);
  const dialog = $("workspace-picker-dialog");
  if (!dialog.open) {
    dialog.showModal();
  }
  try {
    await browseWorkspaceDirectories(currentValue || null);
  } catch (error) {
    if (currentValue) {
      showToast("当前路径不可浏览，已显示允许的位置", "warning");
      try {
        await browseWorkspaceDirectories();
        return;
      } catch (fallbackError) {
        error = fallbackError;
      }
    }
    $("workspace-directory-list").innerHTML =
      `<div class="empty-state">${escapeHtml(humanizeError(error))}</div>`;
    showToast(humanizeError(error), "error");
  }
}

async function applyWorkspaceDirectory(path) {
  const relinking = Boolean(state.workspaceRelinkId);
  const workspaceId = state.workspaceRelinkId || workspaceIdForPath(path);
  const workspace = await registerWorkspace(workspaceId, path);
  await persistWorkspaceSelection(workspace.id, { announce: false });
  closeWorkspacePicker();
  showToast(`${relinking ? "已重新关联" : "已添加"} ${workspaceName(workspace)}`);
}

async function openWorkspacePicker(workspaceId = null, triggerButton = null) {
  const { currentValue } = configureWorkspacePicker(workspaceId);
  if (triggerButton) {
    triggerButton.disabled = true;
    triggerButton.setAttribute("aria-busy", "true");
  }
  try {
    const body = await fetchJson("/workspace-directory-picker", {
      method: "POST",
      body: JSON.stringify({ initial_path: currentValue || null }),
    });
    if (body.cancelled || !body.path) {
      state.workspaceRelinkId = null;
      return;
    }
    await applyWorkspaceDirectory(body.path);
  } catch (error) {
    if (error.status === 501 || error.status === 503) {
      showToast("系统文件夹窗口不可用，已打开备用选择器", "warning");
      await openWorkspaceBrowserPicker(workspaceId);
    } else {
      state.workspaceRelinkId = null;
      showToast(humanizeError(error), "error");
    }
  } finally {
    if (triggerButton) {
      triggerButton.disabled = false;
      triggerButton.removeAttribute("aria-busy");
    }
  }
}

async function chooseWorkspaceDirectory() {
  const path = state.workspaceDirectoryPath;
  if (!path) {
    return;
  }
  const chooseButton = $("choose-workspace-directory-btn");
  chooseButton.disabled = true;
  chooseButton.setAttribute("aria-busy", "true");
  try {
    await applyWorkspaceDirectory(path);
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    chooseButton.disabled = false;
    chooseButton.removeAttribute("aria-busy");
  }
}

async function listWorkspaces() {
  const body = await fetchJson("/workspaces");
  state.workspaces = body.workspaces || [];
  state.workspacesLoaded = true;
  const preferredId = state.activeWorkspaceId
    || state.currentSession?.workspace_id
    || state.defaultWorkspaceId;
  setActiveWorkspace(
    state.workspaces.some((item) => item.id === preferredId) ? preferredId : "",
  );
  renderWorkspaceManager();
  updateContextSummary();
  await loadWorkspaceTokenUsage();
  renderWorkspaceTokenUsage();
}

function renderWorkspaceCatalog() {
  const list = $("workspace-catalog-list");
  if (!list) {
    return;
  }
  if (!state.workspaces.length) {
    list.innerHTML = '<div class="empty-state compact">暂无已登记工作区，请在下方登记代码目录。</div>';
    return;
  }
  list.innerHTML = state.workspaces.map((workspace) => {
    const ready = workspaceIsReady(workspace);
    const current = workspace.id === state.activeWorkspaceId;
    const isDefault = workspace.id === state.defaultWorkspaceId;
    return `
      <article class="workspace-catalog-card ${current ? "is-current" : ""} ${ready ? "" : "is-unavailable"}" data-workspace-id="${escapeHtml(workspace.id)}">
        <span class="workspace-status-dot ${ready ? "is-ready" : "is-unavailable"}" aria-hidden="true"></span>
        <div class="workspace-catalog-copy">
          <div><strong>${escapeHtml(workspace.id)}</strong>${current ? "<span>当前</span>" : ""}${isDefault ? "<span>默认</span>" : ""}</div>
          <code>${escapeHtml(workspace.root_path)}</code>
          <small>${escapeHtml(workspaceRoleLabel(workspace.role))} · ${ready ? "目录可访问" : "目录当前不可访问"}</small>
        </div>
        <div class="workspace-catalog-actions">
          <button class="text-button" type="button" data-workspace-action="select" ${ready && !current ? "" : "disabled"}>${current ? "使用中" : "设为当前"}</button>
          <button class="text-button" type="button" data-workspace-action="edit" ${workspace.can_update ? "" : "disabled"}>编辑登记</button>
        </div>
      </article>`;
  }).join("");
}

async function loadWorkspaceTokenUsage(
  workspaceIds = state.workspaces.map((item) => item.id),
) {
  const results = await Promise.allSettled(
    workspaceIds.map(async (workspaceId) => ({
      workspaceId,
      usage: await fetchJson(
        `/workspaces/${encodeURIComponent(workspaceId)}/token-usage`,
      ),
    })),
  );
  for (const [index, result] of results.entries()) {
    const workspaceId = workspaceIds[index];
    if (result.status === "fulfilled") {
      state.workspaceTokenUsage[workspaceId] = result.value.usage;
      delete state.workspaceTokenUsageErrors[workspaceId];
    } else {
      state.workspaceTokenUsageErrors[workspaceId] = humanizeError(result.reason);
    }
  }
  return results;
}

function renderWorkspaceTokenUsage() {
  const list = $("workspace-token-list");
  if (!list) {
    return;
  }
  list.innerHTML = "";
  if (state.workspaces.length === 0) {
    list.innerHTML = '<div class="empty-state">暂无已注册 Workspace。</div>';
    return;
  }
  for (const workspace of state.workspaces) {
    const usage = state.workspaceTokenUsage[workspace.id];
    const usageError = state.workspaceTokenUsageErrors[workspace.id];
    const workspaceBudget = usage?.budget?.workspace;
    const operationSummary = (usage?.operations || [])
      .map(
        (item) =>
          `${formatUsageOperation(item.operation)} ${formatTokenCount(item.total_tokens)}`,
      )
      .join(" · ");
    const item = document.createElement("article");
    item.className = "workspace-token-card";
    item.innerHTML = `
      <div class="workspace-token-heading">
        <div>
          <strong>${escapeHtml(workspace.id)}</strong>
          <small>${escapeHtml(workspace.root_path)}</small>
        </div>
        <span>${usageError ? "用量不可用" : escapeHtml(formatTokenCount(usage?.total_tokens || 0))}</span>
      </div>
      <div class="workspace-token-metrics">
        <small>输入 <strong>${escapeHtml(formatTokenCount(usage?.input_tokens || 0))}</strong></small>
        <small>输出 <strong>${escapeHtml(formatTokenCount(usage?.output_tokens || 0))}</strong></small>
        <small>思考 <strong>${escapeHtml(formatTokenCount(usage?.thoughts_tokens || 0))}</strong></small>
        <small>会话 <strong>${escapeHtml(usage?.conversation_count || 0)}</strong></small>
      </div>
      <div class="workspace-budget-line ${workspaceBudget?.exceeded ? "is-exceeded" : ""}">
        <small>预算 ${
          workspaceBudget?.limit
            ? `${escapeHtml(formatTokenCount(workspaceBudget.used))} / ${escapeHtml(formatTokenCount(workspaceBudget.limit))}`
            : "未启用"
        } · ${escapeHtml(usage?.budget?.action || "reject")}</small>
        ${operationSummary ? `<small>${escapeHtml(operationSummary)}</small>` : ""}
        ${usageError ? `<small class="workspace-usage-error">${escapeHtml(usageError)}</small>` : ""}
      </div>
    `;
    list.appendChild(item);
  }
}

function formatUsageOperation(operation) {
  const labels = {
    agent: "Agent",
    chat: "Chat",
    conversation_compression: "对话压缩",
    embedding: "Embedding",
    llm: "后台 LLM",
    rag_ask: "RAG Ask",
  };
  return labels[operation] || operation || "未知";
}

function formatInputCountMethod(method) {
  const labels = {
    anthropic_messages_count_tokens: "Anthropic 精确计数",
    fake_lexical_tokenizer: "本地确定性计数",
    gemini_models_count_tokens: "Gemini 精确计数",
    openai_responses_input_tokens: "OpenAI 精确计数",
    provider_usage: "Provider Usage",
  };
  return labels[method] || method || "未知计数方式";
}

async function refreshTokenUsageData() {
  const tasks = [];
  if (state.conversationId) {
    tasks.push(loadSessionTokenUsage([state.conversationId]));
  }
  if (state.workspaces.length) {
    tasks.push(loadWorkspaceTokenUsage());
  }
  await Promise.allSettled(tasks);
  renderWorkspaceTokenUsage();
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
  $("close-settings-btn").addEventListener("click", closeSettings);
  $("settings-dialog").addEventListener("click", (event) => {
    if (event.target === $("settings-dialog")) {
      closeSettings();
    }
  });
  $("open-workspace-picker-btn").addEventListener("click", (event) => {
    openWorkspacePicker(null, event.currentTarget);
  });
  $("agent-workspace-settings-btn").addEventListener("click", openSettings);
  $("close-workspace-picker-btn").addEventListener("click", closeWorkspacePicker);
  $("workspace-picker-dialog").addEventListener("click", (event) => {
    if (event.target === $("workspace-picker-dialog")) {
      closeWorkspacePicker();
    }
  });
  $("workspace-directory-list").addEventListener("click", (event) => {
    const directory = event.target.closest("[data-directory-path]");
    if (!directory) {
      return;
    }
    browseWorkspaceDirectories(directory.dataset.directoryPath)
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  $("workspace-picker-up-btn").addEventListener("click", () => {
    browseWorkspaceDirectories(state.workspaceDirectoryParentPath)
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  $("workspace-picker-roots-btn").addEventListener("click", () => {
    browseWorkspaceDirectories()
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  $("choose-workspace-directory-btn").addEventListener(
    "click",
    chooseWorkspaceDirectory,
  );
  $("workspace-manager-list").addEventListener("click", async (event) => {
    const add = event.target.closest("[data-workspace-add]");
    const select = event.target.closest("[data-workspace-select]");
    const relink = event.target.closest("[data-workspace-relink]");
    const remove = event.target.closest("[data-workspace-remove]");
    if (add) {
      await openWorkspacePicker(null, add);
      return;
    }
    if (relink) {
      await openWorkspacePicker(relink.dataset.workspaceRelink, relink);
      return;
    }
    if (remove) {
      await removeWorkspace(remove.dataset.workspaceRemove);
      return;
    }
    if (select) {
      try {
        await persistWorkspaceSelection(select.dataset.workspaceSelect);
      } catch (error) {
        showToast(humanizeError(error), "error");
      }
    }
  });

  $("toggle-inspector-btn").addEventListener("click", () => {
    setInspectorVisible(document.body.classList.contains("inspector-hidden"));
  });
  $("close-inspector-btn").addEventListener("click", () => setInspectorVisible(false));
  $("trace-tab").addEventListener("click", () => selectInspectorTab("trace"));
  $("raw-tab").addEventListener("click", () => selectInspectorTab("raw"));

  const createNewSession = () => {
    if (canSwitchSession()) {
      createSession().catch(() => {});
    }
  };
  $("create-session-btn").addEventListener("click", createNewSession);
  $("sessions-create-btn").addEventListener("click", createNewSession);
  $("view-all-sessions-btn").addEventListener("click", () => {
    switchView("sessions");
    listSessions(false).catch((error) => showToast(humanizeError(error), "error"));
  });
  $("list-sessions-btn").addEventListener("click", async () => {
    try {
      await listSessions();
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });
  $("session-filter-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await listSessions(true, {
        archived: $("session-state-filter").value === "archived",
        query: $("session-search-input").value.trim(),
      });
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });
  $("load-more-sessions-btn").addEventListener("click", async () => {
    try {
      await listSessions(true, { append: true });
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
    const action = event.target.closest("[data-session-action]")?.dataset.sessionAction;
    if (!row || !action || !canSwitchSession()) {
      return;
    }
    try {
      await handleSessionAction(row.dataset.sessionId, action);
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });
  $("recent-sessions-list").addEventListener("click", async (event) => {
    const row = event.target.closest("[data-session-id]");
    if (!row || !canSwitchSession()) {
      return;
    }
    try {
      await loadSession(true, row.dataset.sessionId);
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });
  $("restore-current-session-btn").addEventListener("click", async () => {
    if (!state.currentSession) {
      return;
    }
    try {
      await updateSessionMetadata(state.currentSession.id, { archived: false });
      await loadSession(true, state.currentSession.id);
      showToast("会话已恢复，可以继续对话");
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });

  $("send-chat-btn").addEventListener("click", submitComposerMessage);
  $("stop-chat-btn").addEventListener("click", stopChat);
  $("composer-mode-input").addEventListener("change", (event) => {
    persistComposerMode(event.target.value);
  });
  $("auto-model-toggle").addEventListener("change", async (event) => {
    state.modelPreference.mode = event.target.checked ? "auto" : "manual";
    renderSessionModelControls();
    try {
      await saveModelPreference();
    } catch {
      await loadModelPreference().catch(() => {});
    }
  });
  $("session-model-select").addEventListener("change", () => {
    saveModelPreference().catch(() => loadModelPreference().catch(() => {}));
  });
  $("model-picker-trigger").addEventListener("click", toggleModelPicker);
  $("model-picker-trigger").addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if ($("model-picker-menu").hidden) toggleModelPicker();
      $("model-picker-menu").querySelector('[role="option"]')?.focus();
    }
  });
  $("model-picker-menu").addEventListener("click", (event) => {
    const option = event.target.closest("[data-model-option]");
    if (!option) return;
    state.modelPreference.preferred_model_id = option.dataset.modelOption;
    $("session-model-select").value = option.dataset.modelOption;
    renderSessionModelControls();
    saveModelPreference().catch(() => loadModelPreference().catch(() => {}));
  });
  $("model-picker-menu").addEventListener("keydown", (event) => {
    const options = [...$("model-picker-menu").querySelectorAll('[role="option"]')];
    const current = options.indexOf(document.activeElement);
    if (event.key === "Escape") {
      event.preventDefault();
      closeModelPicker({ restoreFocus: true });
    } else if (event.key === "ArrowDown" && options.length) {
      event.preventDefault();
      options[(current + 1) % options.length].focus();
    } else if (event.key === "ArrowUp" && options.length) {
      event.preventDefault();
      options[(current - 1 + options.length) % options.length].focus();
    }
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".model-choice-control")) closeModelPicker();
  });
  $("model-fallback-toggle").addEventListener("change", () => {
    saveModelPreference().catch(() => loadModelPreference().catch(() => {}));
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
  $("pause-run-btn").addEventListener("click", () => controlRun("pause"));
  $("cancel-run-btn").addEventListener("click", () => controlRun("cancel"));
  $("steer-run-btn").addEventListener("click", () => controlRun("steer"));
  $("continue-run-btn").addEventListener("click", () => controlRun("continue"));
  $("apply-change-set-btn").addEventListener("click", applyCurrentChangeSet);
  $("reject-change-set-btn").addEventListener("click", rejectCurrentChangeSet);

  $("ingest-doc-btn").addEventListener("click", ingestDocument);
  $("document-files-input").addEventListener("change", renderSelectedDocumentFiles);
  $("new-knowledge-base-btn").addEventListener("click", beginCreateKnowledgeBase);
  $("knowledge-base-search-input").addEventListener("input", renderKnowledgeBases);
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
  $("mobile-knowledge-base-select").addEventListener("change", (event) => {
    selectKnowledgeBase(
      state.knowledgeBases.find((item) => item.id === event.target.value),
    );
  });
  document.querySelectorAll("[data-rag-tab]").forEach((tab) => {
    tab.addEventListener("click", () => setKnowledgeTab(tab.dataset.ragTab));
    tab.addEventListener("keydown", (event) => {
      if (!new Set(["ArrowLeft", "ArrowRight", "Home", "End"]).has(event.key)) return;
      const tabs = [...document.querySelectorAll("[data-rag-tab]:not(:disabled)")];
      const current = tabs.indexOf(event.currentTarget);
      const target = event.key === "Home"
        ? tabs[0]
        : event.key === "End"
          ? tabs.at(-1)
          : tabs[(current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
      if (target) {
        event.preventDefault();
        setKnowledgeTab(target.dataset.ragTab, { focus: true });
      }
    });
  });
  let documentSearchTimer = null;
  $("document-search-input").addEventListener("input", () => {
    window.clearTimeout(documentSearchTimer);
    documentSearchTimer = window.setTimeout(() => {
      state.knowledgeDocumentPage = 1;
      listKnowledgeDocuments().catch((error) => showToast(humanizeError(error), "error"));
    }, 240);
  });
  ["document-status-filter", "document-sort-input"].forEach((id) => {
    $(id).addEventListener("change", () => {
      state.knowledgeDocumentPage = 1;
      listKnowledgeDocuments().catch((error) => showToast(humanizeError(error), "error"));
    });
  });
  $("previous-document-page").addEventListener("click", () => {
    if (state.knowledgeDocumentPage <= 1) return;
    state.knowledgeDocumentPage -= 1;
    listKnowledgeDocuments().catch((error) => showToast(humanizeError(error), "error"));
  });
  $("next-document-page").addEventListener("click", () => {
    state.knowledgeDocumentPage += 1;
    listKnowledgeDocuments().catch((error) => showToast(humanizeError(error), "error"));
  });
  $("knowledge-document-rows").addEventListener("click", (event) => {
    const openButton = event.target.closest("[data-open-document]");
    if (!openButton) return;
    openDocumentDrawer(openButton.dataset.openDocument, openButton)
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  $("knowledge-document-rows").addEventListener("change", (event) => {
    const checkbox = event.target.closest(".document-select");
    const row = checkbox?.closest("[data-document-id]");
    if (!checkbox || !row) return;
    if (checkbox.checked) state.selectedDocumentIds.add(row.dataset.documentId);
    else state.selectedDocumentIds.delete(row.dataset.documentId);
    renderKnowledgeDocuments();
  });
  $("select-all-documents").addEventListener("change", (event) => {
    for (const documentItem of state.knowledgeDocuments) {
      if (event.target.checked) state.selectedDocumentIds.add(documentItem.id);
      else state.selectedDocumentIds.delete(documentItem.id);
    }
    renderKnowledgeDocuments();
  });
  $("bulk-delete-documents-btn").addEventListener("click", bulkDeleteKnowledgeDocuments);
  $("close-document-drawer").addEventListener("click", () => closeDocumentDrawer());
  $("document-drawer-backdrop").addEventListener("click", () => closeDocumentDrawer());
  $("save-document-btn").addEventListener("click", saveKnowledgeDocument);
  $("replace-document-btn").addEventListener("click", replaceKnowledgeDocument);
  $("delete-document-btn").addEventListener("click", () => deleteKnowledgeDocument());
  $("document-drawer").addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeDocumentDrawer();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...$("document-drawer").querySelectorAll(
      'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])',
    )].filter((item) => !item.hidden);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  $("search-rag-btn").addEventListener("click", searchRag);
  $("ask-rag-btn").addEventListener("click", askRag);
  $("rag-rerank-toggle").addEventListener("click", toggleRerank);
  $("refresh-memory-btn").addEventListener("click", refreshProjectMemory);
  $("save-memory-mode-btn").addEventListener("click", saveMemoryMode);
  $("reindex-memory-btn").addEventListener("click", reindexProjectMemories);
  $("create-memory-btn").addEventListener("click", createProjectMemory);
  $("update-memory-btn").addEventListener("click", updateProjectMemory);
  $("confirm-memory-btn").addEventListener("click", () =>
    transitionProjectMemory("confirm"),
  );
  $("reject-memory-btn").addEventListener("click", () =>
    transitionProjectMemory("reject"),
  );
  $("delete-memory-btn").addEventListener("click", forgetProjectMemory);
  $("memory-status-filter").addEventListener("change", listProjectMemories);
  $("memory-kind-filter").addEventListener("change", listProjectMemories);
  $("memory-list").addEventListener("click", async (event) => {
    const row = event.target.closest("[data-memory-id]");
    if (!row) {
      return;
    }
    try {
      const memory = await fetchJson(
        `/workspaces/${encodeURIComponent(requireActiveWorkspace())}/memories/${encodeURIComponent(row.dataset.memoryId)}`,
      );
      selectProjectMemory(memory);
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });

  $("refresh-model-registry-btn").addEventListener("click", () => {
    loadModelRegistry(true)
      .then(() => showToast("模型状态已刷新"))
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  $("provider-connection-grid").addEventListener("click", (event) => {
    const button = event.target.closest("[data-provider-action]");
    if (button) handleProviderAction(button);
  });
  $("discover-provider-models-btn").addEventListener("click", () => {
    discoverProviderModels()
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  $("registered-model-provider-input").addEventListener("change", (event) => {
    resetRegisteredModelForm();
    const connection = state.modelRegistry.connections.find(
      (item) => item.provider === event.target.value,
    );
    if (connection?.credential_configured && connection.enabled) {
      discoverProviderModels(event.target.value, { showToast: false })
        .catch((error) => showToast(humanizeError(error), "error"));
    } else {
      state.modelDiscovery = { provider: event.target.value, models: [], loading: false };
      renderDiscoveredModels();
    }
  });
  $("discovered-model-select").addEventListener("change", renderDiscoveredModels);
  $("save-registered-model-btn").addEventListener("click", saveRegisteredModel);
  $("registered-model-list").addEventListener("click", (event) => {
    const card = event.target.closest("[data-model-id]");
    const action = event.target.closest("[data-model-action]")?.dataset.modelAction;
    if (!card || !action) return;
    if (action === "toggle-enabled") {
      const model = registeredModel(card.dataset.modelId);
      if (model) updateRegisteredModel(model.id, { enabled: !model.enabled });
    }
    if (action === "toggle-auto") {
      const model = registeredModel(card.dataset.modelId);
      if (model) updateRegisteredModel(model.id, { auto_eligible: !model.auto_eligible });
    }
    if (action === "delete") deleteRegisteredModel(card.dataset.modelId);
  });

  $("refresh-overview-btn").addEventListener("click", async () => {
    await Promise.allSettled([
      checkHealth(),
      listSessions(false),
      listWorkspaces(),
    ]);
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

async function restoreInitialSession() {
  const requestedSessionId = new URL(window.location.href).searchParams.get("session");
  if (requestedSessionId) {
    try {
      await loadSession(false, requestedSessionId);
      return;
    } catch (error) {
      showToast(`URL 中的会话无法加载：${humanizeError(error)}`, "warning");
    }
  }

  const candidates = [
    state.preferences?.last_active_session_id,
    state.recentSessions[0]?.id,
  ].filter(Boolean);
  for (const sessionId of [...new Set(candidates)]) {
    try {
      await loadSession(false, sessionId, { requireActive: true });
      return;
    } catch {
      // Continue through the deterministic recovery order.
    }
  }

  state.currentSession = null;
  state.conversationId = "";
  $("conversation-id-input").value = "";
  updateSessionUrl("");
  if (state.preferences) {
    applyConfigurationToInputs(state.preferences, true);
  }
  resetChatView();
  updateComposerAvailability();
  updateContextSummary();
}

async function init() {
  const preferences = loadUiPreferences();
  $("user-id-input").value = "demo_user";
  bindEvents();
  const requestedView = location.hash.replace("#", "");
  const preferredView = document.querySelector(`[data-view-panel="${preferences.view}"]`)
    ? preferences.view
    : "chat";
  const initialView = document.querySelector(`[data-view-panel="${requestedView}"]`)
    ? requestedView
    : preferredView;
  state.composerMode = "chat";
  state.rerankEnabled = preferences.rerankEnabled === true;
  state.preferredKnowledgeBaseId = preferences.knowledgeBaseId || "";
  state.activeKnowledgeTab = ["documents", "ask", "settings"].includes(preferences.knowledgeTab)
    ? preferences.knowledgeTab
    : "documents";
  setKnowledgeTab(state.activeKnowledgeTab);
  renderRerankControl();
  updateComposerMode(state.composerMode);
  switchView(initialView, !location.hash);
  setInspectorVisible(!preferences.inspectorHidden && window.innerWidth > 1120);
  selectInspectorTab("trace");
  setTrace([]);
  renderRequestLog();
  renderSessions();
  renderRecentSessions();
  renderOverview();
  updateContextSummary();
  await Promise.allSettled([
    checkHealth(),
    listSessions(false),
    loadPreferences(),
    loadModelRegistry(),
    listKnowledgeBases(),
    loadRagCapabilities(),
  ]);
  try {
    await listWorkspaces();
  } catch (error) {
    showToast(`工作区列表加载失败：${humanizeError(error)}`, "error");
  }
  await restoreInitialSession();
}

init();
