import { NATIVE_PICKER_LOCAL_ONLY_DETAIL } from './config.js';
import { state } from './state.js';
import { $, escapeHtml, formatTokenCount, iconMarkup } from './format.js';
import { humanizeError, setRaw, showToast } from './feedback.js';
import { closeWorkspacePicker } from './navigation.js';
import { currentWorkspace, setActiveWorkspace, updateContextSummary, workspaceIsReady, workspaceRoleLabel } from './context.js';
import { fetchJson } from './http.js';
import { loadSessionTokenUsage, replaceSessionInLists } from './sessions.js';
import { refreshMemoryWorkbench, refreshProjectMemory } from './memory.js';

export function workspaceName(workspace) {
  return workspace.root_path.split(/[\\/]/).filter(Boolean).at(-1) || workspace.id;
}

export function renderWorkspaceManager() {
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

export async function persistWorkspaceSelection(
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

export async function registerWorkspace(workspaceId, rootPath) {
  const body = await fetchJson(`/workspaces/${encodeURIComponent(workspaceId)}`, {
    method: "PUT",
    body: JSON.stringify({ root_path: rootPath }),
  });
  setRaw(body);
  await listWorkspaces();
  return body;
}

export async function removeWorkspace(workspaceId) {
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

export function workspaceIdForPath(path) {
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

export function renderWorkspaceDirectories(body) {
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

export async function browseWorkspaceDirectories(path = null) {
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

export function configureWorkspacePicker(workspaceId = null) {
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

export async function openWorkspaceBrowserPicker(workspaceId = null) {
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

export async function applyWorkspaceDirectory(path) {
  const relinking = Boolean(state.workspaceRelinkId);
  const workspaceId = state.workspaceRelinkId || workspaceIdForPath(path);
  const workspace = await registerWorkspace(workspaceId, path);
  await persistWorkspaceSelection(workspace.id, { announce: false });
  closeWorkspacePicker();
  showToast(`${relinking ? "已重新关联" : "已添加"} ${workspaceName(workspace)}`);
}

export async function openWorkspacePicker(workspaceId = null, triggerButton = null) {
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

export async function chooseWorkspaceDirectory() {
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

export async function listWorkspaces() {
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
  if (state.currentView === "memory") {
    await refreshMemoryWorkbench();
  }
}

export function renderWorkspaceCatalog() {
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

export async function loadWorkspaceTokenUsage(
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

export function renderWorkspaceTokenUsage() {
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

export function formatUsageOperation(operation) {
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

export function formatInputCountMethod(method) {
  const labels = {
    anthropic_messages_count_tokens: "Anthropic 精确计数",
    fake_lexical_tokenizer: "本地确定性计数",
    gemini_models_count_tokens: "Gemini 精确计数",
    openai_responses_input_tokens: "OpenAI 精确计数",
    provider_usage: "Provider Usage",
  };
  return labels[method] || method || "未知计数方式";
}

export async function refreshTokenUsageData() {
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
