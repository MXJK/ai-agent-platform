import { managedSetTimeout, managedRequestAnimationFrame, listen } from '../lib/resources';
import { flushSync } from 'react-dom';
import { navigation } from '../app/navigation';
import { PANEL_WIDTHS, runtimeRefs } from './config.js';
import { state } from './state.js';
import { $, preferredScrollBehavior } from './format.js';
import { humanizeError, showToast } from './feedback.js';
import { saveUiPreferences } from './composer.js';
import { updateContextSummary } from './context.js';
import { clearAuditPoll, loadAuditRun, loadAuditRuns } from './audit.js';
import { loadMCPRegistry, loadSkillRegistry } from './tools.js';
import { loadEvalDashboard } from './evaluations.js';
import { loadModelRegistry, syncModelRegistryRefresh } from './model-actions.js';
import { refreshMemoryWorkbench } from './memory.js';
import { listWorkspaces, renderWorkspaceManager } from './workspaces.js';

export function conversationIsNearBottom() {
  const output = $("chat-output");
  const remaining = output.scrollHeight - (output.scrollTop + output.clientHeight);
  return remaining < 180;
}

export function updateJumpToLatestButton() {
  const button = $("jump-to-latest-btn");
  if (!button) return;
  const hasMessages = Boolean($("chat-output")?.querySelector(".chat-message"));
  button.hidden = !hasMessages || conversationIsNearBottom();
}

export function scrollConversationToLatest({ behavior = preferredScrollBehavior() } = {}) {
  state.followConversation = true;
  const output = $("chat-output");
  output.scrollTo({
    top: output.scrollHeight,
    behavior,
  });
  managedSetTimeout(updateJumpToLatestButton, behavior === "smooth" ? 240 : 0);
}

export function scheduleConversationFollow() {
  if (!state.followConversation) {
    updateJumpToLatestButton();
    return;
  }
  window.cancelAnimationFrame(runtimeRefs.conversationFollowFrame);
  runtimeRefs.conversationFollowFrame = managedRequestAnimationFrame(() => {
    scrollConversationToLatest({ behavior: "auto" });
  });
}

export function bindConversationFollow() {
  const output = $("chat-output");
  const observer = new MutationObserver(scheduleConversationFollow);
  observer.observe(output, { childList: true, subtree: true, characterData: true });
  listen(output, "scroll", () => {
    state.followConversation = conversationIsNearBottom();
    updateJumpToLatestButton();
  }, { passive: true });
  listen($("jump-to-latest-btn"), "click", () =>
    scrollConversationToLatest({ behavior: preferredScrollBehavior() }));
}

export function setMobileMoreOpen(open) {
  const menu = $("mobile-more-menu");
  const backdrop = $("mobile-nav-backdrop");
  menu.hidden = !open;
  backdrop.hidden = !open;
  $("mobile-more-btn").setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("mobile-more-open", open);
}

export function updateMobileMoreState() {
  const overflowActive = ["memory", "models", "tools", "evals", "trace-audit"].includes(state.currentView);
  const button = $("mobile-more-btn");
  button.classList.toggle("active", overflowActive);
  if (overflowActive) {
    button.setAttribute("aria-current", "page");
  } else {
    button.removeAttribute("aria-current");
  }
}

export function switchView(viewName, updateHash = true) {
  const normalizedView = viewName === "agent"
    ? "chat"
    : (viewName === "mcp" ? "tools" : viewName);
  const panel = document.querySelector(`[data-view-panel="${normalizedView}"]`);
  if (!panel) {
    return;
  }
  state.currentView = normalizedView;
  if (normalizedView !== "trace-audit") clearAuditPoll();
  syncModelRegistryRefresh();
  setMobileMoreOpen(false);
  flushSync(() => navigation.set(normalizedView));
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

export function syncInspectorPresentation(visible) {
  const drawer = window.innerWidth <= 1120;
  $("inspector-backdrop").hidden = !visible || !drawer;
  document.body.classList.toggle("inspector-drawer-open", visible && drawer);
}

export function clampPanelWidth(name, value) {
  const config = PANEL_WIDTHS[name];
  const numeric = Number(value);
  return Math.min(config.max, Math.max(config.min, Number.isFinite(numeric) ? numeric : config.defaultValue));
}

export function panelWidth(name) {
  const config = PANEL_WIDTHS[name];
  const value = getComputedStyle(document.documentElement).getPropertyValue(config.cssVariable);
  return Math.round(clampPanelWidth(name, Number.parseFloat(value)));
}

export function setPanelWidth(name, value, { persist = false } = {}) {
  const width = Math.round(clampPanelWidth(name, value));
  const config = PANEL_WIDTHS[name];
  document.documentElement.style.setProperty(config.cssVariable, `${width}px`);
  $(`${name}-resizer`).setAttribute("aria-valuenow", String(width));
  if (persist) saveUiPreferences();
  return width;
}

export function applyPanelPreferences(preferences) {
  setPanelWidth("sidebar", preferences.sidebarWidth ?? PANEL_WIDTHS.sidebar.defaultValue);
  setPanelWidth("inspector", preferences.inspectorWidth ?? PANEL_WIDTHS.inspector.defaultValue);
}

export function syncSidebarPresentation() {
  const panel = $("primary-sidebar");
  const hidden = document.body.classList.contains("sidebar-hidden") && window.innerWidth > 900;
  panel.setAttribute("aria-hidden", String(hidden));
  panel.inert = hidden;
}

export function setSidebarVisible(visible) {
  document.body.classList.toggle("sidebar-hidden", !visible);
  $("toggle-sidebar-btn").setAttribute("aria-expanded", String(visible));
  $("toggle-sidebar-btn").setAttribute("aria-label", visible ? "隐藏主导航" : "显示主导航");
  $("toggle-sidebar-btn").title = visible ? "隐藏主导航" : "显示主导航";
  syncSidebarPresentation();
  saveUiPreferences();
}

export function setInspectorVisible(visible) {
  const panel = $("inspector-panel");
  document.body.classList.toggle("inspector-hidden", !visible);
  $("toggle-inspector-btn").setAttribute("aria-expanded", String(visible));
  $("toggle-inspector-btn").setAttribute(
    "aria-label",
    visible ? "隐藏最近会话" : "显示最近会话",
  );
  $("toggle-inspector-btn").title = visible ? "隐藏最近会话" : "显示最近会话";
  panel.setAttribute("aria-hidden", String(!visible));
  panel.inert = !visible;
  panel.hidden = !visible;
  syncInspectorPresentation(visible);
  saveUiPreferences();
}

export function bindPanelResizer(name) {
  const handle = $(`${name}-resizer`);
  const config = PANEL_WIDTHS[name];
  const supportsResize = () => window.innerWidth > (name === "sidebar" ? 900 : 1120);
  const widthFromPointer = (clientX) => name === "sidebar" ? clientX : window.innerWidth - clientX;

  listen(handle, "pointerdown", (event) => {
    if (!supportsResize()) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add("is-resizing-panels");
  });
  listen(handle, "pointermove", (event) => {
    if (!handle.hasPointerCapture(event.pointerId)) return;
    setPanelWidth(name, widthFromPointer(event.clientX));
  });
  const finishResize = (event) => {
    if (!handle.hasPointerCapture(event.pointerId)) return;
    handle.releasePointerCapture(event.pointerId);
    document.body.classList.remove("is-resizing-panels");
    saveUiPreferences();
  };
  listen(handle, "pointerup", finishResize);
  listen(handle, "pointercancel", finishResize);
  listen(handle, "dblclick", () => {
    if (supportsResize()) setPanelWidth(name, config.defaultValue, { persist: true });
  });
  listen(handle, "keydown", (event) => {
    if (!supportsResize()) return;
    let next = panelWidth(name);
    if (event.key === "Home") next = config.min;
    else if (event.key === "End") next = config.max;
    else if (event.key === "ArrowLeft") next += name === "sidebar" ? -16 : 16;
    else if (event.key === "ArrowRight") next += name === "sidebar" ? 16 : -16;
    else return;
    event.preventDefault();
    setPanelWidth(name, next, { persist: true });
  });
}

export function openSettings() {
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

export function closeSettings() {
  const dialog = $("settings-dialog");
  if (dialog.open) {
    dialog.close();
  }
}

export function closeWorkspacePicker() {
  const dialog = $("workspace-picker-dialog");
  if (dialog.open) {
    dialog.close();
  }
  state.workspaceRelinkId = null;
}

export function setChatWorkbenchActive(active) {
  const workbench = $("chat-view");
  workbench.classList.toggle("has-conversation", active);
  workbench.setAttribute("aria-labelledby", active ? "active-session-title" : "chat-title");
  $("chat-welcome-header").hidden = active;
  $("active-session-header").hidden = !active;
  if (active) {
    updateContextSummary();
  }
}
