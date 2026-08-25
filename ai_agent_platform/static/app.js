const API_BASE = "/api/v1";
const UI_STORAGE_KEY = "ai-agent-platform-ui-v2";
const NATIVE_PICKER_LOCAL_ONLY_DETAIL =
  "native directory picker is only available for local mode";
const FINAL_RUN_STATUSES = new Set(["completed", "partial", "blocked", "cancelled", "failed"]);
const SUSPENDED_RUN_STATUSES = new Set(["waiting_approval", "waiting_input", "paused"]);
const TERMINAL_RUN_STATUSES = new Set([...FINAL_RUN_STATUSES, ...SUSPENDED_RUN_STATUSES]);
const TRACE_STEP_REVEAL_DELAY_MS = 16;
const MAX_TRACE_REPLAY_MS = 1200;
const responseTimers = new WeakMap();
const COMPOSER_BUILTIN_COMMANDS = [
  {
    kind: "builtin",
    command: "agent",
    title: "切换到代码 Agent",
    description: "读取工作区并按权限运行工具。",
    action: "agent",
    keywords: ["code", "代码", "工具"],
  },
  {
    kind: "builtin",
    command: "chat",
    title: "切换到快速对话",
    description: "使用流式对话，不运行代码工具。",
    action: "chat",
    keywords: ["ask", "对话", "快速"],
  },
  {
    kind: "builtin",
    command: "new",
    title: "新建会话",
    description: "保留当前会话并开始一个新上下文。",
    action: "new",
    keywords: ["session", "会话"],
  },
  {
    kind: "builtin",
    command: "tools",
    title: "打开工具管理",
    description: "统一管理全局 Skill 与 MCP Server。",
    action: "tools",
    keywords: ["skill", "mcp", "server", "tool", "工具"],
  },
];
let composerDraftSaveTimer = null;
let conversationFollowFrame = null;

const state = {
  conversationId: "",
  evalCatalogue: null,
  evalHistory: [],
  evalRun: null,
  evalRunId: "",
  evalPollTimer: null,
  auditRuns: [],
  auditRunId: "",
  auditRunBody: null,
  auditEvents: [],
  auditCategory: "all",
  auditPollTimer: null,
  auditRequestGeneration: 0,
  latestRunId: "",
  latestRunStatus: "",
  latestRunConversationId: "",
  latestRunBody: null,
  checkpointHistory: [],
  checkpointRunId: "",
  selectedCheckpointId: "",
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
  projectMemoryRequestGeneration: 0,
  userMemories: [],
  userMemoryScenes: [],
  selectedUserMemoryId: "",
  userMemoryRequestGeneration: 0,
  conversationMemoryHits: [],
  selectedConversationMemoryHit: -1,
  conversationMemoryRequestGeneration: 0,
  activeMemoryTab: "project",
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
  mcpRegistry: {
    runtime_enabled: false,
    config_writable: false,
    servers: [],
  },
  skillRegistry: {
    root: "~/.ai-agent-platform/skills",
    writable: false,
    skills: [],
    diagnostics: [],
  },
  editingSkill: "",
  editingMCPServer: "",
  composerDrafts: {},
  slashCapabilities: {
    conversation_id: "",
    workspace_id: "",
    skill_commands: [],
    mcp_tools: [],
    diagnostics: [],
  },
  slashCapabilityKey: "",
  slashLoading: false,
  slashError: "",
  slashItems: [],
  slashActiveIndex: 0,
  slashQuery: "",
  slashRequestGeneration: 0,
  followConversation: true,
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

function formatWorkDuration(value) {
  const seconds = Math.max(0, Math.floor(Number(value || 0) / 1000));
  if (seconds < 1) {
    return "不到 1 秒";
  }
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes} 分${remainder ? ` ${remainder} 秒` : ""}`;
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
    assemble_context: "装配上下文预算",
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

function traceToolNames(trace, events = []) {
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
  for (const event of events || []) {
    if (!["tool_selected", "tool_started", "tool_result", "tool_error"].includes(event.type)) {
      continue;
    }
    const name = event.output?.name;
    if (name && !names.includes(name)) names.push(name);
  }
  return names;
}

function executionActivityEvents(events) {
  return (events || [])
    .filter((event) => [
      "node_started",
      "reasoning_summary",
      "tool_started",
      "tool_result",
      "tool_error",
      "answer_completed",
    ].includes(event.type))
    .slice(-16);
}

function executionActivityTitle(event) {
  const output = event.output || {};
  if (event.type === "node_started") return `正在${humanizeAgentNode(event.node)}`;
  if (event.type === "reasoning_summary") return "阶段思路";
  if (event.type === "tool_started") return `调用工具 · ${output.name || "未知工具"}`;
  if (event.type === "tool_result") return `工具完成 · ${output.name || "未知工具"}`;
  if (event.type === "tool_error") return `工具失败 · ${output.name || "未知工具"}`;
  if (event.type === "answer_completed") return "回答生成完成";
  return event.type || "实时活动";
}

function executionActivitySummary(event) {
  const output = event.output || {};
  if (["tool_result", "tool_error"].includes(event.type)) {
    const result = output.result ?? output.error ?? event.summary ?? "";
    const rendered = typeof result === "string" ? result : JSON.stringify(result);
    const preview = rendered.length > 280 ? `${rendered.slice(0, 280)}…` : rendered;
    const duration = output.duration_ms !== undefined ? `${output.duration_ms} ms` : "";
    return [duration, preview].filter(Boolean).join(" · ") || event.summary || "工具已返回结果";
  }
  return event.summary || humanizeAgentNode(event.node);
}

function executionProcessPresentation(status) {
  const normalized = status === "done" ? "completed" : status;
  const presentations = {
    completed: { title: "已工作", tone: "status-complete", stepState: "complete" },
    partial: { title: "部分完成", tone: "status-attention", stepState: "attention" },
    blocked: { title: "工作受阻", tone: "status-attention", stepState: "attention" },
    waiting_approval: { title: "等待审批", tone: "status-attention", stepState: "attention" },
    waiting_input: { title: "等待补充", tone: "status-attention", stepState: "attention" },
    paused: { title: "已暂停", tone: "status-attention", stepState: "attention" },
    failed: { title: "工作中断", tone: "status-failed", stepState: "failed" },
    cancelled: { title: "已停止", tone: "status-cancelled", stepState: "cancelled" },
  };
  return presentations[normalized] || {
    title: "正在工作",
    tone: "status-running",
    stepState: "current",
  };
}

function executionStepState(index, length, presentation) {
  return index < length - 1 ? "complete" : presentation.stepState;
}

function executionStepStateLabel(value) {
  return {
    complete: "已完成",
    current: "当前步骤",
    attention: "等待继续",
    failed: "失败",
    cancelled: "已停止",
  }[value] || "执行步骤";
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
          <span class="execution-summary-copy">
            <span class="execution-summary-line">
              <strong class="execution-title">正在工作</strong>
              <span class="execution-duration">不到 1 秒</span>
            </span>
            <span class="execution-summary-text" role="status" aria-live="polite" aria-atomic="true">准备执行…</span>
          </span>
        </span>
        <span class="execution-chevron" aria-hidden="true"></span>
      </summary>
      <div class="execution-body">
        <ol class="execution-steps"></ol>
        <section class="execution-live-events" hidden>
          <span class="execution-live-events-label">实时活动</span>
          <ol></ol>
        </section>
        <p class="execution-live-announcer sr-only" role="status" aria-live="polite" aria-atomic="true"></p>
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
    events = [],
  } = {},
) {
  const details = ensureExecutionProcess(contentNode);
  const terminal = TERMINAL_RUN_STATUSES.has(status) || status === "done";
  const wasTerminal = details.dataset.terminal === "true";
  const presentation = executionProcessPresentation(status);
  const tools = traceToolNames(trace, events);
  const activities = executionActivityEvents(events);
  const steps = trace.length
    ? trace
    : terminal
    ? []
    : [{
        step: 1,
        node: fallbackNode || "model_request",
        summary: fallbackSummary || "正在建立请求并等待响应。",
        output: {},
      }];
  details.classList.remove(
    "status-running",
    "status-complete",
    "status-attention",
    "status-failed",
    "status-cancelled",
  );
  details.classList.add(presentation.tone);
  details.classList.toggle("complete", terminal);
  details.dataset.status = status === "done" ? "completed" : status;
  details.dataset.terminal = String(terminal);
  const displayedElapsedMs = Math.max(
    Number(details.dataset.elapsedMs || 0),
    Number(elapsedMs || 0),
  );
  details.dataset.elapsedMs = String(displayedElapsedMs);
  if (terminal && !wasTerminal) {
    details.open = false;
  } else if (!terminal && wasTerminal) {
    details.open = true;
  }
  details.querySelector(".execution-title").textContent = presentation.title;
  const terminalSummary = steps.length
    ? `${steps.length} 个步骤${tools.length ? ` · ${tools.length} 个工具` : ""}`
    : "没有阶段详情";
  const latestStep = steps.at(-1);
  details.querySelector(".execution-summary-text").textContent = terminal
    ? presentation.stepState === "complete"
      ? terminalSummary
      : `${latestStep ? humanizeAgentNode(latestStep.node) : terminalSummary}${
          steps.length ? ` · ${steps.length} 个步骤` : ""
        }${tools.length ? ` · ${tools.length} 个工具` : ""}`
    : humanizeAgentNode(latestStep?.node);
  details.querySelector(".execution-duration").textContent = formatWorkDuration(displayedElapsedMs);
  details.querySelector(".execution-steps").innerHTML = steps
    .map((step, index) => {
      const stepState = executionStepState(index, steps.length, presentation);
      return `
      <li class="${escapeHtml(stepState)}"${stepState === "current" ? ' aria-current="step"' : ""}>
        <span class="execution-step-marker" aria-hidden="true"></span>
        <div>
          <strong><span class="sr-only">${escapeHtml(index + 1)}，${escapeHtml(
            executionStepStateLabel(stepState),
          )}：</span>${escapeHtml(humanizeAgentNode(step.node))}</strong>
          <p>${escapeHtml(step.summary || "")}</p>
        </div>
      </li>
    `;
    })
    .join("");
  if (!steps.length) {
    details.querySelector(".execution-steps").innerHTML =
      '<li class="execution-step-empty">本次运行没有返回可解释的阶段详情。</li>';
  }
  const activityList = details.querySelector(".execution-live-events");
  activityList.hidden = activities.length === 0;
  activityList.querySelector("ol").innerHTML = activities.map((event) => `
    <li class="${escapeHtml(event.type === "tool_error" ? "error" : "")}">
      <span class="execution-live-marker" aria-hidden="true"></span>
      <div>
        <strong>${escapeHtml(executionActivityTitle(event))}</strong>
        <p>${escapeHtml(executionActivitySummary(event))}</p>
      </div>
    </li>
  `).join("");
  const latestActivity = activities.at(-1);
  const announcer = details.querySelector(".execution-live-announcer");
  const activitySequence = String(latestActivity?.sequence || "");
  if (announcer.dataset.sequence !== activitySequence) {
    announcer.dataset.sequence = activitySequence;
    announcer.textContent = latestActivity
      ? `${executionActivityTitle(latestActivity)}：${executionActivitySummary(latestActivity)}`
      : "";
  }
  const toolList = details.querySelector(".execution-tools");
  toolList.hidden = tools.length === 0;
  toolList.innerHTML = tools.length
    ? `<span class="execution-tools-label"><svg class="app-icon" aria-hidden="true"><use href="#icon-network"></use></svg><span>相关工具</span></span><span class="execution-tool-list">${tools
        .map((name) => `<code>${escapeHtml(name)}</code>`)
        .join("")}</span>`
    : "";
  return details;
}

function startResponseTimer(contentNode, startedAt) {
  stopResponseTimer(contentNode);
  const timer = window.setInterval(() => {
    const duration = contentNode.closest(".chat-bubble")
      ?.querySelector(".execution-duration");
    if (duration) {
      const elapsedMs = performance.now() - startedAt;
      duration.textContent = formatWorkDuration(elapsedMs);
      duration.closest(".execution-process").dataset.elapsedMs = String(elapsedMs);
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
    reverted: "已回滚",
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
    const composerDrafts = Object.fromEntries(
      Object.entries(state.composerDrafts)
        .filter(([, value]) => String(value || "").trim())
        .slice(-20),
    );
    localStorage.setItem(
      UI_STORAGE_KEY,
      JSON.stringify({
        view: state.currentView,
        inspectorHidden: document.body.classList.contains("inspector-hidden"),
        rerankEnabled: state.rerankEnabled,
        knowledgeBaseId: $("kb-id-input")?.value || "",
        knowledgeTab: state.activeKnowledgeTab,
        composerDrafts,
      }),
    );
  } catch {
    // Device-local preferences are optional; the product remains usable without them.
  }
}

function composerDraftKey(sessionId = state.conversationId) {
  return sessionId || "__new__";
}

function queueUiPreferenceSave() {
  window.clearTimeout(composerDraftSaveTimer);
  composerDraftSaveTimer = window.setTimeout(saveUiPreferences, 120);
}

function saveComposerDraft(value = $("chat-message-input")?.value || "") {
  const key = composerDraftKey();
  if (value) {
    state.composerDrafts[key] = value.slice(0, 8000);
  } else {
    delete state.composerDrafts[key];
  }
  queueUiPreferenceSave();
}

function resizeComposerInput() {
  const input = $("chat-message-input");
  if (!input) return;
  input.style.height = "0px";
  const nextHeight = Math.min(220, Math.max(64, input.scrollHeight));
  input.style.height = `${nextHeight}px`;
  input.style.overflowY = input.scrollHeight > 220 ? "auto" : "hidden";
}

function setComposerValue(value, { save = true, focus = false } = {}) {
  const input = $("chat-message-input");
  input.value = String(value || "").slice(0, 8000);
  input.removeAttribute("aria-invalid");
  resizeComposerInput();
  if (save) saveComposerDraft(input.value);
  updateComposerAvailability();
  if (focus) {
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
}

function restoreComposerDraft(sessionId = state.conversationId) {
  setComposerValue(state.composerDrafts[composerDraftKey(sessionId)] || "", {
    save: false,
  });
}

function clearComposerInput() {
  setComposerValue("");
  closeSlashCommandMenu();
}

function invalidateSlashCapabilities() {
  state.slashRequestGeneration += 1;
  state.slashCapabilityKey = "";
  state.slashCapabilities = {
    conversation_id: "",
    workspace_id: "",
    skill_commands: [],
    mcp_tools: [],
    diagnostics: [],
  };
  state.slashLoading = false;
  state.slashError = "";
}

function slashQueryContext() {
  const input = $("chat-message-input");
  const caret = input.selectionStart ?? input.value.length;
  const prefix = input.value.slice(0, caret);
  const match = prefix.match(/^(\s*)(\/[^\s]*)$/);
  if (!match) return null;
  return {
    start: match[1].length,
    end: caret,
    query: match[2].slice(1).toLowerCase(),
  };
}

function composerSlashItems() {
  const builtin = COMPOSER_BUILTIN_COMMANDS.map((item) => ({ ...item }));
  const skills = (state.slashCapabilities.skill_commands || []).map((command) => ({
    kind: "skill",
    command: command.name,
    title: command.description || command.name,
    description: [
      `${command.source} Skill`,
      command.usage ? `用法：/${command.name} ${command.usage}` : "",
    ].filter(Boolean).join(" · "),
    aliases: command.aliases || [],
    skillName: command.skill_qualified_name,
    keywords: [command.skill_name, command.skill_qualified_name, ...(command.aliases || [])],
  }));
  const mcpTools = (state.slashCapabilities.mcp_tools || []).map((tool) => ({
    kind: "mcp",
    command: tool.name,
    title: tool.description || tool.name,
    description: `${tool.server_name} · ${tool.permission_level}${tool.requires_approval ? " · 需要审批" : ""}`,
    preferredToolName: tool.name,
    keywords: [tool.server_name, tool.provider, tool.permission_level],
  }));
  return [...builtin, ...skills, ...mcpTools];
}

function slashItemMatchScore(item, query) {
  if (!query) return 0;
  const normalizedQuery = query.toLowerCase();
  const command = item.command.toLowerCase();
  if (command === normalizedQuery) return 0;
  if (command.startsWith(normalizedQuery)) return 1;
  if ((item.aliases || []).some((alias) => alias.toLowerCase().startsWith(normalizedQuery))) {
    return 2;
  }
  const haystack = [
    item.command,
    item.title,
    item.description,
    ...(item.aliases || []),
    ...(item.keywords || []),
  ].join(" ").toLowerCase();
  return haystack.includes(normalizedQuery) ? 3 : Number.POSITIVE_INFINITY;
}

function slashKindLabel(kind) {
  return {
    builtin: "命令",
    skill: "Skills",
    mcp: "MCP 工具",
  }[kind] || kind;
}

function renderSlashCommandMenu() {
  const menu = $("slash-command-menu");
  const context = slashQueryContext();
  if (!context || $("chat-message-input").disabled) {
    closeSlashCommandMenu();
    return;
  }
  if (context.query !== state.slashQuery) {
    state.slashQuery = context.query;
    state.slashActiveIndex = 0;
  }
  const items = composerSlashItems()
    .map((item, order) => ({ item, order, score: slashItemMatchScore(item, context.query) }))
    .filter(({ score }) => Number.isFinite(score))
    .sort((left, right) => left.score - right.score || left.order - right.order)
    .map(({ item }) => item);
  state.slashItems = items;
  state.slashActiveIndex = items.length
    ? Math.min(state.slashActiveIndex, items.length - 1)
    : 0;
  const groups = new Map();
  items.forEach((item, index) => {
    const values = groups.get(item.kind) || [];
    values.push({ item, index });
    groups.set(item.kind, values);
  });
  $("slash-command-options").innerHTML = [...groups.entries()]
    .map(([kind, values]) => `
      <section class="slash-command-group" aria-label="${escapeHtml(slashKindLabel(kind))}">
        <h3>${escapeHtml(slashKindLabel(kind))}</h3>
        ${values.map(({ item, index }) => `
          <button
            id="slash-command-option-${index}"
            class="slash-command-option ${index === state.slashActiveIndex ? "active" : ""}"
            type="button"
            role="option"
            aria-selected="${index === state.slashActiveIndex}"
            data-slash-index="${index}"
          >
            <code>/${escapeHtml(item.command)}</code>
            <span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.description)}</small></span>
            <em>${escapeHtml(slashKindLabel(item.kind))}</em>
          </button>
        `).join("")}
      </section>
    `)
    .join("");
  const status = $("slash-command-status");
  const dynamicCount = (state.slashCapabilities.skill_commands || []).length
    + (state.slashCapabilities.mcp_tools || []).length;
  status.textContent = state.slashLoading
    ? "正在读取当前工作区的 Skill 与 MCP 能力…"
    : state.slashError
      ? state.slashError
      : items.length === 0
        ? "没有匹配的命令。"
        : !context.query && dynamicCount === 0 && state.slashCapabilityKey
          ? "当前工作区没有可用的 Skill 或 MCP 工具。"
        : "";
  status.hidden = !status.textContent;
  menu.hidden = false;
  $("chat-message-input").setAttribute("aria-expanded", "true");
  if (items.length) {
    $("chat-message-input").setAttribute(
      "aria-activedescendant",
      `slash-command-option-${state.slashActiveIndex}`,
    );
  } else {
    $("chat-message-input").removeAttribute("aria-activedescendant");
  }
}

function closeSlashCommandMenu() {
  const menu = $("slash-command-menu");
  if (!menu) return;
  menu.hidden = true;
  state.slashItems = [];
  state.slashActiveIndex = 0;
  state.slashQuery = "";
  $("chat-message-input")?.setAttribute("aria-expanded", "false");
  $("chat-message-input")?.removeAttribute("aria-activedescendant");
}

async function loadSlashCapabilities() {
  const workspace = currentWorkspace();
  if (!workspaceIsReady(workspace)) {
    state.slashError = "选择可用工作区后，才能读取 Skill 与 MCP 工具。";
    renderSlashCommandMenu();
    return;
  }
  if (state.slashLoading) return;
  state.slashLoading = true;
  state.slashError = "";
  let generation = 0;
  renderSlashCommandMenu();
  try {
    const conversationId = await ensureSession();
    generation = ++state.slashRequestGeneration;
    state.slashLoading = true;
    const key = `${conversationId}:${workspace.id}`;
    if (state.slashCapabilityKey === key) {
      return;
    }
    const params = new URLSearchParams({
      conversation_id: conversationId,
      workspace_id: workspace.id,
    });
    const body = await fetchJson(`/agent/composer-capabilities?${params}`);
    if (generation !== state.slashRequestGeneration) return;
    state.slashCapabilities = body;
    state.slashCapabilityKey = key;
  } catch (error) {
    if (generation && generation !== state.slashRequestGeneration) return;
    state.slashError = `能力加载失败：${humanizeError(error)}`;
  } finally {
    if (!generation || generation === state.slashRequestGeneration) {
      state.slashLoading = false;
      renderSlashCommandMenu();
    }
  }
}

function updateSlashCommandMenu() {
  const context = slashQueryContext();
  if (!context) {
    closeSlashCommandMenu();
    return;
  }
  renderSlashCommandMenu();
  const expectedKey = state.conversationId && state.activeWorkspaceId
    ? `${state.conversationId}:${state.activeWorkspaceId}`
    : "";
  if (
    !state.slashLoading
    && (!expectedKey || state.slashCapabilityKey !== expectedKey)
  ) {
    loadSlashCapabilities();
  }
}

function moveSlashSelection(offset) {
  if (!state.slashItems.length) return;
  state.slashActiveIndex = (
    state.slashActiveIndex + offset + state.slashItems.length
  ) % state.slashItems.length;
  renderSlashCommandMenu();
  $(`slash-command-option-${state.slashActiveIndex}`)?.scrollIntoView({
    block: "nearest",
  });
}

function insertSlashCommand(item) {
  const input = $("chat-message-input");
  const context = slashQueryContext();
  if (!context) return;
  const before = input.value.slice(0, context.start);
  const after = input.value.slice(context.end);
  setComposerValue(`${before}/${item.command} ${after}`, { focus: true });
  closeSlashCommandMenu();
}

async function runBuiltinComposerCommand(item, remaining = "") {
  closeSlashCommandMenu();
  if (item.action === "agent" || item.action === "chat") {
    await persistComposerMode(item.action);
    setComposerValue(remaining, { focus: true });
    if (remaining.trim()) await submitComposerMessage();
    return;
  }
  if (item.action === "new") {
    const draft = remaining.trim();
    clearComposerInput();
    if (canSwitchSession()) {
      await createSession();
      if (draft) setComposerValue(draft, { focus: true });
    }
    return;
  }
  if (["tools", "mcp"].includes(item.action)) {
    clearComposerInput();
    switchView("tools");
  }
}

function selectSlashItem(index = state.slashActiveIndex) {
  const item = state.slashItems[index];
  if (!item) return;
  if (item.kind === "builtin") {
    runBuiltinComposerCommand(item).catch((error) =>
      showToast(humanizeError(error), "error"),
    );
    return;
  }
  insertSlashCommand(item);
  if (state.composerMode !== "agent") {
    persistComposerMode("agent");
  }
}

function splitSlashArguments(value) {
  const matches = String(value || "").match(/"(?:\\.|[^"\\])*"|'[^']*'|\S+/g) || [];
  return matches.map((item) => {
    if ((item.startsWith('"') && item.endsWith('"')) || (item.startsWith("'") && item.endsWith("'"))) {
      return item.slice(1, -1).replace(/\\(["\\])/g, "$1");
    }
    return item;
  });
}

function parseComposerSlashInvocation(value) {
  const match = String(value || "").match(/^\s*\/([^\s]+)(?:\s+([\s\S]*))?$/);
  if (!match) return null;
  const commandName = match[1].toLowerCase();
  const remaining = match[2] || "";
  const builtin = COMPOSER_BUILTIN_COMMANDS.find(
    (item) => item.command === commandName,
  );
  if (builtin) return { item: builtin, remaining };
  const skill = (state.slashCapabilities.skill_commands || []).find((item) =>
    [item.name, ...(item.aliases || [])].some(
      (name) => name.toLowerCase() === commandName,
    ),
  );
  if (skill) {
    return {
      item: {
        kind: "skill",
        command: skill.name,
        skillName: skill.skill_qualified_name,
      },
      remaining,
    };
  }
  const tool = (state.slashCapabilities.mcp_tools || []).find(
    (item) => item.name.toLowerCase() === commandName,
  );
  return tool
    ? {
        item: {
          kind: "mcp",
          command: tool.name,
          preferredToolName: tool.name,
        },
        remaining,
      }
    : null;
}

function composerSubmission(value) {
  const message = String(value || "").trim();
  const invocation = parseComposerSlashInvocation(message);
  return {
    message,
    invocation,
    skillName: invocation?.item.kind === "skill" ? invocation.item.skillName : null,
    skillArguments: invocation?.item.kind === "skill"
      ? splitSlashArguments(invocation.remaining)
      : [],
    preferredToolName: invocation?.item.kind === "mcp"
      ? invocation.item.preferredToolName
      : null,
  };
}

function conversationIsNearBottom() {
  const output = $("chat-output");
  const remaining = output.scrollHeight - (output.scrollTop + output.clientHeight);
  return remaining < 180;
}

function updateJumpToLatestButton() {
  const button = $("jump-to-latest-btn");
  if (!button) return;
  const hasMessages = Boolean($("chat-output")?.querySelector(".chat-message"));
  button.hidden = !hasMessages || conversationIsNearBottom();
}

function scrollConversationToLatest({ behavior = preferredScrollBehavior() } = {}) {
  state.followConversation = true;
  const output = $("chat-output");
  output.scrollTo({
    top: output.scrollHeight,
    behavior,
  });
  window.setTimeout(updateJumpToLatestButton, behavior === "smooth" ? 240 : 0);
}

function scheduleConversationFollow() {
  if (!state.followConversation) {
    updateJumpToLatestButton();
    return;
  }
  window.cancelAnimationFrame(conversationFollowFrame);
  conversationFollowFrame = window.requestAnimationFrame(() => {
    scrollConversationToLatest({ behavior: "auto" });
  });
}

function bindConversationFollow() {
  const output = $("chat-output");
  const observer = new MutationObserver(scheduleConversationFollow);
  observer.observe(output, { childList: true, subtree: true, characterData: true });
  output.addEventListener("scroll", () => {
    state.followConversation = conversationIsNearBottom();
    updateJumpToLatestButton();
  }, { passive: true });
  $("jump-to-latest-btn").addEventListener("click", () =>
    scrollConversationToLatest({ behavior: preferredScrollBehavior() }),
  );
}

function setMobileMoreOpen(open) {
  const menu = $("mobile-more-menu");
  const backdrop = $("mobile-nav-backdrop");
  menu.hidden = !open;
  backdrop.hidden = !open;
  $("mobile-more-btn").setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("mobile-more-open", open);
}

function updateMobileMoreState() {
  const overflowActive = ["memory", "models", "tools", "evals", "trace-audit"].includes(state.currentView);
  const button = $("mobile-more-btn");
  button.classList.toggle("active", overflowActive);
  if (overflowActive) {
    button.setAttribute("aria-current", "page");
  } else {
    button.removeAttribute("aria-current");
  }
}

function switchView(viewName, updateHash = true) {
  const normalizedView = viewName === "agent"
    ? "chat"
    : (viewName === "mcp" ? "tools" : viewName);
  const panel = document.querySelector(`[data-view-panel="${normalizedView}"]`);
  if (!panel) {
    return;
  }
  state.currentView = normalizedView;
  if (normalizedView !== "trace-audit") clearAuditPoll();
  setMobileMoreOpen(false);
  document.querySelectorAll("[data-view-panel]").forEach((item) => {
    const active = item.dataset.viewPanel === normalizedView;
    item.classList.toggle("active", active);
    item.hidden = !active;
  });
  document.querySelectorAll("[data-view]").forEach((item) => {
    const active = item.dataset.view === normalizedView;
    item.classList.toggle("active", active);
    if (active) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  });
  if (updateHash) {
    history.replaceState(null, "", `#${normalizedView}`);
  }
  updateMobileMoreState();
  saveUiPreferences();
  $("main-workspace").focus({ preventScroll: true });
  if (normalizedView === "memory") {
    refreshMemoryWorkbench();
  }
  if (normalizedView === "models") {
    loadModelRegistry().catch((error) => showToast(humanizeError(error), "error"));
  }
  if (normalizedView === "tools") {
    Promise.all([loadSkillRegistry(), loadMCPRegistry()])
      .catch((error) => showToast(humanizeError(error), "error"));
  }
  if (normalizedView === "evals") {
    loadEvalDashboard().catch((error) => showToast(humanizeError(error), "error"));
  }
  if (normalizedView === "trace-audit") {
    const refresh = state.auditRuns.length && state.auditRunId
      ? loadAuditRun(state.auditRunId, { silent: true })
      : loadAuditRuns();
    refresh.catch((error) => showToast(humanizeError(error), "error"));
  }
}

function syncInspectorPresentation(visible) {
  const drawer = window.innerWidth <= 1120;
  $("inspector-backdrop").hidden = !visible || !drawer;
  document.body.classList.toggle("inspector-drawer-open", visible && drawer);
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
  syncInspectorPresentation(visible);
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

function setChatWorkbenchActive(active) {
  const workbench = $("chat-view");
  workbench.classList.toggle("has-conversation", active);
  workbench.setAttribute("aria-labelledby", active ? "active-session-title" : "chat-title");
  $("chat-welcome-header").hidden = active;
  $("active-session-header").hidden = !active;
  if (active) {
    updateContextSummary();
  }
}

function updateComposerScopeSummary() {
  const workspace = currentWorkspace();
  const ready = workspaceIsReady(workspace);
  const workspaceLabel = workspace ? workspaceName(workspace) : "未选择工作区";
  const workspaceRole = workspace
    ? `${workspaceRoleLabel(workspace.role)}${ready ? "" : " · 不可用"}`
    : "选择";
  const statusDot = $("composer-workspace-status-dot");
  statusDot.className = `workspace-status-dot ${
    workspace ? (ready ? "is-ready" : "is-unavailable") : "is-missing"
  }`;
  $("composer-workspace-label").textContent = workspaceLabel;
  $("composer-workspace-role").textContent = workspaceRole;
  $("composer-workspace-btn").title = workspace?.root_path
    ? `${workspace.root_path} · ${workspaceRole}`
    : "选择 Agent 可以操作的工作区";

  const modelLabel = currentModelSelectionLabel();
  $("composer-model-label").textContent = modelLabel;
  $("composer-model-btn").title = `当前模型：${modelLabel}`;

  const usage = state.conversationId
    ? state.sessionTokenUsage[state.conversationId]
    : null;
  const total = Number(usage?.total_tokens || 0);
  const estimated = Number(usage?.context?.estimated_tokens || 0);
  const budget = Number(usage?.context?.budget_tokens || 0);
  const ratio = budget ? Math.min(1, estimated / budget) : 0;
  const contextNode = $("composer-context-budget");
  contextNode.classList.toggle("warning", ratio >= 0.72 && ratio < 0.9);
  contextNode.classList.toggle("error", ratio >= 0.9);
  $("composer-context-kicker").textContent = usage
    ? `累计 ${formatTokenCount(total)} tokens`
    : "Token usage";
  $("composer-context-label").textContent = usage
    ? estimated
      ? `历史 ≈ ${formatTokenCount(estimated)}${budget ? ` / ${formatTokenCount(budget)}` : ""}`
      : "历史尚未形成"
    : "等待首轮请求";
  $("composer-context-meter-fill").style.width = `${Math.round(ratio * 100)}%`;
  contextNode.title = usage
    ? `累计实际消耗 ${formatTokenCount(total)} tokens。当前会话历史估算 ${formatTokenCount(estimated)}${
        budget ? ` / ${formatTokenCount(budget)}` : ""
      } tokens；历史估算只包含保留的会话消息和摘要，不等同于完整最终 Prompt。`
    : "发起请求后显示累计实际消耗和当前会话历史估算";
}

function updateContextSummary() {
  const workspace = currentWorkspace();
  const workspaceLabel = workspace ? workspaceName(workspace) : "未选择";
  const modelLabel = currentModelSelectionLabel();

  $("context-model").textContent = modelLabel;
  $("context-workspace").textContent = workspaceLabel;
  $("composer-context").textContent = modelLabel;
  $("header-session-id").textContent = state.currentSession?.title
    || state.conversationId
    || "尚未创建";
  $("active-session-title").textContent = state.currentSession?.title
    || state.conversationId
    || "当前会话";
  $("rename-current-session-btn").disabled = !state.currentSession?.id;
  updateComposerScopeSummary();
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
  const previousId = state.activeWorkspaceId;
  const normalizedId = workspaceId || "";
  state.activeWorkspaceId = normalizedId && (
    !state.workspacesLoaded
    || state.workspaces.some((item) => item.id === normalizedId)
  ) ? normalizedId : "";
  if (previousId !== state.activeWorkspaceId) {
    invalidateSlashCapabilities();
  }
  $("workspace-id-input").value = state.activeWorkspaceId;
  $("workspace-root-input").value = currentWorkspace()?.root_path || "";
  updateContextSummary();
  updateComposerAvailability();
  renderWorkspaceManager();
  renderWorkspaceCatalog();
  if (
    state.conversationId
    && state.composerMode === "agent"
    && workspaceIsReady(currentWorkspace())
  ) {
    loadSlashCapabilities().catch((error) => {
      state.slashError = `能力加载失败：${humanizeError(error)}`;
    });
  }
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
    ? "描述代码任务，键入 / 使用 Skill 或 MCP，Enter 交给 Agent…"
    : "输入消息，键入 / 使用命令，Enter 发送…";
  $("send-chat-btn").innerHTML = isAgent
    ? `交给 Agent ${iconMarkup("arrow-right")}`
    : `发送 ${iconMarkup("arrow-up")}`;
  if (state.conversationId && isAgent && workspaceIsReady(currentWorkspace())) {
    loadSlashCapabilities().catch((error) => {
      state.slashError = `能力加载失败：${humanizeError(error)}`;
    });
  }
  updateComposerAvailability();
}

async function persistComposerMode(mode) {
  updateComposerMode(mode);
  try {
    if (
      state.composerMode === "agent"
      && workspaceIsReady(currentWorkspace())
    ) {
      await loadSlashCapabilities();
    }
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
  const empty = !$("chat-message-input").value.trim();
  $("archived-session-notice").hidden = !archived;
  $("chat-message-input").disabled = archived;
  $("composer-mode-input").disabled = archived || streaming;
  $("send-chat-btn").disabled = archived || streaming || agentNeedsWorkspace || empty;
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

function auditRunMatchesStatus(run, filter) {
  if (filter === "all") return true;
  if (filter === "active") {
    return ["queued", "running", "waiting_approval", "waiting_input", "paused"].includes(run.status);
  }
  if (filter === "failed") return ["failed", "blocked", "partial"].includes(run.status);
  return run.status === filter;
}

function renderAuditRuns() {
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

function auditEventMatches(event, filter) {
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

function auditEventCategory(event) {
  const type = String(event.type || "");
  if (type.includes("error") || type.includes("failed") || event.output?.ok === false) return "error";
  if (type.startsWith("tool_")) return "tool";
  if (type.startsWith("approval_")) return "approval";
  if (type.startsWith("run_") || type.startsWith("control_") || type === "input_required") return "state";
  return "node";
}

function auditEventTitle(event) {
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
    answer_completed: "回答生成完成",
  };
  return labels[event.type] || event.summary || event.type || "审计事件";
}

function auditEventCategoryLabel(event) {
  return {
    state: "状态转移",
    node: "执行节点",
    tool: event.type === "tool_selected" ? "工具选择" : "工具结果",
    approval: "审批",
    error: "错误",
  }[auditEventCategory(event)] || "事件";
}

function buildAuditEvents(run, storedEvents) {
  const events = (storedEvents || []).map((event) => ({ ...event, output: event.output || {} }));
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

function auditEventSummary(event) {
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

function renderAuditTimeline() {
  const list = $("trace-audit-timeline");
  const visible = state.auditEvents.filter((event) => auditEventMatches(event, state.auditCategory));
  $("trace-audit-visible-count").textContent = `${visible.length} 条`;
  list.innerHTML = "";
  if (!visible.length) {
    list.innerHTML = '<li class="trace-audit-placeholder">当前筛选下没有审计事件。</li>';
    return;
  }
  for (const event of visible) {
    const category = auditEventCategory(event);
    const item = document.createElement("li");
    item.className = `trace-audit-event category-${category}${event.type === "tool_error" ? " event-failed" : ""}`;
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
            <pre><code>${escapeHtml(jsonPretty(payload))}</code></pre>
          </details>
        ` : ""}
      </article>
    `;
    list.appendChild(item);
  }
}

function renderAuditDetail() {
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
    ? `#${state.auditEvents.at(-1).sequence}`
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

function clearAuditPoll() {
  window.clearTimeout(state.auditPollTimer);
  state.auditPollTimer = null;
}

function scheduleAuditPoll() {
  clearAuditPoll();
  if (state.currentView !== "trace-audit" || !state.auditRunBody) return;
  if (FINAL_RUN_STATUSES.has(state.auditRunBody.status)) return;
  state.auditPollTimer = window.setTimeout(() => {
    loadAuditRun(state.auditRunId, { silent: true }).catch(() => {});
  }, 2500);
}

async function loadAuditRun(runId, options = {}) {
  if (!runId) return;
  const generation = ++state.auditRequestGeneration;
  state.auditRunId = runId;
  renderAuditRuns();
  if (!options.silent) {
    $("trace-audit-empty").hidden = true;
    $("trace-audit-content").hidden = false;
    $("trace-audit-timeline").innerHTML = '<li class="trace-audit-placeholder">正在装载审计事实…</li>';
  }
  try {
    const [run, eventBody] = await Promise.all([
      fetchJson(`/agent/runs/${encodeURIComponent(runId)}`),
      fetchJson(`/agent/runs/${encodeURIComponent(runId)}/events`),
    ]);
    if (generation !== state.auditRequestGeneration) return;
    state.auditRunBody = run;
    state.auditEvents = buildAuditEvents(run, eventBody.events || []);
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

async function loadAuditRuns() {
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

function skillByName(name) {
  return state.skillRegistry.skills.find((item) => item.name === name) || null;
}

function renderSkillRegistry() {
  const registry = state.skillRegistry;
  const skills = registry.skills || [];
  $("skill-registry-root").textContent = registry.root || "~/.ai-agent-platform/skills";
  $("skill-list-count").textContent = String(skills.length);
  const note = $("skill-runtime-note");
  const errors = (registry.diagnostics || []).filter((item) => item.severity === "error");
  if (!registry.writable) {
    note.hidden = false;
    note.className = "mcp-runtime-note error";
    note.textContent = "全局 Skill 目录不可写，当前只能查看已发现项。";
  } else if (errors.length) {
    note.hidden = false;
    note.className = "mcp-runtime-note";
    note.textContent = `有 ${errors.length} 个 Skill 未通过校验，请检查路径或 SKILL.md。`;
  } else {
    note.hidden = true;
    note.textContent = "";
  }
  $("save-skill-btn").disabled = !registry.writable;
  const list = $("skill-list");
  if (!skills.length) {
    list.innerHTML = '<div class="empty-state">尚未注册 Skill。保存后会立即出现在所有 Workspace。</div>';
    return;
  }
  list.innerHTML = skills.map((skill) => {
    const command = skill.command?.name || skill.name;
    const tools = skill.required_tools?.length
      ? skill.required_tools.map((name) => `<code>${escapeHtml(name)}</code>`).join("")
      : "<small>不声明额外工具</small>";
    return `
      <article class="mcp-server-card ${skill.enabled ? "ready" : "disabled"}" data-skill-name="${escapeHtml(skill.name)}">
        <div class="mcp-server-heading">
          <div><strong>${escapeHtml(skill.name)}</strong><small>${escapeHtml(skill.description)}</small></div>
          <span class="status-pill ${skill.enabled ? "ok" : "neutral"}"><span class="status-dot"></span>${skill.enabled ? "已启用" : "已停用"}</span>
        </div>
        <div class="mcp-server-meta"><span>/${escapeHtml(command)}</span><span>${escapeHtml(skill.source)}</span><span>${skill.editable ? "全局用户目录" : "系统内置"}</span></div>
        <div class="mcp-tool-chips">${tools}</div>
        <div class="mcp-server-actions">
          ${skill.editable ? `<button class="button ghost" type="button" data-skill-action="edit">编辑</button><button class="button ghost" type="button" data-skill-action="toggle">${skill.enabled ? "停用" : "启用"}</button><button class="text-button danger" type="button" data-skill-action="delete">删除</button>` : '<span class="meta-badge">只读</span>'}
        </div>
      </article>`;
  }).join("");
}

async function loadSkillRegistry(showRaw = false) {
  const body = await fetchJson("/skills");
  state.skillRegistry = body;
  invalidateSlashCapabilities();
  renderSkillRegistry();
  if (showRaw) setRaw(body);
  return body;
}

function resetSkillForm() {
  state.editingSkill = "";
  $("skill-form").reset();
  $("skill-name-input").readOnly = false;
  $("skill-form-title").textContent = "添加 Skill";
  $("skill-form-hint").textContent = "保存后无需重启，所有 Workspace 立即使用同一份注册表。";
  $("save-skill-btn").textContent = "保存 Skill";
  $("skill-enabled-input").checked = true;
}

function editSkill(name) {
  const skill = skillByName(name);
  if (!skill?.editable) return;
  state.editingSkill = name;
  $("skill-name-input").value = skill.name;
  $("skill-name-input").readOnly = true;
  $("skill-content-input").value = skill.content || "";
  $("skill-enabled-input").checked = skill.enabled;
  $("skill-form-title").textContent = `编辑 ${skill.name}`;
  $("skill-form-hint").textContent = "名称必须与 Frontmatter 的 name 一致。";
  $("save-skill-btn").textContent = "更新 Skill";
  $("skill-form").scrollIntoView({ behavior: preferredScrollBehavior(), block: "start" });
  $("skill-content-input").focus({ preventScroll: true });
}

async function saveSkill(event) {
  event.preventDefault();
  const form = $("skill-form");
  if (!form.reportValidity()) return;
  const name = $("skill-name-input").value.trim();
  const button = $("save-skill-btn");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await fetchJson(`/skills/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({
        content: $("skill-content-input").value,
        enabled: $("skill-enabled-input").checked,
      }),
    });
    await loadSkillRegistry();
    resetSkillForm();
    showToast(`${name} 已保存并同步到所有 Workspace`, "success");
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    button.disabled = !state.skillRegistry.writable;
    button.removeAttribute("aria-busy");
  }
}

async function handleSkillAction(button) {
  const card = button.closest("[data-skill-name]");
  const name = card?.dataset.skillName;
  const action = button.dataset.skillAction;
  const skill = skillByName(name);
  if (!skill?.editable) return;
  if (action === "edit") {
    editSkill(name);
    return;
  }
  if (action === "delete" && !window.confirm(`确认删除全局 Skill“${name}”？`)) return;
  button.disabled = true;
  try {
    if (action === "toggle") {
      await fetchJson(`/skills/${encodeURIComponent(name)}/enabled`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !skill.enabled }),
      });
      showToast(skill.enabled ? `${name} 已停用` : `${name} 已启用`);
    } else if (action === "delete") {
      await fetchJson(`/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (state.editingSkill === name) resetSkillForm();
      showToast(`${name} 已删除`);
    }
    await loadSkillRegistry(true);
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    button.disabled = false;
  }
}

const MCP_STATUS_LABELS = {
  ready: "就绪",
  connecting: "连接中",
  degraded: "异常",
  unavailable: "不可用",
  circuit_open: "熔断中",
  disabled: "已停用",
  restart_required: "等待重启",
  closed: "已关闭",
};

function mcpStatusLabel(value) {
  return MCP_STATUS_LABELS[value] || value || "未知";
}

function mcpStatusClass(value) {
  if (value === "ready") return "ok";
  if (value === "connecting") return "running";
  if (["degraded", "circuit_open", "restart_required"].includes(value)) return "warning";
  if (value === "unavailable") return "error";
  return "neutral";
}

function mcpCardState(value) {
  return new Set([
    "ready",
    "connecting",
    "degraded",
    "unavailable",
    "circuit_open",
    "disabled",
    "restart_required",
  ]).has(value) ? value : "closed";
}

function mcpTransportLabel(value) {
  return {
    stdio: "stdio · 当前协议",
    streamable_http: "Streamable HTTP",
    stdio_2025_06_18: "stdio · 2025-06-18",
    legacy_sse: "HTTP+SSE · 兼容层",
  }[value] || value;
}

function mcpServerByName(name) {
  return state.mcpRegistry.servers.find((item) => item.name === name) || null;
}

function assignmentText(values) {
  return Object.entries(values || {})
    .sort(([left], [right]) => left.localeCompare(right, "en"))
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

function parseAssignments(value, label, { secret = false, existingNames = [] } = {}) {
  const values = {};
  const names = new Set();
  const existing = new Set(existingNames);
  for (const [index, rawLine] of String(value || "").split(/\r?\n/).entries()) {
    if (!rawLine.trim()) continue;
    const separator = rawLine.indexOf("=");
    if (separator < 1) {
      throw new Error(`${label}第 ${index + 1} 行必须使用 KEY=value`);
    }
    const key = rawLine.slice(0, separator).trim();
    const item = rawLine.slice(separator + 1).trim();
    if (!key) throw new Error(`${label}第 ${index + 1} 行缺少名称`);
    if (names.has(key)) throw new Error(`${label}包含重复名称：${key}`);
    names.add(key);
    if (secret && item === "") {
      if (!existing.has(key)) throw new Error(`${label}${key} 缺少凭据值`);
      continue;
    }
    values[key] = item;
  }
  return { values, names };
}

function mcpNumberValue(id, fallback) {
  const value = Number($(id).value);
  return Number.isFinite(value) ? value : fallback;
}

function updateMCPTransportFields() {
  const transport = $("mcp-transport-input").value;
  const stdio = transport === "stdio" || transport === "stdio_2025_06_18";
  $("mcp-stdio-fields").hidden = !stdio;
  $("mcp-http-fields").hidden = stdio;
  $("mcp-command-input").required = stdio;
  $("mcp-url-input").required = !stdio;
}

function renderMCPRegistry() {
  const registry = state.mcpRegistry;
  const servers = registry.servers || [];
  const ready = servers.filter((item) => item.state === "ready");
  const registeredTools = new Set(servers.flatMap((item) => item.registered_tools || []));
  $("mcp-server-count").textContent = String(servers.length);
  $("mcp-ready-count").textContent = String(ready.length);
  $("mcp-tool-count").textContent = String(registeredTools.size);
  $("mcp-list-count").textContent = String(servers.length);

  const note = $("mcp-runtime-note");
  if (!registry.config_writable) {
    note.hidden = false;
    note.className = "mcp-runtime-note error";
    note.textContent = "当前没有配置 MCP_CONFIG_PATH，设置可写配置路径后才能从界面注册 Server。";
  } else if (!registry.runtime_enabled) {
    note.hidden = false;
    note.className = "mcp-runtime-note";
    note.textContent = "配置可以保存，但 MCP 运行时未启用。设置 MCP_ENABLED=true 并重启后，Server 会自动连接。";
  } else {
    note.hidden = true;
    note.textContent = "";
  }
  $("save-mcp-server-btn").disabled = !registry.config_writable;

  const list = $("mcp-server-list");
  if (!servers.length) {
    list.innerHTML = '<div class="empty-state">尚未注册 MCP Server。填写左侧配置即可开始发现工具。</div>';
    return;
  }
  list.innerHTML = servers.map((server) => {
    const tools = server.registered_tools || [];
    const visibleTools = tools.slice(0, 8);
    const toolMarkup = visibleTools.length
      ? visibleTools.map((name) => `<code>${escapeHtml(name)}</code>`).join("")
      : '<small>尚未注册工具</small>';
    const extraTools = tools.length > visibleTools.length
      ? `<span>+${tools.length - visibleTools.length}</span>`
      : "";
    return `
      <article class="mcp-server-card ${mcpCardState(server.state)}" data-mcp-server="${escapeHtml(server.name)}">
        <div class="mcp-server-heading">
          <div>
            <strong>${escapeHtml(server.name)}</strong>
            <small title="${escapeHtml(server.endpoint)}">${escapeHtml(server.endpoint)}</small>
          </div>
          <span class="status-pill ${mcpStatusClass(server.state)}"><span class="status-dot"></span>${escapeHtml(mcpStatusLabel(server.state))}</span>
        </div>
        <div class="mcp-server-meta">
          <span>${escapeHtml(mcpTransportLabel(server.transport))}</span>
          <span>${server.required ? "required" : "optional"}</span>
          <span>${escapeHtml(server.protocol_version || "协议待协商")}</span>
          <span>${server.discovered_tools?.length || 0} 已发现 · ${tools.length} 已注册</span>
          ${server.cache_hit ? "<span>缓存命中</span>" : ""}
        </div>
        ${server.last_error_code ? `<p class="mcp-server-error">错误码：${escapeHtml(server.last_error_code)} · 重试 ${escapeHtml(server.retry_count)}</p>` : ""}
        <div class="mcp-tool-chips">${toolMarkup}${extraTools}</div>
        <div class="mcp-server-actions">
          <button class="button ghost" type="button" data-mcp-action="edit">编辑</button>
          <button class="button ghost" type="button" data-mcp-action="test" ${!registry.runtime_enabled || !server.enabled ? "disabled" : ""}>测试 / 刷新</button>
          <button class="button ghost" type="button" data-mcp-action="toggle">${server.enabled ? "停用" : "启用"}</button>
          <button class="text-button danger" type="button" data-mcp-action="delete">删除</button>
        </div>
      </article>`;
  }).join("");
}

async function loadMCPRegistry(showRaw = false) {
  const body = await fetchJson("/mcp/servers");
  state.mcpRegistry = body;
  invalidateSlashCapabilities();
  renderMCPRegistry();
  if (showRaw) setRaw(body);
  return body;
}

function resetMCPServerForm() {
  state.editingMCPServer = "";
  $("mcp-server-form").reset();
  $("mcp-name-input").readOnly = false;
  $("mcp-form-title").textContent = "添加 Server";
  $("mcp-form-hint").textContent = "保存后立即连接并发现工具；连接失败不会阻止其他 Server。";
  $("save-mcp-server-btn").textContent = "保存并连接";
  updateMCPTransportFields();
  renderMCPRegistry();
}

function editMCPServer(name) {
  const server = mcpServerByName(name);
  if (!server) return;
  state.editingMCPServer = name;
  $("mcp-name-input").value = server.name;
  $("mcp-name-input").readOnly = true;
  $("mcp-transport-input").value = server.transport;
  updateMCPTransportFields();
  $("mcp-command-input").value = server.command || "";
  $("mcp-args-input").value = (server.args || []).join("\n");
  $("mcp-env-input").value = assignmentText(server.env);
  $("mcp-env-secrets-input").value = (server.env_secret_names || []).map((key) => `${key}=`).join("\n");
  $("mcp-url-input").value = server.url || "";
  $("mcp-allowed-hosts-input").value = (server.allowed_hosts || []).join("\n");
  $("mcp-headers-input").value = assignmentText(server.headers);
  $("mcp-header-secrets-input").value = (server.header_secret_names || []).map((key) => `${key}=`).join("\n");
  $("mcp-enabled-input").checked = server.enabled;
  $("mcp-required-input").checked = server.required;
  $("mcp-connect-timeout-input").value = server.connect_timeout_seconds;
  $("mcp-request-timeout-input").value = server.request_timeout_seconds;
  $("mcp-max-retries-input").value = server.max_retries;
  $("mcp-cache-ttl-input").value = server.tool_cache_ttl_seconds;
  $("mcp-insecure-http-input").checked = server.allow_insecure_http;
  $("mcp-private-network-input").checked = server.allow_private_network;
  $("mcp-legacy-input").checked = server.legacy_compatibility;
  $("mcp-form-title").textContent = `编辑 ${server.name}`;
  $("mcp-form-hint").textContent = "留空已有 Secret 的值即可沿用；删除整行会移除该凭据。";
  $("save-mcp-server-btn").textContent = "更新并重连";
  $("mcp-server-form").scrollIntoView({ behavior: preferredScrollBehavior(), block: "start" });
  $(server.command ? "mcp-command-input" : "mcp-url-input").focus({ preventScroll: true });
}

function mcpPayloadFromForm() {
  const name = $("mcp-name-input").value.trim();
  const transport = $("mcp-transport-input").value;
  const stdio = transport === "stdio" || transport === "stdio_2025_06_18";
  const existing = mcpServerByName(state.editingMCPServer || name);
  const env = parseAssignments($("mcp-env-input").value, "环境变量");
  const envSecrets = parseAssignments($("mcp-env-secrets-input").value, "Secret 环境变量", {
    secret: true,
    existingNames: existing?.env_secret_names || [],
  });
  const headers = parseAssignments($("mcp-headers-input").value, "Header");
  const headerSecrets = parseAssignments($("mcp-header-secrets-input").value, "Secret Header", {
    secret: true,
    existingNames: existing?.header_secret_names || [],
  });
  let allowedHosts = $("mcp-allowed-hosts-input").value
    .split(/[\n,]/)
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
  const url = $("mcp-url-input").value.trim();
  if (!stdio && !allowedHosts.length && url) {
    try {
      allowedHosts = [new URL(url).hostname.toLowerCase()];
    } catch {
      // Native form validation and the API provide the precise URL error.
    }
  }
  const legacyTransport = transport === "stdio_2025_06_18" || transport === "legacy_sse";
  return {
    name,
    payload: {
      transport,
      command: stdio ? $("mcp-command-input").value.trim() || null : null,
      args: stdio ? $("mcp-args-input").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean) : [],
      env: stdio ? env.values : {},
      env_secrets: stdio ? envSecrets.values : {},
      remove_env_secrets: existing
        ? (existing.env_secret_names || []).filter((key) => !stdio || !envSecrets.names.has(key))
        : [],
      url: stdio ? null : url || null,
      headers: stdio ? {} : headers.values,
      header_secrets: stdio ? {} : headerSecrets.values,
      remove_header_secrets: existing
        ? (existing.header_secret_names || []).filter((key) => stdio || !headerSecrets.names.has(key))
        : [],
      allowed_hosts: stdio ? [] : [...new Set(allowedHosts)],
      allow_insecure_http: !stdio && $("mcp-insecure-http-input").checked,
      allow_private_network: !stdio && $("mcp-private-network-input").checked,
      legacy_compatibility: legacyTransport || $("mcp-legacy-input").checked,
      required: $("mcp-required-input").checked,
      enabled: $("mcp-enabled-input").checked,
      connect_timeout_seconds: mcpNumberValue("mcp-connect-timeout-input", 10),
      request_timeout_seconds: mcpNumberValue("mcp-request-timeout-input", 10),
      max_retries: mcpNumberValue("mcp-max-retries-input", 1),
      retry_backoff_seconds: existing?.retry_backoff_seconds ?? 0.1,
      circuit_failure_threshold: existing?.circuit_failure_threshold ?? 3,
      circuit_reset_seconds: existing?.circuit_reset_seconds ?? 30,
      tool_cache_ttl_seconds: mcpNumberValue("mcp-cache-ttl-input", 30),
    },
  };
}

async function saveMCPServer(event) {
  event.preventDefault();
  const form = $("mcp-server-form");
  if (!form.reportValidity()) return;
  const button = $("save-mcp-server-btn");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    const { name, payload } = mcpPayloadFromForm();
    const result = await fetchJson(`/mcp/servers/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    await loadMCPRegistry();
    resetMCPServerForm();
    showToast(result.state === "ready"
      ? `${name} 已连接并完成工具发现`
      : `${name} 已保存 · ${mcpStatusLabel(result.state)}`,
    result.state === "ready" ? "success" : "warning");
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    button.disabled = !state.mcpRegistry.config_writable;
    button.removeAttribute("aria-busy");
  }
}

async function handleMCPServerAction(button) {
  const card = button.closest("[data-mcp-server]");
  const name = card?.dataset.mcpServer;
  const action = button.dataset.mcpAction;
  const server = mcpServerByName(name);
  if (!server) return;
  if (action === "edit") {
    editMCPServer(name);
    return;
  }
  if (action === "delete" && !window.confirm(`确认删除 MCP Server“${name}”？对应的托管凭据也会删除。`)) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    if (action === "test") {
      const result = await fetchJson(`/mcp/servers/${encodeURIComponent(name)}/test`, { method: "POST" });
      showToast(result.state === "ready" ? `${name} 连接与工具刷新成功` : `${name} · ${mcpStatusLabel(result.state)}`,
        result.state === "ready" ? "success" : "warning");
    } else if (action === "toggle") {
      await fetchJson(`/mcp/servers/${encodeURIComponent(name)}/enabled`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !server.enabled }),
      });
      showToast(server.enabled ? `${name} 已停用并关闭` : `${name} 已启用`);
    } else if (action === "delete") {
      await fetchJson(`/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (state.editingMCPServer === name) resetMCPServerForm();
      showToast(`${name} 已删除`);
    }
    await loadMCPRegistry(true);
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
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

function closeComposerConfig({ restoreFocus = false } = {}) {
  const config = $("composer-config");
  if (!config.open) return;
  config.open = false;
  closeModelPicker();
  if (restoreFocus) $("composer-model-btn").focus();
}

function bindComposerConfigDismissal() {
  const config = $("composer-config");
  const trigger = $("composer-model-btn");
  document.addEventListener("click", (event) => {
    if (
      config.open
      && !config.contains(event.target)
      && !trigger.contains(event.target)
    ) {
      closeComposerConfig();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (
      event.key !== "Escape"
      || event.defaultPrevented
      || !config.open
      || !$("model-picker-menu").hidden
    ) {
      return;
    }
    event.preventDefault();
    closeComposerConfig({ restoreFocus: true });
  });
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

const EVAL_METRIC_LABELS = {
  pass_rate: { label: "用例通过率 ↑", hint: "通过用例 / 全部完成用例；越高越好", percent: true },
  invalid_action_rate: { label: "无效动作率 ↓", hint: "(实际精确重复 + suppressed) / (executed + suppressed)；越低越好", percent: true },
  mean_step_efficiency: { label: "步数效率 ↓", hint: "executed / 参考步数；不包含 suppressed；越低越好" },
  budget_cap_rate: { label: "预算触顶率 ↓", hint: "预算触顶用例 / 有轨迹用例；越低越好", percent: true },
  failure_recovery_rate: { label: "失败恢复率 ↑", hint: "换策略恢复 / 有真实 ToolResult 故障的用例；越高越好", percent: true },
  citation_content_accuracy: { label: "引用内容准确率 ↑", hint: "磁盘内容一致证据 / 可评分证据；越高越好", percent: true },
  answer_path_grounding_rate: { label: "答案路径落地率 ↑", hint: "有成功读取证据的答案路径 / 答案路径；越高越好", percent: true },
  fully_grounded_case_rate: { label: "完全落地用例率 ↑", hint: "内容与路径均落地用例 / 可评分引用用例；越高越好", percent: true },
  tokens_per_case: { label: "每用例 Token ↓", hint: "total_tokens / 完成用例；仅回归预警，无硬门槛", integer: true },
  total_tokens: { label: "总 Token ↓", hint: "本次 Eval 全部用例 Token；仅回归预警，无硬门槛", integer: true },
  elapsed_ms_per_case: { label: "每用例耗时 ↓", hint: "Eval 墙钟耗时 / 完成用例；仅回归预警，无硬门槛", milliseconds: true },
  elapsed_ms: { label: "总耗时 ↓", hint: "本次 Eval 墙钟耗时；仅回归预警，无硬门槛", milliseconds: true },
  proposed_calls: { label: "Proposed 调用 ↓", hint: "模型提出的调用总数；包含未执行、拒绝和待审批", integer: true },
  executed_calls: { label: "Executed 调用 ↓", hint: "能按 call_id 找到真实 ToolResult 的调用总数", integer: true },
  suppressed_calls: { label: "Suppressed 调用 ↓", hint: "平台抑制且未执行的调用总数；计入无效动作率", integer: true },
};

const EVAL_TERMINAL_STATUSES = new Set(["completed", "failed"]);

function formatEvalMetric(name, value) {
  if (value === null || value === undefined) {
    return "n/a";
  }
  if (EVAL_METRIC_LABELS[name]?.milliseconds) {
    return `${Math.round(value).toLocaleString()} ms`;
  }
  if (EVAL_METRIC_LABELS[name]?.integer) {
    return Math.round(value).toLocaleString();
  }
  return EVAL_METRIC_LABELS[name]?.percent
    ? `${(value * 100).toFixed(1)}%`
    : value.toFixed(2);
}

function formatEvalDelta(name, delta) {
  if (delta === null || delta === undefined || Math.abs(delta) < 0.0005) {
    return { text: "与基线持平", tone: "flat" };
  }
  // The API already signs every delta so that positive always means worse.
  const magnitude = EVAL_METRIC_LABELS[name]?.percent
    ? `${(Math.abs(delta) * 100).toFixed(1)}pp`
    : Math.abs(delta).toFixed(2);
  return delta > 0
    ? { text: `较基线劣化 ${magnitude}`, tone: "worse" }
    : { text: `较基线改善 ${magnitude}`, tone: "better" };
}

async function loadEvalDashboard(runId = "") {
  const [catalogue, history] = await Promise.all([
    fetchJson("/evals/catalogue"),
    fetchJson("/evals/runs?limit=20"),
  ]);
  state.evalCatalogue = catalogue;
  state.evalHistory = history.runs || [];
  renderEvalProviderOptions();
  renderEvalHistory();
  const targetId = runId
    || catalogue.active_run_id
    || state.evalRunId
    || state.evalHistory[0]?.run_id
    || "";
  if (targetId) {
    await loadEvalRun(targetId);
  } else {
    renderEvalRun(null);
  }
  if (catalogue.active_run_id) {
    scheduleEvalPoll(catalogue.active_run_id);
  }
}

async function loadEvalRun(runId) {
  const detail = await fetchJson(`/evals/runs/${encodeURIComponent(runId)}`);
  state.evalRunId = runId;
  state.evalRun = detail;
  renderEvalRun(detail);
  return detail;
}

function renderEvalProviderOptions() {
  const select = $("eval-provider-select");
  const runButton = $("run-eval-btn");
  if (!select) {
    return;
  }
  // The backend reports which providers have an enabled registered model. Any
  // other choice would start a run whose tool selection narrows to nothing.
  const providers = state.evalCatalogue?.providers || [];
  const previous = select.value;
  if (!providers.length) {
    select.innerHTML = '<option value="">没有可用的已注册模型</option>';
    select.disabled = true;
    runButton.disabled = true;
    return;
  }
  select.disabled = false;
  runButton.disabled = false;
  select.innerHTML = providers.map((item) => {
    const paid = item.provider !== "fake";
    const key = `${item.provider}:${item.model}`;
    return `<option value="${escapeHtml(key)}" data-provider="${escapeHtml(item.provider)}" data-model="${escapeHtml(item.model)}">`
      + `${escapeHtml(item.display_name)}（${escapeHtml(item.model)}${paid ? " · 计费" : " · 零成本"}）</option>`;
  }).join("");
  if (previous && select.querySelector(`option[value="${CSS.escape(previous)}"]`)) {
    select.value = previous;
  }
}

function renderEvalRun(detail) {
  renderEvalProgress(detail);
  renderEvalAlerts(detail);
  renderEvalMetrics(detail);
  renderEvalCases(detail);
  const pinButton = $("pin-eval-baseline-btn");
  if (pinButton) {
    const pinnable = Boolean(
      detail
      && detail.status === "completed"
      && detail.run_id !== detail.baseline_run_id,
    );
    pinButton.disabled = !pinnable;
  }
  renderEvalHistory();
}

function renderEvalProgress(detail) {
  const wrapper = $("eval-progress");
  if (!wrapper) {
    return;
  }
  const running = Boolean(detail && detail.status === "running");
  wrapper.hidden = !running;
  $("run-eval-btn").disabled = running;
  if (!running) {
    return;
  }
  const percent = Math.round((detail.progress || 0) * 100);
  $("eval-progress-fill").style.width = `${percent}%`;
  $("eval-progress-label").textContent =
    `正在运行 ${escapeHtml(detail.provider)}：${detail.completed_cases}/${detail.total_cases} 个用例`;
}

function renderEvalAlerts(detail) {
  const list = $("eval-alert-list");
  const badge = $("eval-alert-count");
  if (!list) {
    return;
  }
  const alerts = detail?.alerts || [];
  badge.textContent = String(alerts.length);
  badge.classList.toggle("danger", alerts.some((item) => item.severity === "critical"));
  if (!detail) {
    list.innerHTML = '<div class="empty-state">尚未运行评测</div>';
    return;
  }
  if (!alerts.length) {
    list.innerHTML = detail.status === "completed"
      ? '<div class="empty-state">没有预警：约束全部满足，指标未越界。</div>'
      : '<div class="empty-state">运行中…</div>';
    return;
  }
  list.innerHTML = alerts.map((alert) => `
    <div class="eval-alert ${escapeHtml(alert.severity)}">
      <span class="eval-alert-kind">${escapeHtml(evalAlertKindLabel(alert.kind))}</span>
      <span class="eval-alert-message">${escapeHtml(alert.message)}</span>
    </div>`).join("");
}

function evalAlertKindLabel(kind) {
  const labels = { case: "用例失败", threshold: "越过门槛", regression: "相对基线回归" };
  return labels[kind] || kind;
}

function renderEvalMetrics(detail) {
  const grid = $("eval-metric-grid");
  if (!grid) {
    return;
  }
  if (!detail || !detail.metrics) {
    grid.innerHTML = '<div class="empty-state">尚未运行评测</div>';
    return;
  }
  const deltas = detail.deltas || {};
  grid.innerHTML = Object.entries(EVAL_METRIC_LABELS).map(([name, meta]) => {
    const value = detail.metrics[name];
    const delta = detail.baseline && detail.run_id !== detail.baseline.run_id
      ? formatEvalDelta(name, deltas[name])
      : { text: detail.is_baseline ? "本次即基线" : "无基线可比", tone: "flat" };
    return `
      <article class="metric-card eval-metric-card">
        <span>${escapeHtml(meta.label)}</span>
        <strong>${escapeHtml(formatEvalMetric(name, value))}</strong>
        <small class="eval-delta ${delta.tone}">${escapeHtml(delta.text)}</small>
        <small class="eval-metric-hint">${escapeHtml(meta.hint)}</small>
      </article>`;
  }).join("");
}

function renderEvalCases(detail) {
  const list = $("eval-case-list");
  const summary = $("eval-case-summary");
  if (!list) {
    return;
  }
  const cases = detail?.cases || [];
  summary.textContent = detail
    ? `${detail.passed_cases}/${detail.total_cases}`
    : "0/0";
  if (!cases.length) {
    list.innerHTML = '<div class="empty-state">尚未运行评测</div>';
    return;
  }
  list.innerHTML = cases.map((item) => {
    const metrics = item.metrics || {};
    const citations = item.citations;
    const failed = (item.constraints || []).filter((entry) => !entry.passed);
    const stats = [
      `${metrics.executed_calls ?? 0} 步`,
      `提议 ${metrics.proposed_calls ?? 0}`,
      metrics.step_efficiency == null ? null : `效率 ${Number(metrics.step_efficiency).toFixed(2)}`,
      metrics.repeated_calls ? `重复 ${metrics.repeated_calls}` : null,
      metrics.suppressed_calls ? `抑制 ${metrics.suppressed_calls}` : null,
      metrics.denied_calls ? `拒绝 ${metrics.denied_calls}` : null,
      metrics.pending_approval_calls ? `待审批 ${metrics.pending_approval_calls}` : null,
      metrics.budget_capped ? "预算触顶" : null,
      metrics.failure_recovery && metrics.failure_recovery !== "not_triggered"
        ? `失败恢复：${evalRecoveryLabel(metrics.failure_recovery)}`
        : null,
      citations ? `内容 ${citations.verified}/${citations.scored}` : null,
      citations?.answer_path_grounding_rate == null
        ? null
        : `路径 ${(citations.answer_path_grounding_rate * 100).toFixed(0)}%`,
      `无效率 ${formatEvalMetric("invalid_action_rate", metrics.invalid_action_rate)}`,
      metrics.total_tokens == null ? null : `${Number(metrics.total_tokens).toLocaleString()} tok`,
      metrics.elapsed_ms == null ? null : formatEvalMetric("elapsed_ms", metrics.elapsed_ms),
    ].filter(Boolean);
    return `
      <details class="eval-case ${item.passed ? "passed" : "failed"}">
        <summary>
          <span class="eval-case-verdict">${item.passed ? "PASS" : "FAIL"}</span>
          <span class="eval-case-id">${escapeHtml(item.case_id)}</span>
          <span class="eval-case-stats">${escapeHtml(stats.join(" · "))}</span>
        </summary>
        <div class="eval-case-body">
          ${item.error ? `<p class="eval-case-error">${escapeHtml(item.error)}</p>` : ""}
          ${failed.length ? `<ul class="eval-constraint-list">${failed.map((entry) =>
            `<li><strong>${escapeHtml(entry.name)}</strong> ${escapeHtml(truncate(entry.detail, 240))}</li>`).join("")}</ul>` : ""}
          ${citations && citations.ungrounded_paths?.length
            ? `<p class="eval-case-error">答案引用了未读过的路径：${escapeHtml(citations.ungrounded_paths.join(", "))}</p>`
            : ""}
          ${citations && citations.failures?.length
            ? `<ul class="eval-constraint-list">${citations.failures.map((entry) =>
              `<li><strong>${escapeHtml(entry.status)}</strong> ${escapeHtml(entry.path)}:${entry.start_line}-${entry.end_line}</li>`).join("")}</ul>`
            : ""}
          ${renderEvalCallLifecycle(metrics)}
          ${renderEvalReadEvidence(item.read_evidence || [])}
          ${renderEvalAgentErrors(item.agent_errors || [])}
          <p class="eval-case-trace">${escapeHtml((item.trace_nodes || []).join(" › ") || "无轨迹")}</p>
        </div>
      </details>`;
  }).join("");
}

function renderEvalCallLifecycle(metrics) {
  const groups = [
    ["Proposed", metrics.proposed_call_details || []],
    ["Executed", metrics.executed_call_details || []],
    ["Suppressed", metrics.suppressed_call_details || []],
    ["Denied", metrics.denied_call_details || []],
    ["Pending approval", metrics.pending_approval_call_details || []],
  ].filter(([, calls]) => calls.length);
  if (!groups.length) {
    return "";
  }
  return groups.map(([label, calls]) => `
    <details class="eval-evidence-group">
      <summary>${escapeHtml(label)} · ${calls.length}</summary>
      <ul class="eval-constraint-list">${calls.map((call) => {
        const outcome = call.ok == null ? "" : (call.ok ? " · ok" : " · failed");
        const reason = call.reason ? ` · ${call.reason}` : "";
        const args = truncate(JSON.stringify(call.arguments || {}), 240);
        return `<li><strong>${escapeHtml(call.name || "unknown")}</strong> ${escapeHtml(call.call_id || "no-call-id")}${escapeHtml(outcome + reason)}<br><code>${escapeHtml(args)}</code></li>`;
      }).join("")}</ul>
    </details>`).join("");
}

function renderEvalReadEvidence(evidence) {
  if (!evidence.length) {
    return "";
  }
  return `
    <details class="eval-evidence-group">
      <summary>成功读取证据 · ${evidence.length}</summary>
      <ul class="eval-constraint-list">${evidence.map((item) =>
        `<li><strong>${escapeHtml(item.path || "unknown")}:${item.start_line ?? "?"}-${item.end_line ?? "?"}</strong> ${escapeHtml(item.source || "")} · ${escapeHtml(item.call_id || "initial-context")}${item.truncated ? " · truncated" : ""}</li>`).join("")}</ul>
    </details>`;
}

function renderEvalAgentErrors(errors) {
  if (!errors.length) {
    return "";
  }
  return `
    <details class="eval-evidence-group">
      <summary>Agent 真实错误 · ${errors.length}</summary>
      <ul class="eval-constraint-list">${errors.map((item) =>
        `<li>${escapeHtml(truncate(JSON.stringify(item), 320))}</li>`).join("")}</ul>
    </details>`;
}

function evalRecoveryLabel(value) {
  const labels = {
    recovered: "换策略",
    retry_loop: "死磕重试",
    gave_up: "放弃",
    not_triggered: "未触发",
  };
  return labels[value] || value;
}

function renderEvalHistory() {
  const list = $("eval-history-list");
  if (!list) {
    return;
  }
  const runs = state.evalHistory || [];
  if (!runs.length) {
    list.innerHTML = '<div class="empty-state">暂无历史</div>';
    return;
  }
  list.innerHTML = runs.map((run) => {
    const active = run.run_id === state.evalRunId;
    const passRate = run.metrics
      ? formatEvalMetric("pass_rate", run.metrics.pass_rate)
      : "—";
    return `
      <button type="button" class="eval-history-row${active ? " active" : ""}" data-eval-run="${escapeHtml(run.run_id)}">
        <span class="status-pill ${statusClass(run.status)}"><span class="status-dot"></span>${escapeHtml(humanizeStatus(run.status))}</span>
        <span class="eval-history-provider">${escapeHtml(run.provider)}${run.model ? ` · ${escapeHtml(run.model)}` : ""}</span>
        <span class="eval-history-score">${escapeHtml(passRate)} · ${run.passed_cases}/${run.total_cases}</span>
        <span class="eval-history-alerts${run.critical_alert_count ? " danger" : ""}">${run.alert_count} 预警</span>
        <span class="eval-history-meta">${escapeHtml(formatDate(run.started_at))}${run.is_baseline ? " · 基线" : ""}</span>
      </button>`;
  }).join("");
}

function scheduleEvalPoll(runId) {
  if (state.evalPollTimer) {
    clearTimeout(state.evalPollTimer);
  }
  state.evalPollTimer = setTimeout(async () => {
    state.evalPollTimer = null;
    try {
      const detail = await loadEvalRun(runId);
      if (!EVAL_TERMINAL_STATUSES.has(detail.status)) {
        scheduleEvalPoll(runId);
        return;
      }
      state.evalHistory = (await fetchJson("/evals/runs?limit=20")).runs || [];
      renderEvalHistory();
      if (detail.status === "failed") {
        showToast(`评测失败：${detail.error}`, "error");
      } else {
        showToast(`评测完成：${detail.passed_cases}/${detail.total_cases} 通过`, "success");
      }
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  }, 1200);
}

async function startEvalRun() {
  const select = $("eval-provider-select");
  const provider = select?.selectedOptions?.[0]?.dataset.provider || "fake";
  const model = select?.selectedOptions?.[0]?.dataset.model || "";
  if (provider !== "fake") {
    const confirmed = window.confirm(
      `将对 ${provider} 发起 ${state.evalCatalogue?.cases?.length || 0} 个真实模型用例，会产生 Token 费用。继续？`,
    );
    if (!confirmed) {
      return;
    }
  }
  try {
    const started = await fetchJson("/evals/runs", {
      method: "POST",
      body: JSON.stringify({ provider, model }),
    });
    state.evalRunId = started.run_id;
    await loadEvalRun(started.run_id);
    state.evalHistory = (await fetchJson("/evals/runs?limit=20")).runs || [];
    renderEvalHistory();
    scheduleEvalPoll(started.run_id);
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function pinEvalBaseline() {
  if (!state.evalRunId) {
    return;
  }
  const criticalCount = Number(state.evalRun?.critical_alert_count || 0);
  let force = false;
  if (criticalCount) {
    if (!window.confirm(`本次评测有 ${criticalCount} 个 critical 预警，默认禁止设为基线。是否进入强制确认？`)) {
      return;
    }
    if (!window.confirm("再次确认：强制基线会记录 forced=true，但不会消除预警。继续？")) {
      return;
    }
    force = true;
  }
  try {
    await fetchJson(`/evals/runs/${encodeURIComponent(state.evalRunId)}/baseline?force=${force}`, {
      method: "POST",
    });
    await loadEvalDashboard(state.evalRunId);
    showToast("已设为该 Provider / Model / Suite / Evaluator 的基线", "success");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
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
  invalidateSlashCapabilities();
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
  invalidateSlashCapabilities();
  const selected = registeredModel(state.modelPreference.preferred_model_id);
  if (state.currentSession) {
    state.currentSession.provider = selected?.provider || null;
    state.currentSession.model = selected?.model || null;
  }
  renderSessionModelControls();
  await loadSessionTokenUsage([conversationId]);
  showToast(automatic ? "已启用自动选模" : `首选模型已切换为 ${selected?.display_name || "手动模型"}`);
}

function renderProviderConnections() {
  const grid = $("provider-connection-grid");
  grid.innerHTML = MODEL_PROVIDERS.map(([provider, displayName]) => {
    const connection = state.modelRegistry.connections.find((item) => item.provider === provider);
    const status = connection?.status || "unavailable";
    const configured = connection?.credential_configured === true;
    const credentialMessage = connection?.credential_error
      || (configured ? "凭证已安全保存" : "尚未配置凭证，请重新输入 API Key");
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
        <p>${escapeHtml(credentialMessage)} · ${connection?.model_count || 0} 个模型</p>
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
  $("registered-model-output-limit-input").value = String(selected.max_output_tokens || 16384);
  summary.innerHTML = `<strong>${escapeHtml(selected.display_name)}</strong><span>${Number(selected.context_window_tokens).toLocaleString()} ctx · ${Number(selected.max_output_tokens).toLocaleString()} output · ${escapeHtml(capabilities)} · ${escapeHtml(modelTierLabel(selected.quality_tier))} · ${escapeHtml(modelTierLabel(selected.cost_tier))}</span><small>Provider 元数据 + 后端路由画像；延迟将在真实请求后自动学习。</small>`;
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
  $("registered-model-output-limit-input").value = (
    $("registered-model-provider-input").value === "deepseek" ? "8192" : "16384"
  );
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
  const maxOutputTokens = Number($("registered-model-output-limit-input").value);
  if (
    !Number.isInteger(maxOutputTokens)
    || maxOutputTokens < 1
    || (!manualModel && discovered && maxOutputTokens > discovered.context_window_tokens)
  ) {
    showToast("最大输出 token 必须是正整数，且不能超过上下文窗口", "warning");
    return;
  }
  const payload = {
    provider,
    model,
    max_output_tokens: maxOutputTokens,
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
        max_output_tokens: changes.max_output_tokens ?? model.max_output_tokens,
      }),
    });
    await loadModelRegistry();
    showToast("模型配置已更新");
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
        <div class="model-stat-row"><span>${escapeHtml(latency)}</span><span>成功 ${telemetry.success_rate == null ? "—" : `${Math.round(telemetry.success_rate * 100)}%`}</span><span>${model.context_window_tokens.toLocaleString()} ctx</span><span>${model.max_output_tokens.toLocaleString()} output</span></div>
        <p>${escapeHtml(modelTierLabel(routing.quality_tier))} · ${escapeHtml(modelTierLabel(routing.cost_tier))} · ${model.auto_eligible ? "可自动选择" : "仅手动选择"}${model.enabled ? "" : " · 已停用"}${telemetry.last_error ? ` · ${escapeHtml(truncate(telemetry.last_error, 80))}` : ""}</p>
        <div class="model-output-control"><label>最大输出 token<input type="number" min="1" max="1000000" step="1" value="${model.max_output_tokens}" data-model-output-limit /></label><button class="button ghost" type="button" data-model-action="save-output-limit">保存上限</button></div>
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
  setChatWorkbenchActive(false);
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
  state.changeSetRequestGeneration += 1;
  state.latestRunId = "";
  state.latestRunStatus = "";
  state.latestRunConversationId = "";
  state.latestRunBody = null;
  state.checkpointHistory = [];
  state.checkpointRunId = "";
  state.selectedCheckpointId = "";
  state.currentChangeSet = null;
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
  let contentNode = chatContentForRun(runId);
  if (!contentNode && answerPersisted) {
    const lastAssistant = [...document.querySelectorAll(".chat-message.assistant")].at(-1);
    if (lastAssistant) {
      lastAssistant.dataset.agentRunId = runId;
      contentNode = lastAssistant.querySelector(".message-content");
    }
  }
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
  const previousDraftKey = composerDraftKey();
  try {
    const body = await fetchJson("/sessions", {
      method: "POST",
      body: JSON.stringify({ user_id: $("user-id-input").value.trim() || "demo_user" }),
    });
    state.currentSession = body;
    state.conversationId = body.id;
    if (previousDraftKey === "__new__" && state.composerDrafts.__new__) {
      state.composerDrafts[body.id] = state.composerDrafts.__new__;
      delete state.composerDrafts.__new__;
    }
    invalidateSlashCapabilities();
    resetLatestAgentRunState();
    $("conversation-id-input").value = body.id;
    $("session-status").textContent = "会话已就绪";
    resetChatView();
    applyConfigurationToInputs(body);
    await loadModelPreference(body.id);
    restoreComposerDraft(body.id);
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
  if (sessionIds.includes(state.conversationId)) {
    updateComposerScopeSummary();
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
  const previousConversationId = state.conversationId;
  resetLatestAgentRunState();
  state.currentSession = session;
  state.conversationId = session.id;
  if (previousConversationId !== state.conversationId) {
    invalidateSlashCapabilities();
  }
  $("conversation-id-input").value = session.id;
  state.sessionTokenUsage[conversationId] = usage;
  $("session-status").textContent = session.archived_at ? "正在查看已归档会话" : "会话已加载";
  renderSessionSummary(summary, usage);
  renderMessages(messages.messages || []);
  renderChatHistory(messages.messages || []);
  applyConfigurationToInputs(session);
  await loadModelPreference(session.id);
  restoreComposerDraft(session.id);
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
    updateComposerScopeSummary();
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
              usage.context?.budget_tokens
                ? `模型上下文预算 <strong>${escapeHtml(formatTokenCount(usage.context.budget_tokens))}</strong> tokens`
                : "未解析到模型上下文预算"
            }${
              usage.context?.dropped_messages || usage.context?.truncated_messages
                ? ` · 上一次装配丢弃 ${escapeHtml(usage.context.dropped_messages || 0)} 条、截断 ${escapeHtml(usage.context.truncated_messages || 0)} 条`
                : ""
            }</small>
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
  state.followConversation = true;
  setChatWorkbenchActive(true);
  output.innerHTML = "";
  for (const message of chatMessages) {
    appendChatMessage(message.role, message.content, message.created_at);
  }
  scrollConversationToLatest({ behavior: "auto" });
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
  setChatWorkbenchActive(true);
  const item = document.createElement("article");
  item.className = `chat-message ${role}`;
  item.dataset.messageContent = content;
  const roleLabel = role === "user" ? "你" : "AI 助手";
  const avatar = role === "user" ? "你" : "A";
  item.innerHTML = `
    <div class="chat-avatar" aria-hidden="true">${avatar}</div>
    <div class="chat-bubble">
      <div class="message-label"><strong>${roleLabel}</strong><span>${escapeHtml(createdAt ? formatDate(createdAt) : "刚刚")}</span></div>
      <div class="message-content rich-output">${content ? renderMarkdown(content) : '<span class="typing-indicator" aria-label="正在生成"><span></span><span></span><span></span></span>'}</div>
      <div class="message-actions" aria-label="消息操作">
        <button class="message-action" type="button" data-message-action="copy" aria-label="复制这条消息" title="复制">
          ${iconMarkup("copy")}
        </button>
        ${role === "user" ? `
          <button class="message-action" type="button" data-message-action="edit" aria-label="编辑并重新使用这条消息" title="编辑并重新使用">
            ${iconMarkup("edit")}
          </button>
        ` : ""}
      </div>
    </div>
  `;
  if (runId) {
    item.dataset.agentRunId = runId;
  }
  output.appendChild(item);
  if (role === "user" && !createdAt) {
    state.followConversation = true;
  }
  scheduleConversationFollow();
  return item.querySelector(".message-content");
}

function failureTitle(detail, code = "") {
  const normalized = `${code} ${detail}`.toLowerCase();
  if (normalized.includes("timed out") || normalized.includes("timeout")) {
    return "模型响应超时";
  }
  if (code === "max_output_tokens") {
    return "回答达到输出额度上限";
  }
  return "本次运行未完成";
}

function failureGuidance(detail, code = "") {
  const normalized = `${code} ${detail}`.toLowerCase();
  if (normalized.includes("timed out") || normalized.includes("timeout")) {
    return "可以直接重试，或选择延迟更低的模型后再次运行。";
  }
  if (code === "max_output_tokens") {
    return "已保留生成的部分内容。可以提高模型输出额度，或缩小本次任务范围。";
  }
  return "保留当前会话上下文后重试；需要诊断时打开运行详情查看失败阶段。";
}

function failureRecoveryMarkup(detail, { code = "", requestId = "" } = {}) {
  return `
    <section class="response-error-card" role="alert">
      <h3>${escapeHtml(failureTitle(detail, code))}</h3>
      <p class="response-error-detail">${escapeHtml(detail)}</p>
      <small>${escapeHtml(failureGuidance(detail, code))}${requestId ? ` · Request ID：${escapeHtml(requestId)}` : ""}</small>
      <div class="response-error-actions">
        <button class="button primary" type="button" data-response-action="retry">
          ${iconMarkup("refresh")}重试
        </button>
        <button class="button secondary" type="button" data-response-action="choose-model">更换模型</button>
        <button class="button ghost" type="button" data-response-action="show-details">查看运行详情</button>
      </div>
    </section>
  `;
}

function previousUserPrompt(message) {
  let current = message?.previousElementSibling;
  while (current) {
    if (current.classList.contains("user")) {
      return current.dataset.messageContent || "";
    }
    current = current.previousElementSibling;
  }
  return "";
}

async function handleConversationAction(target) {
  const message = target.closest(".chat-message");
  if (!message) return;
  const messageAction = target.closest("[data-message-action]")?.dataset.messageAction;
  const responseAction = target.closest("[data-response-action]")?.dataset.responseAction;
  if (messageAction === "copy") {
    const failureDetail = message.querySelector(".response-error-detail")?.innerText;
    const text = failureDetail || message.querySelector(".message-content")?.innerText || "";
    try {
      await navigator.clipboard.writeText(text.trim());
      showToast("消息已复制");
    } catch {
      showToast("无法访问剪贴板，请手动选择文本复制", "warning");
    }
    return;
  }
  if (messageAction === "edit") {
    setComposerValue(message.dataset.messageContent || "", { focus: true });
    scrollConversationToLatest({ behavior: preferredScrollBehavior() });
    return;
  }
  if (responseAction === "show-details") {
    setInspectorVisible(true);
    $("close-inspector-btn").focus();
    return;
  }
  if (responseAction === "choose-model") {
    $("composer-config").open = true;
    scrollConversationToLatest({ behavior: preferredScrollBehavior() });
    window.setTimeout(() => {
      const targetControl = $("auto-model-toggle").checked
        ? $("auto-model-toggle")
        : ($("model-picker-trigger").hidden ? $("session-model-select") : $("model-picker-trigger"));
      targetControl.focus();
    }, 0);
    return;
  }
  if (responseAction === "retry") {
    const prompt = previousUserPrompt(message);
    if (!prompt) {
      showToast("没有找到可重试的上一条请求", "warning");
      return;
    }
    setComposerValue(prompt);
    await submitComposerMessage();
  }
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
        <button class="button danger" type="button" data-inline-run-action="cancel">取消 Run</button>
        <button class="button ghost" type="button" data-inline-agent-action="reject">拒绝执行</button>
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
        <button class="button danger" type="button" data-inline-run-action="cancel">取消 Run</button>
        <button class="button primary" type="button" data-inline-agent-action="continue">继续运行</button>
      </div>
    `;
  }

  card.querySelectorAll("[data-inline-agent-action]").forEach((button) => {
    button.addEventListener("click", () => {
      handleInlineAgentAction(contentNode, body, button.dataset.inlineAgentAction, card);
    });
  });
  card.querySelectorAll("[data-inline-run-action]").forEach((button) => {
    button.addEventListener("click", () => {
      handleInlineRunControl(contentNode, body, button.dataset.inlineRunAction, card);
    });
  });
  if (shouldFocusCheckpoint) {
    window.requestAnimationFrame(() => {
      card.scrollIntoView({ behavior: preferredScrollBehavior(), block: "center" });
    });
  }
}

function renderInlineAgentControls(contentNode, body) {
  const bubble = contentNode.closest(".chat-bubble");
  if (!bubble) return;
  const status = agentRunStatus(body);
  let controls = bubble.querySelector(".inline-agent-controls");
  if (!["queued", "running"].includes(status)) {
    controls?.remove();
    return;
  }
  const runId = agentRunId(body) || contentNode.closest("[data-agent-run-id]")?.dataset.agentRunId;
  if (controls?.dataset.runId === runId) return;
  if (!controls) {
    controls = document.createElement("section");
    bubble.appendChild(controls);
  }
  controls.className = "inline-agent-controls";
  controls.dataset.runId = runId;
  controls.setAttribute("aria-label", "Agent 运行控制");
  controls.innerHTML = `
    <label>
      <span>运行中转向 <small>将在下一个安全边界生效</small></span>
      <input maxlength="4000" placeholder="例如：先不要改 API，优先补回归测试" />
    </label>
    <p class="inline-control-error" role="alert" hidden></p>
    <div class="inline-control-actions">
      <button class="button ghost" type="button" data-inline-run-action="steer">发送转向</button>
      <button class="button ghost" type="button" data-inline-run-action="pause">暂停</button>
      <button class="button danger" type="button" data-inline-run-action="cancel">取消</button>
    </div>
  `;
  controls.querySelectorAll("[data-inline-run-action]").forEach((button) => {
    button.addEventListener("click", () => {
      handleInlineRunControl(contentNode, body, button.dataset.inlineRunAction, controls);
    });
  });
}

async function handleInlineRunControl(contentNode, body, action, controls) {
  const runId = controls.dataset.runId;
  const conversationId = agentRunConversationId(body)
    || state.latestRunConversationId
    || state.conversationId;
  if (!runId || conversationId !== state.conversationId) {
    showToast("这条运行记录不属于当前会话，请重新打开对应会话", "warning");
    return;
  }
  const input = controls.querySelector("input, textarea");
  const message = input?.value.trim() || "";
  if (action === "steer" && !message) {
    input.setAttribute("aria-invalid", "true");
    input.focus();
    return;
  }
  const errorNode = controls.querySelector(".inline-control-error");
  const checkpointErrorNode = controls.querySelector(".inline-checkpoint-error");
  controls.setAttribute("aria-busy", "true");
  controls.querySelectorAll("button, input, textarea").forEach((node) => { node.disabled = true; });
  if (errorNode) errorNode.hidden = true;
  if (checkpointErrorNode) checkpointErrorNode.hidden = true;
  try {
    const nextBody = await fetchJson(
      `/agent/runs/${encodeURIComponent(runId)}/${action}`,
      { method: "POST", body: JSON.stringify({ message }) },
    );
    if (conversationId !== state.conversationId) return;
    renderAgentRun(nextBody);
    renderAgentChatResponse(
      contentNode,
      nextBody,
      performance.now() - (nextBody?.result?.metrics?.elapsed_ms || 0),
    );
    setChatStatusFromRun(nextBody);
    showToast({
      pause: "暂停请求已发送，将在安全边界生效",
      cancel: "取消请求已发送",
      steer: "转向信息已加入当前运行",
    }[action] || "运行状态已更新", action === "cancel" ? "warning" : "success");
  } catch (error) {
    controls.removeAttribute("aria-busy");
    controls.querySelectorAll("button, input, textarea").forEach((node) => { node.disabled = false; });
    const visibleErrorNode = errorNode || checkpointErrorNode;
    if (visibleErrorNode) {
      visibleErrorNode.textContent = humanizeError(error);
      visibleErrorNode.hidden = false;
    }
  }
}

function agentChangeSetId(body) {
  return body?.change_set_id || body?.result?.change_set_id || "";
}

function changeSetFileStats(changeSet) {
  const stats = new Map(
    (changeSet.changed_files || []).map((path) => [path, {
      path,
      additions: 0,
      deletions: 0,
      kind: changeSet.baseline_file_hashes?.[path] == null ? "A" : "M",
    }]),
  );
  let current = null;
  let previousPath = null;
  for (const line of String(changeSet.patch || "").split("\n")) {
    const header = line.match(/^diff --git a\/(.+) b\/(.+)$/);
    if (header) {
      current = header[2];
      if (!stats.has(current)) stats.set(current, { path: current, additions: 0, deletions: 0, kind: "M" });
      continue;
    }
    const previousHeader = line.match(/^--- a\/(.+)$/);
    if (previousHeader) {
      previousPath = previousHeader[1];
      continue;
    }
    const nextHeader = line.match(/^\+\+\+ b\/(.+)$/);
    if (nextHeader || line === "+++ /dev/null") {
      current = nextHeader?.[1] || previousPath;
      if (current && !stats.has(current)) {
        stats.set(current, { path: current, additions: 0, deletions: 0, kind: "M" });
      }
      if (current && line === "+++ /dev/null") stats.get(current).kind = "D";
      continue;
    }
    if (!current) continue;
    if (line.startsWith("+") && !line.startsWith("+++")) stats.get(current).additions += 1;
    if (line.startsWith("-") && !line.startsWith("---")) stats.get(current).deletions += 1;
  }
  return [...stats.values()];
}

function ensureInlineChangeReview(contentNode) {
  const bubble = contentNode.closest(".chat-bubble");
  if (!bubble) return null;
  let card = bubble.querySelector(".inline-change-review");
  if (!card) {
    card = document.createElement("section");
    card.className = "inline-change-review";
    card.setAttribute("aria-live", "polite");
    const footer = bubble.querySelector(".inline-run-footer");
    if (footer) {
      bubble.insertBefore(card, footer);
    } else {
      bubble.appendChild(card);
    }
  }
  return card;
}

function renderInlineChangeReview(contentNode, changeSet) {
  const card = ensureInlineChangeReview(contentNode);
  if (!card) return;
  delete card.dataset.loading;
  const files = changeSetFileStats(changeSet);
  const ready = changeSet.status === "ready";
  const patchOnly = changeSet.apply_mode === "patch_only";
  const liveMode = ["direct", "worktree"].includes(changeSet.apply_mode);
  const totalAdditions = files.reduce((sum, file) => sum + file.additions, 0);
  const totalDeletions = files.reduce((sum, file) => sum + file.deletions, 0);
  const applied = changeSet.status === "applied";
  const reverted = changeSet.status === "reverted";
  const rejected = changeSet.status === "rejected";
  const postWriteHashes = changeSet.validation_summary?.post_write_file_hashes || {};
  const recordedLive = liveMode
    && (applied || reverted)
    && files.length > 0
    && files.every((file) => Object.hasOwn(postWriteHashes, file.path));
  const modeNote = patchOnly
    ? "本次运行只修改临时副本；ChangeSet 用于审计和下载，真实工作区保持不变。"
    : reverted
      ? "服务已校验写后哈希并反向应用原补丁；再次回滚会安全返回当前结果。"
      : recordedLive && changeSet.apply_mode === "worktree"
        ? `变更已在 Agent 执行时写入并保留于 Git worktree；当前源码检出未被修改。${changeSet.worktree_path ? ` 路径：${changeSet.worktree_path}` : ""}`
        : recordedLive
          ? "变更已在 Agent 执行时直接写入注册源码根，文件现在对本机其他进程可见；可在下方安全回滚。"
          : changeSet.apply_mode === "worktree"
            ? "这是旧版待应用 ChangeSet；应用前会校验摘要、Git HEAD 和文件基线，并创建隔离 worktree。"
            : "这是旧版待应用 ChangeSet；应用前会重新校验摘要、Workspace revision 和文件基线。";
  const applyLabel = patchOnly
    ? "仅生成补丁"
    : applied
      ? recordedLive ? "回滚已写入变更" : "已应用"
      : reverted
        ? "已安全回滚"
      : rejected
        ? "变更已拒绝"
        : changeSet.apply_mode === "worktree" ? "应用旧版 ChangeSet" : "应用旧版 ChangeSet";
  const safetyTitle = patchOnly
    ? "尚未写入真实工作区"
    : reverted
      ? "已回滚"
      : recordedLive
        ? "已在执行时写入"
        : "历史 ChangeSet 待确认";
  card.dataset.changeSetId = changeSet.id;
  card.dataset.runId = changeSet.run_id;
  card.dataset.status = changeSet.status;
  card.innerHTML = `
    <div class="change-review-heading">
      <div>
        <span class="change-review-kicker">Change review</span>
        <h3>${files.length} 个文件已修改</h3>
      </div>
      <span class="status-pill ${statusClass(changeSet.status)}"><span class="status-dot" aria-hidden="true"></span>${escapeHtml(humanizeStatus(changeSet.status))}</span>
    </div>
    <div class="change-review-summary">
      <span><strong>${files.length}</strong> files</span>
      <span class="diff-addition">+${totalAdditions}</span>
      <span class="diff-deletion">−${totalDeletions}</span>
      <code title="${escapeHtml(changeSet.patch_sha256)}">${escapeHtml(truncate(changeSet.patch_sha256, 18))}</code>
    </div>
    <div class="change-file-list" role="list" aria-label="修改的文件">
      ${files.map((file) => `
        <div class="change-file-row" role="listitem">
          <span class="change-file-mark ${file.kind === "A" ? "added" : file.kind === "D" ? "deleted" : ""}" aria-hidden="true">${file.kind}</span>
          <code title="${escapeHtml(file.path)}">${escapeHtml(file.path)}</code>
          <span><b class="diff-addition">+${file.additions}</b><b class="diff-deletion">−${file.deletions}</b></span>
        </div>
      `).join("")}
    </div>
    <details class="change-diff-details">
      <summary>查看完整 Diff</summary>
      <pre class="change-set-patch"><code>${escapeHtml(changeSet.patch || "没有可显示的补丁")}</code></pre>
    </details>
    <div class="change-safety-note ${patchOnly ? "patch-only" : "writable"}">
      <span aria-hidden="true">${patchOnly ? "!" : reverted ? "↶" : "✓"}</span>
      <p><strong>${escapeHtml(safetyTitle)}</strong>${escapeHtml(modeNote)}</p>
    </div>
    ${changeSet.branch_name || changeSet.worktree_path ? `
      <div class="change-execution-location">
        ${changeSet.branch_name ? `<span>分支 <code>${escapeHtml(changeSet.branch_name)}</code></span>` : ""}
        ${changeSet.worktree_path ? `<span>Worktree <code title="${escapeHtml(changeSet.worktree_path)}">${escapeHtml(changeSet.worktree_path)}</code></span>` : ""}
      </div>
    ` : ""}
    ${changeSet.error ? `<p class="change-review-error" role="alert">${escapeHtml(changeSet.error)}</p>` : ""}
    <div class="change-review-actions">
      <span>${escapeHtml(changeSet.apply_mode)} · ${escapeHtml(changeSet.validation_status)}</span>
      <div>
        <button class="button ghost" type="button" data-inline-change-action="reject" ${ready ? "" : "disabled"}>拒绝变更</button>
        <button class="button primary" type="button" data-inline-change-action="${recordedLive && applied ? "revert" : "apply"}" ${recordedLive && applied ? "" : ready && !patchOnly ? "" : "disabled"}>${applyLabel}</button>
      </div>
    </div>
  `;
  card.querySelectorAll("[data-inline-change-action]").forEach((button) => {
    button.addEventListener("click", () => {
      handleInlineChangeAction(contentNode, changeSet, button.dataset.inlineChangeAction, card);
    });
  });
}

function renderInlineChangeLoading(contentNode) {
  const card = ensureInlineChangeReview(contentNode);
  if (!card) return null;
  card.dataset.loading = "true";
  card.innerHTML = `
    <div class="change-review-loading" role="status">
      <span class="execution-indicator" aria-hidden="true"></span>
      <div><strong>正在准备变更审阅</strong><p>读取文件清单、校验状态和完整 Diff…</p></div>
    </div>
  `;
  return card;
}

async function loadInlineChangeSet(runId, contentNode, { force = false } = {}) {
  const existing = contentNode.closest(".chat-bubble")?.querySelector(".inline-change-review");
  if (!force && (existing?.dataset.loading === "true" || existing?.dataset.changeSetId)) {
    return state.currentChangeSet;
  }
  const requestedConversationId = state.conversationId;
  const generation = ++state.changeSetRequestGeneration;
  const loadingCard = renderInlineChangeLoading(contentNode);
  try {
    const changeSet = await fetchJson(`/agent/runs/${encodeURIComponent(runId)}/changes`);
    if (
      generation !== state.changeSetRequestGeneration
      || requestedConversationId !== state.conversationId
      || changeSet.conversation_id !== state.conversationId
      || !contentNode.isConnected
    ) return null;
    state.currentChangeSet = changeSet;
    renderInlineChangeReview(contentNode, changeSet);
    return changeSet;
  } catch (error) {
    if (loadingCard?.isConnected) {
      delete loadingCard.dataset.loading;
      loadingCard.innerHTML = `<p class="change-review-error" role="alert">ChangeSet 加载失败：${escapeHtml(humanizeError(error))}</p>`;
    }
    throw error;
  }
}

async function handleInlineChangeAction(contentNode, changeSet, action, card) {
  if (changeSet.conversation_id !== state.conversationId) {
    showToast("这份变更不属于当前会话，请重新打开对应会话", "warning");
    return;
  }
  if (action === "apply" && !window.confirm(
    changeSet.apply_mode === "worktree"
      ? `确认从捕获的 Git HEAD 创建隔离 worktree，并应用 ${changeSet.changed_files.length} 个文件？\n补丁摘要：${changeSet.patch_sha256}`
      : `确认将 ${changeSet.changed_files.length} 个文件写入真实工作区？\n补丁摘要：${changeSet.patch_sha256}`,
  )) return;
  if (action === "revert" && !window.confirm(
    `确认回滚 Agent 已写入的 ${changeSet.changed_files.length} 个文件？当前文件必须仍与写入后哈希一致。\n补丁摘要：${changeSet.patch_sha256}`,
  )) return;
  card.setAttribute("aria-busy", "true");
  card.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  try {
    const updated = await fetchJson(
      `/agent/runs/${encodeURIComponent(changeSet.run_id)}/changes/${action}`,
      {
        method: "POST",
        body: JSON.stringify(["apply", "revert"].includes(action)
          ? { change_set_id: changeSet.id, patch_sha256: changeSet.patch_sha256 }
          : { change_set_id: changeSet.id }),
      },
    );
    state.currentChangeSet = updated;
    renderInlineChangeReview(contentNode, updated);
    showToast(
      action === "apply"
        ? "ChangeSet 已应用到真实工作区"
        : action === "revert"
          ? "Agent 写入的变更已安全回滚"
          : "ChangeSet 已拒绝",
      "success",
    );
  } catch (error) {
    await loadInlineChangeSet(changeSet.run_id, contentNode, { force: true }).catch(() => null);
    showToast(humanizeError(error), "error");
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
  const submission = composerSubmission($("chat-message-input").value);
  if (submission.invocation?.item.kind === "builtin") {
    await runBuiltinComposerCommand(
      submission.invocation.item,
      submission.invocation.remaining,
    );
    return;
  }
  if (
    ["skill", "mcp"].includes(submission.invocation?.item.kind)
    && state.composerMode !== "agent"
  ) {
    await persistComposerMode("agent");
  }
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
  const streamEvents = body?.stream_events || [];
  const streamedAnswer = String(body?.streamed_answer || "");
  const elapsedMs = result.metrics?.elapsed_ms ?? Math.round(performance.now() - startedAt);
  renderExecutionProcess(contentNode, {
    trace,
    status,
    elapsedMs,
    fallbackNode: body?.latest_node || "setup_workspace",
    fallbackSummary: actualStatus === "queued"
      ? "Agent 任务已进入执行队列。"
      : "Agent 正在运行 LangGraph 工作流。",
    events: streamEvents,
  });

  if (!holdAnswer && actualStatus === "cancelled") {
    contentNode.innerHTML = "<p><em>Agent 运行已停止。</em></p>";
  } else if (!holdAnswer && ["completed", "partial", "blocked"].includes(actualStatus)) {
    contentNode.innerHTML = result.answer
      ? renderMarkdown(result.answer)
      : "<p>Agent 已完成，但没有返回文本内容。</p>";
  } else if (!holdAnswer && actualStatus === "waiting_approval") {
    contentNode.innerHTML = "<p>Agent 已在执行前暂停。请检查下方计划并决定是否继续。</p>";
  } else if (!holdAnswer && ["paused", "waiting_input"].includes(actualStatus)) {
    contentNode.innerHTML = "<p>Agent 已在安全边界暂停，请在下方补充信息或继续。</p>";
  } else if (!holdAnswer && actualStatus === "failed") {
    const detail = body.error || "Agent 运行失败，请查看运行详情。";
    contentNode.innerHTML = failureRecoveryMarkup(detail, {
      code: body.error_code || "agent_run_failed",
      requestId: body.request_id || "",
    });
  } else if (streamedAnswer) {
    contentNode.innerHTML = renderMarkdown(streamedAnswer);
  } else {
    contentNode.innerHTML = "";
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
    renderInlineAgentControls(contentNode, body);
    const changeSetId = agentChangeSetId(body);
    if (changeSetId && FINAL_RUN_STATUSES.has(actualStatus)) {
      loadInlineChangeSet(agentRunId(body), contentNode).catch((error) => {
        showToast(`ChangeSet 加载失败：${humanizeError(error)}`, "error");
      });
    }
    renderInlineRunFooter(contentNode, body);
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
        if (body?.stream_events) {
          visibleTrace.splice(0, visibleTrace.length, ...fullTrace);
          renderAgentChatResponse(contentNode, body, startedAt, { visibleTrace });
          return;
        }
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
  const submission = composerSubmission(input.value);
  if (!submission.message) {
    input.setAttribute("aria-invalid", "true");
    showToast("请输入一条消息", "warning");
    input.focus();
    return;
  }

  const expectedCapabilityKey = state.conversationId && state.activeWorkspaceId
    ? `${state.conversationId}:${state.activeWorkspaceId}`
    : "";
  if (!expectedCapabilityKey || state.slashCapabilityKey !== expectedCapabilityKey) {
    await loadSlashCapabilities();
  }

  const sendButton = $("send-chat-btn");
  const modeInput = $("composer-mode-input");
  sendButton.disabled = true;
  sendButton.setAttribute("aria-busy", "true");
  modeInput.disabled = true;
  clearComposerInput();
  let submitted = false;
  let assistantContent = null;
  let progressPresenter = null;
  const startedAt = performance.now();
  setChatStatus("正在提交给 Agent", "running");
  try {
    const run = await runAgent({
      message: submission.message,
      focusFiles: [],
      skillName: submission.skillName,
      skillArguments: submission.skillArguments,
      preferredToolName: submission.preferredToolName,
      onSubmitted: (body) => {
        submitted = true;
        appendChatMessage("user", submission.message);
        assistantContent = appendChatMessage("assistant", "", null, {
          runId: agentRunId(body),
        });
        progressPresenter = createAgentProgressPresenter(assistantContent, startedAt);
        progressPresenter.update(body);
        startResponseTimer(assistantContent, startedAt);
        setChatStatus("Agent 运行中", "running");
        showToast("任务已交给 Agent；运行、审批和代码变更会显示在当前对话中");
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
      if (!input.value.trim()) {
        setComposerValue(submission.message);
      }
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
  const submission = composerSubmission(input.value);
  const message = submission.message;
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
  let requestAccepted = false;

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
    clearComposerInput();
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
        requestAccepted = true;
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
        } else if (eventName === "context") {
          const pressure = [];
          if (data.synchronous_compactions) {
            pressure.push(`同步压缩 ${data.synchronous_compactions} 次`);
          }
          if (data.dropped_messages) {
            pressure.push(`丢弃最旧 ${data.dropped_messages} 条`);
          }
          if (data.truncated_messages) {
            pressure.push(`截断 ${data.truncated_messages} 条`);
          }
          chatTrace.push({
            step: chatTrace.length + 1,
            node: "assemble_context",
            summary: `上下文 ≈ ${formatTokenCount(data.estimated_tokens || 0)} / ${
              data.budget_tokens
                ? formatTokenCount(data.budget_tokens)
                : "未设预算"
            } tokens${data.includes_summary ? " · 含滚动摘要" : ""}${
              pressure.length ? ` · ${pressure.join("、")}` : ""
            }。`,
            output: data,
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
    if (!requestAccepted && !input.value.trim()) {
      setComposerValue(message);
    }
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
        const recovery = failureRecoveryMarkup(detail, {
          code: error.code || "llm_provider_error",
        });
        assistantContent.innerHTML = error.preservePartial
          ? `${renderMarkdown(answer)}${recovery}`
          : recovery;
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

async function runAgent({
  message = "",
  focusFiles = [],
  skillName = null,
  skillArguments = [],
  preferredToolName = null,
  onSubmitted = null,
  onProgress = null,
} = {}) {
  const normalizedMessage = message.trim();
  if (!normalizedMessage) {
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
  try {
    conversationId = await ensureSession();
    const payload = {
      conversation_id: conversationId,
      message: normalizedMessage,
      workspace_id: workspace.id,
      ...optionalModelFields(),
      focus_files: focusFiles,
      ...(skillName ? {
        skill_name: skillName,
        skill_arguments: skillArguments,
      } : {}),
      ...(preferredToolName ? { preferred_tool_name: preferredToolName } : {}),
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
      showToast(humanizeError(error), "error");
    }
    return submittedRun;
  } finally {
    updateComposerAvailability();
  }
}

function activeRunPresentation(body) {
  const status = agentRunStatus(body);
  const latestNode = body?.latest_node || body?.result?.latest_node || "";
  const presentations = {
    queued: ["QUEUED RUN", "Agent 正在排队", "任务已提交，等待执行器接手"],
    running: ["ACTIVE RUN", "Agent 正在运行", latestNode
      ? `当前阶段：${humanizeAgentNode(latestNode)} · 控制操作将在安全边界生效`
      : "暂停与取消会在下一个安全边界生效"],
    waiting_approval: ["APPROVAL REQUIRED", "Agent 等待你的确认", "请在对话内查看工具计划并批准或拒绝"],
    waiting_input: ["INPUT REQUIRED", "Agent 等待新的方向", "补充信息后可从当前 checkpoint 继续"],
    paused: ["RUN PAUSED", "Agent 已暂停", "执行状态已保留，可继续运行或选择历史 checkpoint"],
    completed: ["RUN COMPLETE", "Agent 已完成", "可查看 checkpoint 历史并从任意可恢复节点创建新 Run"],
    partial: ["RUN PARTIAL", "Agent 部分完成", "可查看结果，或从历史 checkpoint 创建新 Run"],
    blocked: ["RUN BLOCKED", "Agent 运行受阻", "可查看 checkpoint 历史并尝试新的执行方向"],
    cancelled: ["RUN CANCELLED", "Agent 已取消", "历史 checkpoint 仍然保留"],
    failed: ["RUN FAILED", "Agent 运行失败", body?.error || "可从失败前的 checkpoint 创建新 Run"],
  };
  return presentations[status] || ["AGENT RUN", humanizeStatus(status), "可查看当前执行记录"];
}

function renderInlineRunFooter(contentNode, body) {
  const bubble = contentNode.closest(".chat-bubble");
  if (!bubble) return;
  const runId = agentRunId(body)
    || contentNode.closest("[data-agent-run-id]")?.dataset.agentRunId
    || "";
  const status = agentRunStatus(body);
  let footer = bubble.querySelector(".inline-run-footer");
  if (!runId || !status) {
    footer?.remove();
    return;
  }
  if (!footer) {
    footer = document.createElement("section");
  }
  const [, title, detail] = activeRunPresentation(body);
  const checkpointAvailable = status !== "queued";
  footer.className = "inline-run-footer";
  footer.dataset.runId = runId;
  footer.dataset.status = status;
  footer.setAttribute("aria-label", "Agent Run 状态与检查点");
  footer.innerHTML = `
    <div class="inline-run-footer-copy">
      <span class="inline-run-status-dot" aria-hidden="true"></span>
      <div>
        <strong>${escapeHtml(title)}</strong>
        <small>${escapeHtml(detail)}</small>
      </div>
    </div>
    ${checkpointAvailable ? `
      <button class="button ghost inline-checkpoint-history-button" type="button" data-inline-checkpoint-history>
        ${iconMarkup("branch")}查看检查点
      </button>
    ` : ""}
  `;
  footer.querySelector("[data-inline-checkpoint-history]")?.addEventListener("click", () => {
    openCheckpointHistory(runId).catch((error) => showToast(humanizeError(error), "error"));
  });
  bubble.appendChild(footer);
}

function checkpointTitle(checkpoint) {
  if (checkpoint.latest_node) return humanizeAgentNode(checkpoint.latest_node);
  if (checkpoint.next_nodes?.length) return `进入 ${humanizeAgentNode(checkpoint.next_nodes[0])}`;
  return checkpoint.step < 0 ? "Run 起点" : "执行结束";
}

function checkpointStateLabel(checkpoint) {
  if (checkpoint.is_current) return "当前状态";
  if (checkpoint.interrupt) return "暂停边界";
  if (!checkpoint.can_restore) return "仅供查看";
  return "可恢复";
}

function checkpointBadges(checkpoint) {
  const badges = [];
  if (checkpoint.is_current) badges.push("current");
  if (checkpoint.interrupt) badges.push("interrupt");
  if (checkpoint.origin_run_id) badges.push(checkpoint.restore_mode || "branch");
  if (!checkpoint.can_restore) badges.push("terminal");
  return badges;
}

function renderCheckpointHistory() {
  const list = $("checkpoint-history-list");
  const checkpoints = state.checkpointHistory;
  $("checkpoint-run-label").textContent = state.checkpointRunId
    ? state.checkpointRunId.slice(0, 12)
    : "—";
  if (!checkpoints.length) {
    list.innerHTML = '<div class="empty-state">此 Run 尚未生成 checkpoint。</div>';
    state.selectedCheckpointId = "";
    renderCheckpointDetail();
    return;
  }
  if (!checkpoints.some((item) => item.checkpoint_id === state.selectedCheckpointId)) {
    state.selectedCheckpointId = (checkpoints.find((item) => item.can_restore) || checkpoints[0]).checkpoint_id;
  }
  list.innerHTML = checkpoints.map((checkpoint) => {
    const selected = checkpoint.checkpoint_id === state.selectedCheckpointId;
    const badges = checkpointBadges(checkpoint);
    return `
      <button class="checkpoint-card${checkpoint.is_current ? " current" : ""}${checkpoint.interrupt ? " interrupted" : ""}"
        type="button" role="option" aria-selected="${selected}" data-checkpoint-id="${escapeHtml(checkpoint.checkpoint_id)}">
        <span class="checkpoint-node" aria-hidden="true"></span>
        <span class="checkpoint-card-copy">
          <strong>${escapeHtml(checkpointTitle(checkpoint))}</strong>
          <small>${escapeHtml(checkpoint.summary || `下一节点：${(checkpoint.next_nodes || []).map(humanizeAgentNode).join("、") || "无"}`)}</small>
          ${badges.length ? `<span class="checkpoint-card-badges">${badges.map((badge) => `<span>${escapeHtml(badge)}</span>`).join("")}</span>` : ""}
        </span>
        <span class="checkpoint-card-time">${escapeHtml(formatDate(checkpoint.created_at))}</span>
      </button>
    `;
  }).join("");
  renderCheckpointDetail();
}

function renderCheckpointDetail() {
  const checkpoint = state.checkpointHistory.find(
    (item) => item.checkpoint_id === state.selectedCheckpointId,
  );
  $("checkpoint-detail-empty").hidden = Boolean(checkpoint);
  $("checkpoint-detail").hidden = !checkpoint;
  if (!checkpoint) return;
  $("checkpoint-detail-step").textContent = checkpoint.step < 0
    ? "RUN START"
    : `STEP ${checkpoint.step}`;
  $("checkpoint-detail-title").textContent = checkpointTitle(checkpoint);
  $("checkpoint-detail-state").textContent = checkpointStateLabel(checkpoint);
  $("checkpoint-detail-summary").textContent = checkpoint.summary || "该 checkpoint 没有额外摘要。";
  $("checkpoint-detail-id").textContent = checkpoint.checkpoint_id;
  $("checkpoint-detail-next").textContent = checkpoint.next_nodes?.length
    ? checkpoint.next_nodes.map(humanizeAgentNode).join(" → ")
    : "无（图已结束）";
  $("checkpoint-detail-tools").textContent = formatTokenCount(checkpoint.tool_call_count);
  $("checkpoint-detail-files").textContent = checkpoint.changed_files?.length
    ? checkpoint.changed_files.join("、")
    : "无";
  const sourceIsActive = state.checkpointRunId === state.latestRunId
    && ["queued", "running"].includes(state.latestRunStatus);
  const enabled = checkpoint.can_restore && !sourceIsActive;
  $("checkpoint-fork-btn").disabled = !enabled;
  $("checkpoint-rollback-btn").disabled = !enabled;
  $("checkpoint-action-error").hidden = true;
  $("checkpoint-history-status").textContent = sourceIsActive
    ? "请先暂停当前 Run，再从历史 checkpoint 创建新路径。"
    : checkpoint.can_restore
      ? "恢复会创建新 Run；原 Run 与 checkpoint 会完整保留。"
      : "此节点没有待执行的下一步，仅供审阅。";
}

async function openCheckpointHistory(runId = state.latestRunId) {
  if (!runId) {
    showToast("当前会话还没有 Agent Run", "warning");
    return;
  }
  state.checkpointRunId = runId;
  state.checkpointHistory = [];
  state.selectedCheckpointId = "";
  $("checkpoint-direction-input").value = "";
  $("checkpoint-history-list").innerHTML = '<div class="empty-state">正在读取 checkpoint…</div>';
  $("checkpoint-history-status").textContent = "正在读取执行图历史…";
  $("checkpoint-history-dialog").showModal();
  try {
    const response = await fetchJson(
      `/agent/runs/${encodeURIComponent(runId)}/checkpoints?limit=200`,
    );
    if (runId !== state.checkpointRunId || !$("checkpoint-history-dialog").open) return;
    state.checkpointHistory = response.checkpoints || [];
    renderCheckpointHistory();
  } catch (error) {
    state.checkpointHistory = [];
    $("checkpoint-history-list").innerHTML = `<div class="empty-state">${escapeHtml(humanizeError(error))}</div>`;
    $("checkpoint-history-status").textContent = "Checkpoint 历史加载失败。";
  }
}

function closeCheckpointHistory() {
  const dialog = $("checkpoint-history-dialog");
  if (dialog.open) dialog.close();
}

async function restoreSelectedCheckpoint(mode) {
  const checkpoint = state.checkpointHistory.find(
    (item) => item.checkpoint_id === state.selectedCheckpointId,
  );
  if (!checkpoint || !checkpoint.can_restore) return;
  const runId = state.checkpointRunId;
  const message = $("checkpoint-direction-input").value.trim();
  const errorNode = $("checkpoint-action-error");
  const dialog = $("checkpoint-history-dialog");
  dialog.setAttribute("aria-busy", "true");
  dialog.querySelectorAll("button, textarea").forEach((node) => { node.disabled = true; });
  errorNode.hidden = true;
  let restoreError = "";
  let restoreAccepted = false;
  try {
    const response = await fetchJson(
      `/agent/runs/${encodeURIComponent(runId)}/checkpoints/${encodeURIComponent(checkpoint.checkpoint_id)}/restore`,
      { method: "POST", body: JSON.stringify({ mode, message }) },
    );
    restoreAccepted = true;
    const conversationId = response.forked_conversation_id || response.conversation_id;
    closeCheckpointHistory();
    await refreshRecentSessions().catch(() => null);
    await loadSession(true, conversationId);
    showToast(mode === "fork"
      ? "已从 checkpoint 分叉为新会话，并启动新的 Run"
      : "已从 checkpoint 创建新的执行路径", "success");
  } catch (error) {
    if (restoreAccepted) {
      showToast(`新的执行路径已创建，但界面切换失败：${humanizeError(error)}`, "warning");
    } else {
      restoreError = humanizeError(error);
    }
  } finally {
    dialog.removeAttribute("aria-busy");
    if (dialog.open) {
      dialog.querySelectorAll("button, textarea").forEach((node) => { node.disabled = false; });
      renderCheckpointDetail();
      if (restoreError) {
        errorNode.textContent = restoreError;
        errorNode.hidden = false;
      }
    }
  }
}

function renderAgentRun(body) {
  const result = body.result || {};
  state.latestRunId = body.run_id || result.run_id || "";
  state.latestRunStatus = body.status || result.status || "";
  state.latestRunConversationId = agentRunConversationId(body);
  state.latestRunBody = body;
  setTrace(body.trace || result.trace || []);
  setRaw(body);
  renderOverview();
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
    renderAgentRun(body);
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
  if (!runId) return null;
  const body = await fetchJson(`/agent/runs/${encodeURIComponent(runId)}/events`);
  const isCurrentRun = runId === state.latestRunId
    && conversationId === state.conversationId;
  if (render && isCurrentRun) {
    setTrace(agentProgressBodyFromEvents(body.events || [], runId).trace);
    renderOverview();
  }
  if (showRaw && isCurrentRun) {
    setRaw(body);
  }
  return body;
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
  const lastCompletedSequence = Math.max(
    0,
    ...events
      .filter((event) => event.type === "node_completed")
      .map((event) => Number(event.sequence) || 0),
  );
  const activeNode = [...events].reverse().find(
    (event) => event.type === "node_started"
      && (Number(event.sequence) || 0) > lastCompletedSequence,
  );
  if (activeNode) {
    trace.push({
      step: trace.length + 1,
      node: activeNode.node || "step",
      summary: activeNode.summary || "Agent 正在执行当前阶段。",
      output: activeNode.output || {},
      live: true,
    });
  }
  const latestEvent = events.at(-1) || {};
  return {
    run_id: runId,
    status: latestEvent.status || state.latestRunStatus || "running",
    latest_node: latestEvent.node || trace.at(-1)?.node || null,
    trace,
    stream_events: events,
    streamed_answer: events
      .filter((event) => event.type === "answer_delta")
      .map((event) => String(event.output?.text || ""))
      .join(""),
  };
}

function renderStreamedAgentProgress(events, runId = state.latestRunId) {
  const body = agentProgressBodyFromEvents(events, runId);
  state.latestRunStatus = body.status;
  state.latestRunBody = {
    ...(state.latestRunBody || {}),
    ...body,
    conversation_id: state.latestRunConversationId || state.conversationId,
  };
  setTrace(body.trace);
  renderOverview();
  return body;
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
        const parsed = parseSseBlock(chunk);
        if (!parsed) continue;
        const event = parsed.data;
        const sequence = Number(event.sequence) || 0;
        if (sequence && sequence <= cursor) continue;
        cursor = Math.max(cursor, sequence);
        streamedEvents.push(event);
        const progressBody = renderStreamedAgentProgress(streamedEvents, runId);
        publishProgress(progressBody);
        if (TERMINAL_RUN_STATUSES.has(progressBody.status)) {
          latestBody = {
            ...await refreshRun(runId, { conversationId }),
            stream_events: [...streamedEvents],
            streamed_answer: progressBody.streamed_answer,
          };
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

const MEMORY_STATUS_META = {
  active: { label: "已生效", tone: "active", description: "会参与后续上下文召回" },
  candidate: { label: "待审核", tone: "candidate", description: "确认后才会进入模型上下文" },
  stale: { label: "已过期", tone: "stale", description: "来源变化，等待重新确认" },
  superseded: { label: "已替代", tone: "superseded", description: "已由更新版本取代" },
  rejected: { label: "已拒绝", tone: "rejected", description: "保留审计，但不会被召回" },
};

const PROJECT_MEMORY_KIND_LABELS = {
  architecture_fact: "架构事实",
  constraint: "项目约束",
  decision: "技术决策",
  convention: "项目规范",
  task_outcome: "任务结果",
  incident_lesson: "故障经验",
};

const USER_MEMORY_KIND_LABELS = {
  profile_fact: "个人事实",
  communication_preference: "沟通偏好",
  tooling_preference: "工具偏好",
  workflow_preference: "工作流偏好",
  standing_goal: "长期目标",
  personal_constraint: "个人约束",
};

function memoryStatusMeta(status) {
  return MEMORY_STATUS_META[status] || {
    label: status || "未知状态",
    tone: "neutral",
    description: "当前状态不会自动注入上下文",
  };
}

function memoryStatusPill(status, version = null) {
  const meta = memoryStatusMeta(status);
  const versionText = version == null ? "" : ` · v${escapeHtml(version)}`;
  return `<span class="memory-status-pill ${escapeHtml(meta.tone)}" title="${escapeHtml(meta.description)}">${escapeHtml(meta.label)}${versionText}</span>`;
}

function memoryLoadingMarkup(rows = 4) {
  return `<div class="memory-skeleton" aria-label="正在加载记忆">${Array.from(
    { length: rows },
    () => '<span class="memory-skeleton-row"></span>',
  ).join("")}</div>`;
}

function memoryCounts(items) {
  return items.reduce(
    (counts, item) => {
      counts[item.status] = (counts[item.status] || 0) + 1;
      return counts;
    },
    {},
  );
}

function filteredMemories(items, statusFilterId, kindFilterId) {
  const status = $(statusFilterId).value;
  const kind = $(kindFilterId).value;
  return items.filter(
    (item) => (!status || item.status === status) && (!kind || item.kind === kind),
  );
}

function setMemoryRuntimeStatus(text, tone = "ready") {
  $("memory-status").textContent = text;
  const container = $("memory-status").closest(".memory-runtime-status");
  if (container) container.dataset.tone = tone;
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
  $("memory-detail-heading").textContent = "创建项目记忆";
  $("memory-detail-subtitle").textContent = "手工创建的项目事实会立即生效，并保留版本与证据。";
  $("memory-selection-label").textContent = "新记忆";
  $("memory-selection-label").className = "memory-status-pill draft";
  $("memory-kind-input").value = "architecture_fact";
  $("memory-importance-input").value = "3";
  $("memory-title-input").value = "";
  $("memory-content-input").value = "";
  $("memory-evidence").innerHTML =
    '<div class="memory-empty compact"><strong>证据与版本</strong><p>选择一条记忆后查看来源、置信度和确认时间。</p></div>';
  syncProjectMemoryActions();
  renderProjectMemories();
}

function selectProjectMemory(memory) {
  if (!memory) {
    clearMemorySelection();
    return;
  }
  state.selectedMemoryId = memory.id;
  const meta = memoryStatusMeta(memory.status);
  $("memory-detail-heading").textContent = memory.title;
  $("memory-detail-subtitle").textContent = `${PROJECT_MEMORY_KIND_LABELS[memory.kind] || memory.kind} · ${meta.description}`;
  $("memory-selection-label").textContent = `${meta.label} · v${memory.version}`;
  $("memory-selection-label").className = `memory-status-pill ${meta.tone}`;
  $("memory-kind-input").value = memory.kind;
  $("memory-importance-input").value = memory.importance;
  $("memory-title-input").value = memory.title;
  $("memory-content-input").value = memory.content;
  const evidence = memory.evidence || [];
  $("memory-evidence").innerHTML = `
    <div class="memory-evidence-summary">
      <span><small>置信度</small><strong>${(Number(memory.confidence || 0) * 100).toFixed(0)}%</strong></span>
      <span><small>冲突</small><strong>${escapeHtml(memory.conflict ? "待处理" : "无")}</strong></span>
      <span><small>访问</small><strong>${escapeHtml(memory.access_count || 0)} 次</strong></span>
      <span><small>最后确认</small><strong>${escapeHtml(formatDate(memory.last_confirmed_at))}</strong></span>
    </div>
    ${
      evidence.length
        ? evidence
            .map(
              (item) => `
                <div class="memory-evidence-item">
                  <span class="memory-source-kind">${escapeHtml(item.source_kind)}</span>
                  <strong>${escapeHtml(item.source_id)}</strong>
                  <p>${escapeHtml(item.path || item.excerpt || "已记录来源")}</p>
                </div>
              `,
            )
            .join("")
        : '<div class="memory-empty compact"><strong>暂无附加证据</strong><p>该事实仍保留版本和创建者审计信息。</p></div>'
    }
  `;
  syncProjectMemoryActions();
  renderProjectMemories();
}

function renderProjectMemories() {
  const list = $("memory-list");
  const visible = filteredMemories(
    state.projectMemories,
    "memory-status-filter",
    "memory-kind-filter",
  );
  const counts = memoryCounts(state.projectMemories);
  $("memory-count").textContent = `${visible.length} / ${state.projectMemories.length}`;
  $("memory-project-active-count").textContent = counts.active || 0;
  $("memory-project-candidate-count").textContent = counts.candidate || 0;
  if (!visible.length) {
    list.innerHTML = state.projectMemories.length
      ? '<div class="memory-empty"><strong>没有符合筛选的记忆</strong><p>调整状态或类型筛选查看其他记录。</p></div>'
      : '<div class="memory-empty"><strong>还没有项目记忆</strong><p>明确说“记住”或手工创建一条稳定事实。</p></div>';
    return;
  }
  list.innerHTML = visible
    .map(
      (item) => {
        const meta = memoryStatusMeta(item.status);
        const kindLabel = PROJECT_MEMORY_KIND_LABELS[item.kind] || item.kind;
        return `
        <button
          class="memory-asset-row${item.id === state.selectedMemoryId ? " active" : ""}"
          type="button"
          data-memory-id="${escapeHtml(item.id)}"
          aria-pressed="${item.id === state.selectedMemoryId}"
        >
          <span class="memory-asset-row-head"><strong>${escapeHtml(item.title)}</strong>${memoryStatusPill(item.status)}</span>
          <span class="memory-asset-row-body">${escapeHtml(truncate(item.content, 108))}</span>
          <span class="memory-asset-row-meta"><span>${escapeHtml(kindLabel)}</span><span>重要度 ${escapeHtml(item.importance)}/5</span><span>v${escapeHtml(item.version)}</span></span>
        </button>
      `;
      },
    )
    .join("");
}

function syncProjectMemoryActions() {
  const current = selectedProjectMemory();
  const candidate = current?.status === "candidate" || current?.status === "stale";
  $("create-memory-btn").hidden = Boolean(current);
  $("update-memory-btn").disabled = !current;
  $("confirm-memory-btn").disabled = !candidate;
  $("reject-memory-btn").disabled = !candidate;
  $("delete-memory-btn").disabled = !current;
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

async function listProjectMemories() {
  if (!workspaceIsReady(currentWorkspace())) {
    state.projectMemories = [];
    state.selectedMemoryId = "";
    renderProjectMemories();
    renderMemoryJobs([]);
    return [];
  }
  const workspaceId = requireActiveWorkspace();
  const generation = ++state.projectMemoryRequestGeneration;
  $("memory-list").setAttribute("aria-busy", "true");
  $("memory-list").innerHTML = memoryLoadingMarkup();
  const [memoryBody, jobBody] = await Promise.all([
    fetchJson(`/workspaces/${encodeURIComponent(workspaceId)}/memories?limit=200`),
    fetchJson(`/workspaces/${encodeURIComponent(workspaceId)}/memory-jobs?limit=20`),
  ]);
  if (generation !== state.projectMemoryRequestGeneration) return state.projectMemories;
  state.projectMemories = memoryBody.memories || [];
  if (!state.projectMemories.some((item) => item.id === state.selectedMemoryId)) {
    state.selectedMemoryId = "";
  }
  $("memory-list").setAttribute("aria-busy", "false");
  renderProjectMemories();
  renderMemoryJobs(jobBody.jobs || []);
  if (!state.selectedMemoryId && state.projectMemories.length) {
    selectProjectMemory(state.projectMemories[0]);
  } else {
    syncProjectMemoryActions();
  }
  return state.projectMemories;
}

async function refreshProjectMemory() {
  if (!workspaceIsReady(currentWorkspace())) {
    state.projectMemories = [];
    state.selectedMemoryId = "";
    renderProjectMemories();
    renderMemoryJobs([]);
    setMemoryRuntimeStatus("请先选择可用工作区", "warning");
    return;
  }
  try {
    await listProjectMemories();
    setMemoryRuntimeStatus("L1 自动提炼已连接");
  } catch (error) {
    $("memory-list").setAttribute("aria-busy", "false");
    $("memory-list").innerHTML = '<div class="memory-empty error"><strong>项目记忆加载失败</strong><p>检查本地 SQLite 配置后重新刷新。</p></div>';
    setMemoryRuntimeStatus("项目记忆加载失败", "error");
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

function setMemoryTab(tabName, { focus = false } = {}) {
  const allowed = new Set(["project", "profile", "conversations"]);
  const tab = allowed.has(tabName) ? tabName : "project";
  state.activeMemoryTab = tab;
  document.querySelectorAll("[data-memory-tab]").forEach((button) => {
    const active = button.dataset.memoryTab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    if (active && focus) button.focus();
  });
  document.querySelectorAll("[data-memory-tab-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.memoryTabPanel !== tab;
  });
  refreshMemoryWorkbench();
}

function refreshMemoryWorkbench() {
  if (state.activeMemoryTab === "profile") {
    return refreshUserMemory();
  }
  if (state.activeMemoryTab === "project") {
    return refreshProjectMemory();
  }
  return searchConversationMemory();
}

function userMemoryPayload() {
  return {
    kind: $("user-memory-kind-input").value,
    title: $("user-memory-title-input").value.trim(),
    content: $("user-memory-content-input").value.trim(),
    importance: numberValue("user-memory-importance-input", 3),
  };
}

function selectedUserMemory() {
  return state.userMemories.find((item) => item.id === state.selectedUserMemoryId) || null;
}

function clearUserMemorySelection() {
  state.selectedUserMemoryId = "";
  $("user-memory-detail-heading").textContent = "创建个人事实";
  $("user-memory-detail-subtitle").textContent = "手工创建会立即生效；自动提炼只进入待审核列表。";
  $("user-memory-selection-label").textContent = "新记忆";
  $("user-memory-selection-label").className = "memory-status-pill draft";
  $("user-memory-kind-input").value = "profile_fact";
  $("user-memory-importance-input").value = "3";
  $("user-memory-title-input").value = "";
  $("user-memory-content-input").value = "";
  $("user-memory-evidence").innerHTML =
    '<div class="memory-empty compact"><strong>证据与版本</strong><p>选择一条事实后查看来源与确认时间。</p></div>';
  syncUserMemoryActions();
  renderUserMemories();
}

function selectUserMemory(memory) {
  if (!memory) {
    clearUserMemorySelection();
    return;
  }
  state.selectedUserMemoryId = memory.id;
  const meta = memoryStatusMeta(memory.status);
  $("user-memory-detail-heading").textContent = memory.title;
  $("user-memory-detail-subtitle").textContent = `${USER_MEMORY_KIND_LABELS[memory.kind] || memory.kind} · ${meta.description}`;
  $("user-memory-selection-label").textContent = `${meta.label} · v${memory.version}`;
  $("user-memory-selection-label").className = `memory-status-pill ${meta.tone}`;
  $("user-memory-kind-input").value = memory.kind;
  $("user-memory-importance-input").value = memory.importance;
  $("user-memory-title-input").value = memory.title;
  $("user-memory-content-input").value = memory.content;
  const evidence = memory.evidence || [];
  $("user-memory-evidence").innerHTML = `
    <div class="memory-evidence-summary">
      <span><small>置信度</small><strong>${(Number(memory.confidence || 0) * 100).toFixed(0)}%</strong></span>
      <span><small>重要度</small><strong>${escapeHtml(memory.importance)}/5</strong></span>
      <span><small>版本</small><strong>v${escapeHtml(memory.version)}</strong></span>
      <span><small>最后确认</small><strong>${escapeHtml(formatDate(memory.last_confirmed_at))}</strong></span>
    </div>
    ${evidence.length
      ? evidence.map((item) => `<div class="memory-evidence-item"><span class="memory-source-kind">${escapeHtml(item.source_kind)}</span><strong>${escapeHtml(item.source_id)}</strong><p>${escapeHtml(item.excerpt || "已记录来源")}</p></div>`).join("")
      : '<div class="memory-empty compact"><strong>暂无附加证据</strong><p>该事实仍保留版本和创建者审计信息。</p></div>'}
  `;
  syncUserMemoryActions();
  renderUserMemories();
}

function renderUserMemories() {
  const list = $("user-memory-list");
  const visible = filteredMemories(
    state.userMemories,
    "user-memory-status-filter",
    "user-memory-kind-filter",
  );
  const counts = memoryCounts(state.userMemories);
  $("user-memory-count").textContent = `${visible.length} / ${state.userMemories.length}`;
  $("user-memory-active-count").textContent = counts.active || 0;
  $("user-memory-candidate-count").textContent = counts.candidate || 0;
  if (!visible.length) {
    list.innerHTML = state.userMemories.length
      ? '<div class="memory-empty"><strong>没有符合筛选的事实</strong><p>调整状态或类型筛选查看其他记录。</p></div>'
      : '<div class="memory-empty"><strong>还没有个人记忆</strong><p>明确说明“以后都这样”或手工添加一条偏好。</p></div>';
    return;
  }
  list.innerHTML = visible.map((item) => `
    <button class="memory-asset-row${item.id === state.selectedUserMemoryId ? " active" : ""}" type="button" data-user-memory-id="${escapeHtml(item.id)}" aria-pressed="${item.id === state.selectedUserMemoryId}">
      <span class="memory-asset-row-head"><strong>${escapeHtml(item.title)}</strong>${memoryStatusPill(item.status)}</span>
      <span class="memory-asset-row-body">${escapeHtml(truncate(item.content, 108))}</span>
      <span class="memory-asset-row-meta"><span>${escapeHtml(USER_MEMORY_KIND_LABELS[item.kind] || item.kind)}</span><span>重要度 ${escapeHtml(item.importance)}/5</span><span>v${escapeHtml(item.version)}</span></span>
    </button>
  `).join("");
}

function syncUserMemoryActions() {
  const current = selectedUserMemory();
  const candidate = current?.status === "candidate";
  $("create-user-memory-btn").hidden = Boolean(current);
  $("update-user-memory-btn").disabled = !current;
  $("confirm-user-memory-btn").disabled = !candidate;
  $("reject-user-memory-btn").disabled = !candidate;
  $("delete-user-memory-btn").disabled = !current;
}

function renderUserProfile(profile) {
  $("user-profile-preview").innerHTML = profile.content
    ? `<div class="memory-profile-meta"><span>画像 v${escapeHtml(profile.version)}</span><span>${escapeHtml(profile.source_memory_ids.length)} 个 L1/L2 来源</span><span>${escapeHtml(formatDate(profile.updated_at))}</span></div><div class="markdown-body">${renderMarkdown(profile.content)}</div>`
    : '<div class="memory-empty"><strong>暂无已确认画像</strong><p>确认一条个人事实后，这里会显示模型可见的确定性快照。</p></div>';
}

function renderUserMemoryScenes(scenes) {
  state.userMemoryScenes = scenes;
  $("user-memory-scene-count").textContent = scenes.length;
  $("user-memory-scenes").innerHTML = scenes.length
    ? scenes.map((scene) => `
      <article class="memory-asset-row">
        <span class="memory-asset-row-head"><strong>${escapeHtml(scene.title)}</strong><span class="memory-status-pill active">L2 · v${escapeHtml(scene.version)}</span></span>
        <span class="memory-asset-row-body">${escapeHtml(truncate(scene.content, 220))}</span>
        <span class="memory-asset-row-meta"><span>${escapeHtml(scene.workspace_id)}</span><span>${escapeHtml(scene.source_memory_ids.length)} 条 L1 来源</span><span>${escapeHtml(formatDate(scene.updated_at))}</span></span>
      </article>
    `).join("")
    : '<div class="memory-empty"><strong>暂无项目场景</strong><p>创建或自动提炼一条 L1 后会自动生成。</p></div>';
}

async function listUserMemories() {
  const generation = ++state.userMemoryRequestGeneration;
  $("user-memory-list").setAttribute("aria-busy", "true");
  $("user-memory-list").innerHTML = memoryLoadingMarkup();
  const body = await fetchJson("/users/me/memories?limit=200");
  if (generation !== state.userMemoryRequestGeneration) return state.userMemories;
  state.userMemories = body.memories || [];
  if (!state.userMemories.some((item) => item.id === state.selectedUserMemoryId)) {
    state.selectedUserMemoryId = "";
  }
  $("user-memory-list").setAttribute("aria-busy", "false");
  renderUserMemories();
  if (!state.selectedUserMemoryId && state.userMemories.length) {
    selectUserMemory(state.userMemories[0]);
  } else {
    syncUserMemoryActions();
  }
  return state.userMemories;
}

async function refreshUserMemory() {
  try {
    const [settings, profile, scenes] = await Promise.all([
      fetchJson("/users/me/memory-settings"),
      fetchJson("/users/me/profile"),
      fetchJson("/users/me/memory-scenes"),
      listUserMemories(),
    ]);
    $("user-memory-mode-input").value = settings.mode;
    setMemoryRuntimeStatus(`L3 已连接 · ${settings.mode}`);
    renderUserProfile(profile);
    renderUserMemoryScenes(scenes.scenes || []);
  } catch (error) {
    $("user-memory-list").setAttribute("aria-busy", "false");
    $("user-memory-list").innerHTML = '<div class="memory-empty error"><strong>个人记忆加载失败</strong><p>检查本地 SQLite 配置后重新刷新。</p></div>';
    setMemoryRuntimeStatus("个人记忆加载失败", "error");
    showToast(humanizeError(error), "error");
  }
}

async function saveUserMemoryMode() {
  try {
    const settings = await fetchJson("/users/me/memory-settings", {
      method: "PATCH",
      body: JSON.stringify({ mode: $("user-memory-mode-input").value }),
    });
    setMemoryRuntimeStatus(`L3 已连接 · ${settings.mode}`);
    await refreshUserMemory();
    showToast("个人记忆模式已更新");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function createUserMemory() {
  const payload = userMemoryPayload();
  if (!payload.title || !payload.content) {
    showToast("记忆标题和内容不能为空", "warning");
    return;
  }
  try {
    const body = await fetchJson("/users/me/memories", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await listUserMemories();
    selectUserMemory(body);
    renderUserProfile(await fetchJson("/users/me/profile"));
    showToast("个人记忆已创建并确认");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function updateUserMemory() {
  const current = selectedUserMemory();
  if (!current) {
    showToast("请先选择一条个人记忆", "warning");
    return;
  }
  try {
    const body = await fetchJson(`/users/me/memories/${encodeURIComponent(current.id)}`, {
      method: "PATCH",
      body: JSON.stringify({ ...userMemoryPayload(), version: current.version }),
    });
    await listUserMemories();
    selectUserMemory(body);
    renderUserProfile(await fetchJson("/users/me/profile"));
    showToast("个人记忆已更新");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function transitionUserMemory(action) {
  const current = selectedUserMemory();
  if (!current) {
    showToast("请先选择一条个人记忆", "warning");
    return;
  }
  try {
    const body = await fetchJson(`/users/me/memories/${encodeURIComponent(current.id)}/${action}`, {
      method: "POST",
      body: JSON.stringify({ version: current.version }),
    });
    await listUserMemories();
    selectUserMemory(body);
    renderUserProfile(await fetchJson("/users/me/profile"));
    showToast(action === "confirm" ? "个人记忆已确认" : "个人记忆已拒绝");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function forgetUserMemory() {
  const current = selectedUserMemory();
  if (!current || !window.confirm("完整遗忘这条个人记忆及其证据？")) return;
  try {
    await fetchJson(`/users/me/memories/${encodeURIComponent(current.id)}`, { method: "DELETE" });
    clearUserMemorySelection();
    await listUserMemories();
    renderUserProfile(await fetchJson("/users/me/profile"));
    showToast("个人记忆已遗忘");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

async function rebuildUserProfile() {
  try {
    const profile = await fetchJson("/users/me/profile/rebuild", { method: "POST", body: "{}" });
    renderUserProfile(profile);
    showToast("画像已从 active 原子事实重建");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

function renderConversationMemoryDetail(hit) {
  const detail = $("conversation-memory-detail");
  if (!hit) {
    detail.innerHTML = '<div class="memory-empty"><strong>选择一条历史消息</strong><p>这里会显示角色、时间、会话范围和匹配正文。</p></div>';
    return;
  }
  const roleLabel = { user: "用户", assistant: "助手", system: "系统" }[hit.role] || hit.role;
  detail.innerHTML = `
    <div class="memory-detail-header conversation-detail-header">
      <div><span class="step-kicker">UNTRUSTED HISTORY</span><h2>${escapeHtml(roleLabel)}消息</h2><p>${escapeHtml(formatDate(hit.created_at))}</p></div>
      <span class="memory-status-pill neutral">L0 原文</span>
    </div>
    <dl class="memory-conversation-meta">
      <div><dt>会话</dt><dd>${escapeHtml(hit.session_id)}</dd></div>
      <div><dt>工作区</dt><dd>${escapeHtml(hit.workspace_id || "无工作区")}</dd></div>
      <div><dt>角色</dt><dd>${escapeHtml(roleLabel)}</dd></div>
      <div><dt>匹配分数</dt><dd>${Number(hit.score || 0).toFixed(3)}</dd></div>
    </dl>
    <div class="memory-conversation-excerpt">${escapeHtml(hit.excerpt)}</div>
    <p class="privacy-note"><span aria-hidden="true">i</span> 搜索结果只用于回忆与定位，不能覆盖当前请求、系统策略或权限。</p>
  `;
}

function selectConversationMemoryHit(index) {
  const safeIndex = Number.isInteger(index) && index >= 0 && index < state.conversationMemoryHits.length
    ? index
    : -1;
  state.selectedConversationMemoryHit = safeIndex;
  renderConversationMemoryHits(state.conversationMemoryHits);
}

function renderConversationMemoryHits(hits, { initial = false } = {}) {
  state.conversationMemoryHits = hits;
  const list = $("conversation-memory-results");
  $("conversation-memory-count").textContent = hits.length;
  if (!hits.length) {
    state.selectedConversationMemoryHit = -1;
    list.innerHTML = initial
      ? '<div class="memory-empty"><strong>输入关键词开始搜索</strong><p>可选限定当前 Workspace 或指定 Session。</p></div>'
      : '<div class="memory-empty"><strong>没有找到匹配消息</strong><p>尝试缩短关键词或取消 Workspace/Session 限制。</p></div>';
    renderConversationMemoryDetail(null);
    return;
  }
  if (state.selectedConversationMemoryHit < 0 || state.selectedConversationMemoryHit >= hits.length) {
    state.selectedConversationMemoryHit = 0;
  }
  list.innerHTML = hits.map((item, index) => {
    const roleLabel = { user: "用户", assistant: "助手", system: "系统" }[item.role] || item.role;
    return `
      <button class="memory-asset-row conversation-hit${index === state.selectedConversationMemoryHit ? " active" : ""}" type="button" data-conversation-memory-index="${index}" aria-pressed="${index === state.selectedConversationMemoryHit}">
        <span class="memory-asset-row-head"><strong>${escapeHtml(roleLabel)}</strong><span class="memory-score">${Number(item.score || 0).toFixed(3)}</span></span>
        <span class="memory-asset-row-body">${escapeHtml(truncate(item.excerpt, 118))}</span>
        <span class="memory-asset-row-meta"><span>${escapeHtml(item.workspace_id || "无工作区")}</span><span>${escapeHtml(formatDate(item.created_at))}</span></span>
      </button>
    `;
  }).join("");
  renderConversationMemoryDetail(hits[state.selectedConversationMemoryHit]);
}

async function searchConversationMemory() {
  const query = $("conversation-memory-query").value.trim();
  const params = new URLSearchParams({ limit: "30" });
  if (query) params.set("q", query);
  const sessionId = $("conversation-memory-session").value.trim();
  if (sessionId) params.set("session_id", sessionId);
  if ($("conversation-memory-current-workspace").checked) {
    if (!state.activeWorkspaceId) {
      showToast("当前没有选中的工作区", "warning");
      return;
    }
    params.set("workspace_id", state.activeWorkspaceId);
  }
  const generation = ++state.conversationMemoryRequestGeneration;
  try {
    $("conversation-memory-results").setAttribute("aria-busy", "true");
    $("conversation-memory-results").innerHTML = memoryLoadingMarkup(5);
    const body = await fetchJson(`/memory/conversations/search?${params}`);
    if (generation !== state.conversationMemoryRequestGeneration) return;
    $("conversation-memory-results").setAttribute("aria-busy", "false");
    state.selectedConversationMemoryHit = -1;
    renderConversationMemoryHits(body.hits || []);
    setMemoryRuntimeStatus(`L0 ${query ? "搜索完成" : "最近消息"} · ${(body.hits || []).length} 条`);
  } catch (error) {
    if (generation !== state.conversationMemoryRequestGeneration) return;
    $("conversation-memory-results").setAttribute("aria-busy", "false");
    $("conversation-memory-results").innerHTML = '<div class="memory-empty error"><strong>对话搜索失败</strong><p>检查关键词和本地 SQLite 状态后重试。</p></div>';
    setMemoryRuntimeStatus("L0 搜索失败", "error");
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
    const policyFallback = error.status === 403
      && error.body?.detail === NATIVE_PICKER_LOCAL_ONLY_DETAIL;
    if (policyFallback || error.status === 501 || error.status === 503) {
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
    setComposerValue(prompt.dataset.prompt, { focus: true });
  });

  $("chat-output").addEventListener("click", (event) => {
    if (event.target.closest("[data-message-action], [data-response-action]")) {
      handleConversationAction(event.target)
        .catch((error) => showToast(humanizeError(error), "error"));
    }
  });

  $("mobile-more-btn").addEventListener("click", () => {
    setMobileMoreOpen($("mobile-more-menu").hidden);
  });
  $("mobile-nav-backdrop").addEventListener("click", () => setMobileMoreOpen(false));
  $("mobile-more-settings-btn").addEventListener("click", () => {
    setMobileMoreOpen(false);
    openSettings();
  });

  $("run-eval-btn").addEventListener("click", startEvalRun);
  $("refresh-eval-btn").addEventListener("click", () => {
    loadEvalDashboard(state.evalRunId).catch((error) =>
      showToast(humanizeError(error), "error"));
  });
  $("pin-eval-baseline-btn").addEventListener("click", pinEvalBaseline);
  $("eval-history-list").addEventListener("click", (event) => {
    const row = event.target.closest("[data-eval-run]");
    if (!row) {
      return;
    }
    loadEvalRun(row.dataset.evalRun).catch((error) =>
      showToast(humanizeError(error), "error"));
  });

  $("refresh-trace-audit-btn").addEventListener("click", () => {
    loadAuditRuns()
      .then(() => showToast("Trace 已刷新"))
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  $("trace-run-search").addEventListener("input", renderAuditRuns);
  $("trace-run-status-filter").addEventListener("change", renderAuditRuns);
  $("trace-run-list").addEventListener("click", (event) => {
    const item = event.target.closest("[data-audit-run-id]");
    if (!item || item.dataset.auditRunId === state.auditRunId) return;
    loadAuditRun(item.dataset.auditRunId)
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  $("trace-audit-view").addEventListener("click", (event) => {
    const filter = event.target.closest("[data-audit-filter]");
    if (!filter) return;
    state.auditCategory = filter.dataset.auditFilter;
    document.querySelectorAll("[data-audit-filter]").forEach((button) => {
      const active = button === filter;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    renderAuditTimeline();
  });

  $("open-settings-btn").addEventListener("click", openSettings);
  $("sidebar-settings-btn").addEventListener("click", openSettings);
  $("composer-workspace-btn").addEventListener("click", openSettings);
  $("composer-model-btn").addEventListener("click", () => {
    $("composer-config").open = true;
    window.setTimeout(() => $("composer-config").querySelector("summary").focus(), 0);
  });
  bindComposerConfigDismissal();
  $("close-settings-btn").addEventListener("click", closeSettings);
  $("settings-dialog").addEventListener("click", (event) => {
    if (event.target === $("settings-dialog")) {
      closeSettings();
    }
  });
  $("close-checkpoint-history-btn").addEventListener("click", closeCheckpointHistory);
  $("checkpoint-history-done-btn").addEventListener("click", closeCheckpointHistory);
  $("checkpoint-history-dialog").addEventListener("click", (event) => {
    if (event.target === $("checkpoint-history-dialog")) closeCheckpointHistory();
  });
  $("checkpoint-history-list").addEventListener("click", (event) => {
    const card = event.target.closest("[data-checkpoint-id]");
    if (!card) return;
    state.selectedCheckpointId = card.dataset.checkpointId;
    renderCheckpointHistory();
  });
  $("checkpoint-fork-btn").addEventListener("click", () => {
    restoreSelectedCheckpoint("fork");
  });
  $("checkpoint-rollback-btn").addEventListener("click", () => {
    restoreSelectedCheckpoint("rollback");
  });
  $("open-workspace-picker-btn").addEventListener("click", (event) => {
    openWorkspacePicker(null, event.currentTarget);
  });
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
  $("active-session-inspector-btn").addEventListener("click", () => {
    setInspectorVisible(true);
    $("close-inspector-btn").focus();
  });
  $("close-inspector-btn").addEventListener("click", () => setInspectorVisible(false));
  $("inspector-backdrop").addEventListener("click", () => {
    setInspectorVisible(false);
    $("toggle-inspector-btn").focus();
  });
  window.addEventListener("resize", () => {
    syncInspectorPresentation(!$("inspector-panel").hidden);
    if (window.innerWidth > 900) setMobileMoreOpen(false);
  }, { passive: true });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("inspector-panel").hidden && window.innerWidth <= 1120) {
      setInspectorVisible(false);
      $("toggle-inspector-btn").focus();
    }
    if (event.key === "Escape" && !$("mobile-more-menu").hidden) {
      setMobileMoreOpen(false);
      $("mobile-more-btn").focus();
    }
  });
  $("trace-tab").addEventListener("click", () => selectInspectorTab("trace"));
  $("raw-tab").addEventListener("click", () => selectInspectorTab("raw"));

  const createNewSession = () => {
    if (canSwitchSession()) {
      createSession().catch(() => {});
    }
  };
  $("create-session-btn").addEventListener("click", createNewSession);
  $("sessions-create-btn").addEventListener("click", createNewSession);
  $("rename-current-session-btn").addEventListener("click", () => {
    if (!state.currentSession?.id) return;
    handleSessionAction(state.currentSession.id, "rename")
      .catch((error) => showToast(humanizeError(error), "error"));
  });
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
      event.stopPropagation();
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
    if (!$("slash-command-menu").hidden) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        moveSlashSelection(event.key === "ArrowDown" ? 1 : -1);
        return;
      }
      if (["Enter", "Tab"].includes(event.key) && !event.isComposing) {
        event.preventDefault();
        selectSlashItem();
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closeSlashCommandMenu();
        return;
      }
    }
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submitComposerMessage();
    }
  });
  $("chat-message-input").addEventListener("input", () => {
    $("chat-message-input").removeAttribute("aria-invalid");
    resizeComposerInput();
    saveComposerDraft();
    updateComposerAvailability();
    updateSlashCommandMenu();
  });
  $("chat-message-input").addEventListener("click", updateSlashCommandMenu);
  $("slash-command-options").addEventListener("pointerdown", (event) => {
    if (event.target.closest("[data-slash-index]")) event.preventDefault();
  });
  $("slash-command-options").addEventListener("click", (event) => {
    const option = event.target.closest("[data-slash-index]");
    if (option) selectSlashItem(Number(option.dataset.slashIndex));
  });
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest(".composer")) closeSlashCommandMenu();
  });
  ["chat-message-input", "rag-question-input"].forEach((id) => {
    $(id).addEventListener("input", () => $(id).removeAttribute("aria-invalid"));
  });

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
  document.querySelectorAll("[data-memory-tab]").forEach((tab) => {
    tab.addEventListener("click", () => setMemoryTab(tab.dataset.memoryTab));
    tab.addEventListener("keydown", (event) => {
      if (!new Set(["ArrowLeft", "ArrowRight", "Home", "End"]).has(event.key)) return;
      const tabs = [...document.querySelectorAll("[data-memory-tab]")];
      const current = tabs.indexOf(event.currentTarget);
      const target = event.key === "Home"
        ? tabs[0]
        : event.key === "End"
          ? tabs.at(-1)
          : tabs[(current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
      if (target) {
        event.preventDefault();
        setMemoryTab(target.dataset.memoryTab, { focus: true });
      }
    });
  });
  $("new-project-memory-btn").addEventListener("click", () => {
    clearMemorySelection();
    $("memory-title-input").focus();
  });
  $("create-memory-btn").addEventListener("click", createProjectMemory);
  $("update-memory-btn").addEventListener("click", updateProjectMemory);
  $("confirm-memory-btn").addEventListener("click", () =>
    transitionProjectMemory("confirm"),
  );
  $("reject-memory-btn").addEventListener("click", () =>
    transitionProjectMemory("reject"),
  );
  $("delete-memory-btn").addEventListener("click", forgetProjectMemory);
  $("memory-status-filter").addEventListener("change", renderProjectMemories);
  $("memory-kind-filter").addEventListener("change", renderProjectMemories);
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
  $("refresh-user-memory-btn").addEventListener("click", refreshUserMemory);
  $("new-user-memory-btn").addEventListener("click", () => {
    clearUserMemorySelection();
    $("user-memory-title-input").focus();
  });
  $("save-user-memory-mode-btn").addEventListener("click", saveUserMemoryMode);
  $("rebuild-profile-btn").addEventListener("click", rebuildUserProfile);
  $("create-user-memory-btn").addEventListener("click", createUserMemory);
  $("update-user-memory-btn").addEventListener("click", updateUserMemory);
  $("confirm-user-memory-btn").addEventListener("click", () => transitionUserMemory("confirm"));
  $("reject-user-memory-btn").addEventListener("click", () => transitionUserMemory("reject"));
  $("delete-user-memory-btn").addEventListener("click", forgetUserMemory);
  $("user-memory-status-filter").addEventListener("change", renderUserMemories);
  $("user-memory-kind-filter").addEventListener("change", renderUserMemories);
  $("user-memory-list").addEventListener("click", (event) => {
    const row = event.target.closest("[data-user-memory-id]");
    if (!row) return;
    selectUserMemory(state.userMemories.find((item) => item.id === row.dataset.userMemoryId));
  });
  $("search-conversation-memory-btn").addEventListener("click", searchConversationMemory);
  $("conversation-memory-results").addEventListener("click", (event) => {
    const row = event.target.closest("[data-conversation-memory-index]");
    if (!row) return;
    selectConversationMemoryHit(Number(row.dataset.conversationMemoryIndex));
  });
  $("clear-conversation-memory-btn").addEventListener("click", () => {
    state.conversationMemoryRequestGeneration += 1;
    $("conversation-memory-results").setAttribute("aria-busy", "false");
    $("conversation-memory-query").value = "";
    $("conversation-memory-session").value = "";
    state.selectedConversationMemoryHit = -1;
    searchConversationMemory();
  });
  $("conversation-memory-query").addEventListener("keydown", (event) => {
    if (event.key === "Enter") searchConversationMemory();
  });

  $("refresh-tools-registry-btn").addEventListener("click", () => {
    Promise.all([loadSkillRegistry(true), loadMCPRegistry()])
      .then(() => showToast("Skill 与 MCP 状态已刷新"))
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  $("reset-skill-form-btn").addEventListener("click", resetSkillForm);
  $("skill-form").addEventListener("submit", saveSkill);
  $("skill-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-skill-action]");
    if (button) handleSkillAction(button);
  });
  $("mcp-transport-input").addEventListener("change", (event) => {
    updateMCPTransportFields();
    $("mcp-legacy-input").checked = ["stdio_2025_06_18", "legacy_sse"].includes(event.target.value);
  });
  $("reset-mcp-form-btn").addEventListener("click", resetMCPServerForm);
  $("mcp-server-form").addEventListener("submit", saveMCPServer);
  $("mcp-server-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-mcp-action]");
    if (button) handleMCPServerAction(button);
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
    if (action === "save-output-limit") {
      const model = registeredModel(card.dataset.modelId);
      const maxOutputTokens = Number(card.querySelector("[data-model-output-limit]")?.value);
      if (!model || !Number.isInteger(maxOutputTokens) || maxOutputTokens < 1 || maxOutputTokens > model.context_window_tokens) {
        showToast("最大输出 token 必须是正整数，且不能超过上下文窗口", "warning");
        return;
      }
      updateRegisteredModel(model.id, { max_output_tokens: maxOutputTokens });
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
    if (view === "agent") {
      switchView("chat");
      return;
    }
    if (view === "mcp") {
      switchView("tools");
      return;
    }
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
  invalidateSlashCapabilities();
  $("conversation-id-input").value = "";
  updateSessionUrl("");
  if (state.preferences) {
    applyConfigurationToInputs(state.preferences, true);
  }
  resetChatView();
  restoreComposerDraft("");
  updateComposerAvailability();
  updateContextSummary();
}

async function init() {
  const preferences = loadUiPreferences();
  state.composerDrafts = preferences.composerDrafts && typeof preferences.composerDrafts === "object"
    ? preferences.composerDrafts
    : {};
  $("user-id-input").value = "demo_user";
  updateMCPTransportFields();
  resetSkillForm();
  bindEvents();
  bindConversationFollow();
  resizeComposerInput();
  const requestedHashView = location.hash.replace("#", "");
  const requestedView = requestedHashView === "agent"
    ? "chat"
    : (requestedHashView === "mcp" ? "tools" : requestedHashView);
  const storedView = preferences.view === "agent"
    ? "chat"
    : (preferences.view === "mcp" ? "tools" : preferences.view);
  const preferredView = document.querySelector(`[data-view-panel="${storedView}"]`)
    ? storedView
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
    loadMCPRegistry(),
    listKnowledgeBases(),
    loadRagCapabilities(),
  ]);
  try {
    await listWorkspaces();
  } catch (error) {
    showToast(`工作区列表加载失败：${humanizeError(error)}`, "error");
  }
  await restoreInitialSession();
  if (state.currentView !== initialView) {
    switchView(initialView, true);
  }
}

init();
