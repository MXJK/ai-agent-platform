const API_BASE = "/api/v1";

const state = {
  conversationId: "",
  latestRunId: "",
  latestRunStatus: "",
};

const $ = (id) => document.getElementById(id);

function jsonPretty(value) {
  return JSON.stringify(value, null, 2);
}

function setRaw(value) {
  $("raw-output").textContent =
    typeof value === "string" ? value : jsonPretty(value);
}

function setTrace(items) {
  const traceList = $("trace-list");
  traceList.innerHTML = "";
  if (!items || items.length === 0) {
    return;
  }
  for (const item of items) {
    const node = document.createElement("div");
    node.className = "trace-item";
    node.innerHTML = `
      <strong>${item.step ?? ""}. ${escapeHtml(item.node ?? "step")}</strong>
      <span>${escapeHtml(item.summary ?? "")}</span>
    `;
    traceList.appendChild(node);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function csvValues(value) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function optionalModelFields() {
  const provider = $("provider-input").value.trim();
  const model = $("model-input").value.trim();
  return {
    ...(provider ? { provider } : {}),
    ...(model ? { model } : {}),
  };
}

async function fetchJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!response.ok) {
    const detail = body && body.detail ? body.detail : response.statusText;
    throw new Error(`${response.status} ${detail}`);
  }
  return body;
}

async function checkHealth() {
  const pill = $("health-pill");
  try {
    const body = await fetchJson("/health");
    pill.textContent = body.status;
    pill.className = "pill ok";
  } catch (error) {
    pill.textContent = "offline";
    pill.className = "pill error";
  }
}

async function ensureSession() {
  const existing = $("conversation-id-input").value.trim();
  if (existing) {
    state.conversationId = existing;
    return existing;
  }
  return createSession();
}

async function createSession() {
  $("session-status").textContent = "Creating session...";
  const body = await fetchJson("/sessions", {
    method: "POST",
    body: JSON.stringify({
      user_id: $("user-id-input").value.trim() || "demo_user",
    }),
  });
  state.conversationId = body.id;
  $("conversation-id-input").value = body.id;
  $("session-status").textContent = `Active: ${body.id}`;
  setRaw(body);
  return body.id;
}

async function streamChat() {
  const button = $("send-chat-btn");
  const output = $("chat-output");
  const meta = $("chat-meta");
  button.disabled = true;
  output.textContent = "";
  meta.textContent = "Streaming...";
  setTrace([]);
  try {
    const conversationId = await ensureSession();
    const payload = {
      conversation_id: conversationId,
      message: $("chat-message-input").value.trim(),
      ...optionalModelFields(),
    };
    const events = [];
    await postSse("/chat/stream", payload, (eventName, data) => {
      events.push({ event: eventName, data });
      setRaw(events);
      if (eventName === "meta") {
        meta.textContent = `${data.provider} / ${data.model}`;
      } else if (eventName === "delta") {
        output.textContent += data.text || "";
      } else if (eventName === "usage") {
        meta.textContent = `tokens ${data.total_tokens}`;
      } else if (eventName === "done") {
        meta.textContent = `done in ${data.elapsed_ms} ms`;
      } else if (eventName === "error") {
        meta.textContent = data.code || "error";
        output.textContent += `\n[error] ${data.message}`;
      }
    });
  } catch (error) {
    meta.textContent = "Error";
    output.textContent = error.message;
    setRaw({ error: error.message });
  } finally {
    button.disabled = false;
  }
}

async function postSse(path, payload, onEvent) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) {
    const text = await response.text();
    throw new Error(`${response.status} ${text || response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const parsed = parseSseBlock(part);
      if (parsed) {
        onEvent(parsed.event, parsed.data);
      }
    }
  }
  if (buffer.trim()) {
    const parsed = parseSseBlock(buffer);
    if (parsed) {
      onEvent(parsed.event, parsed.data);
    }
  }
}

function parseSseBlock(block) {
  const lines = block.split("\n");
  let event = "message";
  const dataLines = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  const rawData = dataLines.join("\n");
  let data = rawData;
  try {
    data = JSON.parse(rawData);
  } catch {
    data = { text: rawData };
  }
  return { event, data };
}

async function runAgent() {
  const button = $("run-agent-btn");
  const status = $("agent-status");
  const answer = $("agent-answer");
  button.disabled = true;
  status.textContent = "Running...";
  answer.textContent = "";
  try {
    const conversationId = await ensureSession();
    const payload = {
      conversation_id: conversationId,
      message: $("agent-message-input").value.trim(),
      repository_id: $("repository-id-input").value.trim() || "repo_main",
      focus_files: csvValues($("focus-files-input").value),
    };
    const body = await fetchJson("/agent/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderAgentRun(body);
  } catch (error) {
    status.textContent = "Error";
    answer.textContent = error.message;
    setRaw({ error: error.message });
  } finally {
    button.disabled = false;
  }
}

function renderAgentRun(body) {
  state.latestRunId = body.run_id || "";
  state.latestRunStatus = body.status || "";
  $("agent-status").textContent = `${body.status || "unknown"} ${body.run_id || ""}`;
  $("agent-answer").textContent =
    body.answer ||
    (body.pending_approval
      ? `Waiting approval for: ${body.pending_approval.planned_tools.join(", ")}`
      : "No answer");
  $("approve-run-btn").disabled = body.status !== "waiting_approval";
  $("reject-run-btn").disabled = body.status !== "waiting_approval";
  setTrace(body.trace || []);
  setRaw(body);
}

async function refreshRun() {
  if (!state.latestRunId) {
    $("agent-status").textContent = "No run";
    return;
  }
  const body = await fetchJson(`/agent/runs/${state.latestRunId}`);
  renderAgentRun(body.result || body);
}

async function resumeRun(approved) {
  if (!state.latestRunId) {
    return;
  }
  $("agent-status").textContent = approved ? "Approving..." : "Rejecting...";
  const body = await fetchJson(`/agent/runs/${state.latestRunId}/resume`, {
    method: "POST",
    body: JSON.stringify({
      approved,
      feedback: approved ? "前端调试台批准执行" : "前端调试台拒绝执行",
    }),
  });
  renderAgentRun(body);
}

async function ingestDocument() {
  const status = $("rag-status");
  status.textContent = "Ingesting...";
  try {
    const kbId = $("kb-id-input").value.trim() || "demo_kb";
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(kbId)}/documents`, {
      method: "POST",
      body: JSON.stringify({
        filename: $("document-filename-input").value.trim() || "notes.md",
        content: $("document-content-input").value,
      }),
    });
    status.textContent = `ingested ${body.chunk_count} chunks`;
    setRaw(body);
  } catch (error) {
    status.textContent = "Error";
    $("rag-answer").textContent = error.message;
    setRaw({ error: error.message });
  }
}

async function askRag() {
  const status = $("rag-status");
  const answer = $("rag-answer");
  status.textContent = "Asking...";
  answer.textContent = "";
  try {
    const kbId = $("kb-id-input").value.trim() || "demo_kb";
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(kbId)}/ask`, {
      method: "POST",
      body: JSON.stringify({
        question: $("rag-question-input").value.trim(),
        limit: 5,
        recall_limit: 10,
        ...optionalModelFields(),
      }),
    });
    status.textContent = `citations ${body.citations.length}`;
    answer.textContent = renderRagAnswer(body);
    setRaw(body);
  } catch (error) {
    status.textContent = "Error";
    answer.textContent = error.message;
    setRaw({ error: error.message });
  }
}

function renderRagAnswer(body) {
  const citations = body.citations
    .map((item, index) => {
      return `[${index + 1}] ${item.filename} #${item.chunk_index}\n${item.text}`;
    })
    .join("\n\n");
  return `${body.answer}\n\nCitations\n${citations || "None"}`;
}

async function indexRepository() {
  const status = $("session-status");
  const repositoryId = $("repository-id-input").value.trim() || "repo_main";
  const rootPath = $("repository-root-input").value.trim();
  if (!rootPath) {
    status.textContent = "Fill repository root path first";
    return;
  }
  status.textContent = "Indexing repository...";
  try {
    const body = await fetchJson(`/repositories/${encodeURIComponent(repositoryId)}/index`, {
      method: "POST",
      body: JSON.stringify({
        root_path: rootPath,
        include_patterns: csvValues($("include-patterns-input").value),
        max_file_size: 200000,
      }),
    });
    status.textContent = `indexed ${body.indexed_files}, skipped ${body.skipped_files}`;
    setRaw(body);
  } catch (error) {
    status.textContent = "Index failed";
    setRaw({ error: error.message });
  }
}

function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `${tabName}-tab`);
  });
}

function bindEvents() {
  $("create-session-btn").addEventListener("click", createSession);
  $("send-chat-btn").addEventListener("click", streamChat);
  $("run-agent-btn").addEventListener("click", runAgent);
  $("refresh-run-btn").addEventListener("click", refreshRun);
  $("approve-run-btn").addEventListener("click", () => resumeRun(true));
  $("reject-run-btn").addEventListener("click", () => resumeRun(false));
  $("ingest-doc-btn").addEventListener("click", ingestDocument);
  $("ask-rag-btn").addEventListener("click", askRag);
  $("index-repo-btn").addEventListener("click", indexRepository);
  $("clear-chat-btn").addEventListener("click", () => {
    $("chat-output").textContent = "";
    $("chat-meta").textContent = "Idle";
  });
  $("clear-detail-btn").addEventListener("click", () => {
    setTrace([]);
    setRaw("");
  });
  $("conversation-id-input").addEventListener("input", (event) => {
    state.conversationId = event.target.value.trim();
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });
}

bindEvents();
checkHealth();
