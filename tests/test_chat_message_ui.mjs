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
