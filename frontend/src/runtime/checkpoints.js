import { syncRunInspector, openInspectorChanges } from '../lib/inspector';
import { listen, managedRequestAnimationFrame } from '../lib/resources';
import { FINAL_RUN_STATUSES, SUSPENDED_RUN_STATUSES, TERMINAL_RUN_STATUSES } from './config.js';
import { state } from './state.js';
import { escapeHtml, jsonPretty, preferredScrollBehavior } from './format.js';
import { startResponseTimer, stopResponseTimer } from './execution.js';
import { humanizeApprovalReason, humanizeError, humanizePermissionLevel, humanizeStatus, showToast, statusClass, truncate } from './feedback.js';
import { setChatStatus } from './overview.js';
import { fetchJson } from './http.js';
import { refreshCurrentSessionMetadata, refreshRecentSessions } from './sessions.js';
import { createAgentProgressPresenter, renderAgentChatResponse } from './chat.js';
import { renderAgentRun, watchRunUntilTerminal } from './runs.js';

export function agentRunId(body) {
  return body?.run_id || body?.result?.run_id || "";
}

export function agentRunStatus(body) {
  return body?.status || body?.result?.status || "";
}

export function agentRunConversationId(body) {
  return body?.conversation_id || body?.result?.conversation_id || "";
}

export function agentRunTrace(body) {
  return (body?.trace?.length ? body.trace : body?.result?.trace) || [];
}

export function chatContentForRun(runId) {
  const message = [...document.querySelectorAll("[data-agent-run-id]")]
    .find((item) => item.dataset.agentRunId === runId);
  return message?.querySelector(".message-content") || null;
}

export function setInlineCheckpointBusy(card, busy) {
  card.classList.toggle("is-busy", busy);
  card.setAttribute("aria-busy", String(busy));
  card.querySelectorAll("button, textarea, input").forEach((control) => {
    control.disabled = busy;
  });
}

export function structuredPendingQuestions(pending = {}) {
  const rawQuestions = Array.isArray(pending.questions) && pending.questions.length
    ? pending.questions
    : [{
        id: pending.call_id || "user-input",
        header: "需要你的输入",
        question: pending.question || "Agent 需要你补充信息后才能继续。",
        detail: pending.context || "",
        options: (pending.candidate_paths || []).map((path) => ({
          label: path,
          description: "使用此工作区相对路径继续。",
        })),
        multi_select: false,
      }];
  return rawQuestions.slice(0, 3).map((question, index) => ({
    id: String(question?.id || `user-input-${index + 1}`),
    header: String(question?.header || "需要你的输入"),
    question: String(question?.question || "Agent 需要你补充信息后才能继续。"),
    detail: String(question?.detail || question?.context || ""),
    options: (Array.isArray(question?.options) ? question.options : []).slice(0, 20)
      .map((option) => typeof option === "string"
        ? { label: option, description: "" }
        : {
            label: String(option?.label || ""),
            description: String(option?.description || ""),
          })
      .filter((option) => option.label),
    multiSelect: question?.multi_select === true || question?.multiSelect === true,
  }));
}

export function renderInlineQuestion(question, index) {
  const inputType = question.multiSelect ? "checkbox" : "radio";
  const options = question.options.map((option, optionIndex) => `
    <label class="inline-question-option">
      <input
        type="${inputType}"
        name="inline-question-${index}"
        value="${escapeHtml(option.label)}"
        data-inline-question-option
      >
      <span class="inline-question-option-index">${optionIndex + 1}</span>
      <span class="inline-question-option-copy">
        <strong>${escapeHtml(option.label)}</strong>
        ${option.description ? `<small>${escapeHtml(option.description)}</small>` : ""}
      </span>
    </label>
  `).join("");
  return `
    <fieldset class="inline-user-question" data-inline-question data-question-id="${escapeHtml(question.id)}">
      <legend>${escapeHtml(question.header)}</legend>
      <h4>${escapeHtml(question.question)}</h4>
      ${question.detail ? `<p>${escapeHtml(question.detail)}</p>` : ""}
      ${options ? `<div class="inline-question-options">${options}</div>` : ""}
      <label class="inline-checkpoint-feedback inline-question-custom">
        <span>${options ? "其他答案" : "回复 Agent"} <small>${options ? "可选" : "必填"}</small></span>
        <textarea
          rows="2"
          maxlength="4000"
          data-inline-question-custom
          placeholder="${options ? "输入候选项之外的补充说明" : escapeHtml(question.question)}"
        ></textarea>
      </label>
    </fieldset>
  `;
}

export function collectInlineQuestionAnswers(card) {
  return Array.from(card.querySelectorAll("[data-inline-question]")).map((node) => ({
    id: node.dataset.questionId,
    selected: Array.from(node.querySelectorAll("[data-inline-question-option]:checked"))
      .map((input) => input.value),
    custom: node.querySelector("[data-inline-question-custom]")?.value.trim() || "",
  }));
}

export function inlineQuestionAnswersComplete(answers) {
  return answers.length > 0
    && answers.every((answer) => answer.selected.length > 0 || answer.custom !== "");
}

export function updateInlineQuestionSubmit(card) {
  const submit = card.querySelector('[data-inline-agent-action="continue"]');
  if (submit) submit.disabled = !inlineQuestionAnswersComplete(collectInlineQuestionAnswers(card));
}

export function inlineApprovalTools(approval) {
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

export function renderInlineAgentCheckpoint(contentNode, body) {
  const bubble = contentNode.closest(".chat-bubble");
  if (!bubble) return;
  const status = agentRunStatus(body);
  let card = bubble.querySelector(".inline-agent-checkpoint");
  const shouldFocusCheckpoint = !card || card.dataset.status !== status;
  if (status === "paused") {
    card?.remove();
    return;
  }
  if (!SUSPENDED_RUN_STATUSES.has(status)) {
    if (!card?.dataset.decision) {
      card?.remove();
      return;
    }
    const decision = card.dataset.decision;
    const previousStatus = card.dataset.status;
    const rejected = decision === "reject";
    const skipped = decision === "skip-input";
    const resolvedTitle = rejected
      ? "已拒绝执行"
      : skipped
        ? "已跳过问题"
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
    const questions = structuredPendingQuestions(pending);
    const question = questions[0]?.question || "Agent 需要你补充信息后才能继续。";
    card.setAttribute("aria-label", "Agent 等待输入");
    card.innerHTML = `
      <div class="inline-checkpoint-heading">
        <span class="inline-checkpoint-mark" aria-hidden="true">?</span>
        <div>
          <span>需要你的输入</span>
          <h3>${escapeHtml(question)}</h3>
        </div>
        <code>${escapeHtml(agentRunId(body))}</code>
      </div>
      <div class="inline-user-questions">
        ${questions.map(renderInlineQuestion).join("")}
      </div>
      <p class="inline-checkpoint-error" role="alert" ${pending.validation_error ? "" : "hidden"}>${escapeHtml(pending.validation_error || "")}</p>
      <div class="inline-checkpoint-actions">
        <button class="button danger" type="button" data-inline-run-action="cancel">取消 Run</button>
        <button class="button ghost" type="button" data-inline-agent-action="skip-input">跳过</button>
        <button class="button primary" type="button" data-inline-agent-action="continue" disabled>提交并继续</button>
      </div>
    `;
  }

  card.querySelectorAll("[data-inline-agent-action]").forEach((button) => {
    listen(button, "click", () => {
      handleInlineAgentAction(contentNode, body, button.dataset.inlineAgentAction, card);
    });
  });
  card.querySelectorAll("[data-inline-run-action]").forEach((button) => {
    listen(button, "click", () => {
      handleInlineRunControl(contentNode, body, button.dataset.inlineRunAction, card);
    });
  });
  card.querySelectorAll("[data-inline-question-option], [data-inline-question-custom]").forEach((control) => {
    listen(control, "input", () => updateInlineQuestionSubmit(card));
    listen(control, "change", () => updateInlineQuestionSubmit(card));
  });
  if (shouldFocusCheckpoint) {
    managedRequestAnimationFrame(() => {
      card.scrollIntoView({ behavior: preferredScrollBehavior(), block: "center" });
    });
  }
}

export async function handleInlineRunControl(contentNode, body, action, controls) {
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
  const checkpointErrorNode = controls.querySelector(".inline-checkpoint-error");
  controls.setAttribute("aria-busy", "true");
  controls.querySelectorAll("button, input, textarea").forEach((node) => { node.disabled = true; });
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
    if (checkpointErrorNode) {
      checkpointErrorNode.textContent = humanizeError(error);
      checkpointErrorNode.hidden = false;
    }
  }
}

export function agentChangeSetId(body) {
  return body?.change_set_id || body?.result?.change_set_id || "";
}

export function changeSetFileStats(changeSet) {
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

export function ensureInlineChangeReview(contentNode) {
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

export function renderInlineChangeReview(contentNode, changeSet) {
  const card = ensureInlineChangeReview(contentNode);
  if (!card) return;
  delete card.dataset.loading;
  if (changeSet.run_id === state.latestRunId) syncRunInspector();
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
          <button class="change-file-open text-button" type="button" data-change-path="${escapeHtml(file.path)}" title="${escapeHtml(file.path)}">${escapeHtml(file.path)}</button>
          <span><b class="diff-addition">+${file.additions}</b><b class="diff-deletion">−${file.deletions}</b></span>
        </div>
      `).join("")}
    </div>
    <details class="change-diff-details" ${changeSet.run_id === state.latestRunId ? "hidden" : ""}>
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
  card.querySelectorAll("[data-change-path]").forEach(button => {
    listen(button, "click", () => {
      if (changeSet.run_id === state.latestRunId) { state.currentChangeSet = changeSet; syncRunInspector(); openInspectorChanges(button.dataset.changePath); }
      else { const details = card.querySelector(".change-diff-details"); if (details) details.open = true; }
    });
  });
  card.querySelectorAll("[data-inline-change-action]").forEach((button) => {
    listen(button, "click", () => {
      handleInlineChangeAction(contentNode, changeSet, button.dataset.inlineChangeAction, card);
    });
  });
}

export function renderInlineChangeLoading(contentNode) {
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

export async function loadInlineChangeSet(runId, contentNode, { force = false } = {}) {
  const existing = contentNode.closest(".chat-bubble")?.querySelector(".inline-change-review");
  if (!force && (existing?.dataset.loading === "true" || existing?.dataset.changeSetId)) {
    return state.currentChangeSet;
  }
  const requestedConversationId = state.conversationId;
  const generation = state.changeSetRequestGeneration;
  const loadingCard = renderInlineChangeLoading(contentNode);
  try {
    const changeSet = await fetchJson(`/agent/runs/${encodeURIComponent(runId)}/changes`);
    if (
      generation !== state.changeSetRequestGeneration
      || requestedConversationId !== state.conversationId
      || changeSet.conversation_id !== state.conversationId
      || !contentNode.isConnected
    ) return null;
    if (runId === state.latestRunId) state.currentChangeSet = changeSet;
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

export async function handleInlineChangeAction(contentNode, changeSet, action, card) {
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
    if (changeSet.conversation_id !== state.conversationId) return;
    if (changeSet.run_id === state.latestRunId) state.currentChangeSet = updated;
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

export function setChatStatusFromRun(body) {
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

export async function handleInlineAgentAction(contentNode, body, action, card) {
  const runId = agentRunId(body);
  const conversationId = agentRunConversationId(body);
  if (!runId || conversationId !== state.conversationId) {
    showToast("这条审批不属于当前会话，请重新打开对应会话", "warning");
    return;
  }
  const isApproval = action === "approve" || action === "reject";
  const feedback = isApproval
    ? card.querySelector(".inline-checkpoint-feedback textarea")?.value.trim() || ""
    : "";
  const errorNode = card.querySelector(".inline-checkpoint-error");
  let questionAnswers = [];
  if (!isApproval) {
    questionAnswers = collectInlineQuestionAnswers(card);
    if (action === "skip-input") {
      questionAnswers = questionAnswers.map((answer) => ({
        id: answer.id,
        selected: [],
        skipped: true,
      }));
    } else if (!inlineQuestionAnswersComplete(questionAnswers)) {
      if (errorNode) {
        errorNode.textContent = "请先选择一个候选项或填写答案；如不回答，请明确点击“跳过”。";
        errorNode.hidden = false;
      }
      return;
    }
  }
  setInlineCheckpointBusy(card, true);
  if (errorNode) errorNode.hidden = true;
  try {
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
          : { answers: questionAnswers }),
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
