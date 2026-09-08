import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const source = readFileSync(new URL('../ai_agent_platform/static/app.js', import.meta.url), 'utf8');
class Element {
  children = []; dataset = {}; listeners = {}; _html = ''; textContent = '';
  set innerHTML(value) { this._html = value; this.children = []; }
  get innerHTML() { return this._html; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  querySelector(name) {
    if (name === 'details') {
      if (!this.innerHTML.includes('<details')) return null;
      return this.details ??= new Element();
    }
    return this.code ??= new Element();
  }
  remove() { this.parent.children.splice(this.parent.children.indexOf(this), 1); }
  insertBefore(item, before) {
    if (item.parent) item.remove();
    this.children.splice(before ? this.children.indexOf(before) : this.children.length, 0, item);
    item.parent = this;
  }
}
function harness() {
  const elements = new Map();
  const state = {auditEvents: [], auditStoredEvents: [], auditCursor: 0, auditPage: 0,
    auditCategory: 'all', auditRunId: 'a', auditRequestGeneration: 0, auditRuns: []};
  const context = vm.createContext({state, document: {createElement: () => new Element()},
    $: (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    escapeHtml: String, jsonPretty: JSON.stringify, humanizeAgentNode: String,
    humanizeStatus: String, formatDuration: String, formatTokenCount: String,
    humanizeError: String, iconMarkup: () => '', window: {clearTimeout() {}},
  });
  vm.runInContext(source.slice(source.indexOf('function auditEventMatches('), source.indexOf('function parseErrorDetail(')), context);
  context.renderAuditRuns = () => {};
  context.renderAuditDetail = () => context.renderAuditTimeline();
  context.scheduleAuditPoll = () => {};
  return {context, state, elements};
}
const event = (sequence, type = 'node_completed') => ({sequence, type, output: {text: 'payload'}, status: 'running'});

test('10,000 events render only one page and lazily format expanded payloads', () => {
  const {context: c, state, elements} = harness();
  state.auditEvents = Array.from({length: 10000}, (_, i) => event(i + 1));
  let calls = 0; c.jsonPretty = (value) => { calls++; return JSON.stringify(value); };
  c.renderAuditTimeline();
  const list = elements.get('trace-audit-timeline');
  assert.equal(list.children.length, 100); assert.equal(calls, 0);
  const first = list.children[0]; const details = first.querySelector('details');
  details.open = true; details.listeners.toggle(); details.listeners.toggle();
  assert.equal(calls, 1); assert.match(details.querySelector('code').textContent, /payload/);
  c.renderAuditTimeline(); assert.equal(list.children[0], first); assert.equal(details.open, true);
  state.auditPage = 99; c.renderAuditTimeline();
  assert.equal(list.children[0].auditEvent.sequence, 9901);
  assert.equal(elements.get('trace-audit-next').disabled, true);
  state.auditCategory = 'tool'; c.renderAuditTimeline();
  assert.equal(state.auditPage, 0); assert.match(list.innerHTML, /没有审计事件/);
});

test('incremental requests deduplicate persisted events and never advance cursor with reconstructed rows', async () => {
  const {context: c, state} = harness(); const urls = [];
  let run = {run_id:'a', result:{tool_calls:[{call_id:'missing', name:'read'}]}};
  let incoming = [event(1), event(2)];
  c.fetchJson = async (url) => { urls.push(url); return url.includes('/events?') ? {events:incoming} : run; };
  await c.loadAuditRun('a');
  assert.equal(state.auditCursor, 2); assert.equal(state.auditEvents.length, 3);
  incoming = [event(2), event(3)]; await c.loadAuditRun('a', {silent:true});
  assert.match(urls.at(-1), /after=2$/); assert.equal(state.auditStoredEvents.length, 3);
  assert.equal(state.auditCursor, 3);
  state.auditPage = 4; run = {run_id:'b'}; incoming = [event(1)];
  await c.loadAuditRun('b'); assert.match(urls.at(-1), /after=0$/);
  assert.equal(state.auditPage, 0); assert.equal(state.auditStoredEvents.length, 1);
});

test('late responses from previous runs cannot contaminate current cache', async () => {
  const {context: c, state} = harness(); const pending = [];
  c.fetchJson = (url) => new Promise(resolve => pending.push({url, resolve}));
  const a = c.loadAuditRun('a'); const b = c.loadAuditRun('b');
  pending[2].resolve({run_id:'b'}); pending[3].resolve({events:[event(8)]}); await b;
  pending[0].resolve({run_id:'a'}); pending[1].resolve({events:[event(90)]}); await a;
  assert.equal(state.auditRunId, 'b'); assert.equal(state.auditCursor, 8);
  assert.equal(state.auditStoredEvents.length, 1);
});
