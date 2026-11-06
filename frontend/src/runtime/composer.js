import { managedSetTimeout } from '../lib/resources';
import { UI_STORAGE_KEY, runtimeRefs } from './config.js';
import { state } from './state.js';
import { $, escapeHtml } from './format.js';
import { humanizeError, showToast } from './feedback.js';
import { panelWidth } from './navigation.js';
import { currentWorkspace, updateComposerAvailability, workspaceIsReady } from './context.js';
import { fetchJson } from './http.js';
import { ensureSession } from './sessions.js';
import { runAgentFromComposer } from './chat.js';

export function loadUiPreferences() {
  try {
    return JSON.parse(localStorage.getItem(UI_STORAGE_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

export function saveUiPreferences() {
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
        sidebarHidden: document.body.classList.contains("sidebar-hidden"),
        inspectorHidden: !state.inspectorPreferred,
        sidebarWidth: panelWidth("sidebar"),
        inspectorWidth: panelWidth("inspector"),
        rerankEnabled: state.rerankEnabled,
        knowledgeBaseId: $("kb-id-input")?.value || "",
        knowledgeTab: state.activeKnowledgeTab,
        permissionMode: state.permissionMode,
        composerDrafts,
      }),
    );
  } catch {
    // Device-local preferences are optional; the product remains usable without them.
  }
}

export function composerDraftKey(sessionId = state.conversationId) {
  return sessionId || "__new__";
}

export function queueUiPreferenceSave() {
  window.clearTimeout(runtimeRefs.composerDraftSaveTimer);
  runtimeRefs.composerDraftSaveTimer = managedSetTimeout(saveUiPreferences, 120);
}

export function saveComposerDraft(value = $("chat-message-input")?.value || "") {
  const key = composerDraftKey();
  if (value) {
    state.composerDrafts[key] = value.slice(0, 8000);
  } else {
    delete state.composerDrafts[key];
  }
  queueUiPreferenceSave();
}

export function resizeComposerInput() {
  const input = $("chat-message-input");
  if (!input) return;
  input.style.height = "0px";
  const maxHeight = Math.max(80, Math.floor(window.innerHeight * 0.35));
  const nextHeight = Math.min(maxHeight, Math.max(44, input.scrollHeight));
  input.style.height = `${nextHeight}px`;
  input.style.overflowY = input.scrollHeight > maxHeight ? "auto" : "hidden";
}

export function setComposerValue(value, { save = true, focus = false } = {}) {
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

export function restoreComposerDraft(sessionId = state.conversationId) {
  setComposerValue(state.composerDrafts[composerDraftKey(sessionId)] || "", {
    save: false,
  });
}

export function clearComposerInput() {
  setComposerValue("");
  closeSlashCommandMenu();
}

export function invalidateSlashCapabilities() {
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

export function slashQueryContext() {
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

export function composerSlashItems() {
  const builtin = (state.slashCapabilities.commands || []).map((item) => ({
    kind: "builtin", command: item.name, title: item.description,
    description: item.usage, action: item.name, aliases: item.aliases || [], keywords: [],
  }));
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

export function slashItemMatchScore(item, query) {
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

export function slashKindLabel(kind) {
  return {
    builtin: "命令",
    skill: "Skills",
    mcp: "MCP 工具",
  }[kind] || kind;
}

export function renderSlashCommandMenu() {
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

export function closeSlashCommandMenu() {
  const menu = $("slash-command-menu");
  if (!menu) return;
  menu.hidden = true;
  state.slashItems = [];
  state.slashActiveIndex = 0;
  state.slashQuery = "";
  $("chat-message-input")?.setAttribute("aria-expanded", "false");
  $("chat-message-input")?.removeAttribute("aria-activedescendant");
}

export async function loadSlashCapabilities() {
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

export function updateSlashCommandMenu() {
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

export function moveSlashSelection(offset) {
  if (!state.slashItems.length) return;
  state.slashActiveIndex = (
    state.slashActiveIndex + offset + state.slashItems.length
  ) % state.slashItems.length;
  renderSlashCommandMenu();
  $(`slash-command-option-${state.slashActiveIndex}`)?.scrollIntoView({
    block: "nearest",
  });
}

export function insertSlashCommand(item) {
  const input = $("chat-message-input");
  const context = slashQueryContext();
  if (!context) return;
  const before = input.value.slice(0, context.start);
  const after = input.value.slice(context.end);
  setComposerValue(`${before}/${item.command} ${after}`, { focus: true });
  closeSlashCommandMenu();
}

export async function runBuiltinComposerCommand(item, remaining = "") {
  setComposerValue(`/${item.command} ${remaining}`.trim(), { focus: true });
  closeSlashCommandMenu();
  await runAgentFromComposer();
}

export function selectSlashItem(index = state.slashActiveIndex) {
  const item = state.slashItems[index];
  if (!item) return;
  if (item.kind === "builtin") {
    runBuiltinComposerCommand(item).catch((error) =>
      showToast(humanizeError(error), "error"),
    );
    return;
  }
  insertSlashCommand(item);
}

export function splitSlashArguments(value) {
  const matches = String(value || "").match(/"(?:\\.|[^"\\])*"|'[^']*'|\S+/g) || [];
  return matches.map((item) => {
    if ((item.startsWith('"') && item.endsWith('"')) || (item.startsWith("'") && item.endsWith("'"))) {
      return item.slice(1, -1).replace(/\\(["\\])/g, "$1");
    }
    return item;
  });
}

export function parseComposerSlashInvocation(value) {
  const match = String(value || "").match(/^\s*\/([^\s]+)(?:\s+([\s\S]*))?$/);
  if (!match) return null;
  const commandName = match[1].toLowerCase();
  const remaining = match[2] || "";
  const builtin = composerSlashItems().find(
    (item) => item.kind === "builtin" && (item.command === commandName || item.aliases.includes(commandName)),
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

export function composerSubmission(value) {
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
