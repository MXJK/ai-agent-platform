import { state } from './state.js';
import { $, escapeHtml, formatDate } from './format.js';
import { humanizeError, humanizeStatus, showToast, statusClass, truncate } from './feedback.js';
import { fetchJson } from './http.js';

export const EVAL_METRIC_LABELS = {
  pass_rate: { label: "用例通过率 ↑", hint: "通过用例 / 全部完成用例；越高越好", percent: true },
  invalid_action_rate: { label: "无效动作率 ↓", hint: "(实际精确重复 + suppressed) / (executed + suppressed)；越低越好", percent: true },
  mean_step_efficiency: { label: "步数效率 ↓", hint: "executed / 参考步数；不包含 suppressed；越低越好" },
  budget_cap_rate: { label: "预算触顶率 ↓", hint: "预算触顶用例 / 有轨迹用例；越低越好", percent: true },
  failure_recovery_rate: { label: "失败恢复率 ↑", hint: "换策略恢复 / 有真实 ToolResult 故障的用例；越高越好", percent: true },
  citation_content_accuracy: { label: "引用内容准确率 ↑", hint: "磁盘内容一致证据 / 可评分证据；越高越好", percent: true },
  answer_path_grounding_rate: { label: "答案路径落地率 ↑", hint: "有成功读取证据的答案路径 / 答案路径；越高越好", percent: true },
  fully_grounded_case_rate: { label: "完全落地用例率 ↑", hint: "内容与路径均落地用例 / 可评分引用用例；越高越好", percent: true },
  tokens_per_case: { label: "每用例 Token ↓", hint: "total_tokens / 完成用例；仅回归预警，无硬门槛", integer: true },
  total_tokens: { label: "总 Token ↓", hint: "本次 Eval 全部用例 Token；仅回归预警，无硬门槛", integer: true },
  elapsed_ms_per_case: { label: "每用例耗时 ↓", hint: "Eval 墙钟耗时 / 完成用例；仅回归预警，无硬门槛", milliseconds: true },
  elapsed_ms: { label: "总耗时 ↓", hint: "本次 Eval 墙钟耗时；仅回归预警，无硬门槛", milliseconds: true },
  proposed_calls: { label: "Proposed 调用 ↓", hint: "模型提出的调用总数；包含未执行、拒绝和待审批", integer: true },
  executed_calls: { label: "Executed 调用 ↓", hint: "能按 call_id 找到真实 ToolResult 的调用总数", integer: true },
  suppressed_calls: { label: "Suppressed 调用 ↓", hint: "平台抑制且未执行的调用总数；计入无效动作率", integer: true },
};

export const EVAL_TERMINAL_STATUSES = new Set(["completed", "failed"]);

export function formatEvalMetric(name, value) {
  if (value === null || value === undefined) {
    return "n/a";
  }
  if (EVAL_METRIC_LABELS[name]?.milliseconds) {
    return `${Math.round(value).toLocaleString()} ms`;
  }
  if (EVAL_METRIC_LABELS[name]?.integer) {
    return Math.round(value).toLocaleString();
  }
  return EVAL_METRIC_LABELS[name]?.percent
    ? `${(value * 100).toFixed(1)}%`
    : value.toFixed(2);
}

export function formatEvalDelta(name, delta) {
  if (delta === null || delta === undefined || Math.abs(delta) < 0.0005) {
    return { text: "与基线持平", tone: "flat" };
  }
  // The API already signs every delta so that positive always means worse.
  const magnitude = EVAL_METRIC_LABELS[name]?.percent
    ? `${(Math.abs(delta) * 100).toFixed(1)}pp`
    : Math.abs(delta).toFixed(2);
  return delta > 0
    ? { text: `较基线劣化 ${magnitude}`, tone: "worse" }
    : { text: `较基线改善 ${magnitude}`, tone: "better" };
}

export async function loadEvalDashboard(runId = "") {
  const [catalogue, history] = await Promise.all([
    fetchJson("/evals/catalogue"),
    fetchJson("/evals/runs?limit=20"),
  ]);
  state.evalCatalogue = catalogue;
  state.evalHistory = history.runs || [];
  renderEvalProviderOptions();
  renderEvalHistory();
  const targetId = runId
    || catalogue.active_run_id
    || state.evalRunId
    || state.evalHistory[0]?.run_id
    || "";
  if (targetId) {
    await loadEvalRun(targetId);
  } else {
    renderEvalRun(null);
  }
  if (catalogue.active_run_id) {
    scheduleEvalPoll(catalogue.active_run_id);
  }
}

export async function loadEvalRun(runId) {
  const detail = await fetchJson(`/evals/runs/${encodeURIComponent(runId)}`);
  state.evalRunId = runId;
  state.evalRun = detail;
  renderEvalRun(detail);
  return detail;
}

export function renderEvalProviderOptions() {
  const select = $("eval-provider-select");
  const runButton = $("run-eval-btn");
  if (!select) {
    return;
  }
  // The backend reports which providers have an enabled registered model. Any
  // other choice would start a run whose tool selection narrows to nothing.
  const providers = state.evalCatalogue?.providers || [];
  const previous = select.value;
  if (!providers.length) {
    select.innerHTML = '<option value="">没有可用的已注册模型</option>';
    select.disabled = true;
    runButton.disabled = true;
    return;
  }
  select.disabled = false;
  runButton.disabled = false;
  select.innerHTML = providers.map((item) => {
    const paid = item.provider !== "fake";
    const key = `${item.provider}:${item.model}`;
    return `<option value="${escapeHtml(key)}" data-provider="${escapeHtml(item.provider)}" data-model="${escapeHtml(item.model)}">`
      + `${escapeHtml(item.display_name)}（${escapeHtml(item.model)}${paid ? " · 计费" : " · 零成本"}）</option>`;
  }).join("");
  if (previous && select.querySelector(`option[value="${CSS.escape(previous)}"]`)) {
    select.value = previous;
  }
}

export function renderEvalRun(detail) {
  renderEvalProgress(detail);
  renderEvalAlerts(detail);
  renderEvalMetrics(detail);
  renderEvalCases(detail);
  const pinButton = $("pin-eval-baseline-btn");
  if (pinButton) {
    const pinnable = Boolean(
      detail
      && detail.status === "completed"
      && detail.run_id !== detail.baseline_run_id,
    );
    pinButton.disabled = !pinnable;
  }
  renderEvalHistory();
}

export function renderEvalProgress(detail) {
  const wrapper = $("eval-progress");
  if (!wrapper) {
    return;
  }
  const running = Boolean(detail && detail.status === "running");
  wrapper.hidden = !running;
  $("run-eval-btn").disabled = running;
  if (!running) {
    return;
  }
  const percent = Math.round((detail.progress || 0) * 100);
  $("eval-progress-fill").style.width = `${percent}%`;
  $("eval-progress-label").textContent =
    `正在运行 ${escapeHtml(detail.provider)}：${detail.completed_cases}/${detail.total_cases} 个用例`;
}

export function renderEvalAlerts(detail) {
  const list = $("eval-alert-list");
  const badge = $("eval-alert-count");
  if (!list) {
    return;
  }
  const alerts = detail?.alerts || [];
  badge.textContent = String(alerts.length);
  badge.classList.toggle("danger", alerts.some((item) => item.severity === "critical"));
  if (!detail) {
    list.innerHTML = '<div class="empty-state">尚未运行评测</div>';
    return;
  }
  if (!alerts.length) {
    list.innerHTML = detail.status === "completed"
      ? '<div class="empty-state">没有预警：约束全部满足，指标未越界。</div>'
      : '<div class="empty-state">运行中…</div>';
    return;
  }
  list.innerHTML = alerts.map((alert) => `
    <div class="eval-alert ${escapeHtml(alert.severity)}">
      <span class="eval-alert-kind">${escapeHtml(evalAlertKindLabel(alert.kind))}</span>
      <span class="eval-alert-message">${escapeHtml(alert.message)}</span>
    </div>`).join("");
}

export function evalAlertKindLabel(kind) {
  const labels = { case: "用例失败", threshold: "越过门槛", regression: "相对基线回归" };
  return labels[kind] || kind;
}

export function renderEvalMetrics(detail) {
  const grid = $("eval-metric-grid");
  if (!grid) {
    return;
  }
  if (!detail || !detail.metrics) {
    grid.innerHTML = '<div class="empty-state">尚未运行评测</div>';
    return;
  }
  const deltas = detail.deltas || {};
  grid.innerHTML = Object.entries(EVAL_METRIC_LABELS).map(([name, meta]) => {
    const value = detail.metrics[name];
    const delta = detail.baseline && detail.run_id !== detail.baseline.run_id
      ? formatEvalDelta(name, deltas[name])
      : { text: detail.is_baseline ? "本次即基线" : "无基线可比", tone: "flat" };
    return `
      <article class="metric-card eval-metric-card">
        <span>${escapeHtml(meta.label)}</span>
        <strong>${escapeHtml(formatEvalMetric(name, value))}</strong>
        <small class="eval-delta ${delta.tone}">${escapeHtml(delta.text)}</small>
        <small class="eval-metric-hint">${escapeHtml(meta.hint)}</small>
      </article>`;
  }).join("");
}

export function renderEvalCases(detail) {
  const list = $("eval-case-list");
  const summary = $("eval-case-summary");
  if (!list) {
    return;
  }
  const cases = detail?.cases || [];
  summary.textContent = detail
    ? `${detail.passed_cases}/${detail.total_cases}`
    : "0/0";
  if (!cases.length) {
    list.innerHTML = '<div class="empty-state">尚未运行评测</div>';
    return;
  }
  list.innerHTML = cases.map((item) => {
    const metrics = item.metrics || {};
    const citations = item.citations;
    const failed = (item.constraints || []).filter((entry) => !entry.passed);
    const stats = [
      `${metrics.executed_calls ?? 0} 步`,
      `提议 ${metrics.proposed_calls ?? 0}`,
      metrics.step_efficiency == null ? null : `效率 ${Number(metrics.step_efficiency).toFixed(2)}`,
      metrics.repeated_calls ? `重复 ${metrics.repeated_calls}` : null,
      metrics.suppressed_calls ? `抑制 ${metrics.suppressed_calls}` : null,
      metrics.denied_calls ? `拒绝 ${metrics.denied_calls}` : null,
      metrics.pending_approval_calls ? `待审批 ${metrics.pending_approval_calls}` : null,
      metrics.budget_capped ? "预算触顶" : null,
      metrics.failure_recovery && metrics.failure_recovery !== "not_triggered"
        ? `失败恢复：${evalRecoveryLabel(metrics.failure_recovery)}`
        : null,
      citations ? `内容 ${citations.verified}/${citations.scored}` : null,
      citations?.answer_path_grounding_rate == null
        ? null
        : `路径 ${(citations.answer_path_grounding_rate * 100).toFixed(0)}%`,
      `无效率 ${formatEvalMetric("invalid_action_rate", metrics.invalid_action_rate)}`,
      metrics.total_tokens == null ? null : `${Number(metrics.total_tokens).toLocaleString()} tok`,
      metrics.elapsed_ms == null ? null : formatEvalMetric("elapsed_ms", metrics.elapsed_ms),
    ].filter(Boolean);
    return `
      <details class="eval-case ${item.passed ? "passed" : "failed"}">
        <summary>
          <span class="eval-case-verdict">${item.passed ? "PASS" : "FAIL"}</span>
          <span class="eval-case-id">${escapeHtml(item.case_id)}</span>
          <span class="eval-case-stats">${escapeHtml(stats.join(" · "))}</span>
        </summary>
        <div class="eval-case-body">
          ${item.error ? `<p class="eval-case-error">${escapeHtml(item.error)}</p>` : ""}
          ${failed.length ? `<ul class="eval-constraint-list">${failed.map((entry) =>
            `<li><strong>${escapeHtml(entry.name)}</strong> ${escapeHtml(truncate(entry.detail, 240))}</li>`).join("")}</ul>` : ""}
          ${citations && citations.ungrounded_paths?.length
            ? `<p class="eval-case-error">答案引用了未读过的路径：${escapeHtml(citations.ungrounded_paths.join(", "))}</p>`
            : ""}
          ${citations && citations.failures?.length
            ? `<ul class="eval-constraint-list">${citations.failures.map((entry) =>
              `<li><strong>${escapeHtml(entry.status)}</strong> ${escapeHtml(entry.path)}:${entry.start_line}-${entry.end_line}</li>`).join("")}</ul>`
            : ""}
          ${renderEvalCallLifecycle(metrics)}
          ${renderEvalReadEvidence(item.read_evidence || [])}
          ${renderEvalAgentErrors(item.agent_errors || [])}
          <p class="eval-case-trace">${escapeHtml((item.trace_nodes || []).join(" › ") || "无轨迹")}</p>
        </div>
      </details>`;
  }).join("");
}

export function renderEvalCallLifecycle(metrics) {
  const groups = [
    ["Proposed", metrics.proposed_call_details || []],
    ["Executed", metrics.executed_call_details || []],
    ["Suppressed", metrics.suppressed_call_details || []],
    ["Denied", metrics.denied_call_details || []],
    ["Pending approval", metrics.pending_approval_call_details || []],
  ].filter(([, calls]) => calls.length);
  if (!groups.length) {
    return "";
  }
  return groups.map(([label, calls]) => `
    <details class="eval-evidence-group">
      <summary>${escapeHtml(label)} · ${calls.length}</summary>
      <ul class="eval-constraint-list">${calls.map((call) => {
        const outcome = call.ok == null ? "" : (call.ok ? " · ok" : " · failed");
        const reason = call.reason ? ` · ${call.reason}` : "";
        const args = truncate(JSON.stringify(call.arguments || {}), 240);
        return `<li><strong>${escapeHtml(call.name || "unknown")}</strong> ${escapeHtml(call.call_id || "no-call-id")}${escapeHtml(outcome + reason)}<br><code>${escapeHtml(args)}</code></li>`;
      }).join("")}</ul>
    </details>`).join("");
}

export function renderEvalReadEvidence(evidence) {
  if (!evidence.length) {
    return "";
  }
  return `
    <details class="eval-evidence-group">
      <summary>成功读取证据 · ${evidence.length}</summary>
      <ul class="eval-constraint-list">${evidence.map((item) =>
        `<li><strong>${escapeHtml(item.path || "unknown")}:${item.start_line ?? "?"}-${item.end_line ?? "?"}</strong> ${escapeHtml(item.source || "")} · ${escapeHtml(item.call_id || "initial-context")}${item.truncated ? " · truncated" : ""}</li>`).join("")}</ul>
    </details>`;
}

export function renderEvalAgentErrors(errors) {
  if (!errors.length) {
    return "";
  }
  return `
    <details class="eval-evidence-group">
      <summary>Agent 真实错误 · ${errors.length}</summary>
      <ul class="eval-constraint-list">${errors.map((item) =>
        `<li>${escapeHtml(truncate(JSON.stringify(item), 320))}</li>`).join("")}</ul>
    </details>`;
}

export function evalRecoveryLabel(value) {
  const labels = {
    recovered: "换策略",
    retry_loop: "死磕重试",
    gave_up: "放弃",
    not_triggered: "未触发",
  };
  return labels[value] || value;
}

export function renderEvalHistory() {
  const list = $("eval-history-list");
  if (!list) {
    return;
  }
  const runs = state.evalHistory || [];
  if (!runs.length) {
    list.innerHTML = '<div class="empty-state">暂无历史</div>';
    return;
  }
  list.innerHTML = runs.map((run) => {
    const active = run.run_id === state.evalRunId;
    const passRate = run.metrics
      ? formatEvalMetric("pass_rate", run.metrics.pass_rate)
      : "—";
    return `
      <button type="button" class="eval-history-row${active ? " active" : ""}" data-eval-run="${escapeHtml(run.run_id)}">
        <span class="status-pill ${statusClass(run.status)}"><span class="status-dot"></span>${escapeHtml(humanizeStatus(run.status))}</span>
        <span class="eval-history-provider">${escapeHtml(run.provider)}${run.model ? ` · ${escapeHtml(run.model)}` : ""}</span>
        <span class="eval-history-score">${escapeHtml(passRate)} · ${run.passed_cases}/${run.total_cases}</span>
        <span class="eval-history-alerts${run.critical_alert_count ? " danger" : ""}">${run.alert_count} 预警</span>
        <span class="eval-history-meta">${escapeHtml(formatDate(run.started_at))}${run.is_baseline ? " · 基线" : ""}</span>
      </button>`;
  }).join("");
}

export function scheduleEvalPoll(runId) {
  if (state.evalPollTimer) {
    clearTimeout(state.evalPollTimer);
  }
  state.evalPollTimer = setTimeout(async () => {
    state.evalPollTimer = null;
    try {
      const detail = await loadEvalRun(runId);
      if (!EVAL_TERMINAL_STATUSES.has(detail.status)) {
        scheduleEvalPoll(runId);
        return;
      }
      state.evalHistory = (await fetchJson("/evals/runs?limit=20")).runs || [];
      renderEvalHistory();
      if (detail.status === "failed") {
        showToast(`评测失败：${detail.error}`, "error");
      } else {
        showToast(`评测完成：${detail.passed_cases}/${detail.total_cases} 通过`, "success");
      }
    } catch (error) {
      showToast(humanizeError(error), "error");
    }
  }, 1200);
}

export async function startEvalRun() {
  const select = $("eval-provider-select");
  const provider = select?.selectedOptions?.[0]?.dataset.provider || "fake";
  const model = select?.selectedOptions?.[0]?.dataset.model || "";
  if (provider !== "fake") {
    const confirmed = window.confirm(
      `将对 ${provider} 发起 ${state.evalCatalogue?.cases?.length || 0} 个真实模型用例，会产生 Token 费用。继续？`,
    );
    if (!confirmed) {
      return;
    }
  }
  try {
    const started = await fetchJson("/evals/runs", {
      method: "POST",
      body: JSON.stringify({ provider, model }),
    });
    state.evalRunId = started.run_id;
    await loadEvalRun(started.run_id);
    state.evalHistory = (await fetchJson("/evals/runs?limit=20")).runs || [];
    renderEvalHistory();
    scheduleEvalPoll(started.run_id);
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

export async function pinEvalBaseline() {
  if (!state.evalRunId) {
    return;
  }
  const criticalCount = Number(state.evalRun?.critical_alert_count || 0);
  let force = false;
  if (criticalCount) {
    if (!window.confirm(`本次评测有 ${criticalCount} 个 critical 预警，默认禁止设为基线。是否进入强制确认？`)) {
      return;
    }
    if (!window.confirm("再次确认：强制基线会记录 forced=true，但不会消除预警。继续？")) {
      return;
    }
    force = true;
  }
  try {
    await fetchJson(`/evals/runs/${encodeURIComponent(state.evalRunId)}/baseline?force=${force}`, {
      method: "POST",
    });
    await loadEvalDashboard(state.evalRunId);
    showToast("已设为该 Provider / Model / Suite / Evaluator 的基线", "success");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}
