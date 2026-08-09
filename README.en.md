# AI Agent Platform

[简体中文](README.md) | **English**

FastAPI backend with streaming chat, a task-driven code Agent, managed document
knowledge bases, workspace-scoped project memory, approval-aware sandbox
execution, and optional PostgreSQL, Celery, Redis, and Qdrant infrastructure.
A native browser workspace and an optional OIDC-validating Go gateway provide
the product and traffic boundaries.

The code Agent does not index a repository and does not use embeddings. A run
captures a registered workspace root, searches the live filesystem for the
current task, reads only necessary source ranges, and places those original
snippets in the current model context.

## Local start

Python 3.10 or newer is required by the Google Gen AI SDK:

```bash
python3.10 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp -n .env.example .env
# Replace the sample PostgreSQL password in both POSTGRES_PASSWORD and
# DATABASE_URL with the same random local-only value.
./scripts/start.sh
```

After the initial environment setup, `./scripts/start.sh` is the only command
needed for local startup. It validates the persistent configuration, starts
PostgreSQL/Qdrant/Redis, waits for them to become reachable, applies pending
Alembic migrations, and runs both Celery Worker and FastAPI. Press `Ctrl+C` to
stop the API and Worker; persistent database containers remain running.

Useful startup options:

```bash
./scripts/start.sh --check  # Validate dependencies/configuration without writes.
APP_RELOAD=0 ./scripts/start.sh
APP_PORT=8001 ./scripts/start.sh
```

The web UI is available at `http://127.0.0.1:8000`. It is served directly by
FastAPI and requires no separate frontend build. The example configuration uses
the fake LLM and local embedding provider, which require no API key.
When `AUTH_MODE=disabled`, the startup script rejects non-loopback `APP_HOST`
values instead of relying on an operator warning.

The shared composer offers:

- `快速对话` for direct SSE model responses;
- `代码 Agent` for task-driven workspace exploration, approvals, progress, and
  artifacts;
- a common conversation history with persistent rolling summaries, so a
  compressed history plus bounded recent messages can inform Chat and Agent
  exploration and native tool selection without discarding the original
  messages.

Both response modes render an in-message execution process and response
metrics. Chat uses provider SSE usage; Agent runs aggregate provider-reported
usage across structured planning and answer generation. The UI shows input,
output, thinking, and total tokens per response. Agent cursor SSE events drive
the live LangGraph trace directly, with one full Run snapshot fetched at a
terminal state and polling used only after a disconnect. Fast runs use a
bounded short replay to preserve stage order without losing the final answer or
terminal presentation.

All model use is persisted in one ledger. Chat, Agent model turns, semantic
conversation compression, RAG Ask, and embedding calls carry an `operation`,
resource, session/workspace attribution when available, requested/actual
Provider/Model, input-count method, and budget decision. The sessions page
shows cumulative input, output, thinking, and total tokens for every
conversation plus the estimated size of its currently bounded conversation
context and the most recent final Prompt's provider-counted input tokens. The
operations page shows the same totals, operation distribution, and budget
status for every registered workspace.

The context card remains a local `unicode_heuristic_v1` preview because it is
not yet a provider request. Immediately before an actual LLM request, after
history, memory, RAG citations, and tool schemas have been assembled, the final
provider-shaped Prompt is counted with OpenAI Responses `input_tokens`,
Anthropic Messages `count_tokens`, or Gemini `models.count_tokens`. Provider
completion usage remains the authoritative actual-request value stored in the
ledger; the preflight count is recorded as its audit method and is the fallback
only when completion usage omits input tokens.

The browser workspace also includes:

- managed knowledge-base catalog, multi-file upload, hybrid search, answers,
  citations, and index-job status;
- a project-memory governance page with mode/status/type filters, evidence,
  confidence, optimistic edits, confirm/reject/forget, and index repair;
- local workspace folder management constrained by `WORKSPACE_ALLOWED_ROOTS`,
  including current/default selection, invalid-path relinking, and safe removal;
  the shared composer omits a duplicate code-context strip, while the sidebar
  and settings manage the current workspace and the dedicated Agent input keeps
  its availability and role visible;
- Agent run details, approval risk, validation artifacts, errors, and metrics;
- safe Markdown rendering, response cancellation, responsive navigation, and
  accessible textual status indicators.

### Persistent sessions and restart recovery

PostgreSQL is the source of truth for restart-safe conversations. Session rows
store a deterministic or manually edited title, archive state, last-update
time, workspace and model configuration; `user_preferences` stores defaults
for future sessions and the last active session. API keys, database URLs and
allowed filesystem roots remain server-side configuration and are never part
of the session or preference records.

`GET /api/v1/sessions` returns recent-first list items with `message_count` and
`last_message_preview`, supports title/body substring search, active/archived
filtering and opaque cursor pagination. A newly allocated session stays a local
draft until its first message is persisted: zero-message rows are omitted from
history/search and do not replace the last active conversation.
`PATCH /sessions/{id}` renames,
archives/restores or changes one conversation; optionally it copies that
configuration to user defaults without rewriting older sessions. Archived
conversations remain readable but Chat, message writes and Agent execution
return `409` until the conversation is restored.

The collapsible desktop inspector places the latest 12 active conversations
above run details on the right, grouped by today, the previous seven days and
older entries; the left rail is reserved for primary navigation. Startup recovery checks the URL
session first, then `last_active_session_id`, then the latest active session;
stale zero-message candidates are skipped. With no valid session it keeps the
welcome page and does not create another empty record. Loading a session
restores messages, summary and its own model,
workspace and composer mode. Browser `localStorage` is reserved for device UI
state; it stores neither user IDs nor duplicated conversation configuration.
Local single-user mode uses an internal identity, while authenticated mode gets
identity from the trusted gateway. The health endpoint exposes `session_storage` and
`persistent_sessions`; memory mode is explicitly labeled temporary in the UI.

### Global model registry

This local single-user application has one global model registry shared by all
workspaces. The Model Management page can configure OpenAI, DeepSeek,
Anthropic, and Google once, then register multiple models under each Provider.
API keys are write-only: PostgreSQL stores only a secret reference and the
secret value is placed in the operating-system keyring. Existing environment
variables remain valid bootstrap credentials and are never returned by the API.

After a Provider is saved, Model Management calls that Provider's official
model-list endpoint, filters the text-generation models available to the current
API key, and marks models that are already registered. Registration now requires
only a Provider/model selection plus enabled and automatic-routing preferences.
The display name, context, capabilities, and cold-start routing profile come from
Provider metadata and backend priors. A manual model-ID fallback remains for
catalog gaps, without exposing quality, price, or latency inputs.

The composer exposes the session preference used by Chat, the whole code Agent
run (including resume), and RAG Ask:

- automatic `smart`, `quality`, `cost`, or `latency` routing;
- a manually preferred model with an explicit fallback switch and a custom
  picker sorted by latency, showing exact milliseconds and green/yellow/red
  latency tiers (`≤1000 ms`, `≤3000 ms`, `>3000 ms`);
- per-model availability plus observed P50/P95 first-token and total latency.

`smart` creates a deterministic, explainable task profile without another LLM
call. Easy tasks weight cost and latency more heavily, while difficult tasks
weight the backend quality profile more heavily. Background embedding, conversation
compression, and memory extraction retain their independent service policy.
Connection tests are user-triggered; normal status and latency come from passive
observations of real requests rather than periodic paid probes.

## Gemini streaming

Create a local `.env` file to use Gemini:

```dotenv
LLM_PROVIDER=google
LLM_MODEL=gemini-3.5-flash
LLM_MAX_OUTPUT_TOKENS=4096
LLM_THINKING_LEVEL=low
LLM_TIMEOUT_SECONDS=30
SSE_HEARTBEAT_SECONDS=10
GOOGLE_API_KEY=your_google_ai_studio_key
```

Gemini 3 requests accept `minimal`, `low`, `medium`, or `high` as
`thinking_level`. API requests and existing session configuration can still
override the server default, but the personal workspace manager does not expose
this option. SSE responses emit heartbeats while the provider is idle, report
thinking tokens separately, and
return an explicit `max_output_tokens` error instead of a normal completion
when Gemini finishes with `MAX_TOKENS`.

## Model routing

`LLMClient` delegates model choice to an independent `ModelRouter`. Every
request is processed in this order:

```text
session auto policy or manually preferred model
→ deterministic task-complexity profile for smart routing
→ request requirements
→ capability filter (tool calling, structured output, context window)
→ smart / quality / cost / latency ranking
→ provider health and circuit-breaker filter
→ selected model + route trace
→ provider call; pre-delta failure may try the next cross-provider candidate
```

The persistent registry is the runtime model table and updates the router
without a restart. `LLM_MODEL_CATALOG_JSON` remains a bootstrap/compatibility
source: without persisted rows the application imports its entries, or derives
one conservative entry from `LLM_PROVIDER`, `LLM_MODEL`, and
`LLM_MODEL_CONTEXT_WINDOW_TOKENS`. This abbreviated bootstrap example is
formatted for readability; `.env` values must keep the JSON array on one line:

```json
[
  {
    "provider": "google",
    "model": "your-quality-model",
    "capabilities": {"tool_calling": true, "structured_output": true},
    "context_window_tokens": 200000,
    "input_cost_per_million": 2.0,
    "output_cost_per_million": 8.0,
    "quality_score": 0.92,
    "latency_ms": 900,
    "enabled": true
  },
  {
    "provider": "openai",
    "model": "your-low-latency-model",
    "capabilities": {"tool_calling": true, "structured_output": true},
    "context_window_tokens": 128000,
    "input_cost_per_million": 0.4,
    "output_cost_per_million": 1.6,
    "quality_score": 0.76,
    "latency_ms": 220,
    "enabled": true
  }
]
```

Models registered through discovery receive backend-owned numeric routing priors
and quality/cost tiers; these are not online quality evaluations or live official
Provider prices. The `LLM_MODEL_CATALOG_JSON` compatibility path can still supply
explicit low-level values. Latency uses the backend cold-start prior only until a
successful request is observed; the runtime catalog and `latency`/`smart` ranking
then switch automatically to passive total-latency P50. `quality` maximizes the
backend profile and `cost` minimizes estimated input/output cost. Deterministic
tie-breakers keep tests reproducible. Legacy explicit
request `provider`/`model` values remain hard filters. A session manual choice is
a preferred model, so it can fall back when `fallback_enabled=true`.

Provider health is process-local. A bounded recent-outcome window and consecutive
failure count open the circuit; after the recovery timeout, the provider becomes
`half_open` and a successful probe closes it. Chat emits a `route` SSE event and
shows a `model_route` Trace node containing every candidate, rejection reasons,
health snapshot, selection reason, failures, and final model. Events before the
first non-empty text `delta` are buffered, so a 429, timeout, or transport failure
can safely fall back across providers. After the first text delta, failures are
returned with `partial_response=true` and never replayed on another model.

Registry configuration uses `MODEL_REGISTRY_STORE=postgres` for restart-safe
global configuration and `MODEL_SECRET_BACKEND=keyring` for API keys. The
write endpoints are available only in local `AUTH_MODE=disabled` mode, whose
startup boundary is forced to loopback. Use the in-memory backends only for
tests or explicit temporary runs.

## Dynamic model admission and Token budgets

The persistent model registry is the only runtime admission source for chat and
Agent models. Once a model and its provider connection are registered and
enabled in the frontend, that model is available for manual selection and
automatic routing without a static environment allowlist. Unregistered or
disabled models and disabled provider connections are still rejected before a
count or generation request leaves the process.

`LLM_PROVIDER`, `LLM_MODEL`, and `LLM_MODEL_CATALOG_JSON` only bootstrap an empty
registry and preserve compatibility; they do not create a second admission
policy. After the persistent registry exists, frontend enable/disable changes
update the runtime catalog immediately without a restart.

Session and workspace budgets count every ledger record attributed to that
scope:

```dotenv
SESSION_TOKEN_BUDGET=100000
WORKSPACE_TOKEN_BUDGET=1000000
TOKEN_BUDGET_ACTION=reject
```

`0` disables a scope. With `reject`, a request that cannot leave at least one
output token is rejected before the user message or model call is committed,
and an allowed request's provider output limit is capped to the remaining hard
budget. APIs return `429` with `code=token_budget_exceeded`. With `downgrade`,
configure a lower-cost pair that is imported into the registry:

```dotenv
TOKEN_BUDGET_ACTION=downgrade
TOKEN_BUDGET_FALLBACK_PROVIDER=openai
TOKEN_BUDGET_FALLBACK_MODEL=gpt-5-nano
```

Over-budget calls then continue on the fallback and expose
`budget_decision=downgraded` plus requested and actual model metadata. This is a
soft budget: fallback usage continues to accumulate. Budget preflight reads
committed ledger rows; strict cross-process reservation is intentionally not
claimed.

Routing and governance form one pipeline: the router filters and ranks
registered, enabled, capable, healthy catalog entries; immediately before each
real count/generation attempt, the selected provider/model must pass registry
availability and token-budget preflight again. Any budget downgrade target is
sent back through registry, catalog capability, and health validation before it
can replace the routed candidate. Cross-provider fallback repeats
provider-specific counting and authorization for the new candidate and remains
limited to the pre-delta window. OpenAI, Anthropic, and Google use their count
APIs; DeepSeek currently uses a conservative preflight estimate and the
provider's final usage remains the actual ledger value.

## Code Agent flow

The LangGraph chain starts with:

```text
setup_workspace
→ load_project_instructions
→ classify_request
→ decide_context_source
→ retrieve_project_memory (orthogonal to repo/RAG routing)
   ├─ repo   → plan_exploration → execute_exploration → assess_context
   │                    ↑ change strategy on zero hits/failures/unread candidates ─┘
   ├─ rag    → retrieve_knowledge
   ├─ hybrid → retrieve_knowledge → repository exploration
   └─ none
→ merge_evidence
```

The classifier receives a bounded catalog containing only knowledge-base IDs,
names, descriptions, and tags. It selects `none`, `repo`, `rag`, or `hybrid`
and at most three managed knowledge bases. Repository evidence still comes from
live files; document evidence reuses the independent RAG search stack.
Project memory contributes at most six current-revision active records within a
3,000-character budget. A generic project-overview request that does not name a
managed knowledge base is forced to `repo`; it discovers and reads README files,
project manifests, and entry points instead of filling a live-evidence gap with
unrelated managed documents. `merge_evidence` preserves all provenance types before
tool/change planning or answer generation. Change runs retain human approval,
per-run sandbox copying, validation, one bounded repair attempt, and Diff/test
artifacts. Before sandbox cleanup, a terminal run persists the complete patch as
a ChangeSet. The default `patch_only` mode never changes the source workspace;
`direct` or `worktree` promotion additionally requires explicitly enabled live
writes, a trusted editor approval, the approved digest, and unchanged file hashes.

Running Agent status is read from both the product run store and the latest
LangGraph checkpoint. The API therefore exposes already-completed trace nodes
while a run is still executing. Final metrics include elapsed time, node/tool
counts, changed files, recovered errors, and provider-reported input, output,
thinking, and total tokens.

Default exploration budgets:

- 4 exploration rounds
- 6 read-only tools per round
- 12 distinct source files
- 32,000 source-evidence characters
- 16,000 project-instruction characters

Exploration exposes auditable strategies such as `targeted_search`,
`broaden_file_inventory`, and `read_discovered_entries`. Zero hits, tool errors,
and unread candidates trigger another observation and a changed strategy; an
empty plan alone is not sufficient evidence. The loop exits only with relevant
repository evidence or an explicit round/file/character budget exhaustion.
Repeated calls, line segments, and content do not consume the budget twice; an
evidence-free exhaustion is recorded as a warning and answered with uncertainty.

### Native tool-calling loop

OpenAI, Anthropic, and Google adapters send `ToolSpec` definitions through each
provider's native Function/Tool Calling API. The Agent no longer asks production
models to manufacture a JSON tool plan in prompt text. Provider-specific
function calls are normalized to `LLMToolDecision`, including a stable tool-call
ID; dotted registry names receive provider-safe aliases and are mapped back
before execution. The fake provider retains deterministic rule planning for
offline tests.

Native models use one bounded observe/decide/act loop. Repository reads,
Sandbox mutations, validation commands, status, and diffs are selected in model
order inside the same tool transcript instead of isolated fixed phases:

```text
native tool call
→ ToolRegistry validation and execution
→ result/error linked by call ID
→ provider-native tool result message
→ model observes and either calls another tool or answers
```

The default soft budget is 12 rounds/36 calls; it asks the model to converge but
does not stop execution. Hard limits are 24 rounds/72 calls, 900 seconds, three
no-progress rounds, and three consecutive failures. A hard stop reserves one
tool-disabled text finalization, so exhaustion becomes `partial` or `blocked`
instead of a false `completed`. Configure these with
`AGENT_SOFT_TOOL_ROUNDS`, `AGENT_MAX_TOOL_ROUNDS`, `AGENT_SOFT_TOOL_CALLS`,
`AGENT_MAX_TOOL_CALLS`, `AGENT_MAX_ELAPSED_SECONDS`,
`AGENT_NO_PROGRESS_ROUNDS`, and `AGENT_MAX_CONSECUTIVE_FAILURES`. Older complete
assistant/tool groups are compacted above `AGENT_NATIVE_CONTEXT_MAX_CHARS`, and
`AGENT_GRAPH_RECURSION_LIMIT` remains an independent graph safety fuse.

Every successful or failed result is returned under its call ID. Completed
`(run_id, call_id)` executions can be replayed from the memory or PostgreSQL
ledger; changed argument hashes are rejected. PostgreSQL also stores append-only
Run events. The model can invoke `agent.request_user_input` to enter
`waiting_input`, while users can pause, continue, cancel, or steer at safe tool
boundaries. `AGENT_APPROVAL_POLICY=always|on_request|never` controls approvals;
`never` blocks approval-requiring calls rather than silently authorizing them.

`ToolRegistry` validates complete Draft 2020-12 JSON Schemas at registration and
validates both input and output at execution. Tool specs also declare timeout,
retry, and idempotency behavior. Retries are limited to retryable failures on
idempotent tools; the same `run_id + call_id` replays a cached result and rejects
argument changes. MCP tools use the same registry contract: `structuredContent`
is preferred, text blocks are normalized, and `isError=true` becomes a stable
tool failure instead of a successful payload.

### Project instructions

The Agent loads `AGENTS.md` from the workspace root toward focused file
directories. `AGENTS.override.md` replaces `AGENTS.md` in the same directory;
nearer directories are later and therefore more specific. Multi-directory
tasks retain each rule's applicable path.

README files and directories are not injected unconditionally. Generic project
overviews inventory the workspace and prioritize README/project manifests;
other tasks still read only paths selected by search or file discovery.

Conversation history uses two layers: an incrementally compressed rolling
summary for older turns plus the latest unsummarized messages. Compression runs
after successful Chat or Agent responses, preserves the original messages,
redacts credential-like values, and stores an optimistic-lock version and the
last summarized message. The summary is bounded, lossy, and injected as
untrusted historical context; the current request and live evidence retain
precedence.

Project memory is a separate long-term workspace subsystem. It is neither
conversation history nor LangGraph checkpoint state, and it does not ingest
knowledge-base documents automatically. Memories are historical leads:
system/project instructions, the current request, and live source code always
take precedence.

## Workspace API

`workspace_id` uses letters, digits, `_`, `-`, and `.`. Root paths are
canonical absolute paths and must remain under `WORKSPACE_ALLOWED_ROOTS` after
symbolic links are resolved. One canonical root can belong to only one
workspace; registering the same root under another ID returns `409`. List and
detail responses also expose `status`, `role`, and `can_update` so clients can
render path availability and the current user's capability.

```bash
curl -X PUT http://localhost:8000/api/v1/workspaces/project \
  -H 'content-type: application/json' \
  -d '{"root_path":"/absolute/path/to/project"}'

curl http://localhost:8000/api/v1/workspaces
curl http://localhost:8000/api/v1/workspaces/project
curl -X DELETE http://localhost:8000/api/v1/workspaces/project
curl http://localhost:8000/api/v1/workspaces/project/token-usage
curl http://localhost:8000/api/v1/sessions/{session_id}/token-usage
```

The frontend keeps the active run workspace, the user's default workspace, and
the registration draft as separate states. Draft input does not change Agent
context before registration succeeds, and one token-usage request failure does
not turn successful registration into an error. Agent submission is blocked
client-side until a usable workspace is selected.

With `AUTH_MODE=trusted_header`, directory browsing also requires trusted
gateway identity. Session configuration and user defaults may reference only a
workspace where the actor has at least viewer access. A viewer may start
read-only analysis, but approving a plan containing writes or external side
effects requires editor access; the worker checks that access again before
execution.

The `available` response field reports whether the saved path can currently be
read. `DELETE` performs a soft removal: it removes the workspace from selection
without deleting local files or cascading into sessions, usage, or project
memories. Registering the same ID restores it. Restoring the same path preserves
its revision; only a real root-path change increments `workspaces.revision`, so
old-revision memories stop participating in retrieval. An administrator can
explicitly confirm an old record to copy it into the current revision; the
historical record remains unchanged. Every `agent_runs` row keeps its captured
`workspace_root`.

Start an Agent run:

```bash
curl -X POST http://localhost:8000/api/v1/agent/runs \
  -H 'content-type: application/json' \
  -d '{
    "conversation_id":"sess_xxx",
    "workspace_id":"project",
    "message":"解释 WorkspaceService 的路径校验",
    "focus_files":["ai_agent_platform/services/workspace_service.py"]
  }'
```

The Run lifecycle APIs are:

```text
GET  /api/v1/agent/runs/{run_id}
GET  /api/v1/agent/runs/{run_id}/events?after={cursor}
GET  /api/v1/agent/runs/{run_id}/events/stream?cursor={cursor}
POST /api/v1/agent/runs/{run_id}/pause
POST /api/v1/agent/runs/{run_id}/continue   {"message":"direction or answer"}
POST /api/v1/agent/runs/{run_id}/steer      {"message":"new direction"}
POST /api/v1/agent/runs/{run_id}/cancel
POST /api/v1/agent/runs/{run_id}/resume     {"approved":true,"feedback":"review"}
GET  /api/v1/agent/runs/{run_id}/changes
POST /api/v1/agent/runs/{run_id}/changes/reject {"change_set_id":"chg_xxx"}
POST /api/v1/agent/runs/{run_id}/changes/apply  {"change_set_id":"chg_xxx","patch_sha256":"<64 hex>"}
```

The event stream uses resumable cursors. The browser workbench builds the trace
incrementally from SSE events, fetches one complete Run snapshot at a terminal
state, and falls back to status polling if the stream fails or ends early.
Final statuses are `completed`, `partial`, `blocked`, `cancelled`, and `failed`;
suspended interaction states are `waiting_approval`, `waiting_input`, and
`paused`.

Responses expose `context_route`, `selected_knowledge_base_ids`, and
`context_sources`. Knowledge chunks use `kind=knowledge_chunk` and include
optional `knowledge_base_id`, `document_id`, and `score` provenance fields.
The removed `repository_id` and `rag_context` Agent fields are not accepted or
returned.

A ChangeSet is a separate promotion boundary after tool-plan approval. It stores
the untruncated patch, SHA-256, changed paths, sandbox baseline hashes, workspace
root/revision, validation summary, and lifecycle status. Viewers may inspect it;
only editors may reject or apply it. Apply revalidates the registered root,
symlinks, sensitive/binary paths, concurrent file edits, and the user-approved
digest. Reapplying the same completed ChangeSet is idempotent and conflicts never
overwrite newer user edits. `direct` restores original files on failure;
`worktree` creates a controlled `codex/` branch from the captured Git HEAD while
leaving the source directory untouched. No commit, push, PR, merge, or deployment
is performed automatically.

### Live source tools

- `repo.find_files`: locate by filename or path fragment.
- `repo.list_files`: list paths below a workspace-relative directory.
- `repo.search_code`: use `rg` with `.gitignore`, falling back to Python.
- `repo.read_file`: read a UTF-8 line range with real line numbers and hash.

The tools reject absolute or traversal paths, escaping symlinks, binary or
oversized files, dependency/build directories, real `.env` files, private keys,
and common credential files. File listing skips symlinks, resolved paths outside
the workspace, and ignored directories such as `.venv-*` per entry, so one unsafe
path does not abort the whole inventory.

### Sandbox boundary

Change runs copy regular, non-sensitive workspace files into a per-run
directory. Real `.env` files, credentials, private keys, symbolic links,
unreadable paths, sockets, FIFOs, and other special files are skipped and
reported in `copy_warnings`. Completed, failed, or rejected runs remove their
Sandbox; startup also prunes directories older than
`SANDBOX_WORKSPACE_TTL_SECONDS`. If files changed, a server-internal exporter
persists the full ChangeSet before cleanup; a truncated display Diff is never
used for workspace promotion.

Live writes are disabled by default. `LIVE_WORKSPACE_WRITES_ENABLED=true`
requires `AUTH_MODE=trusted_header`:

```dotenv
CHANGE_SET_STORE=postgres
LIVE_WORKSPACE_WRITES_ENABLED=false
CHANGE_SET_APPLY_MODE=patch_only  # patch_only | direct | worktree
CHANGE_SET_MAX_FILES=100
CHANGE_SET_MAX_PATCH_CHARS=1000000
CHANGE_SET_WORKTREE_PARENT=
CHANGE_SET_BRANCH_PREFIX=codex/
```

`SANDBOX_MODE=local` is intended only for repositories owned and trusted by the
local user. It runs an executable basename from `SANDBOX_ALLOWED_COMMANDS` with
a minimal environment, fixed maximum timeout, bounded output capture, and
process-group termination. Shell wrappers such as `sh -c` and `bash -c` are
rejected. An allowlisted interpreter can still execute arbitrary trusted
repository code, so local mode is not an adversarial host boundary.

Docker mode additionally uses no network, a read-only container root, the
calling non-root UID/GID, dropped Linux capabilities, `no-new-privileges`, a
PID limit, CPU/memory limits, and a bounded tmpfs. Only the copied `/workspace`
mount remains writable.

## Project memory

Project memory is shared by authorized members of one `workspace_id`. Supported
kinds are `architecture_fact`, `constraint`, `decision`, `convention`,
`task_outcome`, and `incident_lesson`. Full source files, temporary discussion,
assistant speculation, credentials, private keys, tokens, connection strings,
and complete environment-variable values are rejected.

Modes are:

- `off`: no extraction or retrieval;
- `shadow`: extract review candidates but do not inject them;
- `review`: retrieve only active records, normally after human confirmation;
- `auto`: high-confidence authoritative candidates may become active.

User-created records and explicit “remember/记住” requests are active with
confidence `1.0`. Other candidates below `0.60` are discarded, values from
`0.60` through `0.84` stay reviewable, and authoritative values at or above
`0.85` may become active in `auto` mode. Assistant-only inference never becomes
active automatically. Equal canonical content adds evidence; authoritative
conflicts supersede the old record, while uncertain conflicts remain
candidates. Source-backed mutable facts are hash-checked before injection and
become `stale` after the source changes. Long-unconfirmed records are
down-ranked rather than deleted solely because of age.

Retrieval combines Qdrant dense recall and PostgreSQL full-text recall using
weighted RRF, then reloads every result from PostgreSQL to verify workspace,
revision, status, expiry, and version. Every eligible candidate receives an
explainable final score:

```text
0.65 × normalized relevance
+ 0.20 × exponential recency
+ 0.15 × normalized importance
```

Recency uses `last_confirmed_at` (falling back to `updated_at`) with a
configurable 180-day half-life. Candidates are globally ranked before the
six-result/3,000-character budget is applied, and Chat/Agent provenance exposes
the final score plus all three components. Qdrant failure degrades to lexical
search, and memory failure never fails the main Chat or Agent answer.

Management endpoints:

```text
GET/PATCH /api/v1/workspaces/{workspace_id}/memory-settings
GET/POST  /api/v1/workspaces/{workspace_id}/memories
GET/PATCH /api/v1/workspaces/{workspace_id}/memories/{memory_id}
POST      /api/v1/workspaces/{workspace_id}/memories/{memory_id}/confirm
POST      /api/v1/workspaces/{workspace_id}/memories/{memory_id}/reject
DELETE    /api/v1/workspaces/{workspace_id}/memories/{memory_id}
GET       /api/v1/workspaces/{workspace_id}/memory-jobs
POST      /api/v1/workspaces/{workspace_id}/memories/reindex
```

PATCH/confirm/reject require the current `version`. Viewers can retrieve and
view; editors can create, edit, confirm, and reject; admins can change mode,
forget, and repair indexes. Forgetting hard-deletes memory/evidence/vector data
but intentionally does not erase the source conversation.

`ChatStreamRequest.workspace_id` remains optional for old clients. When present,
Chat emits `memory_context` before answer tokens and enqueues post-response
extraction. Agent runs execute `retrieve_project_memory` after context routing
and enqueue extraction by `run_id` only after a completed result.

Configuration defaults keep the subsystem disabled:

```dotenv
PROJECT_MEMORY_ENABLED=false
PROJECT_MEMORY_MODE=off
PROJECT_MEMORY_CANDIDATE_THRESHOLD=0.60
PROJECT_MEMORY_AUTO_THRESHOLD=0.85
PROJECT_MEMORY_RECALL_LIMIT=20
PROJECT_MEMORY_RESULT_LIMIT=6
PROJECT_MEMORY_MAX_CONTEXT_CHARS=3000
PROJECT_MEMORY_QDRANT_COLLECTION=project_memories
PROJECT_MEMORY_RELEVANCE_WEIGHT=0.65
PROJECT_MEMORY_RECENCY_WEIGHT=0.20
PROJECT_MEMORY_IMPORTANCE_WEIGHT=0.15
PROJECT_MEMORY_RECENCY_HALF_LIFE_DAYS=180
```

Conversation compression is configured independently:

```dotenv
CONVERSATION_SUMMARY_ENABLED=true
CONVERSATION_SUMMARY_TRIGGER_MESSAGES=12
CONVERSATION_SUMMARY_KEEP_RECENT_MESSAGES=6
CONVERSATION_SUMMARY_MAX_CHARS=2000
CONVERSATION_SUMMARY_MAX_SOURCE_CHARS=12000
```

## Independent knowledge base

Create catalog metadata before ingesting documents:

```text
POST   /api/v1/knowledge-bases
GET    /api/v1/knowledge-bases
GET    /api/v1/knowledge-bases/{knowledge_base_id}
PUT    /api/v1/knowledge-bases/{knowledge_base_id}
DELETE /api/v1/knowledge-bases/{knowledge_base_id}
```

Catalog records contain an immutable ID plus editable name, description, and
tags. The workbench selects a knowledge base on the left and manages its
Documents, Retrieval Q&A, and Settings on the right; the selected base and tab
are device preferences. Narrow screens use a top selector, document cards, and
a full-screen detail panel. Deleting a non-empty base requires typing its ID in
the UI, then removes vectors before cascading PostgreSQL documents and chunks.

Document records expose title, original filename, description, tags, MIME type,
size, content hash, chunk count, index state, and lifecycle timestamps. The
service keeps parsed text, chunks, and upload metadata, but not the original
file or a download endpoint. Document endpoints remain available independently:

```text
GET    /api/v1/knowledge-bases/{knowledge_base_id}/documents
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/bulk-delete
GET    /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
PATCH  /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
PUT    /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/content
DELETE /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
POST /api/v1/knowledge-bases/{knowledge_base_id}/search
POST /api/v1/knowledge-bases/{knowledge_base_id}/ask
GET  /api/v1/rag/capabilities
GET  /api/v1/knowledge-bases/{knowledge_base_id}/index-jobs
GET  /api/v1/knowledge-bases/{knowledge_base_id}/index-jobs/{job_id}
```

Document ingestion accepts a multipart `file` upload (20 MiB maximum):

```bash
curl -X POST http://localhost:8000/api/v1/knowledge-bases/product_docs/documents \
  -F 'file=@/absolute/path/to/manual.pdf'
```

Supported uploads include PDF, DOCX, Markdown, UTF-8 text/configuration files,
and the existing UTF-8 source-code formats. Legacy `.doc` files and scanned
documents requiring OCR are not supported.

The list defaults to 20 documents ordered by latest update and supports
title/filename search, index-state filtering, and sorting. Metadata edits do
not re-embed content. Explicit `PUT` replacement retains the document ID,
title, description, and tags. A duplicate filename within the same knowledge
base returns HTTP 409 `document_filename_conflict` with the existing document
ID instead of overwriting it. Bulk deletion accepts at most 100 IDs and returns
`deleted_ids` plus per-item `failures`.

Only these document endpoints use chunking, embeddings, and the configured
vector store. Agent RAG routing calls the same search implementation and
degrades to an evidence warning when retrieval is unavailable.

Each upload records a queryable index job with the state sequence
`pending → parsing → embedding → vector_written → active`; a failure from any
non-terminal state is recorded as `failed` with a bounded error message.
Replacement parses, chunks, and embeds before switching, while retaining old
document, chunk, and vector snapshots for compensation. A failure keeps the
old searchable version. Deletion removes vectors before metadata and restores
snapshots if a later step fails, preventing searchable orphans.

Search performs two independent candidate recalls:

1. dense similarity recall from the configured vector store;
2. lexical recall using in-memory BM25 locally or PostgreSQL full-text search
   in the persistent runtime.

The rankings are combined with weighted reciprocal-rank fusion (RRF), then the
optional CrossEncoder reranker selects the final results. Responses expose the
raw dense/lexical scores, `dense_rank`, `lexical_rank`, `fusion_score`, and the
optional reranker score. `RAG_LEXICAL_WEIGHT` controls the RRF channel weights
and `RAG_RRF_K` controls rank smoothing.

The knowledge-base page exposes CrossEncoder as an accessible pressed/unpressed
button. Both search and ask accept an optional `rerank_enabled` boolean. When
omitted, `RAG_RERANK_DEFAULT_ENABLED` supplies the server default; the checked-in
default is `false`. Responses include `retrieval.rerank_requested`,
`rerank_applied`, provider/model, candidate/result counts, and rerank duration.
An explicit request returns HTTP 409 when no reranker is configured rather than
silently falling back to RRF.

The collapsed retrieval-parameter summary always shows the active RRF or
CrossEncoder strategy. While search or answer generation is running, the page
locks the related controls, clears stale results, and uses request cancellation
plus generation checks so repeated actions cannot let an older response overwrite
the latest result.

The default Sentence Transformers model is `BAAI/bge-reranker-base`, a
Chinese-and-English CrossEncoder. It is loaded lazily on the first request that
enables reranking, so normal startup and RRF-only requests do not download or
initialize the model. The bilingual model is larger than the previous
English-only MiniLM reranker, so its first download and CPU warm-up take longer.
Its device defaults to `cpu` to avoid process-level crashes in unsupported or
unstable accelerator runtimes (including PyTorch MPS); deployments can explicitly
select another tested device. Set the provider to `none` to disable the
capability and its page button:

```dotenv
RAG_RERANKER_PROVIDER=sentence_transformer
SENTENCE_TRANSFORMER_RERANKER_MODEL=BAAI/bge-reranker-base
SENTENCE_TRANSFORMER_RERANKER_DEVICE=cpu
RAG_RERANK_DEFAULT_ENABLED=false
```

## Storage and migration

Use Alembic for schema changes:

```bash
.venv/bin/alembic upgrade head
```

Revision `20260723_0006`:

- permanently drops `repository_files` and `repository_index_jobs`;
- renames `repositories` to `workspaces` and removes `last_indexed_at`;
- renames `agent_runs.repository_id` to `workspace_id`;
- adds and backfills nullable `agent_runs.workspace_root`.

Revision `20260724_0007` creates the managed `knowledge_bases` catalog,
backfills IDs already present in `documents`, and adds the cascading document
foreign key.

Revision `20260727_0008` adds PostgreSQL lexical search metadata, preserves
chunk line/symbol provenance, and creates the `rag_index_jobs` state journal.

Revision `20260730_0009` adds workspace revision/member/settings tables,
project memories and evidence, extraction jobs, a transactional vector-index
outbox, and body-free audit events. PostgreSQL is the memory source of truth;
the separate Qdrant `project_memories` collection stores only vectors plus
memory/workspace/revision/version identifiers and can be rebuilt.

Revision `20260730_0010` adds persistent rolling conversation summaries with
the summarized message boundary, source-size accounting, and optimistic
versioning. Source messages remain in the session tables.

Revision `20260730_0011` adds nullable workspace attribution and persisted
thinking tokens to `token_usage_records`.

Revision `20260731_0012` turns `token_usage_records` into the unified model-use
ledger: `session_id` becomes nullable for background/global work, and each row
adds operation/resource, requested Provider/Model, input-count method,
and budget decision metadata. Chat, every Agent model turn, semantic
conversation compression, RAG Ask, and embeddings write individual rows.
Historical rows are retained as `operation=chat`; existing missing workspace
attribution is not guessed.

Revision `20260804_0014` adds persistent session titles, update/archive state,
per-session model/workspace/composer configuration, `user_preferences`, recent
session indexes and deterministic backfills. It also snapshots the immutable
provider/model/thinking selection for each Agent run so approval resume cannot
inherit a later session configuration.

Revision `20260807_0015` adds a unique constraint for canonical workspace root
paths. Existing duplicate roots must be resolved before the upgrade.

Revision `20260807_0016` adds manageable knowledge-document metadata, chunk
statistics and index timestamps, backfills legacy rows, and adds per-base
filename and update-order indexes.

Revision `20260807_0017` adds `workspaces.removed_at` for soft removal and
same-path restoration without deleting sessions, usage, or project memories.

Revision `20260808_0018` adds Agent runtime-control state, append-only events,
and the durable tool-call execution ledger.

Revision `20260809_0019` adds one `agent_change_sets` row per Run with the full
patch, file baselines, workspace snapshot, validation result, and apply/reject
state.

Historical migrations remain in the revision chain. The PostgreSQL result
loader alone adapts historical JSON containing `repository_id`/`rag_context`;
new APIs and runs expose only the workspace contract.

For Celery, configure shared storage and identical mounts and allowed roots in
API and workers:

```dotenv
TASK_QUEUE_BACKEND=celery
SESSION_REPOSITORY=postgres
AGENT_RUN_STORE=postgres
CHANGE_SET_STORE=postgres
DOCUMENT_STORE=postgres
WORKSPACE_STORE=postgres
LANGGRAPH_CHECKPOINTER=postgres
RAG_VECTOR_STORE=qdrant
PROJECT_MEMORY_ENABLED=false
PROJECT_MEMORY_MODE=off
WORKSPACE_ALLOWED_ROOTS=/srv/workspaces
```

The persistent runtime assigns one responsibility to each database:

| Component | Responsibility |
| --- | --- |
| PostgreSQL | Sessions/messages, user defaults, per-session configuration and rolling summaries, Agent runs/events/tool ledger/ChangeSets and immutable model snapshots, workspace/knowledge-base catalogs, project-memory facts/evidence/jobs/outbox/audit, document/chunk metadata, lexical search, and LangGraph checkpoints |
| Qdrant | Separate knowledge and project-memory vector collections; project-memory payload is minimal and rebuildable |
| Redis | Celery broker and result backend; it is not the source of truth for business records |
| Chroma | Optional embedded/single-node vector-store alternative to Qdrant |

`RAG_VECTOR_STORE` selects either Qdrant or Chroma. They implement the same
vector-store boundary and are not written simultaneously. The checked-in
example and current persistent runtime select Qdrant; in-memory repositories
remain available only as explicit test doubles.

Before starting the API and Celery worker, start the backing services and apply
the schema migration:

```bash
docker compose up -d postgres adminer qdrant redis
.venv/bin/alembic upgrade head
.venv/bin/celery -A ai_agent_platform.workers.celery_app:celery_app worker
```

The Compose ports for Gateway, PostgreSQL, Qdrant, Redis, and Adminer are bound
to `127.0.0.1`. PostgreSQL credentials come from `.env`; `scripts/start.sh`
derives the Compose variables from `DATABASE_URL`, while direct
`docker compose` usage requires the matching `POSTGRES_DB`, `POSTGRES_USER`,
and `POSTGRES_PASSWORD` entries shown in `.env.example`.

### Browse PostgreSQL with Adminer

The Compose stack includes an Adminer web interface bound to the local machine
only. After starting `postgres` and `adminer`, open
<http://localhost:8081> and use:

| Field | Value |
| --- | --- |
| System | PostgreSQL |
| Server | `postgres` |
| Username | the local `POSTGRES_USER` value |
| Password | the local `POSTGRES_PASSWORD` value |
| Database | the local `POSTGRES_DB` value |

The server name must be `postgres`, not `localhost`, because Adminer connects to
PostgreSQL over the internal Compose network. The local-only port binding is
intended for development; do not publish Adminer without adding appropriate
access controls.

Workers register Agent run/resume, idempotent conversation compression, memory
extraction, and independent project-memory index-Outbox consumption tasks. An
inaccessible captured root fails with the structured
`workspace_unavailable` message.
Failed memory-extraction jobs retain their attempt count; Celery can retry the
same source, while a completed `source_type + source_id` remains idempotent.

## Optional Go gateway

The `gateway/` service provides request admission, request-ID propagation,
optional OIDC/JWT validation through RS256 JWKS, health/readiness probes,
SSE-safe proxying, and graceful shutdown:

```bash
go run ./gateway/cmd/gateway
go test ./gateway/...
go vet ./gateway/...
```

In production, configure `GATEWAY_AUTH_MODE=oidc`, issuer, audience, JWKS URL,
and a shared `GATEWAY_TRUST_SECRET`; configure FastAPI with
`AUTH_MODE=trusted_header` and the same secret. The gateway removes forged
identity headers, validates the bearer token, strips it, and injects the trusted
subject. Local development can keep both auth modes disabled. This is a trusted
identity boundary for sessions and workspace memory, not a claim of complete
multi-tenant authorization across every legacy knowledge-base endpoint.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall ai_agent_platform tests evals
node --check ai_agent_platform/static/app.js
go test ./gateway/...
git diff --check
```

Run offline Agent evaluations with:

```bash
.venv/bin/python evals/run_evals.py
.venv/bin/python evals/run_memory_evals.py
```
