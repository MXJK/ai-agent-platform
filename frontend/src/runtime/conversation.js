import { managedSetTimeout } from '../lib/resources';
import { state } from './state.js';
import { $, escapeHtml, formatDate, iconMarkup, preferredScrollBehavior, renderMarkdown } from './format.js';
import { humanizeError, setRaw, showToast } from './feedback.js';
import { setComposerValue } from './composer.js';
import { scheduleConversationFollow, scrollConversationToLatest, setChatWorkbenchActive, switchView } from './navigation.js';
import { fetchJson } from './http.js';
import { ensureSession, loadSessionSummary, refreshCurrentSessionMetadata, refreshRecentSessions, resetChatView } from './sessions.js';
import { submitComposerMessage } from './chat.js';

export function renderMessages(messages) {
  const list = $("messages-list");
  list.innerHTML = "";
  if (messages.length === 0) {
    list.innerHTML = '<div class="empty-state">这个会话还没有消息。</div>';
    return;
  }
  for (const message of messages) {
    const item = document.createElement("article");
    item.className = `message-item role-${message.role}`;
    item.innerHTML = `
      <div class="message-meta"><strong>${escapeHtml(message.role)}</strong><span>${escapeHtml(formatDate(message.created_at))}</span></div>
      <p>${escapeHtml(message.content)}</p>
    `;
    list.appendChild(item);
  }
}

export function renderChatHistory(messages) {
  const chatMessages = messages.filter((message) => ["user", "assistant"].includes(message.role));
  if (chatMessages.length === 0) {
    resetChatView();
    return;
  }
  const output = $("chat-output");
  state.followConversation = true;
  setChatWorkbenchActive(true);
  output.innerHTML = "";
  for (const message of chatMessages) {
    appendChatMessage(message.role, message.content, message.created_at, {
      runId: message.role === "assistant" ? message.source_run_id || "" : "",
    });
  }
  scrollConversationToLatest({ behavior: "auto" });
}

export async function addMessage() {
  const content = $("message-content-input").value.trim();
  if (!content) {
    showToast("请输入消息内容", "warning");
    return;
  }
  try {
    const conversationId = await ensureSession();
    const body = await fetchJson(`/sessions/${encodeURIComponent(conversationId)}/messages`, {
      method: "POST",
      body: JSON.stringify({
        role: $("message-role-input").value,
        content,
      }),
    });
    renderMessages(body.messages || []);
    setRaw(body);
    await loadSessionSummary();
    await Promise.allSettled([
      refreshCurrentSessionMetadata(),
      refreshRecentSessions(),
    ]);
    showToast("测试消息已添加");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

export function appendChatMessage(role, content = "", createdAt = null, { runId = "" } = {}) {
  const output = $("chat-output");
  const welcome = output.querySelector(".welcome-state");
  if (welcome) {
    output.innerHTML = "";
  }
  setChatWorkbenchActive(true);
  const item = document.createElement("article");
  item.className = `chat-message ${role}`;
  item.dataset.messageContent = content;
  const roleLabel = role === "user" ? "你" : "AI 助手";
  const timestamp = escapeHtml(createdAt ? formatDate(createdAt) : "刚刚");
  item.setAttribute("aria-label", `${roleLabel}的消息，${createdAt ? formatDate(createdAt) : "刚刚"}`);
  item.innerHTML = `
    <div class="chat-avatar" aria-hidden="true">${role === "user" ? "你" : iconMarkup("agent")}</div>
    <div class="chat-bubble">
      <div class="message-label"><strong>${roleLabel}</strong><time>${timestamp}</time></div>
      <div class="message-content rich-output">${content ? renderMarkdown(content) : '<span class="typing-indicator" aria-label="正在生成"><span></span><span></span><span></span></span>'}</div>
      <span class="message-delivery-state" role="status" aria-live="polite" hidden></span>
      <div class="message-actions" aria-label="消息操作">
        <button class="message-action" type="button" data-message-action="copy" aria-label="复制这条消息" title="复制">
          ${iconMarkup("copy")}
        </button>
        ${role === "user" ? `
          <button class="message-action" type="button" data-message-action="edit" aria-label="编辑并重新使用这条消息" title="编辑并重新使用">
            ${iconMarkup("edit")}
          </button>
        ` : ""}
      </div>
    </div>
  `;
  if (runId) {
    item.dataset.agentRunId = runId;
  }
  output.appendChild(item);
  if (role === "user" && !createdAt) {
    state.followConversation = true;
  }
  scheduleConversationFollow();
  return item.querySelector(".message-content");
}

export function setMessageDeliveryState(message, status = "", label = "") {
  if (!message) return;
  const statusNode = message.querySelector(".message-delivery-state");
  message.classList.toggle("is-submitting", status === "submitting");
  message.classList.toggle("is-submit-failed", status === "failed");
  message.dataset.deliveryState = status;
  if (!statusNode) return;
  statusNode.textContent = label;
  statusNode.hidden = !label;
}

export function failureTitle(detail, code = "") {
  const normalized = `${code} ${detail}`.toLowerCase();
  if (normalized.includes("timed out") || normalized.includes("timeout")) {
    return "模型响应超时";
  }
  if (code === "max_output_tokens") {
    return "回答达到输出额度上限";
  }
  return "本次运行未完成";
}

export function failureGuidance(detail, code = "") {
  const normalized = `${code} ${detail}`.toLowerCase();
  if (normalized.includes("timed out") || normalized.includes("timeout")) {
    return "可以直接重试，或选择延迟更低的模型后再次运行。";
  }
  if (code === "max_output_tokens") {
    return "已保留生成的部分内容。可以提高模型输出额度，或缩小本次任务范围。";
  }
  return "保留当前会话上下文后重试；需要诊断时打开 Trace 审计查看失败阶段。";
}

export function failureRecoveryMarkup(detail, { code = "", requestId = "" } = {}) {
  return `
    <section class="response-error-card" role="alert">
      <h3>${escapeHtml(failureTitle(detail, code))}</h3>
      <p class="response-error-detail">${escapeHtml(detail)}</p>
      <small>${escapeHtml(failureGuidance(detail, code))}${requestId ? ` · Request ID：${escapeHtml(requestId)}` : ""}</small>
      <div class="response-error-actions">
        <button class="button primary" type="button" data-response-action="retry">
          ${iconMarkup("refresh")}重试
        </button>
        <button class="button secondary" type="button" data-response-action="choose-model">更换模型</button>
        <button class="button ghost" type="button" data-response-action="show-trace">打开 Trace 审计</button>
      </div>
    </section>
  `;
}

export function previousUserPrompt(message) {
  let current = message?.previousElementSibling;
  while (current) {
    if (current.classList.contains("user")) {
      return current.dataset.messageContent || "";
    }
    current = current.previousElementSibling;
  }
  return "";
}

export async function handleConversationAction(target) {
  const message = target.closest(".chat-message");
  if (!message) return;
  const messageAction = target.closest("[data-message-action]")?.dataset.messageAction;
  const responseAction = target.closest("[data-response-action]")?.dataset.responseAction;
  if (messageAction === "copy") {
    const failureDetail = message.querySelector(".response-error-detail")?.innerText;
    const text = failureDetail || message.querySelector(".message-content")?.innerText || "";
    try {
      await navigator.clipboard.writeText(text.trim());
      showToast("消息已复制");
    } catch {
      showToast("无法访问剪贴板，请手动选择文本复制", "warning");
    }
    return;
  }
  if (messageAction === "edit") {
    setComposerValue(message.dataset.messageContent || "", { focus: true });
    scrollConversationToLatest({ behavior: preferredScrollBehavior() });
    return;
  }
  if (responseAction === "show-trace") {
    switchView("trace-audit");
    return;
  }
  if (responseAction === "choose-model") {
    $("composer-config").open = true;
    scrollConversationToLatest({ behavior: preferredScrollBehavior() });
    managedSetTimeout(() => {
      const targetControl = $("auto-model-toggle").checked
        ? $("auto-model-toggle")
        : ($("model-picker-trigger").hidden ? $("session-model-select") : $("model-picker-trigger"));
      targetControl.focus();
    }, 0);
    return;
  }
  if (responseAction === "retry") {
    const prompt = previousUserPrompt(message);
    if (!prompt) {
      showToast("没有找到可重试的上一条请求", "warning");
      return;
    }
    setComposerValue(prompt);
    await submitComposerMessage();
  }
}
