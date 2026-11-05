import { managedSetInterval } from '../lib/resources';
import { renderProviders, renderModels } from '../features/registry-lists';
import { runtimeRefs } from './config.js';
import { state } from './state.js';
import { $, escapeHtml, formatDate, formatDuration } from './format.js';
import { humanizeError, setRaw, showToast, truncate } from './feedback.js';
import { invalidateSlashCapabilities } from './composer.js';
import { fetchJson } from './http.js';
import { MODEL_PROVIDERS, modelStatusClass, modelStatusLabel, registeredModel, renderSessionModelControls } from './models.js';
import { ensureSession, loadSessionTokenUsage } from './sessions.js';

export async function loadModelRegistry(showRaw = false) {
  const body = await fetchJson("/model-registry");
  state.modelRegistry = body;
  renderProviderConnections();
  renderRegisteredModels();
  renderSessionModelControls();
  if (showRaw) setRaw(body);
  return body;
}

export function syncModelRegistryRefresh() {
  if (runtimeRefs.modelRegistryRefreshTimer) {
    window.clearInterval(runtimeRefs.modelRegistryRefreshTimer);
    runtimeRefs.modelRegistryRefreshTimer = null;
  }
  if (state.currentView !== "models" || document.hidden) return;
  runtimeRefs.modelRegistryRefreshTimer = managedSetInterval(() => {
    if (state.currentView !== "models" || document.hidden) return;
    loadModelRegistry().catch(() => null);
  }, 60_000);
}

export function refreshModelRegistryTelemetry() {
  loadModelRegistry().catch(() => null);
}

export async function loadModelPreference(sessionId = state.conversationId) {
  if (!sessionId) {
    renderSessionModelControls();
    return state.modelPreference;
  }
  const preference = await fetchJson(
    `/sessions/${encodeURIComponent(sessionId)}/model-preference`,
  );
  if (
    preference.mode === "auto"
    && state.currentSession?.provider
    && state.currentSession?.model
  ) {
    const legacy = state.modelRegistry.models.find(
      (item) => item.provider === state.currentSession.provider
        && item.model === state.currentSession.model,
    );
    if (legacy) {
      preference.mode = "manual";
      preference.preferred_model_id = legacy.id;
      preference.fallback_enabled = true;
    }
  }
  state.modelPreference = preference;
  invalidateSlashCapabilities();
  renderSessionModelControls();
  return preference;
}

export async function saveModelPreference() {
  const conversationId = await ensureSession();
  const automatic = $("auto-model-toggle").checked;
  const payload = automatic
    ? {
        mode: "auto",
        routing_policy: $("session-model-select").value || "smart",
        preferred_model_id: null,
        fallback_enabled: true,
      }
    : {
        mode: "manual",
        routing_policy: state.modelPreference.routing_policy || "smart",
        preferred_model_id: $("session-model-select").value || null,
        fallback_enabled: $("model-fallback-toggle").checked,
      };
  if (!automatic && !payload.preferred_model_id) {
    showToast("请先在模型管理中注册并启用一个模型", "warning");
    throw new Error("manual mode requires a registered model");
  }
  state.modelPreference = await fetchJson(
    `/sessions/${encodeURIComponent(conversationId)}/model-preference`,
    { method: "PUT", body: JSON.stringify(payload) },
  );
  invalidateSlashCapabilities();
  const selected = registeredModel(state.modelPreference.preferred_model_id);
  if (state.currentSession) {
    state.currentSession.provider = selected?.provider || null;
    state.currentSession.model = selected?.model || null;
  }
  renderSessionModelControls();
  await loadSessionTokenUsage([conversationId]);
  showToast(automatic ? "已启用自动选模" : `首选模型已切换为 ${selected?.display_name || "手动模型"}`);
}

export function renderProviderConnections() {
  const grid = $("provider-connection-grid");
  const probePolicy = state.modelRegistry.probe_policy || {};
  const probePolicyNote = $("model-probe-policy-note");
  if (probePolicyNote) {
    probePolicyNote.textContent = probePolicy.periodic_enabled
      ? `周期探测已启用：每 ${formatDuration(Number(probePolicy.interval_seconds) * 1000)} 检查长期无真实流量的模型。`
      : "页面状态会自动刷新；周期探测默认关闭，单次测速会产生一次最短模型调用。";
  }
  renderProviders(state.modelRegistry.connections);
}

export async function handleProviderAction(button) {
  const card = button.closest("[data-provider]");
  const provider = card.dataset.provider;
  const action = button.dataset.providerAction;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    if (action === "save") {
      const apiKey = card.querySelector("[data-provider-key]").value.trim();
      const displayName = MODEL_PROVIDERS.find(([id]) => id === provider)?.[1] || provider;
      await fetchJson(`/model-registry/connections/${encodeURIComponent(provider)}`, {
        method: "PUT",
        body: JSON.stringify({
          display_name: displayName,
          ...(apiKey ? { api_key: apiKey } : {}),
          enabled: card.querySelector("[data-provider-enabled]").checked,
        }),
      });
      showToast(`${displayName} 配置已保存`);
    } else {
      const result = await fetchJson(
        `/model-registry/connections/${encodeURIComponent(provider)}/test`,
        { method: "POST" },
      );
      showToast(`${provider} 连接成功 · ${formatDuration(result.elapsed_ms)}`);
    }
    await loadModelRegistry();
    if (action === "save") {
      $("registered-model-provider-input").value = provider;
      await discoverProviderModels(provider, { showToast: false });
    }
    return true;
  } catch (error) {
    showToast(humanizeError(error), "error");
    return false;
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

export function modelTierLabel(value) {
  return {
    advanced: "高质量",
    balanced: "均衡",
    efficient: "高效",
    high: "高成本",
    standard: "标准成本",
    low: "低成本",
  }[value] || value || "后端默认";
}

export function selectedDiscoveredModel() {
  const modelId = $("discovered-model-select").value;
  return state.modelDiscovery.models.find((item) => item.model === modelId) || null;
}

export function renderDiscoveredModels() {
  const select = $("discovered-model-select");
  const summary = $("discovered-model-summary");
  const manualInput = $("manual-model-id-input");
  const models = state.modelDiscovery.models || [];
  const previousSelection = select.value;
  const doubaoRestricted = state.modelDiscovery.provider === "doubao";
  manualInput.placeholder = doubaoRestricted
    ? "仅支持 doubao-seed-evolving / doubao-seed-2.1-turbo / doubao-seed-2.0-lite"
    : "例如 gpt-5-mini";
  const selectable = models.filter((item) => !item.already_registered);
  select.disabled = state.modelDiscovery.loading || !selectable.length;
  select.innerHTML = state.modelDiscovery.loading
    ? '<option value="">正在发现模型…</option>'
    : models.length
      ? models.map((item) => `<option value="${escapeHtml(item.model)}" ${item.already_registered ? "disabled" : ""}>${escapeHtml(item.display_name)} · ${escapeHtml(item.model)}${item.already_registered ? " · 已注册" : ""}</option>`).join("")
      : doubaoRestricted
        ? '<option value="">未发现受支持的豆包模型，可手动填写白名单模型 ID</option>'
        : '<option value="">暂无已发现模型，可使用下方手动兜底</option>';
  if (selectable.length) {
    select.value = selectable.some((item) => item.model === previousSelection)
      ? previousSelection
      : selectable[0].model;
  }
  const selected = selectedDiscoveredModel();
  if (!selected) {
    summary.textContent = state.modelDiscovery.loading
      ? "正在读取 Provider 当前账号的模型目录…"
      : "后端会自动维护显示名称、能力、上下文和路由参数。";
    return;
  }
  const capabilities = [
    selected.capabilities?.tool_calling ? "工具调用" : null,
    selected.capabilities?.structured_output ? "结构化输出" : null,
  ].filter(Boolean).join("、") || "基础文本生成";
  summary.innerHTML = `<strong>${escapeHtml(selected.display_name)}</strong><span>${Number(selected.context_window_tokens).toLocaleString()} ctx · 8,192 普通 / ${Number(selected.max_output_tokens).toLocaleString()} thinking 与恢复 · ${escapeHtml(capabilities)} · ${escapeHtml(modelTierLabel(selected.quality_tier))} · ${escapeHtml(modelTierLabel(selected.cost_tier))}</span><small>输出额度由统一运行策略管理；延迟将在真实请求后自动学习。</small>`;
}

export async function discoverProviderModels(
  provider = $("registered-model-provider-input").value,
  { showToast: shouldToast = true } = {},
) {
  state.modelDiscovery = { provider, models: [], loading: true };
  renderDiscoveredModels();
  try {
    const result = await fetchJson(
      `/model-registry/connections/${encodeURIComponent(provider)}/available-models`,
    );
    state.modelDiscovery = { provider, models: result.models || [], loading: false };
    renderDiscoveredModels();
    if (shouldToast) showToast(`已发现 ${result.models?.length || 0} 个可用模型`);
    return result;
  } catch (error) {
    state.modelDiscovery = { provider, models: [], loading: false };
    renderDiscoveredModels();
    throw error;
  }
}

export function resetRegisteredModelForm() {
  $("manual-model-id-input").value = "";
  $("registered-model-enabled-input").checked = true;
  $("registered-model-auto-input").checked = true;
}

export async function saveRegisteredModel() {
  const provider = $("registered-model-provider-input").value;
  const manualModel = $("manual-model-id-input").value.trim();
  const discovered = selectedDiscoveredModel();
  const model = manualModel || (discovered?.already_registered ? "" : discovered?.model || "");
  if (!model) {
    showToast("请先发现并选择模型，或填写模型 ID", "warning");
    return;
  }
  const payload = {
    provider,
    model,
    enabled: $("registered-model-enabled-input").checked,
    auto_eligible: $("registered-model-auto-input").checked,
  };
  const button = $("save-registered-model-btn");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await fetchJson("/model-registry/models", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    resetRegisteredModelForm();
    await loadModelRegistry();
    await discoverProviderModels(provider, { showToast: false });
    showToast("模型已注册，路由元数据由后端维护");
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

export async function updateRegisteredModel(modelId, changes) {
  const model = registeredModel(modelId);
  if (!model) return;
  try {
    await fetchJson(`/model-registry/models/${encodeURIComponent(modelId)}`, {
      method: "PUT",
      body: JSON.stringify({
        enabled: changes.enabled ?? model.enabled,
        auto_eligible: changes.auto_eligible ?? model.auto_eligible,
      }),
    });
    await loadModelRegistry();
    showToast("模型配置已更新");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

export async function deleteRegisteredModel(modelId) {
  const model = registeredModel(modelId);
  if (!model || !window.confirm(`确认删除模型“${model.display_name}”？`)) return;
  try {
    await fetchJson(`/model-registry/models/${encodeURIComponent(modelId)}`, { method: "DELETE" });
    await loadModelRegistry();
    showToast("模型已删除");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

export async function testRegisteredModel(modelId, button) {
  const model = registeredModel(modelId);
  if (!model) return;
  const originalLabel = button.textContent;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = "测速中…";
  try {
    const result = await fetchJson(
      `/model-registry/models/${encodeURIComponent(modelId)}/test`,
      { method: "POST" },
    );
    await loadModelRegistry();
    showToast(
      `${model.display_name} 测速完成 · ${formatDuration(result.elapsed_ms)}`,
      "success",
    );
  } catch (error) {
    showToast(`测速失败：${humanizeError(error)}`, "error");
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.textContent = originalLabel;
  }
}

export function renderRegisteredModels() {
 const models = state.modelRegistry.models || [];
 $("registered-model-count").textContent = String(models.length);
 renderModels(models);
}
