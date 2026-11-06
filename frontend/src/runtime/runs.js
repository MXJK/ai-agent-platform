import { syncRunInspector } from '../lib/inspector';
import { managedSetTimeout } from '../lib/resources';
import { API_BASE, FINAL_RUN_STATUSES, TERMINAL_RUN_STATUSES } from './config.js';
import { state } from './state.js';
import { $, escapeHtml, formatDate, formatTokenCount, iconMarkup, optionalModelFields } from './format.js';
import { humanizeAgentNode } from './execution.js';
import { humanizeError, humanizeStatus, setRaw, setTrace, showToast } from './feedback.js';
import { openSettings } from './navigation.js';
import { currentWorkspace, updateComposerAvailability, workspaceIsReady } from './context.js';
import { renderOverview } from './overview.js';
import { fetchJson } from './http.js';
import { ensureSession, loadSession, refreshCurrentSessionMetadata, refreshMessages, refreshRecentSessions } from './sessions.js';
import { agentRunConversationId, agentRunId, agentRunStatus } from './checkpoints.js';
import { parseSseBlock } from './streaming.js';
import { refreshTokenUsageData } from './workspaces.js';

export async function runAgent({
  message = "",
  focusFiles = [],
  skillName = null,
  skillArguments = [],
  preferredToolName = null,
  onReady = null,
  onSubmitted = null,
  onSubmissionError = null,
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
      permission_mode: state.permissionMode,
      ...(skillName ? {
        skill_name: skillName,
        skill_arguments: skillArguments,
      } : {}),
      ...(preferredToolName ? { preferred_tool_name: preferredToolName } : {}),
    };
    if (onReady) {
      onReady(conversationId);
    }
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
    if (onSubmissionError) {
      onSubmissionError(error);
    }
    if (!conversationId || conversationId === state.conversationId) {
      showToast(humanizeError(error), "error");
    }
    return submittedRun;
  } finally {
    updateComposerAvailability();
  }
}

export function activeRunPresentation(body) {
  const status = agentRunStatus(body);
  const latestNode = body?.latest_node || body?.result?.latest_node || "";
  const presentations = {
    queued: ["QUEUED RUN", "Agent 正在排队", "任务已提交，等待执行器接手"],
    running: ["ACTIVE RUN", "Agent 正在运行", latestNode
      ? `当前阶段：${humanizeAgentNode(latestNode)} · 控制操作将在安全边界生效`
      : "暂停与取消会在下一个安全边界生效"],
    waiting_approval: ["APPROVAL REQUIRED", "Agent 等待你的确认", "请在对话内查看工具计划并批准或拒绝"],
    waiting_input: ["INPUT REQUIRED", "Agent 等待新的方向", "补充信息后可从当前 checkpoint 继续"],
    paused: ["RUN PAUSED", "Agent 已暂停", "执行状态已保留，可继续运行"],
    completed: ["RUN COMPLETE", "Agent 已完成", "执行快照已保留；输入 /rewind 查看可回退的文件记录"],
    partial: ["RUN PARTIAL", "Agent 部分完成", "可查看结果并在同一会话发起新 Run"],
    blocked: ["RUN BLOCKED", "Agent 运行受阻", body?.legacy_read_only ? "旧运行时已退役；可在本会话发起新的 Cogent Run" : "请查看运行记录，确认下一步操作"],
    cancelled: ["RUN CANCELLED", "Agent 已取消", "历史 checkpoint 仍然保留"],
    failed: ["RUN FAILED", "Agent 运行失败", body?.error || "执行记录已保留，可在同一会话发起新 Run"],
  };
  return presentations[status] || ["AGENT RUN", humanizeStatus(status), "可查看当前执行记录"];
}

export function renderInlineRunFooter(contentNode, body) {
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

export function checkpointTitle(checkpoint) {
  if (checkpoint.latest_node) return humanizeAgentNode(checkpoint.latest_node);
  if (checkpoint.next_nodes?.length) return `进入 ${humanizeAgentNode(checkpoint.next_nodes[0])}`;
  return checkpoint.step < 0 ? "Run 起点" : "执行结束";
}

export function checkpointStateLabel(checkpoint) {
  if (checkpoint.is_current) return "当前状态";
  if (checkpoint.interrupt) return "暂停边界";
  if (!checkpoint.can_restore) return "仅供查看";
  return "可恢复";
}

export function checkpointBadges(checkpoint) {
  const badges = [];
  if (checkpoint.is_current) badges.push("current");
  if (checkpoint.interrupt) badges.push("interrupt");
  if (checkpoint.origin_run_id) badges.push(checkpoint.restore_mode || "branch");
  if (!checkpoint.can_restore) badges.push("terminal");
  return badges;
}

export function renderCheckpointHistory() {
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

export function renderCheckpointDetail() {
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

export async function openCheckpointHistory(runId = state.latestRunId) {
  if (!runId) {
    showToast("当前会话还没有 Agent Run", "warning");
    return;
  }
  state.checkpointRunId = runId;
  state.checkpointHistory = [];
  state.selectedCheckpointId = "";
  $("checkpoint-direction-input").value = "";
  $("checkpoint-history-list").innerHTML = '<div class="empty-state">正在读取 checkpoint…</div>';
  $("checkpoint-history-status").textContent = "正在读取持久化快照…";
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

export function closeCheckpointHistory() {
  const dialog = $("checkpoint-history-dialog");
  if (dialog.open) dialog.close();
}

export async function restoreSelectedCheckpoint(mode) {
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

export function renderAgentRun(body) {
  const result = body.result || {};
  const nextRunId = body.run_id || result.run_id || "";
  if (state.latestRunId !== nextRunId) state.currentChangeSet = null;
  state.latestRunId = nextRunId;
  state.latestRunStatus = body.status || result.status || "";
  state.latestRunConversationId = agentRunConversationId(body);
  state.latestRunBody = body;
  syncRunInspector();
  setTrace(body.trace || result.trace || []);
  setRaw(body);
  renderOverview();
  updateComposerAvailability();
}

export async function refreshRun(
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

export async function refreshEvents(
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
    state.latestRunBody = { ...(state.latestRunBody || {}), stream_events: body.events || [] };
    syncRunInspector();
    setTrace(agentProgressBodyFromEvents(body.events || [], runId).trace);
    renderOverview();
  }
  if (showRaw && isCurrentRun) {
    setRaw(body);
  }
  return body;
}

export function agentProgressBodyFromEvents(events, runId = state.latestRunId) {
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
    streamed_answer: events.reduce((answer, event) => {
      if (event.type === "answer_reset") return "";
      if (event.type === "answer_delta") return answer + String(event.output?.text || "");
      return answer;
    }, ""),
  };
}

export function renderStreamedAgentProgress(events, runId = state.latestRunId) {
  const body = agentProgressBodyFromEvents(events, runId);
  state.latestRunStatus = body.status;
  state.latestRunBody = {
    ...(state.latestRunBody || {}),
    ...body,
    conversation_id: state.latestRunConversationId || state.conversationId,
  };
  syncRunInspector();
  setTrace(body.trace);
  renderOverview();
  updateComposerAvailability();
  return body;
}

export async function watchRunUntilTerminal(options = {}) {
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
        if (FINAL_RUN_STATUSES.has(progressBody.status)) {
          latestBody = {
            ...await refreshRun(runId, { conversationId }),
            stream_events: [...streamedEvents],
            streamed_answer: progressBody.streamed_answer,
          };
          publishProgress(latestBody);
          stopped = true;
          break;
        }
        publishProgress(progressBody);
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
  // The stream can end without a FINAL event when the backend stops it at a
  // suspended boundary (waiting_approval/waiting_input/paused) or on timeout.
  // Preserve the events collected so far so the activity list is not wiped by
  // the follow-up polling, which re-renders from the record without events.
  if (!latestBody && streamedEvents.length) {
    const refreshed = await refreshRun(runId, { conversationId });
    if (refreshed) {
      latestBody = {
        ...refreshed,
        stream_events: [...streamedEvents],
        streamed_answer: streamedEvents.reduce((answer, event) => {
          if (event.type === "answer_reset") return "";
          if (event.type === "answer_delta") return answer + String(event.output?.text || "");
          return answer;
        }, ""),
      };
      publishProgress(latestBody);
      await progressUpdates;
    }
  }
  const polledBody = await pollRunUntilTerminal(
    { ...options, runId, conversationId },
    latestBody,
  );
  return polledBody || latestBody;
}

export async function pollRunUntilTerminal(
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
    await new Promise((resolve) => managedSetTimeout(resolve, 300));
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
