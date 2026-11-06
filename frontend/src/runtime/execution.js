import { managedSetInterval } from '../lib/resources';
import { TERMINAL_RUN_STATUSES, responseTimers } from './config.js';
import { escapeHtml, formatDuration, formatTokenCount, formatWorkDuration } from './format.js';

export function humanizeAgentNode(value) {
  const labels = {
    setup_workspace: "准备工作区",
    load_project_instructions: "加载项目指令",
    classify_request: "识别任务意图",
    decide_context_source: "选择上下文来源",
    retrieve_knowledge: "检索知识库",
    retrieve_project_memory: "检索项目记忆",
    plan_exploration: "规划仓库探索",
    execute_exploration: "执行仓库探索",
    assess_context: "评估上下文",
    assemble_context: "装配上下文预算",
    merge_evidence: "合并证据",
    resolve_change_targets: "解析修改目标",
    plan_tools: "规划工具调用",
    review_tool_plan: "等待工具审批",
    inspect_repository: "检查代码仓库",
    execute_changes: "执行代码修改",
    validate_changes: "验证代码修改",
    review_repair_plan: "等待修复审批",
    collect_artifacts: "汇总变更产物",
    compose_answer: "生成最终回答",
    compose_error_answer: "生成错误说明",
    model_request: "请求模型",
    stream_response: "流式生成回答",
  };
  return labels[value] || value || "准备执行";
}

export function traceToolNames(trace, events = []) {
  const names = [];
  for (const step of trace || []) {
    const output = step.output || {};
    for (const key of [
      "called_tools",
      "planned_tools",
      "repair_planned_tools",
      "approval_required_tools",
    ]) {
      const values = Array.isArray(output[key]) ? output[key] : [];
      for (const value of values) {
        const name = typeof value === "string" ? value : value?.name;
        if (name && !names.includes(name)) {
          names.push(name);
        }
      }
    }
  }
  for (const event of events || []) {
    if (!["tool_selected", "tool_started", "tool_result", "tool_error"].includes(event.type)) {
      continue;
    }
    const name = event.output?.name;
    if (name && !names.includes(name)) names.push(name);
  }
  return names;
}

export const AGENT_ACTIVITY_EVENT_TYPES = new Set([
  "node_started",
  "reasoning_summary",
  "tool_selected",
  "tool_started",
  "tool_result",
  "tool_error",
  "context",
  "answer_completed",
]);

export const AGENT_PERSISTED_PRESENTATION_EVENT_TYPES = new Set([
  ...AGENT_ACTIVITY_EVENT_TYPES,
  "thinking_completed",
]);

export const AGENT_CONTEXT_ACTIVITY_NODES = new Set([
  "load_project_instructions",
  "classify_request",
  "decide_context_source",
  "retrieve_knowledge",
  "retrieve_project_memory",
  "assess_context",
  "merge_evidence",
  "resolve_change_targets",
]);

export function agentToolLabel(name) {
  const labels = {
    "repo.read_file": "读取文件",
    "repo.search_code": "搜索代码",
    "repo.find_files": "查找文件",
    "repo.list_files": "列出文件",
    "sandbox.write_file": "写入文件",
    "sandbox.apply_patch": "应用补丁",
    "sandbox.run_command": "执行命令",
    "sandbox.workspace_status": "查看工作区",
    "sandbox.git_diff": "查看变更",
    "run.read_artifact": "读取运行产物",
    "agent.load_skill": "加载技能",
    "agent.request_user_input": "请求用户输入",
    "file_symbol_locator": "定位符号",
  };
  return labels[name] || name;
}

export function agentToolArgumentsText(name, args = {}) {
  const path = typeof args.path === "string" && args.path ? args.path : "";
  switch (name) {
    case "repo.read_file": {
      const parts = [];
      if (path) parts.push(path);
      const start = Number(args.start_line);
      const end = Number(args.end_line);
      if (start >= 1) {
        parts.push(`第 ${start}–${end >= start ? end : "末"} 行`);
      }
      return parts.join(" · ") || "读取工作区文件";
    }
    case "repo.search_code": {
      const query = typeof args.query === "string" ? args.query : "";
      const parts = [];
      if (query) parts.push(`“${query}”`);
      if (path && path !== ".") parts.push(`在 ${path}`);
      return parts.join(" ") || "搜索代码";
    }
    case "repo.find_files": {
      const query = typeof args.query === "string" ? args.query : "";
      const parts = [];
      if (query) parts.push(`“${query}”`);
      if (path && path !== ".") parts.push(`在 ${path}`);
      return parts.join(" ") || "查找文件";
    }
    case "repo.list_files":
      return path && path !== "." ? `目录 ${path}` : "列出工作区文件";
    case "sandbox.run_command": {
      const command = typeof args.command === "string"
        ? args.command
        : Array.isArray(args.command)
        ? args.command.join(" ")
        : "";
      return command || "运行命令";
    }
    case "sandbox.write_file":
      return path || "写入工作区文件";
    case "sandbox.apply_patch": {
      const patch = typeof args.patch === "string" ? args.patch : "";
      const first = patch.split("\n").map((line) => line.trim()).find(Boolean) || "";
      return first || "应用补丁";
    }
    case "sandbox.git_diff":
      return "生成变更差异";
    case "sandbox.workspace_status":
      return "查看工作区状态";
    case "run.read_artifact": {
      const id = typeof args.artifact_id === "string" ? args.artifact_id : "";
      return id ? `产物 ${id.slice(0, 24)}` : "读取运行产物";
    }
    case "agent.load_skill": {
      const skillName = typeof args.name === "string" ? args.name : "";
      return skillName || "加载技能";
    }
    case "agent.request_user_input": {
      const question = typeof args.question === "string" ? args.question : "";
      return question || "请求用户输入";
    }
    case "file_symbol_locator": {
      const query = typeof args.query === "string" ? args.query : "";
      return query || "定位符号";
    }
    default: {
      const entries = Object.entries(args || {}).filter(
        ([key]) => ![
          "max_results",
          "max_chars",
          "context_lines",
          "start_line",
          "end_line",
          "timeout_seconds",
          "cwd",
          "expected_sha256",
        ].includes(key),
      );
      return entries.slice(0, 2).map(([key, value]) => {
        if (typeof value === "string") {
          return value.length > 80 ? `${value.slice(0, 80)}…` : value;
        }
        return `${key}: ${JSON.stringify(value)}`;
      }).join(" · ") || "";
    }
  }
}

export function agentToolResultText(name, output = {}) {
  const duration = output.duration_ms !== undefined ? formatDuration(output.duration_ms) : "";
  if (output.error) {
    return [duration, String(output.error).slice(0, 200)].filter(Boolean).join(" · ");
  }
  const result = output.result;
  if (result && typeof result === "object") {
    const tail = duration ? ` · ${duration}` : "";
    switch (name) {
      case "repo.read_file": {
        const parts = [];
        if (result.path) parts.push(result.path);
        if (Number.isFinite(Number(result.chars))) parts.push(`${result.chars} 字符`);
        if (result.truncated) parts.push("已截断");
        return (parts.join(" · ") || "已读取文件") + tail;
      }
      case "repo.search_code": {
        const parts = [];
        if (result.query) parts.push(`“${result.query}”`);
        if (Number.isFinite(Number(result.count))) parts.push(`${result.count} 处匹配`);
        return (parts.join(" · ") || "搜索完成") + tail;
      }
      case "repo.find_files": {
        const parts = [];
        if (result.query) parts.push(`“${result.query}”`);
        if (Number.isFinite(Number(result.count))) parts.push(`${result.count} 个文件`);
        return (parts.join(" · ") || "查找完成") + tail;
      }
      case "repo.list_files": {
        const parts = [];
        if (result.path) parts.push(result.path);
        if (Number.isFinite(Number(result.count))) parts.push(`${result.count} 个文件`);
        return (parts.join(" · ") || "列出完成") + tail;
      }
      case "sandbox.run_command": {
        const command = Array.isArray(result.command)
          ? result.command.join(" ")
          : result.command || "";
        const exitCode = result.exit_code !== undefined ? `退出码 ${result.exit_code}` : "";
        const timedOut = result.timed_out ? "超时" : "";
        return [command, exitCode, timedOut].filter(Boolean).join(" · ") + tail || "命令已执行" + tail;
      }
      case "sandbox.write_file": {
        const parts = [];
        if (result.path) parts.push(result.path);
        if (Number.isFinite(Number(result.bytes))) parts.push(`${result.bytes} 字节`);
        return (parts.join(" · ") || "已写入文件") + tail;
      }
      case "sandbox.apply_patch": {
        const changed = Array.isArray(result.changed_files) ? result.changed_files.length : 0;
        return (changed ? `修改 ${changed} 个文件` : "已应用补丁") + tail;
      }
      case "sandbox.git_diff": {
        const changed = Array.isArray(result.changed_files) ? result.changed_files.length : 0;
        return (changed ? `${changed} 个文件变更` : "变更差异已生成") + tail;
      }
      case "sandbox.workspace_status": {
        const changed = Array.isArray(result.changed_files) ? result.changed_files.length : 0;
        return (changed ? `${changed} 个文件已变更` : "工作区状态已查看") + tail;
      }
      case "run.read_artifact": {
        const returned = Number(result.returned_chars);
        const total = Number(result.total_chars);
        const chars = Number.isFinite(total)
          ? `${Number.isFinite(returned) ? returned : total} / ${total} 字符`
          : `${Number.isFinite(returned) ? returned : ""} 字符`;
        return `读取产物 · ${chars}` + tail;
      }
      case "agent.load_skill":
        return `已加载 ${result.name || ""}` + tail;
      case "agent.request_user_input":
        return (typeof result.answer === "string" ? `已回答：${result.answer}` : "已提问") + tail;
      default: {
        const entries = Object.entries(result).filter(([key, value]) => {
          if ([
            "content",
            "stdout",
            "stderr",
            "diff",
            "patch",
            "instructions",
            "text",
            "workspace",
            "execution_root",
          ].includes(key)) return false;
          if (value == null) return false;
          if (typeof value === "object") {
            return Array.isArray(value) && value.length <= 5;
          }
          return String(value).length <= 120;
        });
        const rendered = entries.slice(0, 2).map(([key, value]) => (
          Array.isArray(value) ? `${key}: ${value.join(", ")}` : `${key}: ${value}`
        ));
        return (rendered.join(" · ") || "已完成") + tail;
      }
    }
  }
  return [duration, output.summary || ""].filter(Boolean).join(" · ") || "工具已返回结果";
}

export function agentContextInjectionTitle(node) {
  const titles = {
    load_project_instructions: "注入项目指令",
    classify_request: "识别任务意图",
    decide_context_source: "选择上下文来源",
    retrieve_knowledge: "注入知识库证据",
    retrieve_project_memory: "注入项目记忆",
    assess_context: "装配上下文",
    merge_evidence: "合并上下文证据",
    resolve_change_targets: "解析修改目标",
  };
  return titles[node] || humanizeAgentNode(node);
}

export function agentContextInjectionSummary(node, output = {}) {
  switch (node) {
    case "load_project_instructions": {
      const files = Array.isArray(output.files) ? output.files : [];
      const parts = [];
      if (files.length) {
        parts.push(`${files.length} 份项目指令`);
        parts.push(
          files.slice(0, 3).join("、") + (files.length > 3 ? ` 等 ${files.length} 个` : ""),
        );
      } else {
        parts.push("未加载项目指令");
      }
      if (Number.isFinite(Number(output.chars))) parts.push(`${output.chars} 字符`);
      return parts.join(" · ");
    }
    case "classify_request": {
      const parts = [];
      if (output.intent) parts.push(`意图 ${output.intent}`);
      if (output.proposed_context_route) parts.push(`路由 ${output.proposed_context_route}`);
      return parts.join(" · ") || "识别任务意图";
    }
    case "decide_context_source": {
      const parts = [];
      if (output.context_route) parts.push(`路由 ${output.context_route}`);
      const selected = Array.isArray(output.selected_knowledge_base_ids)
        ? output.selected_knowledge_base_ids
        : [];
      if (selected.length) parts.push(`知识库 ${selected.join("、")}`);
      return parts.join(" · ") || "选择上下文来源";
    }
    case "retrieve_knowledge": {
      const count = Number(output.source_count);
      const hits = output.hit_counts && typeof output.hit_counts === "object"
        ? output.hit_counts
        : {};
      const parts = [];
      if (Number.isFinite(count)) {
        parts.push(count ? `${count} 条知识库证据` : "未检索到知识库证据");
      }
      const hitParts = Object.entries(hits).map(([kb, n]) => `${kb}：${n} 条`);
      if (hitParts.length) parts.push(hitParts.join("，"));
      return parts.join(" · ") || "检索知识库";
    }
    case "retrieve_project_memory": {
      const count = Number(output.source_count);
      if (Number.isFinite(count)) return count ? `${count} 条项目记忆` : "未检索到项目记忆";
      return "检索项目记忆";
    }
    case "assess_context": {
      const parts = [];
      if (Number.isFinite(Number(output.file_count))) {
        parts.push(`${output.file_count} 个文件纳入上下文`);
      }
      if (Number.isFinite(Number(output.chars))) parts.push(`${output.chars} 字符`);
      if (output.budget_exhausted) parts.push("预算已用尽");
      return parts.join(" · ") || "评估上下文";
    }
    case "merge_evidence": {
      const parts = [];
      if (output.repo_source_count) parts.push(`工作区 ${output.repo_source_count} 份`);
      if (output.knowledge_source_count) parts.push(`知识库 ${output.knowledge_source_count} 份`);
      if (output.memory_source_count) parts.push(`记忆 ${output.memory_source_count} 份`);
      return parts.length ? `合并证据：${parts.join("、")}` : "合并证据";
    }
    case "resolve_change_targets": {
      const candidates = Array.isArray(output.candidate_paths)
        ? output.candidate_paths
        : [];
      const targets = Array.isArray(output.resolved_targets)
        ? output.resolved_targets
        : [];
      const status = output.status || "unresolved";
      return `目标${status} · ${candidates.length} 个候选 · ${targets.length} 个已解析`;
    }
    default:
      return output.summary || "";
  }
}

export function agentContextStageSummary(output = {}) {
  const labels = {
    result_externalized: "大结果已外置",
    snip: "已清理远古上下文",
    micro_compact: "已执行 Micro-Compact",
    auto_compact: "已自动压缩上下文",
    compaction_fallback: "已执行确定性压缩兜底",
    compaction_failed: "模型压缩失败，已安全降级",
  };
  const parts = [];
  if (output.stage) parts.push(labels[output.stage] || String(output.stage));
  const metrics = [];
  if (output.evicted) metrics.push(`驱逐 ${output.evicted}`);
  if (output.compacted) metrics.push(`压缩 ${output.compacted}`);
  if (output.dropped) metrics.push(`丢弃 ${output.dropped}`);
  if (output.truncated) metrics.push(`截断 ${output.truncated}`);
  if (metrics.length) parts.push(metrics.join("、"));
  if (Number.isFinite(Number(output.block_count)) && Number(output.block_count) > 0) {
    parts.push(`${output.block_count} 个块`);
  }
  if (Number.isFinite(Number(output.reclaimed_tokens)) && Number(output.reclaimed_tokens) > 0) {
    parts.push(`释放约 ${formatTokenCount(output.reclaimed_tokens)} Token`);
  }
  if (
    Number.isFinite(Number(output.before_tokens))
    && Number.isFinite(Number(output.after_tokens))
  ) {
    parts.push(`${formatTokenCount(output.before_tokens)} → ${formatTokenCount(output.after_tokens)} Token`);
  }
  if (Number.isFinite(Number(output.estimated_tokens))) {
    const budget = Number(output.budget_tokens);
    parts.push(
      budget > 0
        ? `≈ ${formatTokenCount(output.estimated_tokens)} / ${formatTokenCount(budget)} tokens`
        : `≈ ${formatTokenCount(output.estimated_tokens)} tokens`,
    );
  }
  return parts.join(" · ") || "上下文已调整";
}

export function agentActivityEventKey(event) {
  const output = event.output || {};
  if (["tool_selected", "tool_started"].includes(event.type)) {
    return `tool-invoke:${output.call_id || output.name || ""}`;
  }
  if (["tool_result", "tool_error"].includes(event.type)) {
    return `tool-result:${output.call_id || output.name || ""}`;
  }
  if (event.type === "node_started") {
    return `${event.type}:${event.node || ""}:${output.step || ""}`;
  }
  return `${event.type}:${event.sequence || ""}`;
}

export function executionActivityEvents(events) {
  const seen = new Set();
  return (events || [])
    .filter((event) => {
      if (event.type === "node_completed") {
        return AGENT_CONTEXT_ACTIVITY_NODES.has(event.node);
      }
      if (!AGENT_ACTIVITY_EVENT_TYPES.has(event.type)) return false;
      const key = agentActivityEventKey(event);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(-24);
}

export function agentTracePresentationEvents(trace = []) {
  return trace.flatMap((step, index) => {
    const type = step.type
      || (AGENT_PERSISTED_PRESENTATION_EVENT_TYPES.has(step.node) ? step.node : "");
    if (!type) return [];
    return [{
      sequence: Number(step.sequence || step.step) || index + 1,
      type,
      status: step.status || "completed",
      node: step.event_node || step.node || null,
      summary: step.summary || "",
      output: step.output || {},
    }];
  });
}

export function executionActiveActivitySequence(events, status = "running") {
  if (TERMINAL_RUN_STATUSES.has(status) || status === "done") return null;
  const values = events || [];
  const completedToolCalls = new Set(
    values
      .filter((event) => ["tool_result", "tool_error"].includes(event.type))
      .map((event) => event.output?.call_id)
      .filter(Boolean),
  );
  const activeTool = values.findLast((event) => (
    ["tool_selected", "tool_started"].includes(event.type)
    && (!event.output?.call_id || !completedToolCalls.has(event.output.call_id))
  ));
  if (activeTool) return activeTool.sequence;

  const activeNode = values.findLast((event) => {
    if (event.type !== "node_started") return false;
    return !values.some((candidate) => (
      candidate.type === "node_completed"
      && candidate.sequence > event.sequence
      && candidate.node === event.node
    ));
  });
  return activeNode?.sequence ?? null;
}

export function executionActivityTitle(event) {
  const output = event.output || {};
  if (event.type === "node_started") return `正在${humanizeAgentNode(event.node)}`;
  if (event.type === "node_completed") return agentContextInjectionTitle(event.node);
  if (event.type === "reasoning_summary") return "阶段思路";
  if (["tool_selected", "tool_started"].includes(event.type)) {
    return `调用工具 · ${agentToolLabel(output.name)}`;
  }
  if (event.type === "tool_result") return `工具完成 · ${agentToolLabel(output.name)}`;
  if (event.type === "tool_error") return `工具失败 · ${agentToolLabel(output.name)}`;
  if (event.type === "context") return "压缩上下文";
  if (event.type === "answer_completed") return "回答生成完成";
  return event.type || "实时活动";
}

export function executionActivitySummary(event) {
  const output = event.output || {};
  if (["tool_selected", "tool_started"].includes(event.type)) {
    return agentToolArgumentsText(output.name, output.arguments || output.arguments_summary || {});
  }
  if (["tool_result", "tool_error"].includes(event.type)) {
    return agentToolResultText(output.name, output);
  }
  if (event.type === "node_completed") {
    return agentContextInjectionSummary(event.node, output);
  }
  if (event.type === "context") {
    return agentContextStageSummary(output);
  }
  return event.summary || humanizeAgentNode(event.node);
}

export function executionProcessPresentation(status) {
  const normalized = status === "done" ? "completed" : status;
  const presentations = {
    completed: { title: "已工作", tone: "status-complete", stepState: "complete" },
    partial: { title: "部分完成", tone: "status-attention", stepState: "attention" },
    blocked: { title: "工作受阻", tone: "status-attention", stepState: "attention" },
    waiting_approval: { title: "等待审批", tone: "status-attention", stepState: "attention" },
    waiting_input: { title: "等待补充", tone: "status-attention", stepState: "attention" },
    paused: { title: "已暂停", tone: "status-attention", stepState: "attention" },
    failed: { title: "工作中断", tone: "status-failed", stepState: "failed" },
    cancelled: { title: "已停止", tone: "status-cancelled", stepState: "cancelled" },
  };
  return presentations[normalized] || {
    title: "正在工作",
    tone: "status-running",
    stepState: "current",
  };
}

export function executionStepState(index, length, presentation) {
  return index < length - 1 ? "complete" : presentation.stepState;
}

export function executionStepStateLabel(value) {
  return {
    complete: "已完成",
    current: "当前步骤",
    attention: "等待继续",
    failed: "失败",
    cancelled: "已停止",
  }[value] || "执行步骤";
}

export function ensureExecutionProcess(contentNode) {
  const bubble = contentNode.closest(".chat-bubble");
  let details = bubble.querySelector(".execution-process");
  if (!details) {
    details = document.createElement("details");
    details.className = "execution-process";
    details.open = true;
    details.innerHTML = `
      <summary>
        <span class="execution-summary-main">
          <span class="execution-indicator" aria-hidden="true"></span>
          <span class="execution-summary-copy">
            <span class="execution-summary-line">
              <strong class="execution-title">正在工作</strong>
              <span class="execution-duration">不到 1 秒</span>
            </span>
            <span class="execution-summary-text" role="status" aria-live="polite" aria-atomic="true">准备执行…</span>
          </span>
        </span>
        <span class="execution-chevron" aria-hidden="true"></span>
      </summary>
      <div class="execution-body">
        <ol class="execution-steps"></ol>
        <section class="execution-live-events" hidden>
          <span class="execution-live-events-label">实时活动</span>
          <ol></ol>
        </section>
        <p class="execution-live-announcer sr-only" role="status" aria-live="polite" aria-atomic="true"></p>
        <div class="execution-tools" hidden></div>
      </div>
    `;
    bubble.insertBefore(details, contentNode);
  }
  return details;
}

export function renderExecutionProcess(
  contentNode,
  {
    trace = [],
    status = "running",
    elapsedMs = 0,
    fallbackNode = "",
    fallbackSummary = "",
    events = [],
    activityOnly = false,
  } = {},
) {
  const details = ensureExecutionProcess(contentNode);
  const terminal = TERMINAL_RUN_STATUSES.has(status) || status === "done";
  const wasTerminal = details.dataset.terminal === "true";
  const presentation = executionProcessPresentation(status);
  const tools = traceToolNames(trace, events);
  const activities = executionActivityEvents(events);
  const activeActivitySequence = executionActiveActivitySequence(events, status);
  const steps = trace.length
    ? trace
    : terminal
    ? []
    : [{
        step: 1,
        node: fallbackNode || "model_request",
        summary: fallbackSummary || "正在建立请求并等待响应。",
        output: {},
      }];
  details.classList.remove(
    "status-running",
    "status-complete",
    "status-attention",
    "status-failed",
    "status-cancelled",
  );
  details.classList.add(presentation.tone);
  details.classList.toggle("activity-only", activityOnly);
  details.classList.toggle("complete", terminal);
  details.dataset.status = status === "done" ? "completed" : status;
  details.dataset.terminal = String(terminal);
  const displayedElapsedMs = Math.max(
    Number(details.dataset.elapsedMs || 0),
    Number(elapsedMs || 0),
  );
  details.dataset.elapsedMs = String(displayedElapsedMs);
  if (terminal && !wasTerminal) {
    details.open = false;
  } else if (!terminal && wasTerminal) {
    details.open = true;
  }
  details.querySelector(".execution-title").textContent = presentation.title;
  const terminalSummary = activityOnly
    ? `${activities.length} 条实时活动`
    : steps.length
    ? `${steps.length} 个步骤${tools.length ? ` · ${tools.length} 个工具` : ""}`
    : "没有阶段详情";
  const latestStep = steps.at(-1);
  const latestActivity = activities.at(-1);
  details.querySelector(".execution-summary-text").textContent = activityOnly
    ? latestActivity
      ? executionActivityTitle(latestActivity)
      : terminalSummary
    : terminal
    ? presentation.stepState === "complete"
      ? terminalSummary
      : `${latestStep ? humanizeAgentNode(latestStep.node) : terminalSummary}${
          steps.length ? ` · ${steps.length} 个步骤` : ""
        }${tools.length ? ` · ${tools.length} 个工具` : ""}`
    : humanizeAgentNode(latestStep?.node);
  details.querySelector(".execution-duration").textContent = formatWorkDuration(displayedElapsedMs);
  const stepList = details.querySelector(".execution-steps");
  stepList.hidden = activityOnly;
  stepList.innerHTML = activityOnly ? "" : steps
    .map((step, index) => {
      const stepState = executionStepState(index, steps.length, presentation);
      return `
      <li class="${escapeHtml(stepState)}"${stepState === "current" ? ' aria-current="step"' : ""}>
        <span class="execution-step-marker" aria-hidden="true"></span>
        <div>
          <strong><span class="sr-only">${escapeHtml(index + 1)}，${escapeHtml(
            executionStepStateLabel(stepState),
          )}：</span>${escapeHtml(humanizeAgentNode(step.node))}</strong>
          <p>${escapeHtml(step.summary || "")}</p>
        </div>
      </li>
    `;
    })
    .join("");
  if (!activityOnly && !steps.length) {
    stepList.innerHTML =
      '<li class="execution-step-empty">本次运行没有返回可解释的阶段详情。</li>';
  }
  const activityList = details.querySelector(".execution-live-events");
  activityList.hidden = activities.length === 0 && !activityOnly;
  const activityItems = activityList.querySelector("ol");
  const activitySignature = activities
    .map((event) => `${event.sequence}:${event.sequence === activeActivitySequence ? "active" : "idle"}`)
    .join("|");
  if (activityItems.dataset.signature !== activitySignature) {
    activityItems.dataset.signature = activitySignature;
    activityItems.innerHTML = activities.length ? activities.map((event) => {
      const active = event.sequence === activeActivitySequence;
      const isContext = event.type === "context"
        || (event.type === "node_completed" && AGENT_CONTEXT_ACTIVITY_NODES.has(event.node));
      const isTool = ["tool_selected", "tool_result", "tool_error"].includes(event.type);
      const classes = [
        event.type === "tool_error" ? "error" : "",
        active ? "active" : "",
        isContext ? "context" : "",
        isTool ? "tool" : "",
      ].filter(Boolean).join(" ");
      return `
    <li class="${escapeHtml(classes)}"${active ? ' aria-current="true"' : ""}>
      <span class="execution-live-marker" aria-hidden="true"></span>
      <div>
        <strong>${escapeHtml(executionActivityTitle(event))}</strong>
        <p>${escapeHtml(executionActivitySummary(event))}</p>
      </div>
    </li>
  `;
    }).join("") : '<li class="empty"><p>等待首个实时活动…</p></li>';
  }
  const announcer = details.querySelector(".execution-live-announcer");
  const activitySequence = String(latestActivity?.sequence || "");
  if (announcer.dataset.sequence !== activitySequence) {
    announcer.dataset.sequence = activitySequence;
    announcer.textContent = latestActivity
      ? `${executionActivityTitle(latestActivity)}：${executionActivitySummary(latestActivity)}`
      : "";
  }
  const toolList = details.querySelector(".execution-tools");
  toolList.hidden = activityOnly || tools.length === 0;
  toolList.innerHTML = !activityOnly && tools.length
    ? `<span class="execution-tools-label"><svg class="app-icon" aria-hidden="true"><use href="#icon-network"></use></svg><span>相关工具</span></span><span class="execution-tool-list">${tools
        .map((name) => `<code>${escapeHtml(name)}</code>`)
        .join("")}</span>`
    : "";
  return details;
}

export function startResponseTimer(contentNode, startedAt) {
  stopResponseTimer(contentNode);
  const timer = managedSetInterval(() => {
    const duration = contentNode.closest(".chat-bubble")
      ?.querySelector(".execution-duration");
    if (duration) {
      const elapsedMs = performance.now() - startedAt;
      duration.textContent = formatWorkDuration(elapsedMs);
      duration.closest(".execution-process").dataset.elapsedMs = String(elapsedMs);
    }
  }, 200);
  responseTimers.set(contentNode, timer);
}

export function stopResponseTimer(contentNode) {
  const timer = responseTimers.get(contentNode);
  if (timer) {
    window.clearInterval(timer);
    responseTimers.delete(contentNode);
  }
}

export function renderResponseMetrics(contentNode, metrics) {
  const bubble = contentNode.closest(".chat-bubble");
  let footer = bubble.querySelector(".response-metrics");
  if (!footer) {
    footer = document.createElement("details");
    footer.className = "response-metrics";
    bubble.appendChild(footer);
  }
  const values = [];
  if (metrics.elapsed_ms !== undefined) {
    values.push(["耗时", formatDuration(metrics.elapsed_ms)]);
  }
  if (metrics.input_tokens !== undefined) {
    values.push(["累计输入", `${formatTokenCount(metrics.input_tokens)} tokens`]);
  }
  if (metrics.cached_input_tokens !== undefined && metrics.cached_input_tokens !== null) {
    values.push(["缓存命中输入（仍计入累计）", `${formatTokenCount(metrics.cached_input_tokens)} tokens`]);
  }
  if (metrics.uncached_input_tokens !== undefined && metrics.uncached_input_tokens !== null) {
    values.push(["未缓存输入", `${formatTokenCount(metrics.uncached_input_tokens)} tokens`]);
  }
  if (metrics.cache_write_tokens !== undefined && metrics.cache_write_tokens !== null) {
    values.push(["缓存写入", `${formatTokenCount(metrics.cache_write_tokens)} tokens`]);
  }
  if (metrics.prompt_cache_hit_ratio !== undefined && metrics.prompt_cache_hit_ratio !== null) {
    values.push(["缓存命中率", `${(metrics.prompt_cache_hit_ratio * 100).toFixed(1)}%`]);
  }
  if (metrics.retained_context_tokens_estimate !== undefined) {
    values.push(["当前保留上下文（估算）", `≈ ${formatTokenCount(metrics.retained_context_tokens_estimate)} tokens`]);
  }
  if (metrics.stable_prefix_tokens !== undefined) {
    values.push(["稳定指令前缀（估算）", `≈ ${formatTokenCount(metrics.stable_prefix_tokens)} tokens`]);
  }
  if (metrics.tool_schema_tokens !== undefined) {
    values.push(["工具定义（估算）", `≈ ${formatTokenCount(metrics.tool_schema_tokens)} tokens`]);
  }
  if (metrics.visible_tool_count !== undefined) {
    values.push(["模型可见工具", formatTokenCount(metrics.visible_tool_count)]);
  }
  if (metrics.provider || metrics.model) {
    values.push(["Provider / Model", [metrics.provider, metrics.model].filter(Boolean).join(" / ")]);
  }
  if (metrics.cache_capability) {
    const cacheCapabilityLabels = {
      unsupported: "不支持 / 未报告",
      implicit_kv: "隐式 KV Cache",
      explicit_key: "显式 Cache Key",
      "explicit_key+breakpoint": "Cache Key + 显式断点",
      explicit_breakpoint: "显式缓存断点",
      mixed: "混合能力",
    };
    values.push(["缓存能力", cacheCapabilityLabels[metrics.cache_capability] || metrics.cache_capability]);
  }
  if (metrics.output_tokens !== undefined) {
    values.push(["输出", `${formatTokenCount(metrics.output_tokens)} tokens`]);
  }
  if (metrics.thoughts_tokens) {
    values.push(["思考", `${formatTokenCount(metrics.thoughts_tokens)} tokens`]);
  }
  if (metrics.total_tokens !== undefined) {
    values.push(["Provider 合计", `${formatTokenCount(metrics.total_tokens)} tokens`]);
  }
  if (metrics.node_count !== undefined) {
    values.push(["过程记录", formatTokenCount(metrics.node_count)]);
  }
  if (metrics.tool_call_count !== undefined) {
    values.push(["工具", formatTokenCount(metrics.tool_call_count)]);
  }
  if (metrics.model_request_count !== undefined) {
    values.push(["模型请求", formatTokenCount(metrics.model_request_count)]);
  }
  if (metrics.model_retry_count !== undefined) {
    values.push(["模型重试", formatTokenCount(metrics.model_retry_count)]);
  }
  if (metrics.retry_count !== undefined) {
    values.push(["总重试", formatTokenCount(metrics.retry_count)]);
  }
  footer.innerHTML = '<summary>用量与运行统计</summary>' + values
    .map(([label, value]) => `<span><small>${escapeHtml(label)}</small>${escapeHtml(value)}</span>`)
    .join("");
  footer.hidden = values.length === 0;
}
