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

test("composer context meter separates cumulative usage from current history pressure", () => {
  const { context } = loadAgentSubmissionHarness(async () => ({}));
  context.testUsage = {
    total_tokens: 180_000,
    context: { estimated_tokens: 18_000, budget_tokens: 72_704 },
  };

  const presentation = vm.runInContext(
    "composerContextUsagePresentation(testUsage)",
    context,
  );

  assert.equal(presentation.kicker, "累计 180,000 tokens");
  assert.equal(presentation.label, "上下文 ≈ 18,000 / 72,704 · 24.76%");
  assert.equal(presentation.compactLabel, "上下文 24.76% · ≈ 1.8万");
  assert.ok(presentation.meterPercent > 24.75 && presentation.meterPercent < 24.77);
  assert.equal(presentation.tone, null);
  assert.equal(presentation.ringLabel, "25%");
  assert.equal(presentation.hasBreakdown, false);
  assert.equal(presentation.breakdown.length, 3);
  assert.ok(presentation.breakdown.every((item) => item.tokens === 0));
  assert.match(presentation.description, /累计实际消耗 180,000 tokens/);
  assert.match(presentation.description, /会话历史上下文估算 18,000 \/ 72,704 tokens/);
  assert.doesNotMatch(presentation.label, /247\.58%/);
});

test("composer context meter handles unknown budgets and high estimated usage", () => {
  const { context } = loadAgentSubmissionHarness(async () => ({}));
  context.unknownUsage = {
    total_tokens: 9_000,
    context: { estimated_tokens: 420, budget_tokens: 0 },
  };
  context.highUsage = {
    total_tokens: 240_000,
    context: { estimated_tokens: 66_000, budget_tokens: 72_704 },
  };

  const unknown = vm.runInContext(
    "composerContextUsagePresentation(unknownUsage)",
    context,
  );
  const high = vm.runInContext(
    "composerContextUsagePresentation(highUsage)",
    context,
  );

  assert.equal(unknown.label, "上下文 ≈ 420 · 上限未知");
  assert.equal(unknown.compactLabel, "上下文 ≈ 420 · 上限未知");
  assert.equal(unknown.meterPercent, 0);
  assert.equal(unknown.tone, null);
  assert.doesNotMatch(unknown.label, /%/);
  assert.equal(high.label, "上下文 ≈ 66,000 / 72,704 · 90.78%");
  assert.equal(high.compactLabel, "上下文 90.78% · ≈ 6.6万");
  assert.ok(high.meterPercent > 90.77 && high.meterPercent < 90.79);
  assert.equal(high.tone, "error");
});

test("composer context ring breaks context_shares into system, tools, and messages", () => {
  const { context } = loadAgentSubmissionHarness(async () => ({}));
  context.shareUsage = {
    total_tokens: 50_000,
    context: {
      estimated_tokens: 30_000,
      budget_tokens: 72_704,
      shares: {
        system_tokens: 405,
        tool_schema_tokens: 1736,
        evidence_tokens: 17_640,
        history_tokens: 10_584,
        transcript_tokens: 42_339,
      },
    },
  };

  const presentation = vm.runInContext(
    "composerContextUsagePresentation(shareUsage)",
    context,
  );

  assert.equal(presentation.hasBreakdown, true);
  assert.equal(presentation.breakdown[0].key, "system");
  assert.equal(presentation.breakdown[0].tokens, 405 + 1736);
  assert.equal(presentation.breakdown[1].key, "tools");
  assert.equal(presentation.breakdown[1].tokens, 42_339);
  assert.equal(presentation.breakdown[2].key, "messages");
  assert.equal(presentation.breakdown[2].tokens, 17_640 + 10_584);
  assert.equal(
    vm.runInContext("formatTokenK(2141)", context),
    "2.1k",
  );
  assert.equal(
    vm.runInContext("formatTokenK(72_704)", context),
    "72.7k",
  );
  assert.equal(
    vm.runInContext("formatTokenK(1_000_000)", context),
    "1.0M",
  );
  assert.equal(
    vm.runInContext("formatTokenK(420)", context),
    "420",
  );
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
    events.push({ type: "run_completed", sequence: 6, status: "completed", output: {} });
    renderAgentChatResponse(contentNode, agentProgressBodyFromEvents(events), 0);
    testEvents.push(contentNode.innerHTML);
    renderAgentChatResponse(contentNode, {
      ...agentProgressBodyFromEvents(events), status: "completed", result: { answer: "第一段，第二段" },
    }, 0);
    testEvents.push(contentNode.innerHTML);
    renderAgentChatResponse(contentNode, {
      status: "completed", result: { answer: "" },
    }, 0);
    testEvents.push(contentNode.innerHTML);
  `, context);
  assert.deepEqual(context.testEvents, [
    "temporary tool preamble",
    "",
    "第一段",
    "第一段，第二段",
    "第一段，第二段",
    "第一段，第二段",
    "<p>Agent 已完成，但没有返回文本内容。</p>",
  ]);
});

test("only unfinished Agent activities are marked active", () => {
  const { context } = loadAgentSubmissionHarness(async () => ({}));
  const events = [
    { type: "node_started", sequence: 1, node: "plan", output: {} },
    { type: "node_completed", sequence: 2, node: "plan", output: {} },
    { type: "tool_started", sequence: 3, output: { call_id: "done", name: "read" } },
    { type: "tool_result", sequence: 4, output: { call_id: "done", name: "read" } },
    { type: "node_started", sequence: 5, node: "execute", output: {} },
    { type: "tool_started", sequence: 6, output: { call_id: "active", name: "search" } },
  ];
  context.testActivityEvents = events;

  assert.equal(
    vm.runInContext('executionActiveActivitySequence(testActivityEvents, "running")', context),
    6,
  );
  assert.equal(
    vm.runInContext('executionActiveActivitySequence(testActivityEvents, "completed")', context),
    null,
  );
  events.push({
    type: "tool_error",
    sequence: 7,
    output: { call_id: "active", name: "search" },
  });
  assert.equal(
    vm.runInContext('executionActiveActivitySequence(testActivityEvents, "running")', context),
    5,
  );
  events.push({ type: "node_completed", sequence: 8, node: "execute", output: {} });
  assert.equal(
    vm.runInContext('executionActiveActivitySequence(testActivityEvents, "running")', context),
    null,
  );
});

test("composer Run control changes from pause to continue for the current Agent Run", () => {
  const { context } = loadAgentSubmissionHarness(async () => ({}));
  vm.runInContext(`
    state.conversationId = "sess_1";
    state.latestRunId = "run_1";
    state.latestRunConversationId = "sess_1";
    state.latestRunStatus = "running";
  `, context);

  const running = vm.runInContext("composerRunControlPresentation()", context);
  assert.equal(running.action, "pause");
  assert.equal(running.icon, "pause");
  assert.equal(running.label, "暂停 Agent");

  vm.runInContext('state.latestRunStatus = "paused"', context);
  const paused = vm.runInContext("composerRunControlPresentation()", context);
  assert.equal(paused.action, "continue");
  assert.equal(paused.icon, "arrow-up");
  assert.equal(paused.label, "继续 Agent");

  vm.runInContext('state.latestRunConversationId = "sess_other"', context);
  assert.equal(vm.runInContext("composerRunControlPresentation()", context), null);
});

test("/compact queues the current Run before active-run composer interception", async () => {
  const { context, events } = loadAgentSubmissionHarness(async (calls, path, options) => {
    calls.push(`request:${path}`);
    calls.push(`instruction:${JSON.parse(options.body).instruction}`);
    return {
      run_id: "run_1",
      conversation_id: "sess_1",
      status: "running",
      trace: [],
    };
  });
  vm.runInContext(`
    state.conversationId = "sess_1";
    state.latestRunId = "run_1";
    state.latestRunConversationId = "sess_1";
    state.latestRunStatus = "running";
    const composerInput = { value: "/compact 重点保留数据库迁移" };
    document.getElementById = (id) => id === "chat-message-input" ? composerInput : null;
    clearComposerInput = () => { composerInput.value = ""; testEvents.push("clear"); };
    renderAgentRun = (body) => { testEvents.push("render:" + body.status); };
  `, context);

  await vm.runInContext("submitComposerMessage()", context);

  assert.ok(events.includes("request:/agent/runs/run_1/compact"));
  assert.ok(events.includes("instruction:重点保留数据库迁移"));
  assert.ok(events.includes("clear"));
  assert.ok(events.includes("render:running"));
  assert.ok(events.includes("watch"));
});

test("paused composer control continues with optional input", async () => {
  const { context, events } = loadAgentSubmissionHarness(async (calls, path, options) => {
    calls.push(`request:${path}`);
    calls.push(`message:${JSON.parse(options.body).message}`);
    return {
      run_id: "run_1",
      conversation_id: "sess_1",
      status: "running",
      trace: [],
    };
  });
  vm.runInContext(`
    state.conversationId = "sess_1";
    state.latestRunId = "run_1";
    state.latestRunConversationId = "sess_1";
    state.latestRunStatus = "paused";
    performance = { now: () => 100 };
    const composerInput = { value: "只补测试，不改 API" };
    document.getElementById = (id) => id === "chat-message-input" ? composerInput : null;
    chatContentForRun = () => null;
    updateComposerAvailability = () => { testEvents.push("availability"); };
    clearComposerInput = () => { composerInput.value = ""; testEvents.push("clear"); };
    renderAgentRun = (body) => {
      state.latestRunStatus = body.status;
      state.latestRunBody = body;
      testEvents.push("render:" + body.status);
    };
    setChatStatusFromRun = () => { testEvents.push("status"); };
    watchRunUntilTerminal = async () => {
      state.latestRunStatus = "completed";
      testEvents.push("watch");
      return { run_id: "run_1", conversation_id: "sess_1", status: "completed" };
    };
  `, context);

  await vm.runInContext("handleComposerRunControl()", context);

  assert.ok(events.includes("request:/agent/runs/run_1/continue"));
  assert.ok(events.includes("message:只补测试，不改 API"));
  assert.ok(events.includes("clear"));
  assert.ok(events.includes("render:running"));
  assert.ok(events.includes("watch"));
});

function loadWatchRunHarness({ events, refreshStatus }) {
  const captured = [];
  const pollCalls = [];
  const sseText = events
    .map((event) => (
      `id: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`
    ))
    .join("");
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
    performance: { now: () => 100 },
    window: {},
    testCaptured: captured,
    testPollCalls: pollCalls,
    testSseText: sseText,
    testRefreshStatus: refreshStatus,
  });
  const source = readFileSync(APP_PATH, "utf8").replace(/\ninit\(\);\s*$/, "\n");
  vm.runInContext(source, context, { filename: APP_PATH.pathname });
  vm.runInContext(`
    state.conversationId = "sess_1";
    state.latestRunId = "run_1";
    state.latestRunConversationId = "sess_1";
    state.latestRunStatus = "queued";
    state.latestRunBody = null;
    state.agentPollGeneration = 0;
    fetch = async () => {
      let sent = false;
      const encoder = new TextEncoder();
      return {
        ok: true,
        body: {
          getReader() {
            return {
              read: async () => {
                if (sent) return { value: undefined, done: true };
                sent = true;
                return { value: encoder.encode(testSseText), done: false };
              },
              cancel: async () => {},
            };
          },
        },
      };
    };
    document.getElementById = (id) => (
      id === "user-id-input" ? { value: { trim: () => "demo_user" } } : null
    );
    setTrace = () => {};
    renderOverview = () => {};
    updateComposerAvailability = () => {};
    refreshRun = async () => ({
      run_id: "run_1",
      conversation_id: "sess_1",
      status: testRefreshStatus,
      result: {},
    });
    pollRunUntilTerminal = async (options, initialBody) => {
      testPollCalls.push({ options, initialBody });
      return initialBody;
    };
    captureProgress = (body) => { testCaptured.push(body); };
  `, context);
  return { context, captured, pollCalls };
}

test("resume event stream continues past a historical approval boundary", async () => {
  const events = [
    { type: "node_started", sequence: 1, status: "running", node: "plan_tools", output: {} },
    { type: "approval_required", sequence: 2, status: "waiting_approval", node: "review_tool_plan", output: {} },
    { type: "tool_started", sequence: 3, status: "running", output: { call_id: "c1", name: "sandbox.write_file" } },
    { type: "tool_result", sequence: 4, status: "running", output: { call_id: "c1", name: "sandbox.write_file", result: { ok: true } } },
    { type: "run_completed", sequence: 5, status: "completed", node: "compose_answer", output: {} },
  ];
  const { context, captured, pollCalls } = loadWatchRunHarness({
    events,
    refreshStatus: "completed",
  });

  const result = await vm.runInContext(
    'watchRunUntilTerminal({ runId: "run_1", conversationId: "sess_1", onProgress: captureProgress })',
    context,
  );

  assert.equal(result.stream_events.length, 5);
  assert.ok(
    result.stream_events.some(
      (event) => event.type === "tool_result" && event.output?.name === "sandbox.write_file",
    ),
  );
  const completedUpdates = captured.filter((body) => body.status === "completed");
  assert.equal(completedUpdates.length, 1);
  assert.ok(Object.hasOwn(completedUpdates[0], "result"));
  assert.equal(pollCalls.length, 1);
  assert.equal(pollCalls[0].initialBody.stream_events.length, 5);
});

test("suspended stream end preserves collected activities", async () => {
  const events = [
    { type: "node_started", sequence: 1, status: "running", node: "plan_tools", output: {} },
    { type: "approval_required", sequence: 2, status: "waiting_approval", node: "review_tool_plan", output: {} },
  ];
  const { context } = loadWatchRunHarness({
    events,
    refreshStatus: "waiting_approval",
  });

  const result = await vm.runInContext(
    'watchRunUntilTerminal({ runId: "run_1", conversationId: "sess_1", onProgress: captureProgress })',
    context,
  );

  assert.equal(result.status, "waiting_approval");
  assert.equal(result.stream_events.length, 2);
});
