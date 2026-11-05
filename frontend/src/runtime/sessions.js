import { renderSessionCards, renderRecentSessionCards } from '../features/session-lists';
import { state } from './state.js';
import { $, escapeHtml, formatDate, formatTokenCount, latestUserPromptRecord } from './format.js';
import { startResponseTimer, stopResponseTimer } from './execution.js';
import { humanizeError, setRaw, showToast, truncate } from './feedback.js';
import { composerDraftKey, invalidateSlashCapabilities, restoreComposerDraft } from './composer.js';
import { setChatWorkbenchActive, switchView } from './navigation.js';
import { applyConfigurationToInputs, updateComposerAvailability, updateComposerScopeSummary, updateContextSummary } from './context.js';
import { renderOverview, setChatStatus } from './overview.js';
import { fetchJson } from './http.js';
import { loadModelPreference } from './model-actions.js';
import { appendChatMessage, renderChatHistory, renderMessages } from './conversation.js';
import { agentRunConversationId, agentRunId, agentRunStatus, agentRunTrace, chatContentForRun, setChatStatusFromRun } from './checkpoints.js';
import { createAgentProgressPresenter, renderAgentChatResponse } from './chat.js';
import { renderAgentRun, watchRunUntilTerminal } from './runs.js';
import { formatInputCountMethod, formatUsageOperation } from './workspaces.js';

export async function ensureSession() {
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

export function resetChatView() {
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
        <p>由 Cogent 理解上下文并完成任务；需要确认的操作会在执行前暂停。</p>
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

export function resetLatestAgentRunState() {
  state.agentPollGeneration += 1;
  state.changeSetRequestGeneration += 1;
  state.latestRunId = "";
  state.latestRunStatus = "";
  state.latestRunConversationId = "";
  state.latestRunBody = null;
  state.composerRunControlBusy = false;
  state.composerPauseRequestedRunId = "";
  state.checkpointHistory = [];
  state.checkpointRunId = "";
  state.selectedCheckpointId = "";
  state.currentChangeSet = null;
}

export function normalizedMessageText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

export function runAnswerAlreadyPersisted(body, messages) {
  const answer = normalizedMessageText(body?.result?.answer);
  return Boolean(answer) && messages.some(
    (message) => message.role === "assistant"
      && normalizedMessageText(message.content) === answer,
  );
}

export async function restoreHistoricalAgentRuns(conversationId, messages, latestRunId) {
  const historicalRunIds = [...new Set(
    messages
      .filter((message) => message.role === "assistant" && message.source_run_id)
      .map((message) => message.source_run_id)
      .filter((sourceRunId) => sourceRunId !== latestRunId),
  )];
  const historicalRuns = await Promise.allSettled(
    historicalRunIds.map((sourceRunId) => (
      fetchJson(`/agent/runs/${encodeURIComponent(sourceRunId)}`)
    )),
  );
  if (conversationId !== state.conversationId) return;
  historicalRuns.forEach((entry, index) => {
    const sourceRunId = historicalRunIds[index];
    if (entry.status !== "fulfilled") {
      console.warn(`Unable to restore historical Agent Run ${sourceRunId}`, entry.reason);
      return;
    }
    if (agentRunConversationId(entry.value) !== conversationId) return;
    const historicalContent = chatContentForRun(sourceRunId);
    if (!historicalContent) return;
    const historicalStartedAt = performance.now()
      - (entry.value?.result?.metrics?.elapsed_ms || 0);
    renderAgentChatResponse(historicalContent, entry.value, historicalStartedAt);
  });
}

export async function restoreLatestAgentRun(conversationId, messages = []) {
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

  const historicalRestore = restoreHistoricalAgentRuns(conversationId, messages, runId);
  const startedAt = performance.now() - (body?.result?.metrics?.elapsed_ms || 0);
  renderAgentChatResponse(contentNode, body, startedAt);
  if (!["queued", "running"].includes(status)) {
    await historicalRestore;
    return body;
  }

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
  await historicalRestore;
  return body;
}

export async function createSession() {
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

export async function loadPreferences() {
  state.preferences = await fetchJson("/users/me/preferences");
  state.defaultWorkspaceId = state.preferences.default_workspace_id || "";
  if (!state.currentSession) {
    applyConfigurationToInputs(state.preferences, true);
  }
  return state.preferences;
}

export async function listSessions(showRaw = true, options = {}) {
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

export async function refreshRecentSessions() {
  const body = await fetchJson("/sessions?archived=false&limit=12");
  state.recentSessions = body.sessions || [];
  renderRecentSessions();
  return body;
}

export async function loadSessionTokenUsage(sessionIds = state.sessions.map((item) => item.id)) {
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

export function renderSessions() {
  const list = $("sessions-list");
  $("sessions-count").textContent = String(state.sessions.length);
  $("load-more-sessions-btn").hidden = !state.sessionsNextCursor;
  renderSessionCards(state.sessions, state.conversationId);
}

export function recentSessionGroup(value) {
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

export function renderRecentSessions() {
 renderRecentSessionCards(state.recentSessions, state.conversationId);
}

export function updateSessionUrl(sessionId) {
  const url = new URL(window.location.href);
  if (sessionId) {
    url.searchParams.set("session", sessionId);
  } else {
    url.searchParams.delete("session");
  }
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

export function replaceSessionInLists(session) {
  state.sessions = state.sessions.map((item) => item.id === session.id ? session : item);
  state.recentSessions = state.recentSessions.map((item) => item.id === session.id ? session : item);
  renderSessions();
  renderRecentSessions();
}

export function canSwitchSession() {
  if (!state.chatController) {
    return true;
  }
  showToast("请先完成或停止当前流式回答，再切换会话", "warning");
  return false;
}

export async function loadSession(showRaw = true, requestedSessionId = null, options = {}) {
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

export async function updateSessionMetadata(sessionId, payload) {
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

export async function refreshCurrentSessionMetadata() {
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

export async function handleSessionAction(sessionId, action) {
  if (action === "open") {
    await loadSession(true, sessionId, { navigate: false });
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

export async function loadSessionSummary() {
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

export async function refreshMessages(showRaw = true, renderChat = true) {
  const conversationId = await ensureSession();
  const body = await fetchJson(`/sessions/${encodeURIComponent(conversationId)}/messages`);
  renderMessages(body.messages || []);
  if (renderChat) {
    renderChatHistory(body.messages || []);
    try {
      await restoreLatestAgentRun(conversationId, body.messages || []);
    } catch (error) {
      showToast(`Agent 状态恢复失败：${humanizeError(error)}`, "warning");
    }
  }
  if (showRaw) {
    setRaw(body);
  }
  return body;
}

export function renderSessionSummary(
  summary,
  usage = state.sessionTokenUsage[summary.session_id],
) {
  const workspaces = usage?.workspaces || [];
  const operations = usage?.operations || [];
  const sessionBudget = usage?.budget?.session;
  const latestPromptRecord = latestUserPromptRecord(usage?.records);
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
                ? `最近前台 Prompt 输入 <strong>${escapeHtml(formatTokenCount(latestPromptRecord.input_tokens))}</strong> tokens · ${escapeHtml(formatInputCountMethod(latestPromptRecord.input_count_method))}`
                : "尚无 Agent、Chat 或 RAG Ask Prompt 记录"
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
