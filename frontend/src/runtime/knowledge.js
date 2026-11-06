import { renderKnowledgeList, renderDocumentRows } from '../features/knowledge-lists';
import { state } from './state.js';
import { $, csvValues, escapeHtml, formatDate, formatDuration, numberValue, optionalModelFields, renderMarkdown } from './format.js';
import { humanizeError, setRaw, showToast, truncate } from './feedback.js';
import { saveUiPreferences } from './composer.js';
import { fetchJson } from './http.js';
import { ensureSession } from './sessions.js';
import { activeWorkspaceId } from './memory.js';

export async function ingestDocument() {
  const input = $("document-files-input");
  const files = Array.from(input.files || []);
  if (!files.length) {
    showToast("请先选择需要录入的文件", "warning");
    return;
  }

  const kbId = $("kb-id-input").value.trim();
  if (!kbId) {
    showToast("请先创建并选择知识库", "warning");
    return;
  }

  const ingested = [];
  const failures = [];
  $("ingest-doc-btn").disabled = true;
  $("document-files-input").disabled = true;
  try {
    for (const [index, file] of files.entries()) {
      updateUploadQueueItem(index, "uploading", `正在上传 ${index + 1}/${files.length}`);
      $("rag-status").textContent = `正在上传 ${index + 1}/${files.length}…`;
      const form = new FormData();
      form.append("file", file, file.name);
      try {
        let body;
        try {
          body = await fetchJson(
            `/knowledge-bases/${encodeURIComponent(kbId)}/documents`,
            { method: "POST", body: form },
          );
        } catch (error) {
          const detail = error.body?.detail;
          if (
            error.status !== 409
            || detail?.code !== "document_filename_conflict"
          ) {
            throw error;
          }
          updateUploadQueueItem(index, "conflict", "发现同名文档，等待确认");
          const shouldReplace = window.confirm(
            `知识库中已有 ${file.name}。是否替换原文档并重建索引？`,
          );
          if (!shouldReplace) {
            failures.push({ filename: file.name, error: "用户取消替换" });
            updateUploadQueueItem(index, "conflict", "已保留原文档");
            continue;
          }
          const replacement = new FormData();
          replacement.append("file", file, file.name);
          body = await fetchJson(
            `/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(detail.existing_document_id)}/content`,
            { method: "PUT", body: replacement },
          );
        }
        ingested.push(body);
        updateUploadQueueItem(index, "success", `可用 · ${body.chunk_count} 个分块`);
      } catch (error) {
        failures.push({ filename: file.name, error: humanizeError(error) });
        updateUploadQueueItem(index, "failed", humanizeError(error));
      }
    }
    await Promise.all([listKnowledgeBases({ preserveSelection: kbId }), listKnowledgeDocuments()]);
    const chunkCount = ingested.reduce((total, item) => total + item.chunk_count, 0);
    $("rag-status").textContent = failures.length
      ? `已处理 ${ingested.length}，未完成 ${failures.length}`
      : `已建立 ${ingested.length} 个文档索引`;
    setRaw({ documents: ingested, failures });
    input.value = "";
    $("document-upload-actions").hidden = true;
    if (failures.length) {
      showToast(
        `${ingested.length} 个文件已完成，${failures.length} 个未完成`,
        ingested.length ? "warning" : "error",
      );
    } else {
      showToast(`已上传 ${ingested.length} 个文件，共 ${chunkCount} 个分块`);
    }
  } finally {
    $("ingest-doc-btn").disabled = false;
    $("document-files-input").disabled = false;
  }
}

export function renderSelectedDocumentFiles() {
  const files = Array.from($("document-files-input").files || []);
  const queue = $("selected-document-files");
  queue.hidden = !files.length;
  $("document-upload-actions").hidden = !files.length;
  queue.innerHTML = files
    .map(
      (file, index) => `
        <div class="upload-queue-item" data-upload-index="${index}" data-state="pending">
          <strong>${escapeHtml(file.name)}</strong>
          <span>等待上传</span>
          <small>${escapeHtml(formatByteSize(file.size))}</small>
        </div>
      `,
    )
    .join("");
}

export function updateUploadQueueItem(index, status, message) {
  const item = document.querySelector(`[data-upload-index="${index}"]`);
  if (!item) return;
  item.dataset.state = status;
  item.querySelector("span").textContent = message;
}

export function formatByteSize(value) {
  if (value === null || value === undefined) return "未知大小";
  const bytes = Number(value);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

export function renderRerankControl() {
  const capabilities = state.rerankerCapabilities;
  const button = $("rag-rerank-toggle");
  const description = $("rag-rerank-description");
  const strategy = $("rag-strategy-summary");
  const available = Boolean(capabilities.available);
  const operable = available && capabilities.status !== "error";
  const pressed = operable && state.rerankEnabled;
  button.disabled = !operable || Boolean(state.ragRequestController);
  button.setAttribute("aria-pressed", String(pressed));
  button.textContent = `精排：${pressed ? "开启" : "关闭"}`;
  strategy.textContent = capabilities.status === "checking"
    ? "策略：检测中"
    : `策略：${pressed ? "CrossEncoder" : "RRF"}`;
  strategy.dataset.strategy = pressed ? "rerank" : "rrf";

  if (!available) {
    description.textContent = capabilities.status === "checking"
      ? "正在检查服务端精排能力…"
      : "服务端未配置精排模型，当前仅使用 RRF 排序。";
    return;
  }
  const model = capabilities.model || capabilities.provider || "已配置模型";
  const status = capabilities.status === "ready"
    ? "模型已加载"
    : capabilities.status === "error"
      ? "模型加载失败"
      : "首次开启时加载模型";
  description.textContent = `${model} · ${status}；精排会增加响应时间。`;
}

export async function loadRagCapabilities() {
  try {
    const body = await fetchJson("/rag/capabilities");
    state.rerankerCapabilities = body.reranker || {
      available: false,
      status: "unavailable",
    };
  } catch {
    state.rerankerCapabilities = {
      available: false,
      status: "unavailable",
    };
  }
  renderRerankControl();
}

export function toggleRerank() {
  if (!state.rerankerCapabilities.available) {
    return;
  }
  state.rerankEnabled = !state.rerankEnabled;
  renderRerankControl();
  saveUiPreferences();
}

export function retrievalStatus(retrieval) {
  if (!retrieval?.rerank_applied) {
    return "RRF 排序";
  }
  state.rerankerCapabilities.status = "ready";
  renderRerankControl();
  const duration = retrieval.rerank_duration_ms;
  return duration === null || duration === undefined
    ? "CrossEncoder 精排"
    : `CrossEncoder 精排 ${formatDuration(duration)}`;
}

export function setRagRequestBusy(mode, busy) {
  const searchButton = $("search-rag-btn");
  const askButton = $("ask-rag-btn");
  const controls = [
    $("rag-question-input"),
    $("kb-id-input"),
    $("rag-limit-input"),
    $("rag-recall-limit-input"),
  ];
  searchButton.disabled = busy;
  askButton.disabled = busy;
  searchButton.textContent = busy && mode === "search" ? "检索中…" : "仅检索";
  askButton.textContent = busy && mode === "ask" ? "生成中…" : "生成回答";
  searchButton.toggleAttribute("aria-busy", busy && mode === "search");
  askButton.toggleAttribute("aria-busy", busy && mode === "ask");
  $("rag-answer").toggleAttribute("aria-busy", busy);
  for (const control of controls) {
    control.disabled = busy;
  }
  renderRerankControl();
}

export function beginRagRequest(mode) {
  if (state.ragRequestController) {
    state.ragRequestController.abort();
  }
  state.ragRequestGeneration += 1;
  state.ragRequestController = new AbortController();
  setRagRequestBusy(mode, true);
  return {
    controller: state.ragRequestController,
    generation: state.ragRequestGeneration,
  };
}

export function isCurrentRagRequest(generation) {
  return generation === state.ragRequestGeneration;
}

export function finishRagRequest(generation) {
  if (!isCurrentRagRequest(generation)) {
    return;
  }
  state.ragRequestController = null;
  setRagRequestBusy("", false);
}

export function prepareRagOutput(message) {
  $("rag-answer").className = "rich-output empty-output";
  $("rag-answer").textContent = message;
  renderCitations([]);
}

export async function searchRag() {
  const question = $("rag-question-input").value.trim();
  if (!question) {
    showToast("请输入搜索词", "warning");
    return;
  }
  const kbId = $("kb-id-input").value.trim();
  if (!kbId) {
    showToast("请先选择知识库", "warning");
    return;
  }
  const rerankEnabled = state.rerankEnabled;
  const request = beginRagRequest("search");
  $("rag-status").textContent = rerankEnabled ? "正在召回并精排…" : "正在检索…";
  prepareRagOutput(rerankEnabled ? "正在检索并精排相关内容…" : "正在检索相关内容…");
  try {
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(kbId)}/search`, {
      method: "POST",
      signal: request.controller.signal,
      body: JSON.stringify({
        query: question,
        limit: numberValue("rag-limit-input", 5),
        recall_limit: numberValue("rag-recall-limit-input", 20),
        rerank_enabled: rerankEnabled,
      }),
    });
    if (!isCurrentRagRequest(request.generation)) {
      return;
    }
    const results = body.results || [];
    $("rag-status").textContent = `找到 ${results.length} 条结果 · ${retrievalStatus(body.retrieval)}`;
    $("rag-answer").className = "rich-output";
    $("rag-answer").innerHTML = results.length
      ? `<h3>检索完成</h3><p>在知识库 <code>${escapeHtml(kbId)}</code> 中找到 ${results.length} 条相关内容。</p>`
      : "<p>没有找到相关内容。可以调整问题或增加召回数量后重试。</p>";
    renderCitations(results);
    setRaw(body);
  } catch (error) {
    if (error.name === "AbortError" || !isCurrentRagRequest(request.generation)) {
      return;
    }
    $("rag-status").textContent = "检索失败";
    $("rag-answer").className = "rich-output";
    $("rag-answer").innerHTML = `<p>${escapeHtml(humanizeError(error))}</p>`;
    showToast(humanizeError(error), "error");
  } finally {
    finishRagRequest(request.generation);
  }
}

export async function askRag() {
  const question = $("rag-question-input").value.trim();
  if (!question) {
    showToast("请输入问题", "warning");
    return;
  }
  const kbId = $("kb-id-input").value.trim();
  if (!kbId) {
    showToast("请先选择知识库", "warning");
    return;
  }
  const rerankEnabled = state.rerankEnabled;
  const request = beginRagRequest("ask");
  $("rag-status").textContent = rerankEnabled ? "正在精排并生成…" : "正在生成…";
  prepareRagOutput("正在检索相关内容并组织回答…");
  try {
    const conversationId = await ensureSession();
    const workspaceId = activeWorkspaceId();
    const attributedWorkspaceId = state.workspaces.some(
      (workspace) => workspace.id === workspaceId,
    )
      ? workspaceId
      : null;
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(kbId)}/ask`, {
      method: "POST",
      signal: request.controller.signal,
      body: JSON.stringify({
        question,
        conversation_id: conversationId,
        workspace_id: attributedWorkspaceId,
        limit: numberValue("rag-limit-input", 5),
        recall_limit: numberValue("rag-recall-limit-input", 20),
        rerank_enabled: rerankEnabled,
        ...optionalModelFields(),
      }),
    });
    if (!isCurrentRagRequest(request.generation)) {
      return;
    }
    $("rag-status").textContent = `${body.citations.length} 条引用 · ${retrievalStatus(body.retrieval)}`;
    $("rag-answer").className = "rich-output";
    $("rag-answer").innerHTML = renderMarkdown(body.answer || "模型没有返回回答。");
    renderCitations(body.citations || []);
    setRaw(body);
  } catch (error) {
    if (error.name === "AbortError" || !isCurrentRagRequest(request.generation)) {
      return;
    }
    $("rag-status").textContent = "生成失败";
    $("rag-answer").className = "rich-output";
    $("rag-answer").innerHTML = `<p>${escapeHtml(humanizeError(error))}</p>`;
    showToast(humanizeError(error), "error");
  } finally {
    finishRagRequest(request.generation);
  }
}

export function renderCitations(citations) {
  const list = $("rag-citations");
  list.innerHTML = "";
  for (const [index, citation] of citations.entries()) {
    const scores = [];
    if (citation.fusion_score !== null && citation.fusion_score !== undefined) {
      scores.push(`RRF ${Number(citation.fusion_score).toFixed(3)}`);
    }
    if (citation.rerank_score !== null && citation.rerank_score !== undefined) {
      scores.push(`精排 ${Number(citation.rerank_score).toFixed(3)}`);
    }
    if (!scores.length) {
      scores.push(`相关度 ${Number(citation.score ?? 0).toFixed(3)}`);
    }
    const lines = citation.start_line || citation.end_line
      ? ` · 行 ${citation.start_line || "?"}–${citation.end_line || "?"}`
      : "";
    const item = document.createElement("article");
    item.className = "citation-card";
    item.innerHTML = `
      <header>
        <strong>[${index + 1}] ${escapeHtml(citation.filename)} · #${escapeHtml(citation.chunk_index)}${escapeHtml(lines)}</strong>
        <span class="citation-scores">${scores.map((score) => `<span class="score-pill">${escapeHtml(score)}</span>`).join("")}</span>
      </header>
      <p>${escapeHtml(citation.text)}</p>
    `;
    list.appendChild(item);
  }
}

export function knowledgeBasePayload() {
  return {
    name: $("kb-name-input").value.trim(),
    description: $("kb-description-input").value.trim(),
    tags: $("kb-tags-input").value
      .split(/[,，]/)
      .map((item) => item.trim())
      .filter(Boolean),
  };
}

export function currentKnowledgeBase() {
  const id = $("kb-id-input").value;
  return state.knowledgeBases.find((item) => item.id === id) || null;
}

export function setKnowledgeTab(tabName, { focus = false } = {}) {
  const allowed = new Set(["documents", "ask", "settings"]);
  const tab = allowed.has(tabName) ? tabName : "documents";
  state.activeKnowledgeTab = tab;
  document.querySelectorAll("[data-rag-tab]").forEach((button) => {
    const active = button.dataset.ragTab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    if (active && focus) button.focus();
  });
  document.querySelectorAll("[data-rag-tab-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.ragTabPanel !== tab;
  });
  saveUiPreferences();
}

export function renderKnowledgeContext() {
  const knowledgeBase = currentKnowledgeBase();
  const unavailable = !knowledgeBase;
  $("knowledge-documents-tab").disabled = unavailable;
  $("knowledge-ask-tab").disabled = unavailable;
  if (!knowledgeBase) {
    $("selected-kb-name").textContent = "请选择知识库";
    $("selected-kb-description").textContent = "选中后即可查看和管理文档。";
    $("selected-kb-tags").innerHTML = "";
    $("selected-kb-document-count").textContent = "—";
    $("selected-kb-health").className = "index-status neutral";
    $("selected-kb-health").innerHTML = '<i aria-hidden="true"></i>未选择';
    return;
  }
  $("selected-kb-name").textContent = knowledgeBase.name;
  $("selected-kb-description").textContent = knowledgeBase.description || "暂无描述";
  $("selected-kb-tags").innerHTML = (knowledgeBase.tags || [])
    .map((tag) => `<span class="tag-chip">${escapeHtml(tag)}</span>`)
    .join("");
  $("selected-kb-document-count").textContent = String(knowledgeBase.document_count || 0);
  const hasFailures = state.knowledgeDocuments.some(
    (document) => document.last_index_status === "failed",
  );
  const health = hasFailures ? "failed" : "active";
  $("selected-kb-health").className = `index-status ${health}`;
  $("selected-kb-health").innerHTML = `<i aria-hidden="true"></i>${hasFailures ? "存在索引异常" : "索引可用"}`;
}

export function populateKnowledgeBaseForm(knowledgeBase) {
  $("kb-catalog-id-input").value = knowledgeBase.id;
  $("kb-catalog-id-input").disabled = true;
  $("kb-name-input").value = knowledgeBase.name;
  $("kb-description-input").value = knowledgeBase.description || "";
  $("kb-tags-input").value = (knowledgeBase.tags || []).join(", ");
  $("create-knowledge-base-btn").hidden = true;
  $("update-knowledge-base-btn").hidden = false;
  $("delete-knowledge-base-btn").hidden = false;
  $("knowledge-settings-title").textContent = "知识库设置";
}

export function selectKnowledgeBase(knowledgeBase, { loadDocuments = true } = {}) {
  state.selectedDocumentIds.clear();
  state.knowledgeDocumentPage = 1;
  state.activeKnowledgeDocument = null;
  closeDocumentDrawer({ restoreFocus: false });
  if (!knowledgeBase) {
    $("kb-id-input").value = "";
    state.knowledgeDocuments = [];
    state.knowledgeDocumentTotal = 0;
    renderKnowledgeBases();
    renderKnowledgeContext();
    renderKnowledgeDocuments();
    return;
  }
  populateKnowledgeBaseForm(knowledgeBase);
  $("kb-id-input").value = knowledgeBase.id;
  renderKnowledgeBases();
  renderKnowledgeContext();
  saveUiPreferences();
  if (loadDocuments) {
    listKnowledgeDocuments().catch((error) => showToast(humanizeError(error), "error"));
  }
}

export function renderKnowledgeBases() {
  const list = $("knowledge-base-list");
  const selectedId = $("kb-id-input").value;
  const mobileSelect = $("mobile-knowledge-base-select");
  mobileSelect.innerHTML = state.knowledgeBases.length
    ? state.knowledgeBases
      .map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === selectedId ? "selected" : ""}>${escapeHtml(item.name)} · ${escapeHtml(item.document_count)} 个文档</option>`)
      .join("")
    : '<option value="" selected disabled>暂无知识库</option>';
  mobileSelect.disabled = !state.knowledgeBases.length;
  const query = $("knowledge-base-search-input").value.trim().toLowerCase();
  const items = query
    ? state.knowledgeBases.filter((item) => (
      `${item.id} ${item.name} ${item.description || ""} ${(item.tags || []).join(" ")}`
        .toLowerCase()
        .includes(query)
    ))
    : state.knowledgeBases;
  renderKnowledgeList(items, selectedId, state.knowledgeBases.length > 0);
}

export async function listKnowledgeBases({ preserveSelection = "" } = {}) {
  const currentId = $("kb-id-input").value;
  const selectedId = preserveSelection || currentId || state.preferredKnowledgeBaseId;
  const body = await fetchJson("/knowledge-bases");
  state.knowledgeBases = body.knowledge_bases || [];
  renderKnowledgeBases();
  const selected = state.knowledgeBases.find((item) => item.id === selectedId)
    || state.knowledgeBases[0]
    || null;
  if (selected && selected.id === currentId) {
    populateKnowledgeBaseForm(selected);
    renderKnowledgeBases();
    renderKnowledgeContext();
    saveUiPreferences();
  } else {
    selectKnowledgeBase(selected);
  }
  return state.knowledgeBases;
}

export function beginCreateKnowledgeBase() {
  selectKnowledgeBase(null, { loadDocuments: false });
  $("kb-catalog-id-input").disabled = false;
  $("kb-catalog-id-input").value = "";
  $("kb-name-input").value = "";
  $("kb-description-input").value = "";
  $("kb-tags-input").value = "";
  $("create-knowledge-base-btn").hidden = false;
  $("update-knowledge-base-btn").hidden = true;
  $("delete-knowledge-base-btn").hidden = true;
  $("knowledge-settings-title").textContent = "新建知识库";
  setKnowledgeTab("settings");
  $("kb-catalog-id-input").focus();
}

export async function createKnowledgeBase() {
  const id = $("kb-catalog-id-input").value.trim();
  const payload = knowledgeBasePayload();
  if (!id || !payload.name) {
    showToast("知识库 ID 和名称不能为空", "warning");
    return;
  }
  try {
    const body = await fetchJson("/knowledge-bases", {
      method: "POST",
      body: JSON.stringify({ id, ...payload }),
    });
    state.knowledgeBases.push(body);
    selectKnowledgeBase(body);
    setKnowledgeTab("documents");
    setRaw(body);
    showToast("知识库已创建");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

export async function updateKnowledgeBase() {
  const id = $("kb-catalog-id-input").value.trim();
  const payload = knowledgeBasePayload();
  if (!id || !payload.name) {
    showToast("请选择知识库并填写名称", "warning");
    return;
  }
  try {
    const body = await fetchJson(`/knowledge-bases/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    state.knowledgeBases = state.knowledgeBases.map((item) => item.id === id ? body : item);
    selectKnowledgeBase(body, { loadDocuments: false });
    setRaw(body);
    showToast("知识库元数据已更新");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

export async function deleteKnowledgeBase() {
  const id = $("kb-catalog-id-input").value.trim();
  if (!id) {
    showToast("请选择要删除的知识库", "warning");
    return;
  }
  const knowledgeBase = currentKnowledgeBase();
  const confirmed = knowledgeBase?.document_count
    ? window.prompt(`删除后将移除 ${knowledgeBase.document_count} 个文档、分块和向量。输入 ${id} 确认：`) === id
    : window.confirm(`删除知识库 ${id}？`);
  if (!confirmed) return;
  try {
    await fetchJson(`/knowledge-bases/${encodeURIComponent(id)}`, { method: "DELETE" });
    $("kb-catalog-id-input").value = "";
    $("kb-name-input").value = "";
    $("kb-description-input").value = "";
    $("kb-tags-input").value = "";
    $("kb-id-input").value = "";
    await listKnowledgeBases();
    showToast("知识库已删除");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

export function documentIndexPresentation(status, searchable = true) {
  if (status === "failed") {
    return {
      state: "failed",
      className: "failed",
      label: searchable ? "替换失败 · 旧版可用" : "索引失败",
    };
  }
  if (["pending", "parsing", "embedding", "vector_written"].includes(status)) {
    return { state: "processing", className: "processing", label: "建立索引中" };
  }
  return { state: "active", className: "active", label: "可用" };
}

export function renderKnowledgeDocuments({ loading = false } = {}) {
  const body = $("knowledge-document-rows");
  const selectedAll = state.knowledgeDocuments.length > 0
    && state.knowledgeDocuments.every((item) => state.selectedDocumentIds.has(item.id));
  $("select-all-documents").checked = selectedAll;
  $("select-all-documents").disabled = !state.knowledgeDocuments.length;
  $("bulk-delete-documents-btn").disabled = !state.selectedDocumentIds.size;
  $("bulk-delete-documents-btn").textContent = state.selectedDocumentIds.size
    ? `删除已选 (${state.selectedDocumentIds.size})`
    : "删除已选";
  renderDocumentRows({ documents: state.knowledgeDocuments, selected: new Set(state.selectedDocumentIds),
    loading, hasKnowledgeBase: Boolean(currentKnowledgeBase()),
    onSelect: (id, selected) => {
      if (selected) state.selectedDocumentIds.add(id); else state.selectedDocumentIds.delete(id);
      renderKnowledgeDocuments();
    },
  });
  const pageCount = Math.max(1, Math.ceil(state.knowledgeDocumentTotal / state.knowledgeDocumentPageSize));
  $("document-page-summary").textContent = state.knowledgeDocumentTotal
    ? `共 ${state.knowledgeDocumentTotal} 个文档 · 第 ${state.knowledgeDocumentPage}/${pageCount} 页`
    : "暂无文档";
  $("previous-document-page").disabled = state.knowledgeDocumentPage <= 1;
  $("next-document-page").disabled = state.knowledgeDocumentPage >= pageCount;
  renderKnowledgeContext();
}

export async function listKnowledgeDocuments() {
  const knowledgeBaseId = $("kb-id-input").value;
  if (state.documentRequestController) state.documentRequestController.abort();
  if (!knowledgeBaseId) {
    state.knowledgeDocuments = [];
    state.knowledgeDocumentTotal = 0;
    renderKnowledgeDocuments();
    return;
  }
  state.documentRequestController = new AbortController();
  const generation = ++state.documentRequestGeneration;
  state.selectedDocumentIds.clear();
  renderKnowledgeDocuments({ loading: true });
  const params = new URLSearchParams({
    query: $("document-search-input").value.trim(),
    sort: $("document-sort-input").value,
    page: String(state.knowledgeDocumentPage),
    page_size: String(state.knowledgeDocumentPageSize),
  });
  if ($("document-status-filter").value) {
    params.set("status", $("document-status-filter").value);
  }
  try {
    const response = await fetchJson(
      `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents?${params}`,
      { signal: state.documentRequestController.signal },
    );
    if (generation !== state.documentRequestGeneration) return;
    state.knowledgeDocuments = response.items || [];
    state.knowledgeDocumentTotal = response.total || 0;
    $("rag-status").textContent = `已加载 ${state.knowledgeDocumentTotal} 个文档`;
    renderKnowledgeDocuments();
  } catch (error) {
    if (error.name === "AbortError" || generation !== state.documentRequestGeneration) return;
    state.knowledgeDocuments = [];
    state.knowledgeDocumentTotal = 0;
    renderKnowledgeDocuments();
    throw error;
  } finally {
    if (generation === state.documentRequestGeneration) {
      state.documentRequestController = null;
    }
  }
}

export function renderDocumentDrawer(documentItem) {
  $("document-drawer-title").textContent = documentItem.title;
  $("document-title-input").value = documentItem.title;
  $("document-description-input").value = documentItem.description || "";
  $("document-tags-input").value = (documentItem.tags || []).join(", ");
  $("replace-document-file").value = "";
  const status = documentIndexPresentation(
    documentItem.last_index_status,
    documentItem.is_searchable,
  );
  $("document-metadata-list").innerHTML = [
    ["文件名", documentItem.filename],
    ["文档 ID", documentItem.id],
    ["索引状态", status.label],
    ["MIME", documentItem.media_type || "未知"],
    ["文件大小", formatByteSize(documentItem.byte_size)],
    ["分块数", String(documentItem.chunk_count)],
    ["内容哈希", documentItem.content_hash],
    ["创建时间", formatDate(documentItem.created_at)],
    ["索引时间", formatDate(documentItem.indexed_at)],
  ].map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  $("document-index-error").hidden = !documentItem.last_index_error;
  $("document-index-error").textContent = documentItem.last_index_error
    ? `最近索引失败：${documentItem.last_index_error}。当前仍使用上一版可用内容。`
    : "";
}

export async function openDocumentDrawer(documentId, returnFocus = document.activeElement) {
  if (documentMetadataDirty() && !window.confirm("文档详情有未保存修改，放弃修改并切换？")) return;
  const knowledgeBaseId = $("kb-id-input").value;
  state.documentDrawerReturnFocus = returnFocus;
  const documentItem = await fetchJson(
    `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(documentId)}`,
  );
  state.activeKnowledgeDocument = documentItem;
  renderDocumentDrawer(documentItem);
  $("document-drawer").hidden = false;
  document.body.style.overflow = "hidden";
  $("close-document-drawer").focus();
}

export function documentMetadataDirty() {
  const item = state.activeKnowledgeDocument;
  return Boolean(item && ($("document-title-input").value !== item.title
    || $("document-description-input").value !== (item.description || "")
    || $("document-tags-input").value !== (item.tags || []).join(", ")
    || $("replace-document-file").files?.length));
}

export function closeDocumentDrawer({ restoreFocus = true } = {}) {
  const drawer = $("document-drawer");
  if (!drawer || drawer.hidden) return;
  if (documentMetadataDirty() && !window.confirm("文档详情有未保存修改，放弃修改并关闭？")) return;
  drawer.hidden = true;
  document.body.style.overflow = "";
  state.activeKnowledgeDocument = null;
  if (restoreFocus && state.documentDrawerReturnFocus?.isConnected) {
    state.documentDrawerReturnFocus.focus();
  }
}

export async function saveKnowledgeDocument() {
  const documentItem = state.activeKnowledgeDocument;
  if (!documentItem) return;
  const payload = {
    title: $("document-title-input").value.trim(),
    description: $("document-description-input").value.trim(),
    tags: csvValues($("document-tags-input").value.replaceAll("，", ",")),
  };
  if (!payload.title) {
    showToast("文档标题不能为空", "warning");
    return;
  }
  $("save-document-btn").disabled = true;
  try {
    const updated = await fetchJson(
      `/knowledge-bases/${encodeURIComponent(documentItem.knowledge_base_id)}/documents/${encodeURIComponent(documentItem.id)}`,
      { method: "PATCH", body: JSON.stringify(payload) },
    );
    state.activeKnowledgeDocument = updated;
    renderDocumentDrawer(updated);
    await Promise.all([listKnowledgeDocuments(), listKnowledgeBases({ preserveSelection: documentItem.knowledge_base_id })]);
    showToast("文档元数据已保存");
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    $("save-document-btn").disabled = false;
  }
}

export async function replaceKnowledgeDocument() {
  const documentItem = state.activeKnowledgeDocument;
  const file = $("replace-document-file").files?.[0];
  if (!documentItem || !file) {
    showToast("请先选择替换文件", "warning");
    return;
  }
  const form = new FormData();
  form.append("file", file, file.name);
  $("replace-document-btn").disabled = true;
  $("replace-document-btn").textContent = "重建索引中…";
  try {
    const response = await fetchJson(
      `/knowledge-bases/${encodeURIComponent(documentItem.knowledge_base_id)}/documents/${encodeURIComponent(documentItem.id)}/content`,
      { method: "PUT", body: form },
    );
    state.activeKnowledgeDocument = response.document;
    renderDocumentDrawer(response.document);
    await Promise.all([listKnowledgeDocuments(), listKnowledgeBases({ preserveSelection: documentItem.knowledge_base_id })]);
    showToast("文件已替换，新索引可用");
  } catch (error) {
    showToast(`替换失败，继续使用旧内容：${humanizeError(error)}`, "error");
    try {
      const latest = await fetchJson(
        `/knowledge-bases/${encodeURIComponent(documentItem.knowledge_base_id)}/documents/${encodeURIComponent(documentItem.id)}`,
      );
      state.activeKnowledgeDocument = latest;
      renderDocumentDrawer(latest);
    } catch {
      // Keep the existing drawer content if status refresh also fails.
    }
  } finally {
    $("replace-document-btn").disabled = false;
    $("replace-document-btn").textContent = "替换并重建索引";
  }
}

export async function deleteKnowledgeDocument(documentItem = state.activeKnowledgeDocument) {
  if (!documentItem || !window.confirm(`删除文档“${documentItem.title}”？其分块和向量也会被移除。`)) return;
  try {
    await fetchJson(
      `/knowledge-bases/${encodeURIComponent(documentItem.knowledge_base_id)}/documents/${encodeURIComponent(documentItem.id)}`,
      { method: "DELETE" },
    );
    closeDocumentDrawer();
    await Promise.all([listKnowledgeDocuments(), listKnowledgeBases({ preserveSelection: documentItem.knowledge_base_id })]);
    showToast("文档已删除");
  } catch (error) {
    showToast(humanizeError(error), "error");
  }
}

export async function bulkDeleteKnowledgeDocuments() {
  const ids = [...state.selectedDocumentIds];
  if (!ids.length) return;
  const names = state.knowledgeDocuments
    .filter((item) => state.selectedDocumentIds.has(item.id))
    .map((item) => item.title);
  if (!window.confirm(`删除已选 ${ids.length} 个文档？\n${names.join("、")}`)) return;
  const knowledgeBaseId = $("kb-id-input").value;
  $("bulk-delete-documents-btn").disabled = true;
  try {
    const response = await fetchJson(
      `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/bulk-delete`,
      { method: "POST", body: JSON.stringify({ document_ids: ids }) },
    );
    state.selectedDocumentIds.clear();
    await Promise.all([listKnowledgeDocuments(), listKnowledgeBases({ preserveSelection: knowledgeBaseId })]);
    if (response.failures?.length) {
      showToast(`已删除 ${response.deleted_ids.length} 个，${response.failures.length} 个失败`, "warning");
    } else {
      showToast(`已删除 ${response.deleted_ids.length} 个文档`);
    }
    setRaw(response);
  } catch (error) {
    showToast(humanizeError(error), "error");
  } finally {
    renderKnowledgeDocuments();
  }
}
