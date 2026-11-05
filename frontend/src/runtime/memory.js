import { state } from './state.js';
import { $, escapeHtml, formatDate, numberValue } from './format.js';
import { humanizeError, showToast, truncate } from './feedback.js';
import { currentWorkspace, workspaceIsReady } from './context.js';
import { fetchJson } from './http.js';

export function activeWorkspaceId() {
  return state.activeWorkspaceId;
}

export function requireActiveWorkspace() {
  const workspace = currentWorkspace();
  if (!workspace) {
    throw new Error("请先选择工作区");
  }
  if (!workspaceIsReady(workspace)) {
    throw new Error("当前工作区路径不可用，请切换或更新登记");
  }
  return workspace.id;
}

export const MEMORY_STATUS_META = {
  active: { label: "已生效", tone: "active", description: "会参与后续上下文召回" },
  candidate: { label: "待审核", tone: "candidate", description: "确认后才会进入模型上下文" },
  stale: { label: "已过期", tone: "stale", description: "来源变化，等待重新确认" },
  superseded: { label: "已替代", tone: "superseded", description: "已由更新版本取代" },
  rejected: { label: "已拒绝", tone: "rejected", description: "保留审计，但不会被召回" },
};

export const PROJECT_MEMORY_KIND_LABELS = {
  architecture_fact: "架构事实",
  constraint: "项目约束",
  decision: "技术决策",
  convention: "项目规范",
  task_outcome: "任务结果",
  incident_lesson: "故障经验",
};

export const USER_MEMORY_KIND_LABELS = {
  profile_fact: "个人事实",
  communication_preference: "沟通偏好",
  tooling_preference: "工具偏好",
  workflow_preference: "工作流偏好",
  standing_goal: "长期目标",
  personal_constraint: "个人约束",
};

export const USER_PROFILE_HEADING_LABELS = {
  "Standing constraints": "长期约束",
  "Communication preferences": "沟通偏好",
  "Workflow preferences": "工作流偏好",
  "Tooling preferences": "工具偏好",
  "Standing goals": "长期目标",
  "Profile facts": "个人事实",
  "Project scenes": "项目记忆摘要",
};

export function memoryStatusMeta(status) {
  return MEMORY_STATUS_META[status] || {
    label: status || "未知状态",
    tone: "neutral",
    description: "当前状态不会自动注入上下文",
  };
}

export function memoryStatusPill(status, version = null) {
  const meta = memoryStatusMeta(status);
  const versionText = version == null ? "" : ` · v${escapeHtml(version)}`;
  return `<span class="memory-status-pill ${escapeHtml(meta.tone)}" title="${escapeHtml(meta.description)}">${escapeHtml(meta.label)}${versionText}</span>`;
}

export function memoryLoadingMarkup(rows = 4) {
  return `<div class="memory-skeleton" aria-label="正在加载记忆">${Array.from(
    { length: rows },
    () => '<span class="memory-skeleton-row"></span>',
  ).join("")}</div>`;
}

export function memoryCounts(items) {
  return items.reduce(
    (counts, item) => {
      counts[item.status] = (counts[item.status] || 0) + 1;
      return counts;
    },
    {},
  );
}

export function filteredMemories(items, statusFilterId, kindFilterId) {
  const status = $(statusFilterId).value;
  const kind = $(kindFilterId).value;
  return items.filter(
    (item) => (!status || item.status === status) && (!kind || item.kind === kind),
  );
}

export function setMemoryRuntimeStatus(text, tone = "ready") {
  $("memory-status").textContent = text;
  const container = $("memory-status").closest(".memory-runtime-status");
  if (container) container.dataset.tone = tone;
}

export function memoryPayload() {
  return {
    kind: $("memory-kind-input").value,
    title: $("memory-title-input").value.trim(),
    content: $("memory-content-input").value.trim(),
    importance: numberValue("memory-importance-input", 3),
    expires_at: null,
  };
}

export function selectedProjectMemory() {
  return state.projectMemories.find((item) => item.id === state.selectedMemoryId) || null;
}

export function setMemoryTab(tabName, { focus = false } = {}) {
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

export function refreshMemoryWorkbench() {
  if (state.activeMemoryTab === "profile") {
    return refreshUserMemory();
  }
  if (state.activeMemoryTab === "project") {
    return refreshProjectMemory();
  }
  return searchConversationMemory();
}

// File-backed Cogent memory workbench. These definitions replace the retired
// candidate/profile UI above while keeping the surrounding view navigation.
export function memoryTopicBody(text) {
  return String(text || "").replace(/^---\s*\n[\s\S]*?\n---\s*\n?/, "").trimStart();
}

export function fileMemoryPayload(scope) {
  const user = scope === "user";
  return {
    workspace_id: requireActiveWorkspace(),
    scope,
    name: $(user ? "user-memory-name-input" : "memory-name-input").value.trim(),
    type: $(user ? "user-memory-kind-input" : "memory-kind-input").value,
    description: $(user ? "user-memory-title-input" : "memory-title-input").value.trim(),
    body: $(user ? "user-memory-content-input" : "memory-content-input").value.trim(),
  };
}

export function clearMemorySelection() {
  state.selectedMemoryId = "";
  $("memory-name-input").disabled = false;
  $("memory-name-input").value = "";
  $("memory-kind-input").value = "project";
  $("memory-title-input").value = "";
  $("memory-content-input").value = "";
  $("memory-detail-heading").textContent = "创建项目主题";
  $("memory-detail-subtitle").textContent = "主题名用于文件名，描述进入 MEMORY.md 索引。";
  $("memory-selection-label").textContent = "新文件";
  $("memory-selection-label").className = "memory-status-pill draft";
  $("create-memory-btn").hidden = false;
  $("update-memory-btn").disabled = true;
  $("delete-memory-btn").disabled = true;
  renderProjectMemories();
}

export function selectProjectMemory(memory) {
  if (!memory) return clearMemorySelection();
  state.selectedMemoryId = memory.id;
  $("memory-name-input").value = memory.name;
  $("memory-name-input").disabled = true;
  $("memory-kind-input").value = memory.type;
  $("memory-title-input").value = memory.description;
  $("memory-content-input").value = memoryTopicBody(memory.text);
  $("memory-detail-heading").textContent = memory.name;
  $("memory-detail-subtitle").textContent = memory.description;
  $("memory-selection-label").textContent = memory.type;
  $("memory-selection-label").className = "memory-status-pill active";
  $("create-memory-btn").hidden = true;
  $("update-memory-btn").disabled = false;
  $("delete-memory-btn").disabled = false;
  $("memory-evidence").innerHTML = `<div class="memory-evidence-summary"><span><small>文件</small><strong>${escapeHtml(memory.id)}</strong></span><span><small>SHA-256</small><strong title="${escapeHtml(memory.sha256)}">${escapeHtml(memory.sha256.slice(0, 12))}</strong></span><span><small>修改时间</small><strong>${escapeHtml(formatDate(memory.mtime_ms))}</strong></span></div>`;
  renderProjectMemories();
}

export function renderProjectMemories() {
  const kind = $("memory-kind-filter").value;
  const visible = state.projectMemories.filter((item) => !kind || item.type === kind);
  $("memory-count").textContent = String(visible.length);
  $("memory-project-active-count").textContent = String(state.projectMemories.length);
  const list = $("memory-list");
  if (!visible.length) {
    list.innerHTML = '<div class="memory-empty"><strong>还没有项目主题</strong><p>创建 project 决策或 reference 参考资料。</p></div>';
    return;
  }
  list.innerHTML = visible.map((item) => `<button class="memory-asset-row${item.id === state.selectedMemoryId ? " active" : ""}" type="button" data-memory-id="${escapeHtml(item.id)}" aria-pressed="${item.id === state.selectedMemoryId}"><span class="memory-asset-row-head"><strong>${escapeHtml(item.name)}</strong><span class="memory-status-pill active">${escapeHtml(item.type)}</span></span><span class="memory-asset-row-body">${escapeHtml(item.description)}</span><span class="memory-asset-row-meta"><span>${escapeHtml(formatDate(item.mtime_ms))}</span><span>${escapeHtml(item.sha256.slice(0, 10))}</span></span></button>`).join("");
}

export function renderMemoryJobs(jobs) {
  $("memory-job-list").innerHTML = jobs.length
    ? jobs.map((job) => `<div class="request-item ${job.status === "failed" || job.status === "blocked" ? "error" : "ok"}"><span><strong>${escapeHtml(job.operation || "maintenance")}</strong><small>${escapeHtml(job.id)}</small></span><span class="request-status">${escapeHtml(job.status)} · ${escapeHtml(job.boundary || "queued")}</span></div>`).join("")
    : '<div class="empty-state">暂无维护任务</div>';
}

export async function loadMemoryIndex(scope, targetId) {
  const params = new URLSearchParams({ workspace_id: requireActiveWorkspace(), scope });
  const index = await fetchJson(`/memory/index?${params}`);
  $(targetId).innerHTML = index.content
    ? `<div class="memory-evidence-summary"><span><small>MEMORY.md</small><strong>${escapeHtml(index.sha256.slice(0, 12))}</strong></span></div><pre class="memory-index-content">${escapeHtml(index.content)}</pre>`
    : '<div class="memory-empty compact"><strong>索引为空</strong><p>创建第一个主题后会自动写入 MEMORY.md。</p></div>';
}

export async function refreshProjectMemory() {
  if (!workspaceIsReady(currentWorkspace())) {
    state.projectMemories = [];
    renderProjectMemories();
    setMemoryRuntimeStatus("请先选择可用工作区", "warning");
    return;
  }
  const generation = ++state.projectMemoryRequestGeneration;
  $("memory-list").setAttribute("aria-busy", "true");
  $("memory-list").innerHTML = memoryLoadingMarkup();
  try {
    const workspace = encodeURIComponent(requireActiveWorkspace());
    const [files, jobs] = await Promise.all([
      fetchJson(`/memory/files?workspace_id=${workspace}&scope=project`),
      fetchJson(`/memory/maintenance?workspace_id=${workspace}`),
    ]);
    if (generation !== state.projectMemoryRequestGeneration) return;
    state.projectMemories = files.files || [];
    if (!state.projectMemories.some((item) => item.id === state.selectedMemoryId)) state.selectedMemoryId = "";
    renderProjectMemories();
    renderMemoryJobs(jobs.jobs || []);
    if (!state.selectedMemoryId && state.projectMemories.length) selectProjectMemory(state.projectMemories[0]);
    await loadMemoryIndex("project", "memory-evidence");
    setMemoryRuntimeStatus("项目文件记忆已连接");
  } catch (error) {
    $("memory-list").innerHTML = '<div class="memory-empty error"><strong>项目记忆加载失败</strong><p>检查工作区权限和文件状态后重试。</p></div>';
    setMemoryRuntimeStatus("项目记忆加载失败", "error");
    showToast(humanizeError(error), "error");
  } finally {
    $("memory-list").setAttribute("aria-busy", "false");
  }
}

export async function saveFileMemory(scope, creating) {
  const payload = fileMemoryPayload(scope);
  if (!payload.name || !payload.description || !payload.body) {
    showToast("主题名、索引描述和正文不能为空", "warning");
    return;
  }
  const collection = scope === "user" ? state.userMemories : state.projectMemories;
  const selectedId = scope === "user" ? state.selectedUserMemoryId : state.selectedMemoryId;
  const current = collection.find((item) => item.id === selectedId);
  if (!creating && current) payload.expected_hash = current.sha256;
  try {
    const saved = await fetchJson("/memory/files", {
      method: creating ? "POST" : "PUT",
      body: JSON.stringify(payload),
    });
    if (scope === "user") {
      await refreshUserMemory();
      selectUserMemory(saved);
    } else {
      await refreshProjectMemory();
      selectProjectMemory(saved);
    }
    showToast(creating ? "记忆主题已创建" : "记忆主题已保存");
  } catch (error) {
    showToast(error.status === 409 ? "文件已被其他操作修改，请刷新后重试" : humanizeError(error), error.status === 409 ? "warning" : "error");
  }
}

export async function createProjectMemory() { return saveFileMemory("project", true); }
export async function updateProjectMemory() { return saveFileMemory("project", false); }

export async function forgetProjectMemory() {
  const current = state.projectMemories.find((item) => item.id === state.selectedMemoryId);
  if (!current || !window.confirm(`删除项目主题 ${current.name}？会话证据仍保留。`)) return;
  try {
    await fetchJson("/memory/files", { method: "DELETE", body: JSON.stringify({ workspace_id: requireActiveWorkspace(), scope: "project", name: current.name, expected_hash: current.sha256 }) });
    clearMemorySelection();
    await refreshProjectMemory();
    showToast("项目主题已删除");
  } catch (error) { showToast(humanizeError(error), "error"); }
}

export function clearUserMemorySelection() {
  state.selectedUserMemoryId = "";
  $("user-memory-name-input").disabled = false;
  $("user-memory-name-input").value = "";
  $("user-memory-kind-input").value = "user";
  $("user-memory-title-input").value = "";
  $("user-memory-content-input").value = "";
  $("user-memory-detail-heading").textContent = "创建用户主题";
  $("user-memory-detail-subtitle").textContent = "主题会在同一登录身份的工作区间复用。";
  $("user-memory-selection-label").textContent = "新文件";
  $("user-memory-selection-label").className = "memory-status-pill draft";
  $("create-user-memory-btn").hidden = false;
  $("update-user-memory-btn").disabled = true;
  $("delete-user-memory-btn").disabled = true;
  renderUserMemories();
}

export function selectUserMemory(memory) {
  if (!memory) return clearUserMemorySelection();
  state.selectedUserMemoryId = memory.id;
  $("user-memory-name-input").value = memory.name;
  $("user-memory-name-input").disabled = true;
  $("user-memory-kind-input").value = memory.type;
  $("user-memory-title-input").value = memory.description;
  $("user-memory-content-input").value = memoryTopicBody(memory.text);
  $("user-memory-detail-heading").textContent = memory.name;
  $("user-memory-detail-subtitle").textContent = memory.description;
  $("user-memory-selection-label").textContent = memory.type;
  $("user-memory-selection-label").className = "memory-status-pill active";
  $("create-user-memory-btn").hidden = true;
  $("update-user-memory-btn").disabled = false;
  $("delete-user-memory-btn").disabled = false;
  $("user-memory-evidence").innerHTML = `<div class="memory-evidence-summary"><span><small>文件</small><strong>${escapeHtml(memory.id)}</strong></span><span><small>SHA-256</small><strong title="${escapeHtml(memory.sha256)}">${escapeHtml(memory.sha256.slice(0, 12))}</strong></span></div>`;
  renderUserMemories();
}

export function renderUserMemories() {
  const kind = $("user-memory-kind-filter").value;
  const visible = state.userMemories.filter((item) => !kind || item.type === kind);
  $("user-memory-count").textContent = String(visible.length);
  const list = $("user-memory-list");
  list.innerHTML = visible.length
    ? visible.map((item) => `<button class="memory-asset-row${item.id === state.selectedUserMemoryId ? " active" : ""}" type="button" data-user-memory-id="${escapeHtml(item.id)}" aria-pressed="${item.id === state.selectedUserMemoryId}"><span class="memory-asset-row-head"><strong>${escapeHtml(item.name)}</strong><span class="memory-status-pill active">${escapeHtml(item.type)}</span></span><span class="memory-asset-row-body">${escapeHtml(item.description)}</span><span class="memory-asset-row-meta"><span>${escapeHtml(formatDate(item.mtime_ms))}</span><span>${escapeHtml(item.sha256.slice(0, 10))}</span></span></button>`).join("")
    : '<div class="memory-empty"><strong>还没有用户主题</strong><p>添加 user 信息或 feedback 纠正。</p></div>';
}

export async function refreshUserMemory() {
  if (!workspaceIsReady(currentWorkspace())) return;
  const generation = ++state.userMemoryRequestGeneration;
  $("user-memory-list").innerHTML = memoryLoadingMarkup();
  $("user-memory-list").setAttribute("aria-busy", "true");
  try {
    const workspace = encodeURIComponent(requireActiveWorkspace());
    const files = await fetchJson(`/memory/files?workspace_id=${workspace}&scope=user`);
    if (generation !== state.userMemoryRequestGeneration) return;
    state.userMemories = files.files || [];
    if (!state.userMemories.some((item) => item.id === state.selectedUserMemoryId)) state.selectedUserMemoryId = "";
    renderUserMemories();
    if (!state.selectedUserMemoryId && state.userMemories.length) selectUserMemory(state.userMemories[0]);
    await loadMemoryIndex("user", "user-memory-evidence");
    setMemoryRuntimeStatus("用户文件记忆已连接");
  } catch (error) {
    $("user-memory-list").innerHTML = '<div class="memory-empty error"><strong>用户记忆加载失败</strong><p>检查身份和文件权限后重试。</p></div>';
    setMemoryRuntimeStatus("用户记忆加载失败", "error");
    showToast(humanizeError(error), "error");
  } finally { $("user-memory-list").setAttribute("aria-busy", "false"); }
}

export async function createUserMemory() { return saveFileMemory("user", true); }
export async function updateUserMemory() { return saveFileMemory("user", false); }

export async function forgetUserMemory() {
  const current = state.userMemories.find((item) => item.id === state.selectedUserMemoryId);
  if (!current || !window.confirm(`删除用户主题 ${current.name}？`)) return;
  try {
    await fetchJson("/memory/files", { method: "DELETE", body: JSON.stringify({ workspace_id: requireActiveWorkspace(), scope: "user", name: current.name, expected_hash: current.sha256 }) });
    clearUserMemorySelection();
    await refreshUserMemory();
    showToast("用户主题已删除");
  } catch (error) { showToast(humanizeError(error), "error"); }
}

export async function consolidateFileMemory() {
  if (!state.currentSession?.id) {
    showToast("请先选择一个有 Run 的会话", "warning");
    return;
  }
  try {
    await fetchJson("/memory/maintenance", { method: "POST", body: JSON.stringify({ conversation_id: state.currentSession.id, operation: "consolidate" }) });
    showToast("已安排文件记忆治理任务");
    await refreshProjectMemory();
  } catch (error) { showToast(humanizeError(error), "error"); }
}

export function renderConversationMemoryDetail(hit) {
  const detail = $("conversation-memory-detail");
  if (!hit) {
    detail.innerHTML = '<div class="memory-empty"><strong>选择一条历史消息</strong><p>这里会显示角色、时间、会话范围和匹配正文。</p></div>';
    return;
  }
  const roleLabel = { user: "用户", assistant: "助手", system: "系统" }[hit.role] || hit.role;
  detail.innerHTML = `
    <div class="memory-detail-header conversation-detail-header">
      <div><span class="step-kicker">UNTRUSTED HISTORY</span><h2>${escapeHtml(roleLabel)}消息</h2><p>${escapeHtml(formatDate(hit.created_at))}</p></div>
      <span class="memory-status-pill neutral">原始消息</span>
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

export function selectConversationMemoryHit(index) {
  const safeIndex = Number.isInteger(index) && index >= 0 && index < state.conversationMemoryHits.length
    ? index
    : -1;
  state.selectedConversationMemoryHit = safeIndex;
  renderConversationMemoryHits(state.conversationMemoryHits);
}

export function renderConversationMemoryHits(hits, { initial = false } = {}) {
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

export async function searchConversationMemory() {
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
    setMemoryRuntimeStatus(`${query ? "对话搜索完成" : "最近消息"} · ${(body.hits || []).length} 条`);
  } catch (error) {
    if (generation !== state.conversationMemoryRequestGeneration) return;
    $("conversation-memory-results").setAttribute("aria-busy", "false");
    $("conversation-memory-results").innerHTML = '<div class="memory-empty error"><strong>对话搜索失败</strong><p>检查关键词和本地 SQLite 状态后重试。</p></div>';
    setMemoryRuntimeStatus("对话搜索失败", "error");
    showToast(humanizeError(error), "error");
  }
}
