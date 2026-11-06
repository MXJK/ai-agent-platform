import { syncRunInspector } from '../lib/inspector';
import { listen } from '../lib/resources';
import { runtimeRefs } from './config.js';
import { state } from './state.js';
import { $, PERMISSION_MODE_HINTS, composerContextUsagePresentation, escapeHtml, formatTokenK, iconMarkup } from './format.js';
import { humanizeError } from './feedback.js';
import { invalidateSlashCapabilities, loadSlashCapabilities } from './composer.js';
import { setChatStatus } from './overview.js';
import { currentModelSelectionLabel, renderSessionModelControls } from './models.js';
import { renderWorkspaceCatalog, renderWorkspaceManager, workspaceName } from './workspaces.js';

export function updateComposerScopeSummary() {
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
  const contextUsage = composerContextUsagePresentation(usage);
  const contextNode = $("composer-context-budget");
  contextNode.classList.toggle("warning", contextUsage.tone === "warning");
  contextNode.classList.toggle("error", contextUsage.tone === "error");
  $("composer-context-kicker").textContent = contextUsage.kicker;
  $("composer-context-label-full").textContent = contextUsage.label;
  $("composer-context-label-compact").textContent = contextUsage.compactLabel;
  $("composer-context-percentage").textContent = contextUsage.percentageLabel;
  renderContextRing(contextUsage);
  contextNode.title = contextUsage.description;
  contextNode.setAttribute("aria-label", contextUsage.description);
}


export function ensureContextRingTooltip() {
  if (runtimeRefs.contextRingTooltipNode || !document.body) return runtimeRefs.contextRingTooltipNode;
  const node = document.createElement("div");
  node.id = "composer-context-ring-tooltip";
  node.className = "context-ring-tooltip";
  node.setAttribute("role", "tooltip");
  node.hidden = true;
  document.body.appendChild(node);
  const anchor = $("composer-context-budget");
  if (anchor) anchor.setAttribute("aria-describedby", node.id);
  runtimeRefs.contextRingTooltipNode = node;
  return node;
}

export function contextRingTooltipHtml(contextUsage) {
  const headline = contextUsage.hasMeasuredPrompt
    ? `上次模型输入 ${formatTokenK(contextUsage.promptInput)}`
    : "尚无前台模型请求";
  const identity = [contextUsage.provider, contextUsage.model].filter(Boolean).join(" / ");
  const rows = [
    contextUsage.comparableWindow
      ? ["模型上下文窗口", formatTokenK(contextUsage.contextWindow)]
      : null,
    contextUsage.inputBudget > 0
      ? ["平台输入预算", formatTokenK(contextUsage.inputBudget)]
      : null,
    contextUsage.inputBudgetExceeded
      ? ["超出输入预算", `+${formatTokenK(contextUsage.promptInput - contextUsage.inputBudget)}`]
      : null,
    contextUsage.reservedOutput > 0
      ? ["预留输出", formatTokenK(contextUsage.reservedOutput)]
      : null,
    ["历史消息/摘要估算", `≈ ${formatTokenK(contextUsage.estimatedHistory)}`],
    identity ? ["Provider / Model", identity] : null,
  ].filter(Boolean).map(([label, value]) => `
    <li>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </li>`).join("");
  const note = contextUsage.hasMeasuredPrompt
    ? contextUsage.comparableWindow
      ? "主百分比按完整模型窗口计算；平台输入预算是独立的装配安全线。"
      : "主数字来自最近一次前台模型请求；模型已变化，占比不可直接比较。"
    : "完成一次 Agent、Chat 或 RAG Ask 请求后显示实际输入。";
  return `
    <div class="context-ring-tooltip-head">
      <strong>${escapeHtml(headline)}</strong>
      <em>${contextUsage.percentage ?? "–"}</em>
    </div>
    <ul class="context-ring-tooltip-list">${rows}</ul>
    <div class="context-ring-tooltip-note">${escapeHtml(note)}</div>`;
}

export function positionContextRingTooltip(anchor) {
  const node = runtimeRefs.contextRingTooltipNode;
  if (!node || !anchor) return;
  const rect = anchor.getBoundingClientRect();
  const margin = 8;
  const nodeWidth = node.offsetWidth;
  const nodeHeight = node.offsetHeight;
  let left = rect.left;
  if (left + nodeWidth > window.innerWidth - margin) {
    left = Math.max(margin, window.innerWidth - nodeWidth - margin);
  }
  let top = rect.top - nodeHeight - margin;
  if (top < margin) {
    top = rect.bottom + margin;
  }
  node.style.left = `${Math.round(left)}px`;
  node.style.top = `${Math.round(top)}px`;
}

export function renderContextRing(contextUsage) {
  const ring = $("composer-context-ring");
  if (!ring) return;
  const fill = $("composer-context-ring-fill");
  const label = $("composer-context-ring-label");
  const circumference = 2 * Math.PI * 15.5;
  const fraction = Math.max(0, Math.min(1, (contextUsage.ringPercent || 0) / 100));
  if (fill) {
    fill.setAttribute("stroke-dasharray", circumference.toFixed(2));
    fill.setAttribute("stroke-dashoffset", (circumference * (1 - fraction)).toFixed(2));
  }
  if (label) label.textContent = contextUsage.ringLabel;

  const node = ensureContextRingTooltip();
  if (node) node.innerHTML = contextRingTooltipHtml(contextUsage);
  wireContextRingTooltip();
}

export function wireContextRingTooltip() {
  const anchor = $("composer-context-budget");
  if (!anchor || anchor.__contextRingTooltipWired) return;
  anchor.__contextRingTooltipWired = true;
  let clickPinned = false;
  const show = () => {
    const node = ensureContextRingTooltip();
    if (!node) return;
    node.hidden = false;
    anchor.setAttribute("aria-expanded", "true");
    positionContextRingTooltip(anchor);
  };
  const hide = () => {
    if (runtimeRefs.contextRingTooltipNode) runtimeRefs.contextRingTooltipNode.hidden = true;
    anchor.setAttribute("aria-expanded", "false");
  };
  listen(anchor, "mouseenter", show);
  listen(anchor, "mouseleave", () => {
    if (!clickPinned) hide();
  });
  listen(anchor, "focus", show);
  listen(anchor, "blur", () => {
    clickPinned = false;
    hide();
  });
  listen(anchor, "click", () => {
    clickPinned = !clickPinned;
    if (clickPinned) show();
    else hide();
  });
  listen(anchor, "keydown", (event) => {
    if (event.key !== "Escape") return;
    clickPinned = false;
    hide();
    anchor.focus();
  });
}

export function updateContextSummary() {
  const workspace = currentWorkspace();
  const workspaceLabel = workspace ? workspaceName(workspace) : "未选择";
  const modelLabel = currentModelSelectionLabel();

  $("context-model").textContent = modelLabel;
  $("context-workspace").textContent = workspaceLabel;
  $("composer-context").textContent = modelLabel;
  $("header-session-id").textContent = state.currentSession?.title
    || state.conversationId
    || "新会话";
  $("header-session-id").title = $("header-session-id").textContent;
  $("active-session-title").textContent = state.currentSession?.title
    || state.conversationId
    || "当前会话";
  $("rename-current-session-btn").disabled = !state.currentSession?.id;
  updateComposerScopeSummary();
  syncRunInspector();
}

export function currentWorkspace() {
  return state.workspaces.find((item) => item.id === state.activeWorkspaceId) || null;
}

export function workspaceIsReady(workspace) {
  return Boolean(
    workspace
    && workspace.status !== "unavailable"
    && workspace.available !== false,
  );
}

export function workspaceRoleLabel(role) {
  return {
    admin: "管理员",
    editor: "可编辑",
    viewer: "只读",
  }[role] || "本地管理";
}

export function setActiveWorkspace(workspaceId) {
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
    && workspaceIsReady(currentWorkspace())
  ) {
    loadSlashCapabilities().catch((error) => {
      state.slashError = `能力加载失败：${humanizeError(error)}`;
    });
  }
}

export function applyConfigurationToInputs(source, defaults = false) {
  const value = (name) => source?.[defaults ? `default_${name}` : name] ?? "";
  $("provider-input").value = value("provider");
  $("model-input").value = value("model");
  $("thinking-level-input").value = value("thinking_level");
  setActiveWorkspace(value("workspace_id"));
  updateComposer();
  renderWorkspaceManager();
  updateContextSummary();
}
export function updateComposer() {
  $("composer-mode-description").textContent = "Cogent 统一处理对话和代码任务；权限边界始终生效。";
  $("chat-message-input").placeholder = "描述任务，键入 / 使用命令、Skill 或 MCP，Enter 发送…";
  $("send-chat-btn").innerHTML = `发送 ${iconMarkup("arrow-right")}`;
  const permission = $("permission-mode-input");
  if (permission) permission.value = state.permissionMode;
  const permissionHint = $("permission-mode-hint");
  if (permissionHint) permissionHint.textContent = PERMISSION_MODE_HINTS[state.permissionMode];
  updateComposerAvailability();
}

export function updateComposerAvailability() {
  const archived = Boolean(state.currentSession?.archived_at);
  const streaming = Boolean(state.chatController);
  const agentNeedsWorkspace = !workspaceIsReady(currentWorkspace());
  const empty = !$("chat-message-input").value.trim();
  $("archived-session-notice").hidden = !archived;
  $("chat-message-input").disabled = archived;
  $("permission-mode-input").disabled = archived || streaming;
  const runControl = updateComposerRunControl({ archived });
  const cancellable = !archived && state.latestRunId && state.latestRunConversationId === state.conversationId
    && ["queued", "running", "paused", "waiting_approval", "waiting_input"].includes(state.latestRunStatus);
  const cancel = $("stop-chat-btn");
  cancel.classList.toggle("hidden", !cancellable && !streaming);
  cancel.disabled = state.composerCancelBusy;
  cancel.setAttribute("aria-busy", String(state.composerCancelBusy));
  $("send-chat-btn").disabled = Boolean(runControl)
    || archived
    || streaming
    || agentNeedsWorkspace
    || empty;
  if (archived) {
    setChatStatus("已归档 · 恢复后可继续", "warning");
  }
  renderSessionModelControls();
}

export function composerRunControlPresentation({ archived = false } = {}) {
  if (
    archived
    || !state.latestRunId
    || state.latestRunConversationId !== state.conversationId
  ) {
    return null;
  }
  if (state.latestRunStatus === "running") {
    const pending = state.composerPauseRequestedRunId === state.latestRunId;
    return {
      action: "pause",
      icon: "pause",
      label: pending ? "正在暂停 Agent" : "暂停 Agent",
      pending,
    };
  }
  if (state.latestRunStatus === "paused") {
    return {
      action: "continue",
      icon: "arrow-up",
      label: "继续 Agent",
      pending: false,
    };
  }
  return null;
}

export function updateComposerRunControl({ archived = false } = {}) {
  if (
    state.composerPauseRequestedRunId
    && state.latestRunStatus !== "running"
  ) {
    state.composerPauseRequestedRunId = "";
  }
  const presentation = composerRunControlPresentation({ archived });
  const button = $("agent-run-control-btn");
  const sendButton = $("send-chat-btn");
  const input = $("chat-message-input");
  if (!input.dataset.defaultPlaceholder) {
    input.dataset.defaultPlaceholder = input.placeholder;
  }
  button.hidden = !presentation;
  sendButton.hidden = Boolean(presentation);
  input.placeholder = presentation?.action === "continue"
    ? "补充要求（可选），点击右侧继续…"
    : input.dataset.defaultPlaceholder;
  if (!presentation) {
    button.disabled = true;
    button.removeAttribute("data-action");
    button.removeAttribute("aria-busy");
    return null;
  }
  const busy = state.composerRunControlBusy || presentation.pending;
  button.dataset.action = presentation.action;
  button.disabled = busy;
  button.setAttribute("aria-label", presentation.label);
  button.setAttribute("title", presentation.label);
  button.setAttribute("aria-busy", String(busy));
  button.querySelector("use").setAttribute("href", `#icon-${presentation.icon}`);
  return presentation;
}
