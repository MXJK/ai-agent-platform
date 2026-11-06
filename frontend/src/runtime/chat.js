import { managedSetTimeout } from '../lib/resources';
import { FINAL_RUN_STATUSES, MAX_TRACE_REPLAY_MS, TERMINAL_RUN_STATUSES, TRACE_STEP_REVEAL_DELAY_MS } from './config.js';
import { state } from './state.js';
import { $, formatTokenCount, prefersReducedMotion, renderMarkdown } from './format.js';
import { agentTracePresentationEvents, renderExecutionProcess, renderResponseMetrics, startResponseTimer, stopResponseTimer } from './execution.js';
import { humanizeError, showToast } from './feedback.js';
import { clearComposerInput, composerSubmission, loadSlashCapabilities, runBuiltinComposerCommand, setComposerValue } from './composer.js';
import { composerRunControlPresentation, updateComposerAvailability } from './context.js';
import { setChatStatus } from './overview.js';
import { fetchJson } from './http.js';
import { refreshModelRegistryTelemetry } from './model-actions.js';
import { refreshCurrentSessionMetadata, refreshRecentSessions } from './sessions.js';
import { appendChatMessage, failureRecoveryMarkup, setMessageDeliveryState } from './conversation.js';
import { agentChangeSetId, agentRunConversationId, agentRunId, agentRunStatus, agentRunTrace, chatContentForRun, loadInlineChangeSet, renderInlineAgentCheckpoint, setChatStatusFromRun } from './checkpoints.js';
import { renderAgentRun, renderInlineRunFooter, runAgent, watchRunUntilTerminal } from './runs.js';

export async function stopChat() {
  const runId = state.latestRunId;
  const conversationId = state.conversationId;
  const active = runId && state.latestRunConversationId === conversationId
    && !FINAL_RUN_STATUSES.has(state.latestRunStatus);
  if (!active) { state.chatController?.abort(); return; }
  if (state.composerCancelBusy || state.currentSession?.archived_at) return;
  state.composerCancelBusy = true;
  updateComposerAvailability();
  try {
    const body = await fetchJson(`/agent/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST", body: JSON.stringify({ message: "用户取消运行" }),
    });
    if (runId !== state.latestRunId || conversationId !== state.conversationId) return;
    renderAgentRun(body);
    const content = chatContentForRun(runId);
    if (content) renderAgentChatResponse(content, body, performance.now() - (body.result?.metrics?.elapsed_ms || 0));
    setChatStatusFromRun(body);
    showToast("取消请求已发送", "warning");
  } catch (error) { showToast(humanizeError(error), "error"); }
  finally { state.composerCancelBusy = false; updateComposerAvailability(); }
}

export async function handleComposerRunControl() {
  const presentation = composerRunControlPresentation({
    archived: Boolean(state.currentSession?.archived_at),
  });
  if (!presentation || state.composerRunControlBusy || presentation.pending) return;
  const runId = state.latestRunId;
  const conversationId = state.latestRunConversationId || state.conversationId;
  const contentNode = chatContentForRun(runId);
  const input = $("chat-message-input");
  const message = input.value.trim();
  let pauseAccepted = false;
  state.composerRunControlBusy = true;
  if (presentation.action === "pause") {
    state.composerPauseRequestedRunId = runId;
  }
  updateComposerAvailability();
  try {
    const nextBody = await fetchJson(
      `/agent/runs/${encodeURIComponent(runId)}/${presentation.action}`,
      {
        method: "POST",
        body: JSON.stringify({
          message: presentation.action === "continue" ? message : "",
        }),
      },
    );
    if (
      runId !== state.latestRunId
      || conversationId !== state.conversationId
    ) {
      return;
    }
    pauseAccepted = presentation.action === "pause";
    if (presentation.action === "continue" && message) {
      clearComposerInput();
    }
    renderAgentRun(nextBody);
    if (
      contentNode
      && (
        presentation.action === "continue"
        || agentRunStatus(nextBody) !== "running"
      )
    ) {
      renderAgentChatResponse(
        contentNode,
        nextBody,
        performance.now() - (nextBody?.result?.metrics?.elapsed_ms || 0),
      );
    }
    setChatStatusFromRun(nextBody);
    if (presentation.action === "pause") {
      showToast("暂停请求已发送，将在安全边界生效", "success");
      return;
    }

    const startedAt = performance.now() - (nextBody?.result?.metrics?.elapsed_ms || 0);
    const presenter = contentNode
      ? createAgentProgressPresenter(contentNode, startedAt, {
          initialTrace: agentRunTrace(nextBody),
        })
      : null;
    if (contentNode) startResponseTimer(contentNode, startedAt);
    state.composerRunControlBusy = false;
    updateComposerAvailability();
    if (presenter) await presenter.update(nextBody);
    const finalBody = await watchRunUntilTerminal({
      runId,
      conversationId,
      preserveChat: true,
      onProgress: (latestBody) => presenter?.update(latestBody),
    });
    if (
      runId !== state.latestRunId
      || conversationId !== state.conversationId
    ) {
      if (contentNode) stopResponseTimer(contentNode);
      return;
    }
    if (finalBody && presenter) await presenter.update(finalBody);
    setChatStatusFromRun(finalBody || nextBody);
    if (contentNode && TERMINAL_RUN_STATUSES.has(state.latestRunStatus)) {
      stopResponseTimer(contentNode);
    }
    await Promise.allSettled([
      refreshCurrentSessionMetadata(),
      refreshRecentSessions(),
    ]);
  } catch (error) {
    if (
      conversationId === state.conversationId
      && runId === state.latestRunId
    ) {
      showToast(humanizeError(error), "error");
    }
  } finally {
    state.composerRunControlBusy = false;
    if (!pauseAccepted && state.composerPauseRequestedRunId === runId) {
      state.composerPauseRequestedRunId = "";
    }
    updateComposerAvailability();
  }
}

export async function submitComposerMessage() {
  const submission = composerSubmission($("chat-message-input").value);
  if (
    submission.invocation?.item.kind === "builtin"
  ) {
    await runBuiltinComposerCommand(
      submission.invocation.item,
      submission.invocation.remaining,
    );
    return;
  }
  const runControl = composerRunControlPresentation({
    archived: Boolean(state.currentSession?.archived_at),
  });
  if (runControl) {
    if (runControl.action === "continue") {
      await handleComposerRunControl();
    } else {
      showToast("Agent 仍在运行；可使用输入框右侧按钮先暂停", "warning");
    }
    return;
  }
  await runAgentFromComposer();
}

export function renderAgentChatResponse(
  contentNode,
  body,
  startedAt,
  { visibleTrace = null, holdAnswer = false } = {},
) {
  const result = body?.result || {};
  const actualStatus = body?.status || result.status || "running";
  const status = holdAnswer ? "running" : actualStatus;
  const trace = visibleTrace || (body?.trace?.length ? body.trace : result.trace) || [];
  const streamEvents = body?.stream_events || [];
  const presentationEvents = streamEvents.length
    ? streamEvents
    : agentTracePresentationEvents(trace);
  const streamedAnswer = String(body?.streamed_answer || "");
  renderCogentThinking(contentNode, presentationEvents, result.metrics);
  const elapsedMs = result.metrics?.elapsed_ms ?? Math.round(performance.now() - startedAt);
  renderExecutionProcess(contentNode, {
    trace,
    status,
    elapsedMs,
    fallbackNode: body?.latest_node || "setup_workspace",
    fallbackSummary: actualStatus === "queued"
      ? "Agent 任务已进入执行队列。"
      : "Cogent 正在执行当前任务。",
    events: presentationEvents,
    activityOnly: true,
  });

  if (!holdAnswer && actualStatus === "cancelled") {
    contentNode.innerHTML = "<p><em>Agent 运行已停止。</em></p>";
  } else if (!holdAnswer && ["completed", "partial", "blocked"].includes(actualStatus)) {
    const terminalAnswer = String(result.answer || streamedAnswer || "");
    contentNode.innerHTML = terminalAnswer
      ? renderMarkdown(terminalAnswer)
      : "<p>Agent 已完成，但没有返回文本内容。</p>";
  } else if (!holdAnswer && actualStatus === "waiting_approval") {
    contentNode.innerHTML = "<p>Agent 已在执行前暂停。请检查下方计划并决定是否继续。</p>";
  } else if (!holdAnswer && actualStatus === "paused") {
    contentNode.innerHTML = "<p>Agent 已在安全边界暂停。可在输入框补充要求，或点击右侧继续。</p>";
  } else if (!holdAnswer && actualStatus === "waiting_input") {
    contentNode.innerHTML = "<p>Agent 正在等待你的补充，请在下方回复后继续。</p>";
  } else if (!holdAnswer && actualStatus === "failed") {
    const detail = body.error || "Agent 运行失败，请打开 Trace 审计查看详情。";
    contentNode.innerHTML = failureRecoveryMarkup(detail, {
      code: body.error_code || "agent_run_failed",
      requestId: body.request_id || "",
    });
  } else if (streamedAnswer) {
    contentNode.innerHTML = renderMarkdown(streamedAnswer);
  } else {
    contentNode.innerHTML = "";
  }

  if (result.metrics) {
    renderResponseMetrics(contentNode, {
      elapsed_ms: result.metrics.elapsed_ms,
      node_count: result.metrics.node_count,
      tool_call_count: result.metrics.tool_call_count,
      model_request_count: result.metrics.model_request_count,
      model_retry_count: result.metrics.model_retry_count,
      retry_count: result.metrics.retry_count,
      input_tokens: result.metrics.input_tokens,
      output_tokens: result.metrics.output_tokens,
      thoughts_tokens: result.metrics.thoughts_tokens,
      total_tokens: result.metrics.total_tokens,
      cached_input_tokens: result.metrics.cached_input_tokens,
      uncached_input_tokens: result.metrics.uncached_input_tokens,
      cache_write_tokens: result.metrics.cache_write_tokens,
      prompt_cache_hit_ratio: result.metrics.prompt_cache_hit_ratio,
      retained_context_tokens_estimate: result.metrics.retained_context_tokens_estimate,
      stable_prefix_tokens: result.metrics.stable_prefix_tokens,
      tool_schema_tokens: result.metrics.tool_schema_tokens,
      visible_tool_count: result.metrics.visible_tool_count,
      provider: result.metrics.provider,
      model: result.metrics.model,
      cache_capability: result.metrics.cache_capability,
    });
  }
  if (!holdAnswer) {
    renderInlineAgentCheckpoint(contentNode, body);
    const changeSetId = agentChangeSetId(body);
    if (changeSetId && FINAL_RUN_STATUSES.has(actualStatus)) {
      loadInlineChangeSet(agentRunId(body), contentNode).catch((error) => {
        showToast(`ChangeSet 加载失败：${humanizeError(error)}`, "error");
      });
    }
    renderInlineRunFooter(contentNode, body);
  }
}

export function cogentThinkingPresentation(events, metrics = {}) {
  const deltaText = (events || [])
    .filter((event) => event.type === "thinking_delta")
    .map((event) => String(event.output?.text || ""))
    .join("");
  const completedText = (events || [])
    .filter((event) => event.type === "thinking_completed")
    .map((event) => String(event.output?.text || "").trim())
    .filter(Boolean)
    .join("\n\n");
  const text = deltaText || completedText;
  const usage = (events || []).filter((event) => event.type === "usage");
  const tokens = metrics?.thoughts_tokens || usage.reduce(
    (total, event) => total + Number(event.output?.thoughts_tokens || 0), 0,
  );
  return { text, tokens, visible: Boolean(text || tokens) };
}

export function renderCogentThinking(contentNode, events, metrics) {
  const presentation = cogentThinkingPresentation(events, metrics);
  const bubble = contentNode.closest(".chat-bubble");
  if (!bubble) return;
  let details = bubble.querySelector(".cogent-thinking");
  if (!details && !presentation.visible) return;
  if (!details) {
    details = document.createElement("details");
    details.className = "cogent-thinking";
    const summary = document.createElement("summary");
    summary.textContent = "思考摘要";
    details.append(summary, document.createElement("pre"));
    bubble.insertBefore(details, contentNode);
  }
  details.hidden = !presentation.visible;
  details.querySelector("summary").textContent = presentation.tokens
    ? `思考摘要 · ${formatTokenCount(presentation.tokens)} tokens` : "思考摘要";
  details.querySelector("pre").textContent = presentation.text
    || "Provider 仅返回思考用量，未提供可展示的摘要。";
}

export function createAgentProgressPresenter(
  contentNode,
  startedAt,
  { initialTrace = [] } = {},
) {
  const visibleTrace = [...initialTrace];
  let pending = Promise.resolve();

  return {
    update(body) {
      pending = pending.then(async () => {
        const result = body?.result || {};
        const fullTrace = (body?.trace?.length ? body.trace : result.trace) || [];
        if (body?.stream_events) {
          visibleTrace.splice(0, visibleTrace.length, ...fullTrace);
          renderAgentChatResponse(contentNode, body, startedAt, { visibleTrace });
          return;
        }
        const newSteps = fullTrace.slice(visibleTrace.length);
        const revealDelayMs = prefersReducedMotion()
          ? 0
          : Math.min(
              TRACE_STEP_REVEAL_DELAY_MS,
              Math.floor(MAX_TRACE_REPLAY_MS / Math.max(1, newSteps.length)),
            );
        for (const step of newSteps) {
          visibleTrace.push(step);
          renderAgentChatResponse(contentNode, body, startedAt, {
            visibleTrace,
            holdAnswer: true,
          });
          if (revealDelayMs > 0) {
            await new Promise((resolve) => managedSetTimeout(resolve, revealDelayMs));
          }
        }
        renderAgentChatResponse(contentNode, body, startedAt, { visibleTrace });
      });
      return pending;
    },
  };
}

export async function runAgentFromComposer() {
  const input = $("chat-message-input");
  const submission = composerSubmission(input.value);
  if (!submission.message) {
    input.setAttribute("aria-invalid", "true");
    showToast("请输入一条消息", "warning");
    input.focus();
    return;
  }

  const expectedCapabilityKey = state.conversationId && state.activeWorkspaceId
    ? `${state.conversationId}:${state.activeWorkspaceId}`
    : "";
  if (!expectedCapabilityKey || state.slashCapabilityKey !== expectedCapabilityKey) {
    await loadSlashCapabilities();
  }

  const sendButton = $("send-chat-btn");
  const modeInput = $("permission-mode-input");
  sendButton.disabled = true;
  sendButton.setAttribute("aria-busy", "true");
  modeInput.disabled = true;
  let submitted = false;
  let optimisticUserMessage = null;
  let assistantContent = null;
  let progressPresenter = null;
  let submissionError = null;
  const startedAt = performance.now();
  setChatStatus("正在提交给 Agent", "running");
  try {
    const run = await runAgent({
      message: submission.message,
      focusFiles: [],
      skillName: submission.skillName,
      skillArguments: submission.skillArguments,
      preferredToolName: submission.preferredToolName,
      onReady: () => {
        const userContent = appendChatMessage("user", submission.message);
        optimisticUserMessage = userContent.closest(".chat-message");
        setMessageDeliveryState(optimisticUserMessage, "submitting", "正在提交…");
        assistantContent = appendChatMessage("assistant");
        assistantContent.closest(".chat-message")?.classList.add("is-thinking");
        clearComposerInput();
      },
      onSubmitted: (body) => {
        submitted = true;
        setMessageDeliveryState(optimisticUserMessage);
        const assistantMessage = assistantContent?.closest(".chat-message");
        if (assistantMessage) {
          assistantMessage.dataset.agentRunId = agentRunId(body);
          assistantMessage.classList.remove("is-thinking");
        }
        progressPresenter = createAgentProgressPresenter(assistantContent, startedAt);
        progressPresenter.update(body);
        startResponseTimer(assistantContent, startedAt);
        setChatStatus("Agent 运行中", "running");
        showToast("任务已交给 Agent；运行、审批和代码变更会显示在当前对话中");
      },
      onSubmissionError: (error) => {
        submissionError = error;
      },
      onProgress: async (body) => {
        if (progressPresenter) {
          await progressPresenter.update(body);
        }
      },
    });
    if (run && agentRunConversationId(run) !== state.conversationId) {
      if (assistantContent) stopResponseTimer(assistantContent);
      return;
    }
    if (!run && !submitted) {
      if (!input.value.trim()) {
        setComposerValue(submission.message);
      }
      setMessageDeliveryState(optimisticUserMessage, "failed", "未提交");
      if (assistantContent) {
        const detail = submissionError
          ? humanizeError(submissionError)
          : "Agent 任务未能创建，请检查工作区和连接后重试。";
        assistantContent.closest(".chat-message")?.classList.remove("is-thinking");
        assistantContent.innerHTML = failureRecoveryMarkup(detail, {
          code: submissionError?.code || "agent_submission_failed",
        });
      }
      setChatStatus("Agent 提交失败", "failed");
      return;
    }
    if (progressPresenter && run) {
      await progressPresenter.update(run);
    }
    setChatStatusFromRun({ status: state.latestRunStatus });
    if (state.latestRunStatus === "waiting_approval") {
      showToast("Agent 需要你的确认，已在对话中显示执行检查点", "warning");
    } else if (["waiting_input", "paused"].includes(state.latestRunStatus)) {
      showToast("Agent 已在对话中等待你的补充", "warning");
    }
  } finally {
    if (assistantContent && TERMINAL_RUN_STATUSES.has(state.latestRunStatus)) {
      stopResponseTimer(assistantContent);
    }
    sendButton.disabled = false;
    sendButton.removeAttribute("aria-busy");
    modeInput.disabled = false;
    updateComposerAvailability();
    if (submitted) refreshModelRegistryTelemetry();
  }
}
