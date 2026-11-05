import { state } from './state.js';
import { $ } from './format.js';
import { humanizeError, setTrace, showToast } from './feedback.js';
import { invalidateSlashCapabilities, loadUiPreferences, resizeComposerInput, restoreComposerDraft } from './composer.js';
import { applyPanelPreferences, bindConversationFollow, setInspectorVisible, setSidebarVisible, switchView } from './navigation.js';
import { applyConfigurationToInputs, updateComposer, updateComposerAvailability, updateContextSummary } from './context.js';
import { renderOverview, renderRequestLog } from './overview.js';
import { checkHealth } from './http.js';
import { loadMCPRegistry, resetSkillForm, updateMCPTransportFields } from './tools.js';
import { loadModelRegistry } from './model-actions.js';
import { listSessions, loadPreferences, loadSession, renderRecentSessions, renderSessions, resetChatView, updateSessionUrl } from './sessions.js';
import { listKnowledgeBases, loadRagCapabilities, renderRerankControl, setKnowledgeTab } from './knowledge.js';
import { listWorkspaces } from './workspaces.js';
import { bindEvents } from './events.js';

export async function restoreInitialSession() {
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

export async function init() {
  const preferences = loadUiPreferences();
  applyPanelPreferences(preferences);
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
  state.permissionMode = ["default", "acceptEdits", "plan", "bypassPermissions"].includes(preferences.permissionMode)
    ? preferences.permissionMode : "default";
  state.rerankEnabled = preferences.rerankEnabled === true;
  state.preferredKnowledgeBaseId = preferences.knowledgeBaseId || "";
  state.activeKnowledgeTab = ["documents", "ask", "settings"].includes(preferences.knowledgeTab)
    ? preferences.knowledgeTab
    : "documents";
  setKnowledgeTab(state.activeKnowledgeTab);
  renderRerankControl();
  updateComposer();
  switchView(initialView, !location.hash);
  setSidebarVisible(!preferences.sidebarHidden);
  setInspectorVisible(!preferences.inspectorHidden && window.innerWidth > 1120);
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
