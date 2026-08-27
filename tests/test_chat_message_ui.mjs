import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const APP_PATH = new URL("../ai_agent_platform/static/app.js", import.meta.url);

function loadAgentSubmissionHarness(fetchImplementation) {
  const events = [];
  const context = vm.createContext({
    AbortController,
    Blob,
    DOMException,
    EventSource: class {},
    FormData,
    Headers,
    Intl,
    Map,
    Request,
    Response,
    Set,
    TextDecoder,
    TextEncoder,
    URL,
    URLSearchParams,
    WeakMap,
    console,
    document: {},
    location: { hash: "" },
    navigator: {},
    testEvents: events,
    testFetch: fetchImplementation,
    window: {},
  });
  const source = readFileSync(APP_PATH, "utf8").replace(/\ninit\(\);\s*$/, "\n");
  vm.runInContext(source, context, { filename: APP_PATH.pathname });
  vm.runInContext(`
    state.conversationId = "sess_1";
    currentWorkspace = () => ({ id: "workspace_1" });
    workspaceIsReady = () => true;
    optionalModelFields = () => ({});
    ensureSession = async () => { testEvents.push("session"); return "sess_1"; };
    fetchJson = async (...args) => testFetch(testEvents, ...args);
    renderAgentRun = () => { testEvents.push("render"); };
    watchRunUntilTerminal = async () => { testEvents.push("watch"); return null; };
    refreshCurrentSessionMetadata = async () => { testEvents.push("metadata"); };
    refreshRecentSessions = async () => { testEvents.push("recent"); };
    updateComposerAvailability = () => { testEvents.push("availability"); };
    showToast = () => { testEvents.push("toast"); };
  `, context);
  return {
    context,
    events,
    run: () => vm.runInContext(`runAgent({
      message: "检查消息时序",
      onReady: () => testEvents.push("ready"),
      onSubmitted: () => testEvents.push("submitted"),
      onSubmissionError: () => testEvents.push("submission-error"),
    })`, context),
  };
}

test("Agent prompt becomes renderable before the Run creation request", async () => {
  const harness = loadAgentSubmissionHarness(async (events, path) => {
    events.push(`request:${path}`);
    return { run_id: "run_1", conversation_id: "sess_1", status: "queued" };
  });

  const result = await harness.run();

  assert.ok(result, JSON.stringify(harness.events));
  assert.equal(result.run_id, "run_1");
  assert.ok(harness.events.indexOf("ready") < harness.events.indexOf("request:/agent/runs"));
  assert.equal(harness.events.filter((event) => event === "ready").length, 1);
  assert.equal(harness.events.filter((event) => event === "submitted").length, 1);
  assert.equal(harness.events.includes("submission-error"), false);
});

test("Run creation failure preserves the optimistic UI through an error callback", async () => {
  const harness = loadAgentSubmissionHarness(async (events, path) => {
    events.push(`request:${path}`);
    throw new Error("network unavailable");
  });

  const result = await harness.run();

  assert.equal(result, null);
  assert.ok(harness.events.indexOf("ready") < harness.events.indexOf("request:/agent/runs"));
  assert.ok(harness.events.indexOf("request:/agent/runs") < harness.events.indexOf("submission-error"));
  assert.equal(harness.events.includes("submitted"), false);
});

test("message delivery state is explicit and clears after acceptance", () => {
  const statusNode = { hidden: true, textContent: "" };
  const classes = new Set();
  const message = {
    classList: {
      toggle(name, enabled) {
        if (enabled) classes.add(name);
        else classes.delete(name);
      },
    },
    dataset: {},
    querySelector: () => statusNode,
  };
  const context = vm.createContext({
    AbortController,
    Blob,
    DOMException,
    EventSource: class {},
    FormData,
    Headers,
    Intl,
    Map,
    Request,
    Response,
    Set,
    TextDecoder,
    TextEncoder,
    URL,
    URLSearchParams,
    WeakMap,
    document: {},
    location: { hash: "" },
    navigator: {},
    testMessage: message,
    window: {},
  });
  const source = readFileSync(APP_PATH, "utf8").replace(/\ninit\(\);\s*$/, "\n");
  vm.runInContext(source, context, { filename: APP_PATH.pathname });

  vm.runInContext('setMessageDeliveryState(testMessage, "submitting", "正在提交…")', context);
  assert.equal(classes.has("is-submitting"), true);
  assert.equal(statusNode.hidden, false);
  assert.equal(statusNode.textContent, "正在提交…");

  vm.runInContext("setMessageDeliveryState(testMessage)", context);
  assert.equal(classes.size, 0);
  assert.equal(statusNode.hidden, true);
  assert.equal(statusNode.textContent, "");
});

function loadSessionNavigationHarness({ archived = false } = {}) {
  const session = { id: "history_1", message_count: 1, archived_at: archived ? "2026-08-26" : null };
  const message = { role: "user", content: "历史会话内容" };
  const { context, events } = loadAgentSubmissionHarness(async (calls, path) => {
    calls.push(`request:${path}`);
    if (path.endsWith("/summary")) return { session_id: session.id, message_count: 1 };
    if (path.endsWith("/messages")) return { messages: [message] };
    if (path.endsWith("/token-usage")) return { total_tokens: 42 };
    if (path === "/users/me/preferences") return { last_active_session_id: session.id };
    if (path === `/sessions/${session.id}`) return session;
    throw new Error(`Unexpected request: ${path}`);
  });
  vm.runInContext(`
    state.currentView = "sessions";
    const nodes = new Map();
    document.getElementById = (id) => {
      if (!nodes.has(id)) nodes.set(id, { value: "", textContent: "" });
      return nodes.get(id);
    };
    resetLatestAgentRunState = () => {};
    invalidateSlashCapabilities = () => {};
    renderSessionSummary = (summary, usage) => {
      testEvents.push("summary:" + summary.session_id + ":" + usage.total_tokens);
    };
    renderMessages = (messages) => { testEvents.push("messages:" + messages[0].content); };
    renderChatHistory = () => {};
    applyConfigurationToInputs = () => {};
    loadModelPreference = async () => {};
    restoreComposerDraft = () => {};
    updateSessionUrl = () => {};
    replaceSessionInLists = () => {};
    renderSessions = () => {};
    updateContextSummary = () => {};
    switchView = (view) => { state.currentView = view; testEvents.push("navigate:" + view); };
    restoreLatestAgentRun = async () => {};
    setRaw = () => {};
  `, context);
  return { context, events };
}

for (const archived of [false, true]) {
  test(`opening an ${archived ? "archived" : "active"} history entry renders details without leaving sessions`, async () => {
    const { context, events } = loadSessionNavigationHarness({ archived });

    await vm.runInContext('handleSessionAction("history_1", "open")', context);

    assert.equal(vm.runInContext("state.currentView", context), "sessions");
    assert.equal(vm.runInContext("state.currentSession.id", context), "history_1");
    assert.ok(events.includes("summary:history_1:42"));
    assert.ok(events.includes("messages:历史会话内容"));
    assert.equal(events.some((event) => event.startsWith("navigate:")), false);
  });
}

test("direct session loading still opens the conversation workbench", async () => {
  const { context, events } = loadSessionNavigationHarness();

  await vm.runInContext('loadSession(true, "history_1")', context);

  assert.equal(vm.runInContext("state.currentView", context), "chat");
  assert.ok(events.includes("navigate:chat"));
});

test("Agent answer deltas render while running and resets remove tool preambles", () => {
  const { context } = loadAgentSubmissionHarness(async () => ({}));
  vm.runInContext(`
    renderExecutionProcess = () => {};
    renderInlineAgentCheckpoint = () => {};
    renderInlineAgentControls = () => {};
    renderInlineRunFooter = () => {};
    renderMarkdown = (text) => text;
    performance = { now: () => 100 };
    const contentNode = { innerHTML: "" };
    const events = [
      { type: "answer_reset", sequence: 1, status: "running", output: {} },
      { type: "answer_delta", sequence: 2, status: "running", output: { text: "temporary tool preamble" } },
    ];
    renderAgentChatResponse(contentNode, agentProgressBodyFromEvents(events), 0);
    testEvents.push(contentNode.innerHTML);
    events.push({ type: "answer_reset", sequence: 3, status: "running", output: {} });
    renderAgentChatResponse(contentNode, agentProgressBodyFromEvents(events), 0);
    testEvents.push(contentNode.innerHTML);
    events.push({ type: "answer_delta", sequence: 4, status: "running", output: { text: "第一段" } });
    renderAgentChatResponse(contentNode, agentProgressBodyFromEvents(events), 0);
    testEvents.push(contentNode.innerHTML);
    events.push({ type: "answer_delta", sequence: 5, status: "running", output: { text: "，第二段" } });
    renderAgentChatResponse(contentNode, agentProgressBodyFromEvents(events), 0);
    testEvents.push(contentNode.innerHTML);
    renderAgentChatResponse(contentNode, {
      ...agentProgressBodyFromEvents(events), status: "completed", result: { answer: "第一段，第二段" },
    }, 0);
    testEvents.push(contentNode.innerHTML);
  `, context);
  assert.deepEqual(context.testEvents, ["temporary tool preamble", "", "第一段", "第一段，第二段", "第一段，第二段"]);
});
