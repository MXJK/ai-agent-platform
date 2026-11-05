import { listen, managedSetTimeout } from '../lib/resources';
import { $, escapeHtml } from './format.js';

export function humanizeStatus(value) {
  const labels = {
    ok: "服务正常",
    offline: "连接失败",
    queued: "排队中",
    running: "运行中",
    waiting_approval: "等待审批",
    waiting_input: "等待输入",
    paused: "已暂停",
    completed: "已完成",
    partial: "部分完成",
    blocked: "受阻",
    cancelled: "已取消",
    completed_with_errors: "完成，有警告",
    failed: "失败",
    pending: "等待中",
    ready: "待审阅",
    applying: "应用中",
    applied: "已应用",
    reverted: "已回滚",
    rejected: "已拒绝",
    conflicted: "存在冲突",
  };
  return labels[value] || value || "未知";
}

export function humanizeApprovalReason(value) {
  if (value === "one or more planned tools require human approval before execution") {
    return "以下操作可能产生变更，需要你确认后才能执行。";
  }
  return value || "以下操作需要在执行前获得你的确认。";
}

export function humanizePermissionLevel(value) {
  const labels = {
    read_only: "只读",
    write_safe: "沙箱内可写",
    external_side_effect: "外部副作用",
  };
  return labels[value] || value || "需确认";
}

export function statusClass(value) {
  if (value === "completed_with_errors") {
    return "warning";
  }
  if (value === "ready") return "pending";
  if (value === "applying") return "running";
  if (value === "applied") return "completed";
  if (value === "rejected") return "cancelled";
  if (value === "conflicted") return "warning";
  if (["completed", "partial", "blocked", "cancelled", "failed", "waiting_approval", "waiting_input", "paused", "running", "queued"].includes(value)) {
    return value;
  }
  return "neutral";
}

export function truncate(value, maxLength = 120) {
  const text = String(value ?? "");
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

export function humanizeError(error) {
  if (!error) {
    return "发生未知错误";
  }
  if (error.name === "AbortError") {
    return "操作已停止";
  }
  return error.message || String(error);
}

export function showToast(message, type = "success", timeout = 4200) {
  const region = $("toast-region");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${escapeHtml(message)}</span><button type="button" aria-label="关闭通知">×</button>`;
  const close = () => toast.remove();
  listen(toast.querySelector("button"), "click", close);
  region.appendChild(toast);
  managedSetTimeout(close, timeout);
}

export function setRaw(_value) {}

export function setTrace(_items) {}

export function setLastRequestId(_value) {}
