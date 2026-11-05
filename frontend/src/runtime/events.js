import { listen, managedSetTimeout } from '../lib/resources';
import { state } from './state.js';
import { $ } from './format.js';
import { humanizeError, showToast } from './feedback.js';
import { closeSlashCommandMenu, moveSlashSelection, queueUiPreferenceSave, resizeComposerInput, saveComposerDraft, selectSlashItem, setComposerValue, updateSlashCommandMenu } from './composer.js';
import { bindPanelResizer, closeSettings, closeWorkspacePicker, openSettings, setInspectorVisible, setMobileMoreOpen, setSidebarVisible, switchView, syncInspectorPresentation, syncSidebarPresentation } from './navigation.js';
import { updateComposer, updateComposerAvailability, updateContextSummary } from './context.js';
import { renderOverview, renderRequestLog } from './overview.js';
import { loadAuditRun, loadAuditRuns, renderAuditRuns, renderAuditTimeline } from './audit.js';
import { checkHealth } from './http.js';
import { handleMCPServerAction, handleSkillAction, loadMCPRegistry, loadSkillRegistry, resetMCPServerForm, resetSkillForm, saveMCPServer, saveSkill, updateMCPTransportFields } from './tools.js';
import { bindComposerConfigDismissal, closeModelPicker, registeredModel, renderSessionModelControls, toggleModelPicker } from './models.js';
import { loadEvalDashboard, loadEvalRun, pinEvalBaseline, startEvalRun } from './evaluations.js';
import { deleteRegisteredModel, discoverProviderModels, handleProviderAction, loadModelPreference, loadModelRegistry, renderDiscoveredModels, resetRegisteredModelForm, saveModelPreference, saveRegisteredModel, syncModelRegistryRefresh, testRegisteredModel, updateRegisteredModel } from './model-actions.js';
import { canSwitchSession, createSession, handleSessionAction, listSessions, loadSession, loadSessionSummary, refreshMessages, updateSessionMetadata } from './sessions.js';
import { addMessage, handleConversationAction } from './conversation.js';
import { handleComposerRunControl, stopChat, submitComposerMessage } from './chat.js';
import { closeCheckpointHistory, renderCheckpointHistory, restoreSelectedCheckpoint } from './runs.js';
import { askRag, beginCreateKnowledgeBase, bulkDeleteKnowledgeDocuments, closeDocumentDrawer, createKnowledgeBase, deleteKnowledgeBase, deleteKnowledgeDocument, ingestDocument, listKnowledgeBases, listKnowledgeDocuments, openDocumentDrawer, renderKnowledgeBases, renderKnowledgeDocuments, renderSelectedDocumentFiles, replaceKnowledgeDocument, saveKnowledgeDocument, searchRag, selectKnowledgeBase, setKnowledgeTab, toggleRerank, updateKnowledgeBase } from './knowledge.js';
import { clearMemorySelection, clearUserMemorySelection, consolidateFileMemory, createProjectMemory, createUserMemory, forgetProjectMemory, forgetUserMemory, loadMemoryIndex, refreshProjectMemory, refreshUserMemory, renderProjectMemories, renderUserMemories, searchConversationMemory, selectConversationMemoryHit, selectProjectMemory, selectUserMemory, setMemoryTab, updateProjectMemory, updateUserMemory } from './memory.js';
import { browseWorkspaceDirectories, chooseWorkspaceDirectory, listWorkspaces, openWorkspacePicker, persistWorkspaceSelection, removeWorkspace } from './workspaces.js';

export function bindEvents() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    listen(button, "click", () => switchView(button.dataset.view));
  });

  listen(document, "click", (event) => {
    const prompt = event.target.closest("[data-prompt]");
    if (!prompt) {
      return;
    }
    setComposerValue(prompt.dataset.prompt, { focus: true });
  });

  listen($("chat-output"), "click", (event) => {
    if (event.target.closest("[data-message-action], [data-response-action]")) {
      handleConversationAction(event.target)
        .catch((error) => showToast(humanizeError(error), "error"));
    }
  });

  listen($("mobile-more-btn"), "click", () => {
    setMobileMoreOpen($("mobile-more-menu").hidden);
  });
  listen($("mobile-nav-backdrop"), "click", () => setMobileMoreOpen(false));
  listen($("mobile-more-settings-btn"), "click", () => {
    setMobileMoreOpen(false);
    openSettings();
  });

  listen($("run-eval-btn"), "click", startEvalRun);
  listen($("refresh-eval-btn"), "click", () => {
    loadEvalDashboard(state.evalRunId).catch((error) =>
      showToast(humanizeError(error), "error"));
  });
  listen($("pin-eval-baseline-btn"), "click", pinEvalBaseline);
  listen($("eval-history-list"), "click", (event) => {
    const row = event.target.closest("[data-eval-run]");
    if (!row) {
      return;
    }
    loadEvalRun(row.dataset.evalRun).catch((error) =>
      showToast(humanizeError(error), "error"));
  });

  listen($("refresh-trace-audit-btn"), "click", () => {
    loadAuditRuns()
      .then(() => showToast("Trace 已刷新"))
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("trace-audit-prev"), "click", () => {
    state.auditPage = Math.max(0, state.auditPage - 1);
    renderAuditTimeline();
  });
  listen($("trace-audit-next"), "click", () => {
    state.auditPage += 1;
    renderAuditTimeline();
  });
  listen($("trace-run-search"), "input", renderAuditRuns);
  listen($("trace-run-status-filter"), "change", renderAuditRuns);
  listen($("trace-run-list"), "click", (event) => {
    const item = event.target.closest("[data-audit-run-id]");
    if (!item || item.dataset.auditRunId === state.auditRunId) return;
    loadAuditRun(item.dataset.auditRunId)
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("trace-audit-view"), "click", (event) => {
    const filter = event.target.closest("[data-audit-filter]");
    if (!filter) return;
    state.auditCategory = filter.dataset.auditFilter;
    state.auditPage = 0;
    document.querySelectorAll("[data-audit-filter]").forEach((button) => {
      const active = button === filter;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    renderAuditTimeline();
  });

  listen($("open-settings-btn"), "click", openSettings);
  listen($("sidebar-settings-btn"), "click", openSettings);
  listen($("composer-workspace-btn"), "click", openSettings);
  listen($("composer-model-btn"), "click", () => {
    $("composer-config").open = true;
    managedSetTimeout(() => $("composer-config").querySelector("summary").focus(), 0);
  });
  bindComposerConfigDismissal();
  listen($("close-settings-btn"), "click", closeSettings);
  listen($("settings-dialog"), "click", (event) => {
    if (event.target === $("settings-dialog")) {
      closeSettings();
    }
  });
  listen($("close-checkpoint-history-btn"), "click", closeCheckpointHistory);
  listen($("checkpoint-history-done-btn"), "click", closeCheckpointHistory);
  listen($("checkpoint-history-dialog"), "click", (event) => {
    if (event.target === $("checkpoint-history-dialog")) closeCheckpointHistory();
  });
  listen($("checkpoint-history-list"), "click", (event) => {
    const card = event.target.closest("[data-checkpoint-id]");
    if (!card) return;
    state.selectedCheckpointId = card.dataset.checkpointId;
    renderCheckpointHistory();
  });
  listen($("checkpoint-fork-btn"), "click", () => {
    restoreSelectedCheckpoint("fork");
  });
  listen($("checkpoint-rollback-btn"), "click", () => {
    restoreSelectedCheckpoint("rollback");
  });
  listen($("open-workspace-picker-btn"), "click", (event) => {
    openWorkspacePicker(null, event.currentTarget);
  });
  listen($("close-workspace-picker-btn"), "click", closeWorkspacePicker);
  listen($("workspace-picker-dialog"), "click", (event) => {
    if (event.target === $("workspace-picker-dialog")) {
      closeWorkspacePicker();
    }
  });
  listen($("workspace-directory-list"), "click", (event) => {
    const directory = event.target.closest("[data-directory-path]");
    if (!directory) {
      return;
    }
    browseWorkspaceDirectories(directory.dataset.directoryPath)
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("workspace-picker-up-btn"), "click", () => {
    browseWorkspaceDirectories(state.workspaceDirectoryParentPath)
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("workspace-picker-roots-btn"), "click", () => {
    browseWorkspaceDirectories()
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("choose-workspace-directory-btn"), "click", chooseWorkspaceDirectory);
  listen($("workspace-manager-list"), "click", async (event) => {
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

  listen($("toggle-sidebar-btn"), "click", () => {
    setSidebarVisible(document.body.classList.contains("sidebar-hidden"));
  });
  listen($("toggle-inspector-btn"), "click", () => {
    setInspectorVisible(document.body.classList.contains("inspector-hidden"));
  });
  listen($("close-inspector-btn"), "click", () => setInspectorVisible(false));
  listen($("inspector-backdrop"), "click", () => {
    setInspectorVisible(false);
    $("toggle-inspector-btn").focus();
  });
  listen(window, "resize", () => {
    syncInspectorPresentation(!$("inspector-panel").hidden);
    syncSidebarPresentation();
    if (window.innerWidth > 900) setMobileMoreOpen(false);
  }, { passive: true });
  listen(document, "keydown", (event) => {
    if (event.key === "Escape" && !$("inspector-panel").hidden && window.innerWidth <= 1120) {
      setInspectorVisible(false);
      $("toggle-inspector-btn").focus();
    }
    if (event.key === "Escape" && !$("mobile-more-menu").hidden) {
      setMobileMoreOpen(false);
      $("mobile-more-btn").focus();
    }
  });
  bindPanelResizer("sidebar");
  bindPanelResizer("inspector");

  const createNewSession = () => {
    if (canSwitchSession()) {
      createSession().catch(() => {});
    }
  };
  listen($("create-session-btn"), "click", createNewSession);
  listen($("sessions-create-btn"), "click", createNewSession);
  listen($("rename-current-session-btn"), "click", () => {
    if (!state.currentSession?.id) return;
    handleSessionAction(state.currentSession.id, "rename")
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("view-all-sessions-btn"), "click", () => {
    switchView("sessions");
    listSessions(false).catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("list-sessions-btn"), "click", async () => {
    try {
      await listSessions();
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });
  listen($("session-filter-form"), "submit", async (event) => {
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
  listen($("load-more-sessions-btn"), "click", async () => {
    try {
      await listSessions(true, { append: true });
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });
  listen($("summary-session-btn"), "click", loadSessionSummary);
  listen($("refresh-messages-btn"), "click", async () => {
    try {
      await refreshMessages();
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  });
  listen($("add-message-btn"), "click", addMessage);
  listen($("sessions-list"), "click", async (event) => {
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
  listen($("recent-sessions-list"), "click", async (event) => {
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
  listen($("restore-current-session-btn"), "click", async () => {
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

  listen($("send-chat-btn"), "click", submitComposerMessage);
  listen($("stop-chat-btn"), "click", stopChat);
  listen($("agent-run-control-btn"), "click", handleComposerRunControl);
  listen($("permission-mode-input"), "change", (event) => {
    state.permissionMode = event.target.value;
    queueUiPreferenceSave();
    updateComposer();
    showToast("已更新 Cogent 权限模式；工作区和平台的安全限制不会被绕过。", "info");
  });
  listen($("auto-model-toggle"), "change", async (event) => {
    state.modelPreference.mode = event.target.checked ? "auto" : "manual";
    renderSessionModelControls();
    try {
      await saveModelPreference();
    } catch {
      await loadModelPreference().catch(() => {});
    }
  });
  listen($("session-model-select"), "change", () => {
    saveModelPreference().catch(() => loadModelPreference().catch(() => {}));
  });
  listen($("model-picker-trigger"), "click", toggleModelPicker);
  listen($("model-picker-trigger"), "keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if ($("model-picker-menu").hidden) toggleModelPicker();
      $("model-picker-menu").querySelector('[role="option"]')?.focus();
    }
  });
  listen($("model-picker-menu"), "click", (event) => {
    const option = event.target.closest("[data-model-option]");
    if (!option) return;
    state.modelPreference.preferred_model_id = option.dataset.modelOption;
    $("session-model-select").value = option.dataset.modelOption;
    renderSessionModelControls();
    saveModelPreference().catch(() => loadModelPreference().catch(() => {}));
  });
  listen($("model-picker-menu"), "keydown", (event) => {
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
  listen(document, "click", (event) => {
    if (!event.target.closest(".model-choice-control")) closeModelPicker();
  });
  listen($("model-fallback-toggle"), "change", () => {
    saveModelPreference().catch(() => loadModelPreference().catch(() => {}));
  });
  listen($("chat-message-input"), "keydown", (event) => {
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
  listen($("chat-message-input"), "input", () => {
    $("chat-message-input").removeAttribute("aria-invalid");
    resizeComposerInput();
    saveComposerDraft();
    updateComposerAvailability();
    updateSlashCommandMenu();
  });
  listen($("chat-message-input"), "click", updateSlashCommandMenu);
  listen($("slash-command-options"), "pointerdown", (event) => {
    if (event.target.closest("[data-slash-index]")) event.preventDefault();
  });
  listen($("slash-command-options"), "click", (event) => {
    const option = event.target.closest("[data-slash-index]");
    if (option) selectSlashItem(Number(option.dataset.slashIndex));
  });
  listen(document, "pointerdown", (event) => {
    if (!event.target.closest(".composer")) closeSlashCommandMenu();
  });
  ["chat-message-input", "rag-question-input"].forEach((id) => {
    listen($(id), "input", () => $(id).removeAttribute("aria-invalid"));
  });

  listen($("ingest-doc-btn"), "click", ingestDocument);
  listen($("document-files-input"), "change", renderSelectedDocumentFiles);
  listen($("new-knowledge-base-btn"), "click", beginCreateKnowledgeBase);
  listen($("knowledge-base-search-input"), "input", renderKnowledgeBases);
  listen($("refresh-knowledge-bases-btn"), "click", () => {
    listKnowledgeBases().catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("create-knowledge-base-btn"), "click", createKnowledgeBase);
  listen($("update-knowledge-base-btn"), "click", updateKnowledgeBase);
  listen($("delete-knowledge-base-btn"), "click", deleteKnowledgeBase);
  listen($("knowledge-base-list"), "click", (event) => {
    const row = event.target.closest("[data-knowledge-base-id]");
    if (!row) {
      return;
    }
    selectKnowledgeBase(
      state.knowledgeBases.find((item) => item.id === row.dataset.knowledgeBaseId),
    );
  });
  listen($("mobile-knowledge-base-select"), "change", (event) => {
    selectKnowledgeBase(
      state.knowledgeBases.find((item) => item.id === event.target.value),
    );
  });
  document.querySelectorAll("[data-rag-tab]").forEach((tab) => {
    listen(tab, "click", () => setKnowledgeTab(tab.dataset.ragTab));
    listen(tab, "keydown", (event) => {
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
  listen($("document-search-input"), "input", () => {
    window.clearTimeout(documentSearchTimer);
    documentSearchTimer = managedSetTimeout(() => {
      state.knowledgeDocumentPage = 1;
      listKnowledgeDocuments().catch((error) => showToast(humanizeError(error), "error"));
    }, 240);
  });
  ["document-status-filter", "document-sort-input"].forEach((id) => {
    listen($(id), "change", () => {
      state.knowledgeDocumentPage = 1;
      listKnowledgeDocuments().catch((error) => showToast(humanizeError(error), "error"));
    });
  });
  listen($("previous-document-page"), "click", () => {
    if (state.knowledgeDocumentPage <= 1) return;
    state.knowledgeDocumentPage -= 1;
    listKnowledgeDocuments().catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("next-document-page"), "click", () => {
    state.knowledgeDocumentPage += 1;
    listKnowledgeDocuments().catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("knowledge-document-rows"), "click", (event) => {
    const openButton = event.target.closest("[data-open-document]");
    if (!openButton) return;
    openDocumentDrawer(openButton.dataset.openDocument, openButton)
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("select-all-documents"), "change", (event) => {
    for (const documentItem of state.knowledgeDocuments) {
      if (event.target.checked) state.selectedDocumentIds.add(documentItem.id);
      else state.selectedDocumentIds.delete(documentItem.id);
    }
    renderKnowledgeDocuments();
  });
  listen($("bulk-delete-documents-btn"), "click", bulkDeleteKnowledgeDocuments);
  listen($("close-document-drawer"), "click", () => closeDocumentDrawer());
  listen($("document-drawer-backdrop"), "click", () => closeDocumentDrawer());
  listen($("save-document-btn"), "click", saveKnowledgeDocument);
  listen($("replace-document-btn"), "click", replaceKnowledgeDocument);
  listen($("delete-document-btn"), "click", () => deleteKnowledgeDocument());
  listen($("document-drawer"), "keydown", (event) => {
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
  listen($("search-rag-btn"), "click", searchRag);
  listen($("ask-rag-btn"), "click", askRag);
  listen($("rag-rerank-toggle"), "click", toggleRerank);
  document.querySelectorAll("[data-memory-tab]").forEach((tab) => {
    listen(tab, "click", () => setMemoryTab(tab.dataset.memoryTab));
    listen(tab, "keydown", (event) => {
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
  listen($("new-project-memory-btn"), "click", () => {
    clearMemorySelection();
    $("memory-name-input").focus();
  });
  listen($("create-memory-btn"), "click", createProjectMemory);
  listen($("update-memory-btn"), "click", updateProjectMemory);
  listen($("delete-memory-btn"), "click", forgetProjectMemory);
  listen($("memory-kind-filter"), "change", renderProjectMemories);
  listen($("refresh-project-memory-btn"), "click", refreshProjectMemory);
  listen($("consolidate-memory-btn"), "click", consolidateFileMemory);
  listen($("memory-list"), "click", async (event) => {
    const row = event.target.closest("[data-memory-id]");
    if (!row) return;
    selectProjectMemory(state.projectMemories.find((item) => item.id === row.dataset.memoryId));
  });
  listen($("refresh-user-memory-btn"), "click", refreshUserMemory);
  listen($("new-user-memory-btn"), "click", () => {
    clearUserMemorySelection();
    $("user-memory-name-input").focus();
  });
  listen($("create-user-memory-btn"), "click", createUserMemory);
  listen($("update-user-memory-btn"), "click", updateUserMemory);
  listen($("delete-user-memory-btn"), "click", forgetUserMemory);
  listen($("user-memory-kind-filter"), "change", renderUserMemories);
  listen($("show-user-memory-index-btn"), "click", () =>
    loadMemoryIndex("user", "user-memory-evidence").catch((error) => showToast(humanizeError(error), "error")));
  listen($("user-memory-list"), "click", (event) => {
    const row = event.target.closest("[data-user-memory-id]");
    if (!row) return;
    selectUserMemory(state.userMemories.find((item) => item.id === row.dataset.userMemoryId));
  });
  listen($("search-conversation-memory-btn"), "click", searchConversationMemory);
  listen($("conversation-memory-results"), "click", (event) => {
    const row = event.target.closest("[data-conversation-memory-index]");
    if (!row) return;
    selectConversationMemoryHit(Number(row.dataset.conversationMemoryIndex));
  });
  listen($("clear-conversation-memory-btn"), "click", () => {
    state.conversationMemoryRequestGeneration += 1;
    $("conversation-memory-results").setAttribute("aria-busy", "false");
    $("conversation-memory-query").value = "";
    $("conversation-memory-session").value = "";
    state.selectedConversationMemoryHit = -1;
    searchConversationMemory();
  });
  listen($("conversation-memory-query"), "keydown", (event) => {
    if (event.key === "Enter") searchConversationMemory();
  });

  listen($("refresh-tools-registry-btn"), "click", () => {
    Promise.all([loadSkillRegistry(true), loadMCPRegistry()])
      .then(() => showToast("Skill 与 MCP 状态已刷新"))
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("reset-skill-form-btn"), "click", resetSkillForm);
  listen($("skill-form"), "submit", saveSkill);
  
  listen($("mcp-transport-input"), "change", (event) => {
    updateMCPTransportFields();
    $("mcp-legacy-input").checked = ["stdio_2025_06_18", "legacy_sse"].includes(event.target.value);
  });
  listen($("reset-mcp-form-btn"), "click", resetMCPServerForm);
  listen($("mcp-server-form"), "submit", saveMCPServer);
  

  listen($("refresh-model-registry-btn"), "click", () => {
    loadModelRegistry(true)
      .then(() => showToast("模型状态已刷新"))
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  
  listen($("discover-provider-models-btn"), "click", () => {
    discoverProviderModels()
      .catch((error) => showToast(humanizeError(error), "error"));
  });
  listen($("registered-model-provider-input"), "change", (event) => {
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
  listen($("discovered-model-select"), "change", renderDiscoveredModels);
  listen($("save-registered-model-btn"), "click", saveRegisteredModel);
  
  listen(document, "visibilitychange", () => {
    syncModelRegistryRefresh();
    if (!document.hidden && state.currentView === "models") {
      loadModelRegistry().catch(() => null);
    }
  });

  listen($("refresh-overview-btn"), "click", async () => {
    await Promise.allSettled([
      checkHealth(),
      listSessions(false),
      listWorkspaces(),
    ]);
  });
  listen($("clear-log-btn"), "click", () => {
    state.requestLog = [];
    renderRequestLog();
    renderOverview();
  });
  listen($("conversation-id-input"), "input", (event) => {
    updateContextSummary();
  });
  ["user-id-input", "provider-input", "model-input", "thinking-level-input", "workspace-id-input", "workspace-root-input"].forEach((id) => {
    listen($(id), "input", updateContextSummary);
    listen($(id), "change", updateContextSummary);
  });

  listen(window, "hashchange", () => {
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
