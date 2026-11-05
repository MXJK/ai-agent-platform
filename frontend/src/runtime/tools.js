import { renderSkills, renderMcpServers } from '../features/registry-lists';
import { state } from './state.js';
import { $, escapeHtml, preferredScrollBehavior } from './format.js';
import { humanizeError, setRaw, showToast } from './feedback.js';
import { invalidateSlashCapabilities } from './composer.js';
import { fetchJson } from './http.js';

export function skillByName(name) {
  return state.skillRegistry.skills.find((item) => item.name === name) || null;
}

export function renderSkillRegistry() {
  const registry = state.skillRegistry;
  const skills = registry.skills || [];
  $("skill-registry-root").textContent = registry.root || "~/.cogent/skills";
  $("skill-list-count").textContent = String(skills.length);
  const note = $("skill-runtime-note");
  const errors = (registry.diagnostics || []).filter((item) => item.severity === "error");
  if (!registry.writable) {
    note.hidden = false;
    note.className = "mcp-runtime-note error";
    note.textContent = "全局 Skill 目录不可写，当前只能查看已发现项。";
  } else if (errors.length) {
    note.hidden = false;
    note.className = "mcp-runtime-note";
    note.textContent = `有 ${errors.length} 个 Skill 未通过校验，请检查路径或 SKILL.md。`;
  } else {
    note.hidden = true;
    note.textContent = "";
  }
  $("save-skill-btn").disabled = !registry.writable;
  renderSkills(skills);
}

export async function loadSkillRegistry(showRaw = false) {
  const body = await fetchJson("/skills");
  state.skillRegistry = body;
  invalidateSlashCapabilities();
  renderSkillRegistry();
  if (showRaw) setRaw(body);
  return body;
}

export function resetSkillForm() {
  state.editingSkill = "";
  $("skill-form").reset();
  $("skill-name-input").readOnly = false;
  $("skill-form-title").textContent = "添加 Skill";
  $("skill-form-hint").textContent = "保存后无需重启，所有 Workspace 立即使用同一份注册表。";
  $("save-skill-btn").textContent = "保存 Skill";
  $("skill-enabled-input").checked = true;
}

export function editSkill(name) {
  const skill = skillByName(name);
  if (!skill?.editable) return;
  state.editingSkill = name;
  $("skill-name-input").value = skill.name;
  $("skill-name-input").readOnly = true;
  $("skill-content-input").value = skill.content || "";
  $("skill-enabled-input").checked = skill.enabled;
  $("skill-form-title").textContent = `编辑 ${skill.name}`;
  $("skill-form-hint").textContent = "名称必须与 Frontmatter 的 name 一致。";
  $("save-skill-btn").textContent = "更新 Skill";
  $("skill-form").scrollIntoView({ behavior: preferredScrollBehavior(), block: "start" });
  $("skill-content-input").focus({ preventScroll: true });
}

export async function saveSkill(event) {
  event.preventDefault();
  const form = $("skill-form");
  if (!form.reportValidity()) return;
  const name = $("skill-name-input").value.trim();
  const button = $("save-skill-btn");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await fetchJson(`/skills/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({
        content: $("skill-content-input").value,
        enabled: $("skill-enabled-input").checked,
      }),
    });
    await loadSkillRegistry();
    resetSkillForm();
    showToast(`${name} 已保存并同步到所有 Workspace`, "success");
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    button.disabled = !state.skillRegistry.writable;
    button.removeAttribute("aria-busy");
  }
}

export async function handleSkillAction(button) {
  const card = button.closest("[data-skill-name]");
  const name = card?.dataset.skillName;
  const action = button.dataset.skillAction;
  const skill = skillByName(name);
  if (!skill?.editable) return;
  if (action === "edit") {
    editSkill(name);
    return;
  }
  if (action === "delete" && !window.confirm(`确认删除全局 Skill“${name}”？`)) return;
  button.disabled = true;
  try {
    if (action === "toggle") {
      await fetchJson(`/skills/${encodeURIComponent(name)}/enabled`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !skill.enabled }),
      });
      showToast(skill.enabled ? `${name} 已停用` : `${name} 已启用`);
    } else if (action === "delete") {
      await fetchJson(`/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (state.editingSkill === name) resetSkillForm();
      showToast(`${name} 已删除`);
    }
    await loadSkillRegistry(true);
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    button.disabled = false;
  }
}

export const MCP_STATUS_LABELS = {
  ready: "就绪",
  connecting: "连接中",
  degraded: "异常",
  unavailable: "不可用",
  circuit_open: "熔断中",
  disabled: "已停用",
  restart_required: "等待重启",
  closed: "已关闭",
};

export function mcpStatusLabel(value) {
  return MCP_STATUS_LABELS[value] || value || "未知";
}

export function mcpStatusClass(value) {
  if (value === "ready") return "ok";
  if (value === "connecting") return "running";
  if (["degraded", "circuit_open", "restart_required"].includes(value)) return "warning";
  if (value === "unavailable") return "error";
  return "neutral";
}

export function mcpCardState(value) {
  return new Set([
    "ready",
    "connecting",
    "degraded",
    "unavailable",
    "circuit_open",
    "disabled",
    "restart_required",
  ]).has(value) ? value : "closed";
}

export function mcpTransportLabel(value) {
  return {
    stdio: "stdio · 当前协议",
    streamable_http: "Streamable HTTP",
    stdio_2025_06_18: "stdio · 2025-06-18",
    legacy_sse: "HTTP+SSE · 兼容层",
  }[value] || value;
}

export function mcpServerByName(name) {
  return state.mcpRegistry.servers.find((item) => item.name === name) || null;
}

export function assignmentText(values) {
  return Object.entries(values || {})
    .sort(([left], [right]) => left.localeCompare(right, "en"))
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

export function parseAssignments(value, label, { secret = false, existingNames = [] } = {}) {
  const values = {};
  const names = new Set();
  const existing = new Set(existingNames);
  for (const [index, rawLine] of String(value || "").split(/\r?\n/).entries()) {
    if (!rawLine.trim()) continue;
    const separator = rawLine.indexOf("=");
    if (separator < 1) {
      throw new Error(`${label}第 ${index + 1} 行必须使用 KEY=value`);
    }
    const key = rawLine.slice(0, separator).trim();
    const item = rawLine.slice(separator + 1).trim();
    if (!key) throw new Error(`${label}第 ${index + 1} 行缺少名称`);
    if (names.has(key)) throw new Error(`${label}包含重复名称：${key}`);
    names.add(key);
    if (secret && item === "") {
      if (!existing.has(key)) throw new Error(`${label}${key} 缺少凭据值`);
      continue;
    }
    values[key] = item;
  }
  return { values, names };
}

export function mcpNumberValue(id, fallback) {
  const value = Number($(id).value);
  return Number.isFinite(value) ? value : fallback;
}

export function updateMCPTransportFields() {
  const transport = $("mcp-transport-input").value;
  const stdio = transport === "stdio" || transport === "stdio_2025_06_18";
  $("mcp-stdio-fields").hidden = !stdio;
  $("mcp-http-fields").hidden = stdio;
  $("mcp-command-input").required = stdio;
  $("mcp-url-input").required = !stdio;
}

export function renderMCPRegistry() {
  const registry = state.mcpRegistry;
  const servers = registry.servers || [];
  const ready = servers.filter((item) => item.state === "ready");
  const registeredTools = new Set(servers.flatMap((item) => item.registered_tools || []));
  $("mcp-server-count").textContent = String(servers.length);
  $("mcp-ready-count").textContent = String(ready.length);
  $("mcp-tool-count").textContent = String(registeredTools.size);
  $("mcp-list-count").textContent = String(servers.length);

  const note = $("mcp-runtime-note");
  if (!registry.config_writable) {
    note.hidden = false;
    note.className = "mcp-runtime-note error";
    note.textContent = "当前没有配置 MCP_CONFIG_PATH，设置可写配置路径后才能从界面注册 Server。";
  } else if (!registry.runtime_enabled) {
    note.hidden = false;
    note.className = "mcp-runtime-note";
    note.textContent = "配置可以保存，但 MCP 运行时未启用。设置 MCP_ENABLED=true 并重启后，Server 会自动连接。";
  } else {
    note.hidden = true;
    note.textContent = "";
  }
  $("save-mcp-server-btn").disabled = !registry.config_writable;

  renderMcpServers(servers, registry.runtime_enabled);
}

export async function loadMCPRegistry(showRaw = false) {
  const body = await fetchJson("/mcp/servers");
  state.mcpRegistry = body;
  invalidateSlashCapabilities();
  renderMCPRegistry();
  if (showRaw) setRaw(body);
  return body;
}

export function resetMCPServerForm() {
  state.editingMCPServer = "";
  $("mcp-server-form").reset();
  $("mcp-name-input").readOnly = false;
  $("mcp-form-title").textContent = "添加 Server";
  $("mcp-form-hint").textContent = "保存后立即连接并发现工具；连接失败不会阻止其他 Server。";
  $("save-mcp-server-btn").textContent = "保存并连接";
  updateMCPTransportFields();
  renderMCPRegistry();
}

export function editMCPServer(name) {
  const server = mcpServerByName(name);
  if (!server) return;
  state.editingMCPServer = name;
  $("mcp-name-input").value = server.name;
  $("mcp-name-input").readOnly = true;
  $("mcp-transport-input").value = server.transport;
  updateMCPTransportFields();
  $("mcp-command-input").value = server.command || "";
  $("mcp-args-input").value = (server.args || []).join("\n");
  $("mcp-env-input").value = assignmentText(server.env);
  $("mcp-env-secrets-input").value = (server.env_secret_names || []).map((key) => `${key}=`).join("\n");
  $("mcp-url-input").value = server.url || "";
  $("mcp-allowed-hosts-input").value = (server.allowed_hosts || []).join("\n");
  $("mcp-headers-input").value = assignmentText(server.headers);
  $("mcp-header-secrets-input").value = (server.header_secret_names || []).map((key) => `${key}=`).join("\n");
  $("mcp-enabled-input").checked = server.enabled;
  $("mcp-required-input").checked = server.required;
  $("mcp-connect-timeout-input").value = server.connect_timeout_seconds;
  $("mcp-request-timeout-input").value = server.request_timeout_seconds;
  $("mcp-max-retries-input").value = server.max_retries;
  $("mcp-cache-ttl-input").value = server.tool_cache_ttl_seconds;
  $("mcp-insecure-http-input").checked = server.allow_insecure_http;
  $("mcp-private-network-input").checked = server.allow_private_network;
  $("mcp-legacy-input").checked = server.legacy_compatibility;
  $("mcp-form-title").textContent = `编辑 ${server.name}`;
  $("mcp-form-hint").textContent = "留空已有 Secret 的值即可沿用；删除整行会移除该凭据。";
  $("save-mcp-server-btn").textContent = "更新并重连";
  $("mcp-server-form").scrollIntoView({ behavior: preferredScrollBehavior(), block: "start" });
  $(server.command ? "mcp-command-input" : "mcp-url-input").focus({ preventScroll: true });
}

export function mcpPayloadFromForm() {
  const name = $("mcp-name-input").value.trim();
  const transport = $("mcp-transport-input").value;
  const stdio = transport === "stdio" || transport === "stdio_2025_06_18";
  const existing = mcpServerByName(state.editingMCPServer || name);
  const env = parseAssignments($("mcp-env-input").value, "环境变量");
  const envSecrets = parseAssignments($("mcp-env-secrets-input").value, "Secret 环境变量", {
    secret: true,
    existingNames: existing?.env_secret_names || [],
  });
  const headers = parseAssignments($("mcp-headers-input").value, "Header");
  const headerSecrets = parseAssignments($("mcp-header-secrets-input").value, "Secret Header", {
    secret: true,
    existingNames: existing?.header_secret_names || [],
  });
  let allowedHosts = $("mcp-allowed-hosts-input").value
    .split(/[\n,]/)
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
  const url = $("mcp-url-input").value.trim();
  if (!stdio && !allowedHosts.length && url) {
    try {
      allowedHosts = [new URL(url).hostname.toLowerCase()];
    } catch {
      // Native form validation and the API provide the precise URL error.
    }
  }
  const legacyTransport = transport === "stdio_2025_06_18" || transport === "legacy_sse";
  return {
    name,
    payload: {
      transport,
      command: stdio ? $("mcp-command-input").value.trim() || null : null,
      args: stdio ? $("mcp-args-input").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean) : [],
      env: stdio ? env.values : {},
      env_secrets: stdio ? envSecrets.values : {},
      remove_env_secrets: existing
        ? (existing.env_secret_names || []).filter((key) => !stdio || !envSecrets.names.has(key))
        : [],
      url: stdio ? null : url || null,
      headers: stdio ? {} : headers.values,
      header_secrets: stdio ? {} : headerSecrets.values,
      remove_header_secrets: existing
        ? (existing.header_secret_names || []).filter((key) => stdio || !headerSecrets.names.has(key))
        : [],
      allowed_hosts: stdio ? [] : [...new Set(allowedHosts)],
      allow_insecure_http: !stdio && $("mcp-insecure-http-input").checked,
      allow_private_network: !stdio && $("mcp-private-network-input").checked,
      legacy_compatibility: legacyTransport || $("mcp-legacy-input").checked,
      required: $("mcp-required-input").checked,
      enabled: $("mcp-enabled-input").checked,
      connect_timeout_seconds: mcpNumberValue("mcp-connect-timeout-input", 10),
      request_timeout_seconds: mcpNumberValue("mcp-request-timeout-input", 10),
      max_retries: mcpNumberValue("mcp-max-retries-input", 1),
      retry_backoff_seconds: existing?.retry_backoff_seconds ?? 0.1,
      circuit_failure_threshold: existing?.circuit_failure_threshold ?? 3,
      circuit_reset_seconds: existing?.circuit_reset_seconds ?? 30,
      tool_cache_ttl_seconds: mcpNumberValue("mcp-cache-ttl-input", 30),
    },
  };
}

export async function saveMCPServer(event) {
  event.preventDefault();
  const form = $("mcp-server-form");
  if (!form.reportValidity()) return;
  const button = $("save-mcp-server-btn");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    const { name, payload } = mcpPayloadFromForm();
    const result = await fetchJson(`/mcp/servers/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    await loadMCPRegistry();
    resetMCPServerForm();
    showToast(result.state === "ready"
      ? `${name} 已连接并完成工具发现`
      : `${name} 已保存 · ${mcpStatusLabel(result.state)}`,
    result.state === "ready" ? "success" : "warning");
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    button.disabled = !state.mcpRegistry.config_writable;
    button.removeAttribute("aria-busy");
  }
}

export async function handleMCPServerAction(button) {
  const card = button.closest("[data-mcp-server]");
  const name = card?.dataset.mcpServer;
  const action = button.dataset.mcpAction;
  const server = mcpServerByName(name);
  if (!server) return;
  if (action === "edit") {
    editMCPServer(name);
    return;
  }
  if (action === "delete" && !window.confirm(`确认删除 MCP Server“${name}”？对应的托管凭据也会删除。`)) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    if (action === "test") {
      const result = await fetchJson(`/mcp/servers/${encodeURIComponent(name)}/test`, { method: "POST" });
      showToast(result.state === "ready" ? `${name} 连接与工具刷新成功` : `${name} · ${mcpStatusLabel(result.state)}`,
        result.state === "ready" ? "success" : "warning");
    } else if (action === "toggle") {
      await fetchJson(`/mcp/servers/${encodeURIComponent(name)}/enabled`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !server.enabled }),
      });
      showToast(server.enabled ? `${name} 已停用并关闭` : `${name} 已启用`);
    } else if (action === "delete") {
      await fetchJson(`/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (state.editingMCPServer === name) resetMCPServerForm();
      showToast(`${name} 已删除`);
    }
    await loadMCPRegistry(true);
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}
