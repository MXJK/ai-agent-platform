import { listen } from '../lib/resources';
import { state } from './state.js';
import { $, escapeHtml } from './format.js';
import { updateContextSummary } from './context.js';

export const MODEL_PROVIDERS = [
  ["openai", "OpenAI"],
  ["deepseek", "DeepSeek"],
  ["anthropic", "Anthropic"],
  ["google", "Google"],
  ["glm", "智谱 GLM"],
  ["minimax", "MiniMax"],
  ["doubao", "豆包 · 火山方舟"],
];

export const ROUTING_POLICY_LABELS = {
  smart: "智能 · 按任务难度",
  quality: "质量优先",
  cost: "成本优先",
  latency: "速度优先",
};

export function modelStatusLabel(status) {
  return {
    available: "可用",
    degraded: "不稳定",
    unavailable: "不可用",
    disabled: "已停用",
    unknown: "待观测",
  }[status] || status || "待观测";
}

export function modelStatusClass(status) {
  if (status === "available") return "ok";
  if (status === "degraded" || status === "unknown") return "warning";
  if (status === "unavailable") return "error";
  return "neutral";
}

export function registeredModel(modelId) {
  return state.modelRegistry.models.find((item) => item.id === modelId) || null;
}

export function modelLatency(model) {
  const routing = model.routing_metadata || {};
  const value = routing.routing_latency_ms;
  if (value != null && Number.isFinite(Number(value))) {
    return {
      milliseconds: Math.max(0, Math.round(Number(value))),
      source: routing.latency_source === "observed_p50" ? "实测 P50" : "冷启动先验",
    };
  }
  return { milliseconds: null, source: "暂无数据" };
}

export function latencyTier(milliseconds) {
  if (milliseconds == null) return { key: "unknown", label: "待观测", symbol: "⚪" };
  if (milliseconds <= 1000) return { key: "fast", label: "快", symbol: "🟢" };
  if (milliseconds <= 3000) return { key: "moderate", label: "一般", symbol: "🟡" };
  return { key: "slow", label: "慢", symbol: "🔴" };
}

export function formatLatencyMs(milliseconds) {
  return milliseconds == null
    ? "— ms"
    : `${new Intl.NumberFormat("zh-CN").format(milliseconds)} ms`;
}

export function modelsByLatency(models) {
  return [...models].sort((left, right) => {
    if (left.enabled !== right.enabled) return left.enabled ? -1 : 1;
    const leftLatency = modelLatency(left).milliseconds ?? Number.POSITIVE_INFINITY;
    const rightLatency = modelLatency(right).milliseconds ?? Number.POSITIVE_INFINITY;
    return leftLatency - rightLatency
      || left.display_name.localeCompare(right.display_name, "zh-CN");
  });
}

export function currentModelSelectionLabel() {
  const preference = state.modelPreference;
  if (preference.mode === "manual") {
    const model = registeredModel(preference.preferred_model_id);
    if (!model) return "手动模型未配置";
    const latency = modelLatency(model);
    return `${model.display_name} · ${formatLatencyMs(latency.milliseconds)} · ${modelStatusLabel(model.status)}`;
  }
  return `自动 · ${ROUTING_POLICY_LABELS[preference.routing_policy] || preference.routing_policy}`;
}

export function modelOptionLabel(model) {
  const latency = modelLatency(model);
  const tier = latencyTier(latency.milliseconds);
  return `${tier.symbol} ${model.display_name} · ${model.provider} · ${formatLatencyMs(latency.milliseconds)} · ${latency.source}`;
}

export function modelPickerOptionMarkup(model, selectedId) {
  const latency = modelLatency(model);
  const tier = latencyTier(latency.milliseconds);
  const selected = model.id === selectedId;
  return `
    <button
      class="model-picker-option${selected ? " selected" : ""}"
      type="button"
      role="option"
      aria-selected="${selected}"
      aria-disabled="${!model.enabled}"
      data-model-option="${escapeHtml(model.id)}"
      ${model.enabled ? "" : "disabled"}
    >
      <span class="latency-dot ${tier.key}" aria-label="延迟${escapeHtml(tier.label)}"></span>
      <span class="model-picker-copy">
        <strong>${escapeHtml(model.display_name)}</strong>
        <small>${escapeHtml(model.provider)} · ${escapeHtml(model.model)}</small>
      </span>
      <span class="model-picker-metric">
        <strong>${escapeHtml(formatLatencyMs(latency.milliseconds))}</strong>
        <small>${escapeHtml(latency.source)} · ${escapeHtml(modelStatusLabel(model.status))}</small>
      </span>
      <span class="model-picker-check" aria-hidden="true">✓</span>
    </button>`;
}

export function renderManualModelPicker(models, selectedId, disabled) {
  const trigger = $("model-picker-trigger");
  const menu = $("model-picker-menu");
  const selected = registeredModel(selectedId);
  trigger.hidden = false;
  trigger.disabled = disabled || !models.length;
  trigger.setAttribute("aria-expanded", "false");
  menu.hidden = true;
  $("model-picker-options").innerHTML = models.length
    ? models.map((model) => modelPickerOptionMarkup(model, selectedId)).join("")
    : '<div class="model-picker-empty">请先在模型管理中注册并启用模型</div>';

  if (!selected) {
    $("model-picker-trigger-content").innerHTML = `
      <span class="latency-dot unknown"></span>
      <span class="model-picker-copy"><strong>选择首选模型</strong><small>暂无可用模型</small></span>`;
    return;
  }
  const latency = modelLatency(selected);
  const tier = latencyTier(latency.milliseconds);
  $("model-picker-trigger-content").innerHTML = `
    <span class="latency-dot ${tier.key}"></span>
    <span class="model-picker-copy">
      <strong>${escapeHtml(selected.display_name)}</strong>
      <small>${escapeHtml(selected.provider)} · ${escapeHtml(modelStatusLabel(selected.status))}</small>
    </span>
    <span class="model-picker-metric">
      <strong>${escapeHtml(formatLatencyMs(latency.milliseconds))}</strong>
      <small>${escapeHtml(latency.source)}</small>
    </span>`;
}

export function closeModelPicker({ restoreFocus = false } = {}) {
  const trigger = $("model-picker-trigger");
  $("model-picker-menu").hidden = true;
  trigger.setAttribute("aria-expanded", "false");
  if (restoreFocus) trigger.focus();
}

export function closeComposerConfig({ restoreFocus = false } = {}) {
  const config = $("composer-config");
  if (!config.open) return;
  config.open = false;
  closeModelPicker();
  if (restoreFocus) $("composer-model-btn").focus();
}

export function bindComposerConfigDismissal() {
  const config = $("composer-config");
  const trigger = $("composer-model-btn");
  listen(document, "click", (event) => {
    if (
      config.open
      && !config.contains(event.target)
      && !trigger.contains(event.target)
    ) {
      closeComposerConfig();
    }
  });
  listen(document, "keydown", (event) => {
    if (
      event.key !== "Escape"
      || event.defaultPrevented
      || !config.open
      || !$("model-picker-menu").hidden
    ) {
      return;
    }
    event.preventDefault();
    closeComposerConfig({ restoreFocus: true });
  });
}

export function toggleModelPicker() {
  const trigger = $("model-picker-trigger");
  if (trigger.disabled) return;
  const menu = $("model-picker-menu");
  const opening = menu.hidden;
  menu.hidden = !opening;
  trigger.setAttribute("aria-expanded", String(opening));
  if (opening) {
    menu.querySelector('[aria-selected="true"]')?.focus();
  }
}

export function renderSessionModelControls() {
  const preference = state.modelPreference;
  const automatic = preference.mode !== "manual";
  const select = $("session-model-select");
  const disabled = !state.conversationId || Boolean(state.currentSession?.archived_at);
  const choiceControl = select.closest(".model-choice-control");
  choiceControl.classList.toggle("manual", !automatic);
  choiceControl.closest(".composer-mode-bar")?.classList.toggle(
    "manual-model-mode",
    !automatic,
  );
  $("auto-model-toggle").checked = automatic;
  $("session-model-label").textContent = automatic ? "路由策略" : "首选模型";
  $("model-fallback-control").hidden = automatic;
  $("model-fallback-toggle").checked = preference.fallback_enabled !== false;
  if (automatic) {
    closeModelPicker();
    $("model-picker-trigger").hidden = true;
    select.hidden = false;
    select.innerHTML = (state.modelRegistry.routing_policies || Object.keys(ROUTING_POLICY_LABELS))
      .map((policy) => `<option value="${escapeHtml(policy)}">${escapeHtml(ROUTING_POLICY_LABELS[policy] || policy)}</option>`)
      .join("");
    select.value = preference.routing_policy || "smart";
    $("provider-input").value = "";
    $("model-input").value = "";
  } else {
    const models = modelsByLatency(state.modelRegistry.models);
    const selectableModels = models.filter((item) => item.enabled);
    select.hidden = true;
    select.innerHTML = models.length
      ? models.map((model) => `<option value="${escapeHtml(model.id)}" ${model.enabled ? "" : "disabled"}>${escapeHtml(modelOptionLabel(model))}</option>`).join("")
      : '<option value="">请先在模型管理中注册模型</option>';
    const selectedId = preference.preferred_model_id || selectableModels[0]?.id || "";
    select.value = selectedId;
    if (selectedId && !preference.preferred_model_id) {
      preference.preferred_model_id = selectedId;
    }
    const selected = registeredModel(selectedId);
    $("provider-input").value = selected?.provider || "";
    $("model-input").value = selected?.model || "";
    renderManualModelPicker(models, selectedId, disabled);
  }
  $("auto-model-toggle").disabled = disabled;
  select.disabled = disabled;
  $("model-fallback-toggle").disabled = disabled;
  updateContextSummary();
}
