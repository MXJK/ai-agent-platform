import { formatInputCountMethod } from './workspaces.js';

export const $ = (id) => document.getElementById(id);

export const PERMISSION_MODE_HINTS = {
  default: "只读自动执行；写入和命令逐次确认",
  acceptEdits: "只读和文件编辑自动执行；命令逐次确认",
  plan: "只读规划；仅计划文件自动写入，其他写入和命令需确认",
  bypassPermissions: "允许的读写和命令跳过普通确认；硬性安全边界仍生效",
};

export function iconMarkup(name) {
  return `<svg class="app-icon" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;
}

export function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function preferredScrollBehavior() {
  return prefersReducedMotion() ? "auto" : "smooth";
}

export function jsonPretty(value) {
  return JSON.stringify(value, null, 2);
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function inlineMarkdown(value) {
  return value
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

export function renderMarkdown(value) {
  const escaped = escapeHtml(value).replace(/\r\n/g, "\n");
  const codeBlocks = [];
  const withTokens = escaped.replace(/```([\w.+-]*)\n?([\s\S]*?)```/g, (_, language, code) => {
    const token = `@@CODE_BLOCK_${codeBlocks.length}@@`;
    codeBlocks.push(
      `<pre><code${language ? ` data-language="${escapeHtml(language)}"` : ""}>${code.trim()}</code></pre>`,
    );
    return `\n${token}\n`;
  });

  const lines = withTokens.split("\n");
  const output = [];
  let listType = "";

  const closeList = () => {
    if (listType) {
      output.push(`</${listType}>`);
      listType = "";
    }
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const codeMatch = line.trim().match(/^@@CODE_BLOCK_(\d+)@@$/);
    if (codeMatch) {
      closeList();
      output.push(codeBlocks[Number(codeMatch[1])] || "");
      continue;
    }
    if (!line.trim()) {
      closeList();
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      output.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const nextType = unordered ? "ul" : "ol";
      if (listType !== nextType) {
        closeList();
        listType = nextType;
        output.push(`<${listType}>`);
      }
      output.push(`<li>${inlineMarkdown((unordered || ordered)[1])}</li>`);
      continue;
    }
    closeList();
    output.push(`<p>${inlineMarkdown(line)}</p>`);
  }
  closeList();
  return output.join("");
}

export function csvValues(value) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function numberValue(id, fallback) {
  const value = Number.parseInt($(id).value, 10);
  return Number.isFinite(value) ? value : fallback;
}

export function optionalModelFields() {
  const thinkingLevel = $("thinking-level-input").value.trim();
  return {
    ...(thinkingLevel ? { thinking_level: thinkingLevel } : {}),
  };
}

export function formatDate(value) {
  if (!value) {
    return "未知时间";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDuration(value) {
  const milliseconds = Number(value || 0);
  if (milliseconds < 1000) {
    return `${milliseconds} ms`;
  }
  return `${(milliseconds / 1000).toFixed(milliseconds < 10000 ? 1 : 0)} s`;
}

export function formatWorkDuration(value) {
  const seconds = Math.max(0, Math.floor(Number(value || 0) / 1000));
  if (seconds < 1) {
    return "不到 1 秒";
  }
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes} 分${remainder ? ` ${remainder} 秒` : ""}`;
}

export function formatTokenCount(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

export function formatCompactTokenCount(value) {
  return new Intl.NumberFormat("zh-CN", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(Math.max(0, Number(value || 0)));
}

export function formatTokenPercentage(value) {
  const normalized = Number.isFinite(value) ? Math.max(0, value) : 0;
  if (normalized > 0 && normalized < 0.0001) {
    return "<0.01%";
  }
  return new Intl.NumberFormat("zh-CN", {
    style: "percent",
    maximumFractionDigits: 2,
  }).format(normalized);
}

export function formatTokenK(value) {
  const n = Math.max(0, Number(value || 0));
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

export const USER_PROMPT_OPERATIONS = new Set(["agent", "chat", "rag_ask"]);

export function latestUserPromptRecord(records) {
  return [...(records || [])]
    .reverse()
    .find((record) => (
      USER_PROMPT_OPERATIONS.has(String(record?.operation || ""))
      && Number.isFinite(Number(record?.input_tokens))
      && Number(record.input_tokens) >= 0
    )) || null;
}

export function promptMatchesContextWindow(record, context) {
  if (!record || Number(context?.context_window_tokens || 0) <= 0) return false;
  const provider = String(record.provider || "").trim().toLowerCase();
  const model = String(record.model || "").trim();
  const budgetProvider = String(context?.budget_provider || "").trim().toLowerCase();
  const budgetModel = String(context?.budget_model || "").trim();
  return Boolean(
    provider
    && model
    && budgetProvider
    && budgetModel
    && provider === budgetProvider
    && model === budgetModel
  );
}

export function contextRingCenterLabel(ratio, hasBudget) {
  if (!hasBudget) return "–";
  const pct = ratio * 100;
  if (pct <= 0) return "0%";
  if (pct < 1) return "<1%";
  if (pct >= 99.5) return "100%";
  return `${Math.round(pct)}%`;
}

export function composerContextUsagePresentation(usage) {
  if (!usage) {
    return {
      kicker: "本会话累计 Token",
      label: "等待首轮请求",
      compactLabel: "等待首轮请求",
      percentageLabel: "–",
      meterPercent: 0,
      tone: null,
      description: "发起模型请求后显示最近一次实际 Prompt 输入和会话累计消耗",
      ringPercent: 0,
      ringLabel: "–",
      percentage: null,
      promptInput: 0,
      estimatedHistory: 0,
      contextWindow: 0,
      inputBudget: 0,
      reservedOutput: 0,
      inputBudgetExceeded: false,
      provider: "",
      model: "",
      inputCountMethod: "",
      hasMeasuredPrompt: false,
      comparableWindow: false,
    };
  }

  const total = Math.max(0, Number(usage.total_tokens || 0));
  const estimatedHistory = Math.max(0, Number(usage.context?.estimated_tokens || 0));
  const contextWindow = Math.max(0, Number(usage.context?.context_window_tokens || 0));
  const inputBudget = Math.max(0, Number(usage.context?.budget_tokens || 0));
  const reservedOutput = Math.max(0, Number(usage.context?.reserved_output_tokens || 0));
  const latestPrompt = latestUserPromptRecord(usage.records);
  const promptInput = Math.max(0, Number(latestPrompt?.input_tokens || 0));
  const comparableWindow = promptMatchesContextWindow(latestPrompt, usage.context);
  const ratio = comparableWindow ? promptInput / contextWindow : 0;
  const percentage = comparableWindow ? formatTokenPercentage(ratio) : null;
  const inputBudgetExceeded = comparableWindow
    && inputBudget > 0
    && promptInput > inputBudget;
  const promptIdentity = latestPrompt
    ? [latestPrompt.provider, latestPrompt.model].filter(Boolean).join(" / ")
    : "";
  const countMethod = latestPrompt?.input_count_method || "";
  const promptDescription = latestPrompt
    ? `最近一次前台模型请求${promptIdentity ? `（${promptIdentity}）` : ""}记录输入 ${formatTokenCount(promptInput)} tokens${countMethod ? `，计数方式：${formatInputCountMethod(countMethod)}` : ""}`
    : "尚无 Agent、Chat 或 RAG Ask 的模型请求记录";
  const windowDescription = comparableWindow
    ? `该请求模型的完整上下文窗口为 ${formatTokenCount(contextWindow)} tokens（占用 ${percentage}）`
    : contextWindow > 0
      ? "最近请求与上下文窗口的 Provider/Model 不一致，因此不显示不可比的占用百分比"
      : "当前未解析到可比较的模型上下文窗口";
  const inputBudgetDescription = inputBudget > 0
    ? inputBudgetExceeded
      ? `平台输入预算为 ${formatTokenCount(inputBudget)} tokens，本次超出 ${formatTokenCount(promptInput - inputBudget)} tokens`
      : `平台输入预算为 ${formatTokenCount(inputBudget)} tokens`
    : "当前未解析到平台输入预算";
  return {
    kicker: `本会话累计 ${formatTokenCount(total)} tokens`,
    label: latestPrompt
      ? comparableWindow
        ? `上次模型输入 ${formatTokenCount(promptInput)} / ${formatTokenCount(contextWindow)} · ${percentage}`
        : `上次模型输入 ${formatTokenCount(promptInput)} tokens`
      : "尚无前台模型请求",
    compactLabel: latestPrompt
      ? comparableWindow
        ? `最近 Prompt ${formatCompactTokenCount(promptInput)} / ${formatCompactTokenCount(contextWindow)}`
        : `最近 Prompt ${formatCompactTokenCount(promptInput)} tokens`
      : "等待首轮请求",
    percentageLabel: latestPrompt
      ? comparableWindow
        ? percentage
        : contextWindow > 0 ? "不可比" : "无窗口"
      : "–",
    meterPercent: Number((Math.min(1, ratio) * 100).toFixed(4)),
    tone: comparableWindow
      ? ratio >= 0.9 ? "error" : ratio >= 0.72 || inputBudgetExceeded ? "warning" : null
      : null,
    description: `本会话累计消耗 ${formatTokenCount(total)} tokens。${promptDescription}。${windowDescription}。${inputBudgetDescription}。会话历史/摘要另估算为 ${formatTokenCount(estimatedHistory)} tokens，不代表完整 Prompt。`,
    ringPercent: Number((Math.min(1, ratio) * 100).toFixed(4)),
    ringLabel: contextRingCenterLabel(ratio, comparableWindow),
    percentage,
    promptInput,
    estimatedHistory,
    contextWindow,
    inputBudget,
    reservedOutput,
    inputBudgetExceeded,
    provider: latestPrompt?.provider || "",
    model: latestPrompt?.model || "",
    inputCountMethod: countMethod,
    hasMeasuredPrompt: Boolean(latestPrompt),
    comparableWindow,
  };
}
