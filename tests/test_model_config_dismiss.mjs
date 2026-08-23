import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const APP_PATH = new URL("../ai_agent_platform/static/app.js", import.meta.url);

function loadDismissalHarness() {
  const listeners = new Map();
  let modelButtonFocused = false;
  let pickerTriggerFocused = false;

  const modelButton = {
    contains: (target) => target?.zone === "trigger",
    focus: () => { modelButtonFocused = true; },
  };
  const config = {
    open: true,
    contains: (target) => target?.zone === "config",
  };
  const pickerMenu = { hidden: true };
  const pickerTrigger = {
    focus: () => { pickerTriggerFocused = true; },
    setAttribute() {},
  };
  const elements = new Map([
    ["composer-config", config],
    ["composer-model-btn", modelButton],
    ["model-picker-menu", pickerMenu],
    ["model-picker-trigger", pickerTrigger],
  ]);
  const document = {
    addEventListener: (type, listener) => listeners.set(type, listener),
    getElementById: (id) => elements.get(id),
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
    document,
    location: { hash: "" },
    navigator: {},
    window: {},
  });
  const source = readFileSync(APP_PATH, "utf8").replace(/\ninit\(\);\s*$/, "\n");
  vm.runInContext(source, context, { filename: APP_PATH.pathname });
  vm.runInContext("bindComposerConfigDismissal()", context);

  return {
    config,
    listeners,
    modelButtonFocused: () => modelButtonFocused,
    pickerMenu,
    pickerTriggerFocused: () => pickerTriggerFocused,
    run: (expression) => vm.runInContext(expression, context),
  };
}

function keyEvent(key) {
  return {
    defaultPrevented: false,
    key,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() {},
  };
}

test("outside click closes the model configuration without stealing focus", () => {
  const harness = loadDismissalHarness();

  harness.listeners.get("click")({ target: { zone: "outside" } });

  assert.equal(harness.config.open, false);
  assert.equal(harness.modelButtonFocused(), false);
});

test("clicks inside the configuration or on its trigger keep it open", () => {
  const harness = loadDismissalHarness();

  harness.listeners.get("click")({ target: { zone: "config" } });
  assert.equal(harness.config.open, true);
  harness.listeners.get("click")({ target: { zone: "trigger" } });
  assert.equal(harness.config.open, true);
});

test("Escape closes the outer configuration and restores model-button focus", () => {
  const harness = loadDismissalHarness();
  const event = keyEvent("Escape");

  harness.listeners.get("keydown")(event);

  assert.equal(event.defaultPrevented, true);
  assert.equal(harness.config.open, false);
  assert.equal(harness.modelButtonFocused(), true);
});

test("Escape leaves the outer configuration open while the nested picker is open", () => {
  const harness = loadDismissalHarness();
  harness.pickerMenu.hidden = false;
  const event = keyEvent("Escape");

  harness.listeners.get("keydown")(event);

  assert.equal(event.defaultPrevented, false);
  assert.equal(harness.config.open, true);
  assert.equal(harness.modelButtonFocused(), false);

  harness.run('closeModelPicker({ restoreFocus: true })');
  assert.equal(harness.pickerMenu.hidden, true);
  assert.equal(harness.pickerTriggerFocused(), true);
});
