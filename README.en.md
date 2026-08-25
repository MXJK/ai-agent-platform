# AI Agent Platform

[简体中文](README.md) | **English**

Single-user FastAPI self-hosted AI Agent platform with streaming chat, a
task-driven code Agent, managed document knowledge bases, workspace-scoped
project memory, and approval-aware controlled execution. The current product runs
the App, PostgreSQL, and Qdrant through Docker Compose with an in-process queue.

The code Agent does not index a repository and does not use embeddings. A run
captures a registered workspace root, searches the live filesystem for the
current task, reads only necessary source ranges, and places those original
snippets in the current model context.

## Single-node Docker start

The only supported product path is single-user, single-instance Docker Compose
self-hosting. The persistent services are the FastAPI/Web UI App, PostgreSQL, and
Qdrant; a one-shot `migrate` service applies the existing Alembic chain. Agent work
uses the in-process bounded queue, so no Go gateway, Redis, Celery Worker, or
Adminer is started.
The App image installs `requirements.self-hosted.txt`, excluding Celery/Redis,
Chroma, OS keyring, and local SentenceTransformer/Torch dependencies that the
current topology never loads. Those adapters remain in the full development set.

```bash
cp -n .env.example .env
mkdir -p workspaces
docker compose up -d --build
docker compose ps
```

Open `http://127.0.0.1:8000`. Only the App is published to host loopback;
PostgreSQL and Qdrant remain private to the Compose network. Set
`WORKSPACE_HOST_PATH` to one trusted host directory, mounted as `/workspaces`; the
existing web directory browser registers projects below that root without trying
to open the host Finder from a container.

The official `RUNTIME_PROFILE=custom` combination reuses the existing PostgreSQL
repositories, Qdrant vector stores, and `in_process` task queue. Project memory is
enabled and user memory is disabled. Provider API keys are entered only in Model
Management; Compose encrypts them in the private persistent `app_state` volume while
PostgreSQL stores only opaque references. The default Fake LLM keeps the stack
offline until a real Provider connection and model are registered in the UI.

`AUTH_MODE=single_user` ignores caller-controlled identities and assigns every
request to `SINGLE_USER_ID=owner`, who may administer models and MCP. It is not
public-network authentication: do not publish this port on LAN or Internet
interfaces. Commands execute inside the App container and the MVP supports only
repositories the owner already trusts. The code Agent edits the registered source
root directly by default and no longer asks the user to choose an execution location.

When a persistent installation moves from a legacy local or trusted-gateway
identity to `single_user`, startup grants the fixed owner administrator membership
for every existing Workspace, including soft-removed records, without deleting
legacy members or project data. A legacy host-absolute root must still be relinked
through the UI to its container-visible `/workspaces/...` path.

SQLite, Celery, Go gateway, OIDC, and multi-worker implementations remain as
compatibility and test code, not supported deployment paths. `start-local.sh` now
forwards to the same Compose entrypoint; `./scripts/start.sh --check` performs a
static Compose check.

## CLI, REPL, SDK, and process entrypoints

The installed entrypoints are thin adapters over `RuntimeContainer` and
`QueryService`:

```bash
.venv/bin/ai-agent --workspace /absolute/path/to/project print "Explain the entrypoints"
.venv/bin/ai-agent --workspace /absolute/path/to/project repl
.venv/bin/ai-agent-api --host 127.0.0.1 --port 8000
```

Print mode emits one canonical `AgentEvent` JSON object per stdout line. The REPL
keeps one conversation across turns and provides `/skills`, `/tools`, `/mcp`,
`/permissions`, `/resume`, and `/exit`. `Ctrl+C` during a Run requests cancellation
of that Run; signal handling exists only in the process-owning CLI, never in the SDK
or Query Kernel.

`AgentSDK.query()` and `resume()` return `AsyncIterator[AgentEvent]`, while
`control()` and `result()` return `QueryResult`. The official App image starts
FastAPI from `ai_agent_platform.api.entrypoint` and lifespan closes the same
`RuntimeContainer`. The Celery process lifecycle adapter remains as compatibility
code, but the current Compose stack does not start it.

## Layered runtime configuration

The official Compose stack uses `RUNTIME_PROFILE=custom` and locks the following
single-node product contract in service-owned environment values:

| Boundary | Current MVP |
| --- | --- |
| Structured facts, checkpoints, model registry | PostgreSQL |
| Document and project-memory vectors | Qdrant |
| Agent, compression, and memory tasks | Bounded queue in the API process |
| Identity | Fixed `single_user` owner |
| Workspace | `/workspaces` bind mount and direct source edits |
| Sandbox | Local execution inside the App container for trusted repositories |

The named `local` and `production` profiles remain compatibility implementations,
not public startup paths. Existing Celery validation still requires fully shared
PostgreSQL/Qdrant state; this Compose stack does not select Celery and therefore
does not require Redis or a separate Worker.

`ConfigResolver` resolves configuration in one fixed order: `Settings` defaults,
user JSON, project JSON, environment/`.env`, then explicit entry-point overrides.
The conventional files are `~/.config/ai-agent-platform/config.json` and
`.ai-agent-platform/config.json` under the project root; override their paths with
`AI_AGENT_PLATFORM_USER_CONFIG` and `AI_AGENT_PLATFORM_PROJECT_CONFIG`. Files use
standard-library JSON and divide fields into `process_security`, `runtime`, and
`project_session`. Unknown sections, unknown fields, and wrong native types fail at
startup.

```json
{
  "process_security": {
    "workspace_allowed_roots": ["/srv/code"],
    "mcp_allowed": true,
    "skills_allowed": true,
    "skill_allowlist": ["review"],
    "tool_allowlist": ["file_symbol_locator", "repo.search_code"],
    "sandbox_mode": "docker",
    "sandbox_allowed_commands": ["python", "pytest"]
  },
  "runtime": {
    "llm_model": "example-model",
    "session_token_budget": 50000
  },
  "project_session": {
    "project_instructions": ["Run affected tests first."],
    "enabled_tools": ["file_symbol_locator"],
    "skills_enabled": true,
    "enabled_skills": ["review"],
    "mcp_enabled": false
  }
}
```

User files and the process environment establish trusted policy. Project files
cannot change databases, authentication, API keys/secret backends, allowed roots,
live-write switches, or the MCP config path. Sandbox mode, image, command allowlist,
timeouts, output limit, workspace parent, and lifetime all construct the executor at
process startup, so they belong to `process_security`. A project cannot create an
override that changes only its snapshot while leaving execution unchanged. Projects
may still tighten approval and select smaller tool/Skill/MCP subsets, but cannot
bypass `mcp_allowed=false`, `skills_allowed=false`, or process allowlists. The
process Tool Registry remains the hard capability ceiling. Each Run builds an
immutable `ToolCatalog` with explicit
base/local/MCP sources and namespaces, then a shared `ToolPoolBuilder` intersects
project selection, Agent/mode, model capabilities, Workspace role, central display
deny, explicit deny, Sandbox capabilities, and Skill requirements into an
`EffectiveToolPool` without mutating the Registry.

Legacy unprefixed environment names and `.env` remain supported for non-Provider
configuration, including the old `SESSION_REPOSITORY`/`AGENT_RUN_STORE` fallback
chains. Provider API keys are no longer configuration fields and matching environment
variables are ignored.
Store fallback applies only when the target Store supports that backend, so local
SQLite is not propagated into the memory/PostgreSQL-only model registry or ChangeSet
Store. Session and Run Stores must also select the same backend; configuration fails
before runtime resource construction when an atomic Query start would be impossible.
The new `AI_AGENT_PLATFORM_<FIELD>` namespace rejects unknown names. The immutable
`ResolvedConfig` exposes a compatible `settings` view, three frozen sections, and
per-field provenance. `Settings.from_env()` still returns `Settings`. API/Worker
containers retain `ResolvedConfig.safe_snapshot()` as `config_snapshot`, the
supported serialization view for logs, Run snapshots, and configuration
diagnostics. Structured logging also recursively redacts nested keys, secrets,
tokens, and credential-bearing connection strings.
New Runs write RunContext schema v3 with catalog/pool contract versions, normalized
hash-only summaries, hashes, selection provenance, and safe exclusion diagnostics;
tool Schemas, headers, credentials, and sensitive arguments are not persisted.

## Skill discovery and slash commands

When `skills_enabled` is true, the runtime discovers only `SKILL.md` files below
these roots:

- bundled: `ai_agent_platform/bundled_skills/<skill>/SKILL.md`;
- user: `~/.ai-agent-platform/skills/<skill>/SKILL.md`;
- project: `.agents/skills/<skill>/SKILL.md` under the authorized Workspace.

The fixed precedence is `project > user > bundled`, with qualified names such as
`project:review`. Same-source duplicates use the lexicographically first relative
path and emit an error diagnostic. Cross-source overrides and slash command/alias
conflicts emit stable diagnostics, and final definitions are sorted by normalized
name. Project Skills always enter the Run snapshot as `untrusted_project_skill`
context, including when they override a user or bundled definition.

The first version accepts strict, duplicate-free YAML frontmatter:

```markdown
---
name: review
description: Review requested code changes
agents: [coding]
modes: [default]
context_budget: 4000
tools: [repo.search_code, repo.read_file]
command:
  name: review
  description: Review code in the current Workspace
  usage: "[path]"
  aliases: [rv]
---
Inspect live evidence before giving review findings.
```

Only those fields are accepted. A file is limited to 64 KiB; one discovery pass
considers at most 64 candidates and loads at most 128 KiB of text. One Skill can
request at most 16,000 context characters and still shares the Run instruction
budget. Invalid UTF-8, malformed or duplicate YAML, unknown fields, oversized
files, and individual bad Skills become diagnostics without aborting discovery.
Symlink roots, directories, and `SKILL.md` files are not followed, and canonical
paths must remain below their source root.

Skills are declarative data. Python and shell files are never executed, and
Markdown cannot register functions. `tools` lists requirements only: a Skill with
missing registered tools is not injected, while existing tools remain governed by
`ToolUseContext`, sandbox, and allow/ask/deny policy. Skills cannot register tools,
reduce approval, expand allowlists, or grant permission. For a non-built-in REPL
slash command, the effective Workspace Skill catalog applies source precedence,
enabled Skills, Agent/mode, and `required_tools`, then submits an ordinary
`QueryParams(skill_name, skill_arguments)`. The selected instructions are frozen
before queueing; unknown, disabled, or dependency-missing commands return stable
diagnostics and never execute Skill-directory code.

The browser composer reuses the same path. Typing `/` groups and filters built-in
commands, effective Skills, and MCP tools from the current `EffectiveToolPool`,
with Arrow, Enter/Tab, Escape, and pointer operation. A Skill selection sends its
qualified name plus quote-aware arguments to Agent. An MCP selection freezes only
a user preference to use that tool; provider-native tool calling, central
permission resolution, approval, and the sandbox still decide whether it runs.
`GET /api/v1/agent/composer-capabilities` builds this read-only catalog from the
authenticated conversation, Workspace, model, and effective configuration, so a
process-registered but unavailable capability is not presented as usable.

The shared composer offers:

- `快速对话` for direct SSE model responses;
- `代码 Agent` for task-driven workspace exploration with progress, approvals,
  changed files, Diff, and ChangeSet actions rendered in the same assistant message;
- `/chat`, `/agent`, `/new`, and `/mcp` built-ins plus effective Skill/MCP choices
  from the same composer; selecting Skill or MCP switches to code Agent;
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
- a dedicated `Trace Audit` page (`/#trace-audit`) that lists the current actor's
  recent Agent Runs and presents a filterable, read-only event timeline for
  state transitions, nodes, exact tool selections and arguments, complete
  results or errors, approval requests, and approval decisions. Active and
  suspended Runs refresh automatically. `run.read_artifact` is the deliberate
  exception: its event retains artifact identity, ranges, hashes, and token
  metadata without copying protected page content. The existing in-conversation
  Trace and approval controls remain the live interaction surface;
  the shared composer omits a duplicate code-context strip, while the sidebar
  and settings manage the current workspace; there is no separate Code Agent page;
  current Compose disables the container-native picker, and the web directory
  browser only exposes content mounted under `/workspaces`; every selection still
  passes the allowed-root check. The macOS Finder picker remains a non-default
  local-development compatibility implementation;
- approval, input, pause, Run controls, and checkpoint history access rendered at
  the bottom of the matching assistant message, without a ribbon above the
  conversation. Running work can be steered, paused, or cancelled, suspended work
  can continue with a new direction,
  and any finished Run exposes a Git-like checkpoint rail. Restoring a historical
  boundary always creates a new Run: it can continue in the current conversation or
  fork the prefix before the source Run into a new conversation, while preserving
  the source Run and parent checkpoint. Terminal messages include a changed-file ledger, line counts, expandable
  Diff, validation state, and safe revert actions. Current `direct` Runs show the
  source location already written and require digest confirmation to revert;
  historical `patch_only` / `worktree` records retain their original presentation.
  Reopening a session restores its
  latest Run and ChangeSet so `waiting_approval` or Sandbox-only output is not
  mistaken for a stalled or already-applied change;
- an auto-growing conversation input with per-session unsent drafts, availability
  that reflects empty/busy/archived/workspace states, and follow-near-bottom
  scrolling with an explicit jump-to-latest action when the user reads history;
- safe Markdown rendering, response cancellation, responsive navigation, and
  accessible textual status indicators.

### Persistent sessions and restart recovery

PostgreSQL is the source of truth for restart-safe conversations. Session rows
store a deterministic or manually edited title, archive state, last-update
time, workspace and model configuration; `user_preferences` stores defaults
for future sessions and the last active session. Provider API keys live in the
server-side Secret Store; database URLs and allowed filesystem roots remain
server-side configuration. None are part of session or preference records.

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
state and at most 20 non-empty per-session drafts; it stores neither user IDs nor
duplicated conversation configuration. Drafts stay on that device and do not
enter server-side history until submission succeeds.
Local single-user mode uses an internal identity, while authenticated mode gets
identity from the trusted gateway. The health endpoint exposes `session_storage` and
`persistent_sessions`; memory mode is explicitly labeled temporary in the UI.

### Global model registry

This local single-user application has one global model registry shared by all
workspaces. The Model Management page can configure OpenAI, DeepSeek,
Anthropic, and Google once, then register multiple models under each Provider.
API keys are write-only: PostgreSQL stores only a secret reference and the secret
value goes to the selected Secret Store. Native runs use the operating-system keyring. Official
Compose stores Fernet ciphertext plus an owner-only random host key in its private
persistent volume. Provider environment variables and `.env` are no longer bootstrap
credentials and keys are never returned by the API. Legacy `env:*` references require
one UI re-entry after upgrade and are never resolved from the environment.

After a Provider is saved, Model Management calls that Provider's official
model-list endpoint, filters the text-generation models available to the current
API key, and marks models that are already registered. Registration now requires
only a Provider/model selection, a per-model maximum output-token capability,
plus enabled and automatic-routing preferences. The display name, context,
capabilities, and cold-start routing profile come from
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

## Gemini protocol support

To use Gemini, save the Google API key, discover models, and register the target
model in Model Management. `.env` neither selects nor imports Google/Gemini models,
and Provider API keys are not read from `.env` or process environment variables.
`LLM_MAX_OUTPUT_TOKENS`, `LLM_THINKING_LEVEL`, `LLM_TIMEOUT_SECONDS`, and
`SSE_HEARTBEAT_SECONDS` configure shared runtime policy rather than Provider/model
registration.

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
→ error-code retry policy and bounded backoff
→ provider call; an exhausted pre-delta failure may try the next cross-provider candidate
```

The persistent registry is the runtime model table and updates the router without a
restart. The PostgreSQL product runtime does not import candidates from
`LLM_PROVIDER`, `LLM_MODEL`, or `LLM_MODEL_CATALOG_JSON`; an empty registry stays
empty until the local owner registers a Provider and model in the frontend.
Static/default catalogs remain only for explicit `memory` test or ephemeral local
runtimes.

Models registered through discovery receive backend-owned numeric routing priors
and quality/cost tiers; these are not online quality evaluations or live official
Provider prices. Latency uses the backend cold-start prior only until a
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

The reliability policy follows LiteLLM Router's separation of error
classification, retries, cooldown, and fallback while retaining this project's
lightweight `LLMClient`; LiteLLM is not added as a runtime dependency. By
default every retryable error keeps using `LLM_MAX_RETRIES`. A strict JSON map
can override stable gateway error codes:

```dotenv
LLM_MAX_RETRIES=2
LLM_RETRY_POLICY_JSON={"rate_limit":0,"llm_timeout":2,"llm_transport_error":2,"llm_server_error":1,"default":2}
LLM_RETRY_BASE_DELAY_SECONDS=0.2
LLM_RETRY_BACKOFF_MAX_SECONDS=2
LLM_RETRY_AFTER_MAX_SECONDS=60
LLM_RETRY_JITTER_SECONDS=0.1
```

Supported override keys include `rate_limit`, `llm_timeout`,
`llm_transport_error`, `llm_server_error`, `token_count_failed`, the three
tool-output correction errors, `llm_provider_error`, and `default`. Unknown
keys, negative values, and non-integers fail during startup. Both normal HTTP and
SSE 429/5xx responses accept delta-seconds or HTTP-date `Retry-After` values.
A positive value at or below `LLM_RETRY_AFTER_MAX_SECONDS` takes precedence;
otherwise the gateway uses bounded exponential backoff. Jitter is bounded as
well, so an unsafe upstream header cannot hold a worker indefinitely. Route
Trace exposes a `retries` array with candidate, error code, retry number,
effective budget, delay, and `retry_after` / `exponential_backoff` source.
No wait or replay is introduced after the first non-empty delta.

The Compose stack uses `MODEL_REGISTRY_STORE=postgres` for restart-safe model
catalog state and `MODEL_SECRET_BACKEND=encrypted_file` for Provider API keys entered
through the UI. Ciphertext and its random owner-only host key live in the private
`app_state` volume; PostgreSQL, API responses, logs, and browser storage never contain
plaintext. The fixed `single_user` owner may save/test/discover connections and mutate
models, and caller identity headers cannot replace that owner. `memory` is test-only,
native runs may use the OS keyring, and multi-node deployments require an external
KMS/Vault instead of this single-node file backend.

## Dynamic model admission and Token budgets

The persistent model registry is the only runtime admission source for chat and
Agent models. Once a model and its provider connection are registered and
enabled in the frontend, that model is available for manual selection and
automatic routing without a static environment allowlist. Unregistered or
disabled models and disabled provider connections are still rejected before a
count or generation request leaves the process.

The PostgreSQL product runtime does not read `LLM_PROVIDER`, `LLM_MODEL`, or
`LLM_MODEL_CATALOG_JSON` to form startup candidates and does not maintain a second
static admission policy. Frontend registration and enable/disable changes update
the runtime catalog immediately without a restart.

Each registry row stores the context window and a separate maximum output-token
capability, editable in Model Management. Normal Chat requests
`LLM_MAX_OUTPUT_TOKENS`; the coding Agent requests phase budgets of
`AGENT_PLAN_MAX_OUTPUT_TOKENS=4096`, `AGENT_MUTATION_MAX_OUTPUT_TOKENS=16384`,
and `AGENT_FINAL_MAX_OUTPUT_TOKENS=4096`. The provider receives the minimum of
the phase request, model capability, remaining context, and Usage Ledger
authorization. The 16K mutation budget therefore never overrides a smaller
registered model limit.

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
tool/change planning or answer generation. Before tools are built, the current
product freezes the registered source root as the server-selected `direct`
execution root; neither the UI nor `POST /agent/runs` accepts a per-Run location
choice. Exact tool approval, validation, bounded repair, and Diff/test artifacts
remain in force. Terminal capture persists the full patch, pre/post hashes, and
source location as an applied ChangeSet protected by baseline conflict checks, a
mutation journal, the single-writer guard, and digest-bound safe revert. Historical
`patch_only` and `worktree` Runs and ChangeSets remain readable.

Running Agent status uses the product run store as its source of truth, while the
LangGraph checkpointer retains ordered history. The read endpoint normalizes parent,
timestamp, graph step, next nodes, interrupt metadata, and restore eligibility.
Restore clones the selected boundary into a fresh `run_id`/`thread_id`, restores the
frozen Run context and Effective Tool Pool, and invokes its next graph node.
`rollback` advances the current conversation through a new auditable Run; `fork`
creates a new conversation. Neither mode rewrites current workspace files—file
reversal remains a ChangeSet revert operation. Every restored Run freezes an independent
execution root; a cleaned `patch_only` copy is rebuilt from the currently registered
source, so graph restoration never masquerades as an implicit historical file snapshot.
Once the product record reaches a terminal
state, a late running snapshot cannot overwrite it; resume failures also retain
the original error and clean up the sandbox. Final metrics include elapsed time,
node/tool counts, changed files, recovered errors, and provider-reported input,
output, thinking, and total tokens.

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

OpenAI, DeepSeek, Anthropic, and Google adapters send `ToolSpec` definitions through each
provider's native Function/Tool Calling API. The Agent no longer asks production
models to manufacture a JSON tool plan in prompt text. Provider-specific
function calls are normalized to `LLMToolDecision`, including a stable tool-call
ID; dotted registry names receive provider-safe aliases and are mapped back
before execution. The fake provider retains deterministic rule planning for
offline tests.

Create/modify tasks must still call `sandbox.write_file` or
`sandbox.apply_patch` in an empty workspace; directory inventory uses
`repo.list_files`. The model-visible `sandbox.run_command` contract lists its
allowed executable basenames and positions commands as post-change validation.
A failed pre-change diagnostic command is returned for replanning and does not
trigger artifact finalization or an empty ChangeSet.
`change_planning` also has a runtime completion gate: a text-only final answer
is returned for explicit replanning until a Sandbox mutation succeeds. Three
unfulfilled attempts become `blocked`, never a zero-file `completed` result.
For Google Developer API tool-call preflight, the system-instruction text and
tool schemas are counted as an additional content part because that API rejects
`CountTokensConfig.system_instruction/tools`; the real generation request still
uses native system instructions and tools, so a Google fallback does not break
budgeting.
On cross-provider fallback, only Google's own original provider items are
replayed as native function calls with their `thought_signature`; calls and
results from other providers become explicit text observations, preserving
evidence without fabricating a Gemini signature.
Runtime collection of workspace status and diffs synthesizes one assistant
tool-history turn. DeepSeek thinking mode requires `reasoning_content` on every
tool turn, so a locally synthesized turn uses an empty string as the
provider-supported marker for “no private reasoning”; real provider reasoning
items are still replayed unchanged. OpenAI and Anthropic keep their valid
synthetic function/tool-use forms, while Google continues to downgrade foreign
or runtime calls to text rather than fabricating a `thought_signature`.
Provider JSON error messages are credential-redacted and length-bounded before
entering Run diagnostics, without retaining the request body.

The production planner may accept one consecutive batch of independent,
idempotent, approval-free `read_only` calls per turn. The batch is bounded by
`AGENT_MAX_READ_TOOLS_PER_ROUND` and the remaining hard call budget; the executor
runs those reads concurrently while replaying results in model-proposed order.
OpenAI enables `parallel_tool_calls` and Anthropic permits parallel `tool_use`,
but the harness still checks each ToolSpec and effective permission. Mutations,
validations, approval-bound tools, user input, and mixed read/write plans remain
limited to one accepted call per turn; extra calls receive `single_tool_turn`,
while reads beyond the batch cap receive `read_batch_limit`. Mutation prompts
still ask for one file and a small `sandbox.apply_patch` at a time instead of
embedding multiple complete files in one JSON argument. Provider adapters still
use their native structured-argument formats; this does not claim freeform
OpenAI `apply_patch` custom-tool support for DeepSeek, Anthropic, or Google.

Malformed tool-argument JSON is retryable. Output-limit finish reasons are
classified as truncation, and `LLMClient` retries within `LLM_MAX_RETRIES` after
adding a corrective one-tool/one-file/small-patch instruction and rerunning token
preflight and authorization. Failed-attempt usage still reaches the unified
ledger. Run errors retain only safe diagnostics—finish reason, argument length,
and JSON parse position—not the source-bearing raw argument.

Native models use one bounded observe/decide/act loop. Repository reads,
Sandbox mutations, validation commands, status, and diffs are selected in model
order inside the same tool transcript instead of isolated fixed phases:

```text
native tool call
→ EffectiveToolPool exposes only this Run's frozen, verified ToolSpec values
→ PermissionResolver resolves allow/ask/deny from ToolUseContext
→ ToolRegistry validation and execution
→ result/error linked by call ID
→ harness token cap shared by built-in and MCP results; full overflow becomes a Run artifact
→ provider-native tool result message
→ model observes and either calls another tool or answers
```

The default soft budget is 12 rounds/36 calls; it asks the model to converge but
does not stop execution. Hard limits are 24 rounds/72 calls, 900 seconds, three
no-progress rounds, and three consecutive failures. A hard stop reserves one
text finalization that still declares the same tools and forbids calling them
through the provider's own tool choice, because replayed tool_use/tool_result
blocks are rejected by providers when a request declares no tools. Exhaustion
therefore becomes `partial` or `blocked` instead of a false `completed`.
Configure these with
`AGENT_SOFT_TOOL_ROUNDS`, `AGENT_MAX_TOOL_ROUNDS`, `AGENT_SOFT_TOOL_CALLS`,
`AGENT_MAX_TOOL_CALLS`, `AGENT_MAX_ELAPSED_SECONDS`,
`AGENT_NO_PROGRESS_ROUNDS`, and `AGENT_MAX_CONSECUTIVE_FAILURES`. Model-output
phase limits use `AGENT_PLAN_MAX_OUTPUT_TOKENS`,
`AGENT_MUTATION_MAX_OUTPUT_TOKENS`, and `AGENT_FINAL_MAX_OUTPUT_TOKENS`.
Before any result enters the model transcript, `AGENT_TOOL_RESULT_MAX_TOKENS`
(default `2000`, minimum `64`) applies unconditionally to built-in and MCP tools alike. An
oversized result becomes a head/tail placeholder with its original token
estimate and `artifact_id`; the exact result remains available as a
`tool_result` Run artifact. Existing per-tool character caps remain a second
line of defense, and `agent_tool_results_truncated_total` counts these events.
“Exact” means the canonical JSON of the complete `ToolResult` that reached the
Agent Harness; it does not promise to recover Provider payload bytes already
truncated before the Harness. Small results below the per-result limit are not
externalized eagerly. `plan_tools` creates a content-addressed Artifact only when
eviction, fold, drop/truncate, or the single forced Provider-overflow recovery is
actually about to transform that body, and persists the Artifact additions and
reduced messages in the same LangGraph state update. No database migration is
required.
Above `AGENT_NATIVE_CONTEXT_MAX_CHARS` or the unified input allowance minus the
tool-schema share,
the harness reduces the transcript in a fixed order. It first replaces older tool
result bodies with one-line markers while keeping the newest
`AGENT_TOOL_RESULT_KEEP_RECENT` results complete (default `6`). If still over
budget, it folds complete assistant/tool groups, re-measures the fold, and then
drops whole groups or truncates bodies through the shared budget primitives.
Multi-call assistant turns remain atomic with all matching results. Initial user
requests, checkpoint restore directions, and pause/resume steering are verbatim and
non-truncatable; a Run blocks explicitly when those instructions cannot fit.

The model may read those bodies through the read-only, idempotent
`run.read_artifact` tool. Its only arguments are `artifact_id`,
`view=page|head_tail`, `offset_chars`, and `max_tokens` (default `800`, range
`64..2000`); it cannot supply a Run, conversation, Workspace, or actor identity.
The runtime searches only runtime-created, model-readable Artifacts inherited by
the selected checkpoint state. It performs no global hash lookup and never asks
the Run Store, so missing, cross-Run, wrong-type, or hash-corrupt values all fail
as `artifact_not_found`; an invalid offset returns
`artifact_offset_out_of_range`. An ID forged inside MCP output grants no access,
and legacy v1/v2 `RunContextSnapshot` values do not gain the new tool implicitly.
Read pages are ephemeral and can never become nested Artifacts. Trace/SSE/metric
metadata records only IDs, call/tool, ranges, character and token counts, hashes,
and error codes—never body text. Pause/resume, rollback, and fork therefore expose
exactly the Artifact state inherited from the selected checkpoint.

Folding is capped by `AGENT_NATIVE_MAX_COMPACTIONS` (default `3`). A transcript
that still cannot converge terminates as
`blocked/context_compaction_exhausted`. Provider context-length failures normalize
to `context_overflow` and receive exactly one forced reduction plus one retry.
Native reduction emits canonical SSE `context` events with a `stage` field; stages
inherited by a checkpoint branch are marked `replayed` with their source Run and
checkpoint. Related metrics use the `agent_native_context_*` prefix.
`AGENT_GRAPH_RECURSION_LIMIT` remains an independent graph safety fuse.

This phase does not expose a manual `/compact` command. Every ordinary CLI input
creates a new Run, so attaching a force flag to that Run cannot reduce an existing
Session or an older Run transcript. A real manual command requires the unified budget,
structured snapshot, and Session compression API to define its instruction semantics,
before/after token evidence, and observable outcome together; it is deferred to the
structured-snapshot phase.

One authority resolves and divides the model input allowance in `setup_workspace`.
It first measures explicit fixed shares for the system prompt and visible tool input
and output schemas, then assigns `LLM_CONTEXT_EVIDENCE_RATIO` (default `0.25`) and
`LLM_CONTEXT_HISTORY_RATIO` (default `0.15`) from what remains; the native tool
transcript receives the exact remainder. The named shares add back up to the one
allowance derived through `LLM_CONTEXT_INPUT_TOKEN_RATIO`, persist in run state and
the setup trace, and are read by every later layer without another window ratio.

Evidence and history are fitted field by field while the seed is assembled. Lower
ranked evidence sources are dropped before the final source's `text` is trimmed,
history token mode selects full normalized messages newest-first and truncates only the
last content field that cannot fit—without the static 600-character summary or
280-character per-message snippets. The RAG character cap is only a fallback when model
information is unavailable. The system prompt, current request, checkpoint directions,
and all steering stay verbatim, so seed JSON remains parseable. Provider overflow
recovery rebuilds optional seed fields at smaller shares before the one forced reduction
and retry. A window whose fixed overhead leaves no transcript capacity blocks before the
provider as `context_budget_too_small`.

The production boundary stays finite: Session assembly first freezes
`RunContextSnapshot.controlled_history` under its message ceiling and token budget.
Coding Runtime keeps that already-controlled input in checkpoint state without a
second 12-message slice, and the history share decides the native model view. Direct
Runtime callers get the same share-based projection; static message and character
limits apply only when model shares are unavailable.

```dotenv
AGENT_NATIVE_CONTEXT_MAX_CHARS=48000
LLM_CONTEXT_EVIDENCE_RATIO=0.25
LLM_CONTEXT_HISTORY_RATIO=0.15
AGENT_TOOL_RESULT_MAX_TOKENS=2000
AGENT_TOOL_RESULT_KEEP_RECENT=6
AGENT_NATIVE_MAX_COMPACTIONS=3
```

Every successful or failed result is returned under its call ID. Completed
`(run_id, call_id)` executions can be replayed from the memory or PostgreSQL
ledger; changed argument hashes are rejected. PostgreSQL also stores append-only
Run events. The model can invoke `agent.request_user_input` to enter
`waiting_input`, while users can pause, continue, cancel, or steer at safe tool
boundaries. `AGENT_APPROVAL_POLICY=always|on_request|never` controls approvals;
`never` blocks approval-requiring calls rather than silently authorizing them.

`ToolRegistry` validates complete Draft 2020-12 JSON Schemas at registration and
validates both input and output at execution. A validation failure reports the
path, the failed constraint, the schema-side expectation, and the type and size
of the rejected value, so credentials and file contents never travel back to the
model inside an error string. Tool specs also declare timeout, retry, and
idempotency behavior. Retries are limited to retryable failures on idempotent
tools; the same `run_id + call_id` replays a cached result and rejects argument
changes. A timeout is not a cancellation: tools run on their own daemon thread,
a timed-out worker is abandoned, and the result says the call may still be
running; while an abandoned side-effecting call is still alive, further
side-effecting calls in that Run return `tool_timeout_in_flight` instead of
racing it. MCP tools use the same registry contract: `structuredContent` is
preferred, text blocks are normalized, and `isError=true` becomes a stable tool
failure instead of a successful payload; the execution context travels under the
reserved `__tool_context__` parameter, so a server-declared `context` argument is
forwarded untouched.

### MCP lifecycle and transports

MCP uses the exactly pinned official Python SDK `mcp==2.0.0` to negotiate the
current `2026-07-28` protocol. `stdio` and stateless `streamable_http` are the
current paths. The fixed `2025-06-18` client is available only as the explicit
`stdio_2025_06_18` adapter; old HTTP+SSE requires `legacy_sse` plus
`legacy_compatibility=true` and is never a default fallback. The decision is
recorded in
[`docs/adr/0001-mcp-official-python-sdk-v2.md`](docs/adr/0001-mcp-official-python-sdk-v2.md).

`MCPConnectionManager` gives every Server its own connection, event loop,
connection/request timeouts, idempotent retries, backoff, circuit breaker,
catalog cache, cancellation, shutdown, and redacted status. One failed Server is
isolated during startup. Only a failed `required=true` Server makes
`/api/v1/health` return `503` with `ready=false`; an optional failure degrades
health without blocking startup. `tools/list` consumes every page, rejects
repeated cursors or tool names, sorts deterministically, honors
`ttlMs`/`cacheScope`, and supports explicit refresh. Calls preserve the
ToolRegistry call ID and expose stable timeout, cancellation, connection,
circuit, and tool-error codes.

HTTP Servers require an explicit host allowlist. HTTPS is the default;
redirects and proxy environments are disabled, and private/local resolutions
are rejected unless explicitly enabled. Credentials enter only through shared
`SecretStore` references in `header_refs`/`env_refs`, never through snapshots,
diagnostics, or object representations. Reserved/injected headers, dangerous
stdio variables, and inherited API keys are blocked. All MCP permission
annotations pass through the central `PermissionResolver`; missing or high-risk
hints conservatively become external side effects rather than authorization.

When MCP is enabled, open **MCP Connections** (`/#mcp`) to register,
edit, test/refresh, enable, disable, or delete a Server. The UI supports current
stdio, current Streamable HTTP, and both explicit compatibility paths while
showing per-Server state, protocol version, retry errors, and discovered versus
registered tool counts. UI writes require at least
`MCP_CONFIG_PATH=/path/to/mcp.json`; the file may be absent and the first save
creates it atomically with mode `0600`. With `MCP_ENABLED=true`, a save
immediately replaces that Server's connection and synchronizes the ToolRegistry.
Otherwise the configuration is persisted and shown as awaiting restart. Literal
environment variables/headers are separate from Secret inputs. Secret values go
only to the shared `SecretStore`; the config file and later GET responses retain
only references or key names.

The management API consists of `GET /api/v1/mcp/servers`,
`PUT /api/v1/mcp/servers/{name}`,
`PATCH /api/v1/mcp/servers/{name}/enabled`,
`POST /api/v1/mcp/servers/{name}/test`, and
`DELETE /api/v1/mcp/servers/{name}`. Each mutation affects only its target
Server; disable/delete closes that connection and atomically removes its dynamic
tools without rebuilding other Server lifecycles. Under the current
`AUTH_MODE=single_user`, management writes always belong to the fixed `owner` and
rely on Compose publishing only a loopback port. Direct unauthenticated loopback
and trusted local-gateway proofs remain compatibility modes, not the product path.

```json
{
  "mcp_servers": {
    "local-tools": {
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "example_mcp_server"],
      "env_refs": {"EXAMPLE_TOKEN": "keyring:mcp/example"},
      "required": false
    },
    "remote-tools": {
      "transport": "streamable_http",
      "url": "https://mcp.example.com/mcp",
      "allowed_hosts": ["mcp.example.com"],
      "header_refs": {"Authorization": "keyring:mcp/remote"},
      "required": true
    }
  }
}
```

### Project instructions

The Agent loads `AGENTS.md` from the workspace root toward focused file
directories. `AGENTS.override.md` replaces `AGENTS.md` in the same directory;
`CLAUDE.md` is a compatibility fallback only when neither AGENTS file exists,
so the existing AGENTS precedence is unchanged. Nearer directories are later
and therefore more specific. Multi-directory tasks retain each rule's scope.

Before queueing, `ExecutionContextFactory` freezes identity, bounded session
history/summary/model selection, Workspace revision/root/cwd/Git summary, safe
configuration version, project instructions, and additional directories into a
deeply immutable, JSON-round-trippable schema-v3 `RunContextSnapshot`, including
the Effective Tool Pool catalog/pool summaries and hashes. API, Worker, CLI/REPL,
SDK, and the Agent Loop share this contract. Worker tasks carry
only `run_id`; after restart they recover the persisted snapshot instead of
re-reading changed history, model preferences, or instruction files. Missing
Git, a non-repository directory, an unborn HEAD, or a status-probe failure is a
diagnostic rather than an unconditional Run failure.

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

`workspace_id` uses letters, digits, `_`, `-`, and `.`. Root paths are canonical
absolute paths and must remain under `WORKSPACE_ALLOWED_ROOTS` after symbolic
links are resolved. One canonical root can belong to only one workspace;
registering the same root under another ID returns `409`. List and detail
responses expose `status`, `role`, and `can_update`. The official Compose stack
fixes `WORKSPACE_ALLOWED_ROOTS=/workspaces` and maps `WORKSPACE_HOST_PATH` there;
the browser hides dot-directories and out-of-bound symbolic links.

Containers cannot open the host Finder on behalf of a browser user, so the
official Compose stack fixes `NATIVE_DIRECTORY_PICKER_MODE=disabled`. The frontend
uses the existing constrained web directory browser under `/workspaces`, and each
selected path still passes `WORKSPACE_ALLOWED_ROOTS`. The loopback and trusted
local-gateway native picker implementations remain compatibility code only.

A Workspace root belongs to the filesystem that actually executes the Agent. If
a future control plane runs in the cloud while code remains on a user's computer,
a local Agent or desktop companion must own directory authorization and execution;
the cloud service's Finder cannot select a directory on the browser's machine.

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

In `single_user` mode every session and Workspace operation belongs to the fixed
owner; request bodies, `X-User-ID`, and `X-Authenticated-User` cannot switch
identity. Workspace RBAC and Worker reauthorization remain implemented underneath,
but multi-user collaboration is outside this MVP. At startup the fixed owner gains
administrator membership for every persisted Workspace, including soft-removed
records. This single-user compatibility takeover retains legacy members and does
not rewrite saved root paths.

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

Before presenting Slash capabilities, the browser reads the effective catalog:

```text
GET /api/v1/agent/composer-capabilities?conversation_id=sess_xxx&workspace_id=project
```

The response contains only Skill commands and MCP tools available in that context.
An Agent POST may additionally include `skill_name`, `skill_arguments`, or
`preferred_tool_name`; a preferred tool is user intent, not authorization, and
cannot bypass the effective pool or approval.

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
GET  /api/v1/agent/runs?limit=50
GET  /api/v1/sessions/{conversation_id}/agent/runs/latest
GET  /api/v1/agent/runs/{run_id}/events?after={cursor}
GET  /api/v1/agent/runs/{run_id}/events/stream?cursor={cursor}
GET  /api/v1/agent/runs/{run_id}/checkpoints?limit=100
POST /api/v1/agent/runs/{run_id}/checkpoints/{checkpoint_id}/restore {"mode":"rollback|fork","message":"optional direction"}
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
Terminal results project every call into a `tool_selected` event with exact
arguments followed by its complete `tool_result` or `tool_error`. Artifact-read
result events reuse the body-free observability projection for `run.read_artifact`,
preserving artifact and integrity metadata without leaking page content.
Approval resume appends an `approval_decided` event, including the actor and original
request, before the Run is queued again. The audit page can therefore replay
facts by sequence without relying on transient frontend state. Recent-Run
listing is filtered by conversation ownership in `QueryService`. Orphan Runs
whose conversations were deleted are treated as invisible instead of failing
the collection query.
Final statuses are `completed`, `partial`, `blocked`, `cancelled`, and `failed`;
suspended interaction states are `waiting_approval`, `waiting_input`, and
`paused`. When a saved session is opened, the frontend uses the conversation-level
latest-Run endpoint to restore the most recent run, reattach approval/input controls,
and restore the persistent Run ribbon. Checkpoint history is actor-authorized per
Run; terminal boundaries without a next node remain inspectable but are not
restorable, and an active source Run must first reach a pause boundary. Run observers capture both Run and
conversation IDs so late events from a previously viewed session cannot overwrite
the active one.

Responses expose `context_route`, `selected_knowledge_base_ids`, and
`context_sources`. Knowledge chunks use `kind=knowledge_chunk` and include
optional `knowledge_base_id`, `document_id`, and `score` provenance fields.
The removed `repository_id`, `rag_context`, and per-Run `workspace_mode` Agent
fields are not accepted. Run status still returns the server-frozen final mode and
execution root as audit metadata.

A ChangeSet is an audit and recovery boundary after tool approval. It stores the
untruncated patch, SHA-256, changed paths, pre/post hashes, source/execution roots,
workspace revision, Git/branch/worktree metadata, validation summary, and status.
Viewers may inspect it; only editors may reject or revert it. New `direct` and
`worktree` records are captured as already `applied` and cannot be applied twice.
Revert validates the digest and post-write hashes, preserves newer user edits on
conflict, and is idempotent. Historical ready ChangeSets retain apply compatibility.
No commit, push, PR, merge, or deployment is performed automatically.

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

### Execution workspaces and command isolation

Before project instructions or the tool pool are built, every Run selects an
execution workspace and freezes its source root, mode, execution root, Git HEAD,
branch, and cleanup policy in RunContext v4. Repository reads, mutations,
commands, status, and Diff always use that same execution root. The registered
source root remains the authorization boundary and cannot be replaced by model
or request parameters. The current product fixes the mode to `direct`; the
retained audit modes are:

- `patch_only`: copy regular, non-sensitive files into a disposable per-Run
  directory, export a ChangeSet at terminal state, then delete it;
- `direct`: read, write, and execute in the registered source root, with changes
  immediately visible to other local processes;
- `worktree`: require a clean Git repository, create and retain a `codex/`
  branch worktree from the frozen HEAD, and leave the current checkout unchanged.

Real `.env` files, credentials, private keys, symbolic links, unreadable paths,
sockets, FIFOs, and other special files are rejected or skipped and recorded.
Writes accept an optional `expected_sha256` and validate both the Run baseline
and current bytes; patches additionally validate paths, context, and pre-write
hashes. Same-directory temporary files, `fsync`, atomic replacement, and a
durable pre-write mutation journal protect updates. `direct` has a per-Workspace
single-writer lock, so external edits and concurrent Agent writers fail without
overwriting content.

The current product supports only `direct`: after exact tool approval, the code
Agent edits the registered source root and its terminal ChangeSet records the write
with conflict-safe revert. The page and Run request expose no execution-location
choice. The official settings are:

```dotenv
CHANGE_SET_STORE=postgres
LIVE_WORKSPACE_WRITES_ENABLED=true
AGENT_WORKSPACE_DEFAULT_MODE=direct
AGENT_WORKSPACE_ALLOWED_MODES=direct
CHANGE_SET_APPLY_MODE=patch_only  # historical ChangeSet apply compatibility only
CHANGE_SET_MAX_FILES=100
CHANGE_SET_MAX_PATCH_CHARS=1000000
CHANGE_SET_WORKTREE_PARENT=
CHANGE_SET_BRANCH_PREFIX=codex/
```

`patch_only`, `worktree`, and historical apply remain as persistence compatibility
code. Compose locks the only allowed mode to `direct`, and current product users
receive neither a selector nor a per-Run override field.

For `direct` and `worktree`, the ChangeSet records a write that already happened:
it is captured as `applied` and is never applied twice. The conversation shows
the actual source or worktree path and branch and can call
`POST /agent/runs/{run_id}/changes/revert`. Revert revalidates the patch digest
and post-write hashes, preserves newer user content on conflict, and is
idempotent. Historical `patch_only` records explicitly remain unwritten and
non-promotable. Historical ready ChangeSets retain their original apply contract
without a database migration.

`SANDBOX_MODE=local` executes inside the App container and is intended only for
repositories owned and trusted by the user. It runs an executable basename from `SANDBOX_ALLOWED_COMMANDS` with
a minimal environment, fixed maximum timeout, bounded output capture, and
process-group termination. Shell wrappers such as `sh -c` and `bash -c` are
rejected. An allowlisted interpreter can still execute arbitrary trusted
repository code, so the App container is not an adversarial isolation boundary.
The current Compose stack does not mount the Docker Socket and makes no claim of
supporting untrusted repositories.

The retained `SANDBOX_MODE=docker` implementation controls command-process isolation,
not the file target. It
mounts the same Run execution root at `/workspace`, disables networking, uses a
read-only container root and the caller's non-root UID/GID, drops Linux
capabilities, enables `no-new-privileges`, and applies PID, CPU, memory, and
tmpfs limits.

## Project memory

The memory architecture implements **L0 → L1 → L2 → L3**. L0 stores original
messages and supports explicit search; L1 is governed project knowledge for the
current workspace/revision; L2 deterministically composes active L1 records into
a user/workspace scene; and L3 combines those scenes with active user facts into
a bounded profile. Live source, `.workflow/tasks`, Agent Runs, checkpoints, and
ChangeSets remain authoritative for task progress; L2 only summarizes active L1.
See the editable
[`architecture overview`](docs/architecture/local-layered-memory-overview.drawio)
and its [`PNG`](docs/architecture/local-layered-memory-overview.png); the same
directory also contains editable context-assembly, write-governance, and
persistence detail diagrams.

Project memory is shared by authorized members of one `workspace_id`. Supported
kinds are `architecture_fact`, `constraint`, `decision`, `convention`,
`task_outcome`, and `incident_lesson`. Full source files, temporary discussion,
assistant speculation, credentials, private keys, tokens, connection strings,
and complete environment-variable values are rejected.

Modes are:

- `off`: no extraction or retrieval;
- `shadow`: extract review candidates but do not inject them;
- `review`: retrieve only active records, normally after human confirmation;
- `auto`: extracted candidates that pass safety and quality gates become active.

User-created records and explicit “remember/记住” requests are active with
confidence `1.0`. Extractions below `PROJECT_MEMORY_CANDIDATE_THRESHOLD` are
discarded; review mode keeps eligible candidates reviewable, while auto mode
activates them directly and promotes existing candidates when selected. Equal canonical content adds evidence; authoritative
conflicts supersede the old record, while uncertain conflicts remain
candidates. Source-backed mutable facts are hash-checked before injection and
become `stale` after the source changes. Long-unconfirmed records are
down-ranked rather than deleted solely because of age.

Retrieval combines dense and lexical recall using weighted RRF, then reloads
every result from the configured L1 source of truth to verify workspace,
revision, status, expiry, and version. PostgreSQL/Qdrant remain available for
distributed deployments. The single-process local profile uses SQLite FTS5
BM25 and float32 vector BLOBs carrying model, dimensions, and memory version;
cosine similarity is computed over the small current workspace/revision set.
FTS5 and vector failures degrade to bounded `LIKE` and lexical-only recall.
Every eligible candidate receives an explainable final score:

```text
0.65 × normalized relevance
+ 0.20 × exponential recency
+ 0.15 × normalized importance
```

Recency uses `last_confirmed_at` (falling back to `updated_at`) with a
configurable 180-day half-life. Candidates are globally ranked before the
six-result/3,000-character budget is applied, and Chat/Agent provenance exposes
the final score plus all three components. Dense retrieval failure degrades to
lexical search, and memory failure never fails the main Chat or Agent answer.

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

### L0 conversation search and the L2/L3 profile pipeline

L0 indexes the original `messages` table rather than copying another log.
SQLite uses aligned CJK n-grams on writes and queries and rebuilds old FTS data
during the v2 migration; PostgreSQL uses escaped `ILIKE` substring matching.
An empty query lists recent messages. Both listing and search are user-scoped,
can constrain workspace/session, and are never injected automatically. They are available through
`GET /api/v1/memory/conversations/search` and the read-only
`memory.search_conversations` tool.

L3 is a separate `UserMemory` domain with `profile_fact`,
`communication_preference`, `tooling_preference`, `workflow_preference`,
`standing_goal`, and `personal_constraint`. Manual and explicitly global
remember requests become active immediately; ordinary preferences are active in
auto mode and candidates in review mode. Every L1 mutation asynchronously
rebuilds the workspace's `UserMemoryScene` (L2), then `UserProfileSnapshot` (L3)
is rebuilt without an LLM from L2 scenes and active user facts, within the
configured character budget. Chat and Agent inject it only as untrusted historical preferences;
it cannot override the current request, policy, project instructions,
permissions, or live evidence. Agent answer output can produce L1 evidence but
never L3 inference. Credentials, complete environment values, privilege
escalation requests, and prompt injection are rejected before L1/L3 storage.

```text
GET/PATCH /api/v1/users/me/memory-settings
GET       /api/v1/users/me/memory-scenes
GET/POST  /api/v1/users/me/memories
GET/PATCH/DELETE /api/v1/users/me/memories/{memory_id}
POST      /api/v1/users/me/memories/{memory_id}/confirm
POST      /api/v1/users/me/memories/{memory_id}/reject
GET       /api/v1/users/me/profile
POST      /api/v1/users/me/profile/rebuild
GET       /api/v1/memory/conversations/search
```

The Memory Workbench mirrors the backend layers with L1, L2/L3, and L0
views. Project and user facts use an asset-list/detail-governance split with
active/candidate counts, status and kind filters, evidence, versions, and
contextual actions. The profile view previews the exact deterministic snapshot
visible to the model, while conversation search pairs user-scoped hits with a
full message detail panel and automatically loads recent messages. The profile
view exposes L2 scenes and their L1 source counts. L1 extraction is fixed to the
automatic path, so the UI no longer exposes workspace mode, manual reindex, or
refresh controls; opening the view loads it automatically.

The Docker MVP enables the complete pipeline: PostgreSQL stores L0/L1 facts,
Qdrant stores rebuildable L1 vectors, and a mounted SQLite v2 database stores
L2 scenes and L3 profiles:

```dotenv
PROJECT_MEMORY_ENABLED=true
PROJECT_MEMORY_MODE=auto
PROJECT_MEMORY_STORE=postgres
PROJECT_MEMORY_VECTOR_STORE=qdrant
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
USER_MEMORY_ENABLED=true
USER_MEMORY_MODE=auto
USER_PROFILE_MAX_CONTEXT_CHARS=1500
```

The default [`.env.example`](.env.example) describes the official single-node
Compose combination. [`.env.local-memory.example`](.env.local-memory.example)
provides a single-process variant where all structured state uses SQLite.

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

Revision `20260810_0020` adds the immutable `run_context_snapshot` JSONB column
to `agent_runs`, persisting identity, session/summary/model, Workspace/Git,
safe configuration, instructions, and authorized additional directories for
Run-ID-only Worker recovery.

Revision `20260813_0021` adds `messages.source_run_id` and a per-Run/role unique
constraint for atomic Query starts and idempotent final-assistant recovery.

Revision `20260820_0022` adds `registered_models.max_output_tokens`. Existing
DeepSeek rows are backfilled to 8192, fake rows to 4096, and other rows to
16384. The revision ships with this change but was not applied to the current
database by this task.

Historical migrations remain in the revision chain. The PostgreSQL result
loader alone adapts historical JSON containing `repository_id`/`rag_context`;
new APIs and runs expose only the workspace contract.

The current single-node product does not start Celery. Its official combination is:

```dotenv
TASK_QUEUE_BACKEND=in_process
SESSION_REPOSITORY=postgres
AGENT_RUN_STORE=postgres
CHANGE_SET_STORE=postgres
DOCUMENT_STORE=postgres
WORKSPACE_STORE=postgres
LANGGRAPH_CHECKPOINTER=postgres
RAG_VECTOR_STORE=qdrant
PROJECT_MEMORY_ENABLED=true
PROJECT_MEMORY_MODE=auto
USER_MEMORY_ENABLED=true
USER_MEMORY_MODE=auto
PROJECT_MEMORY_STORE=postgres
PROJECT_MEMORY_VECTOR_STORE=qdrant
WORKSPACE_ALLOWED_ROOTS=/workspaces
```

The persistent runtime assigns one responsibility to each database:

| Component | Responsibility |
| --- | --- |
| PostgreSQL | Sessions/messages, user defaults, per-session configuration and rolling summaries, Agent runs/events/tool ledger/ChangeSets plus immutable model and Run-context snapshots, workspace/knowledge-base catalogs, project-memory facts/evidence/jobs/outbox/audit, document/chunk metadata, lexical search, and LangGraph checkpoints |
| Qdrant | Separate knowledge and project-memory vector collections; project-memory payload is minimal and rebuildable |
| SQLite | Retained compatibility/test adapters; the product may use it only for the not-yet-migrated single-node user-memory implementation |
| Redis/Celery | Retained multi-Worker extension; not started by current Compose |
| Chroma | Retained embedded vector alternative; not selected by current Compose |

`RAG_VECTOR_STORE` still supports Qdrant, Chroma, or memory, but official Compose
locks Qdrant and does not dual-write another vector backend.

At product startup, the one-shot `migrate` service reuses the existing Alembic
chain; the App starts only after migration succeeds:

```bash
docker compose up -d --build
docker compose ps
```

Only the App publishes `127.0.0.1:${SELF_HOSTED_PORT}`. PostgreSQL and Qdrant
have no host ports. Database credentials come from the user's local `.env` and
are injected only into the private Compose network. Adminer, Gateway, Redis, and
Celery are absent from the current service set.

The in-process queue registers Agent run/resume, conversation compression, memory
extraction, and project-memory index-Outbox tasks with the same task semantics as
the retained Celery adapter. Workspace, configuration, and tool context are still
frozen before execution. Restarting the App interrupts work that is running or
queued at that moment; persisted Runs, events, and Outbox evidence remain, but
this MVP does not promise automatic recovery of every interrupted Run.

### Runtime assembly and lifecycle

FastAPI `create_app()` and the retained process-local Celery Worker adapter both enter
the same `ApplicationFactory` through
`build_runtime(settings, role=api|worker|cli)`. Repositories, the LLM, model
registry, Workspace, RAG, MCP, Tool Registry, LangGraph checkpointer, Agent
runtime, and business services therefore share one dependency graph. CLI print,
REPL, and SDK are active thin Query Kernel adapters. A shared non-owning
`ToolPoolBuilder` is injected into context creation, Query recovery, and the
decomposed Agent Loop; RuntimeContainer still owns and closes Registry/MCP resources.

The returned `RuntimeContainer` explicitly owns its immutable resolved config,
redacted snapshot, shared `SecretStore`, `MCPConnectionManager`,
`ExecutionContextFactory`, services, and resources and records `config_loaded`,
`stores_ready`, `mcp_ready`, `tools_ready`, and
`agent_ready` startup checkpoints in order. FastAPI lifespan shutdown, Worker
shutdown, and partial-startup rollback use the same idempotent `close()`;
cleanup callbacks run strictly in reverse registration order and each resource
is closed at most once. Tests can still inject the LLM, RAG service, Agent
runtime, and directory picker into `create_app()`, or override component
builders on `ApplicationFactory`.

## Retained extension implementations

The `gateway/`, Celery/Redis, and local SQLite profile code and tests remain to
demonstrate later multi-user, multi-Worker, or alternate-storage evolution. They
are not services in the current Docker MVP. The Go gateway can still be tested
independently:

```bash
go run ./gateway/cmd/gateway
go test ./gateway/...
go vet ./gateway/...
```

`AUTH_MODE=trusted_header`, OIDC/JWKS, local-gateway assertions, and multi-Worker
reliability tests remain valid implemented extension evidence, but they are not
the default deployment and do not demonstrate production-scale operation. A
future public or multi-user deployment must revisit authentication, tenant
authorization, secrets, backups, observability, and untrusted execution instead
of exposing the `single_user` stack.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall ai_agent_platform tests evals
docker compose --env-file .env.example config --quiet
bash -n scripts/start.sh scripts/start-local.sh
node --check ai_agent_platform/static/app.js
git diff --check
```

After changing the retained Go gateway compatibility implementation, also run
`go test ./gateway/...`.

Run offline Agent evaluations with:

```bash
.venv/bin/python evals/run_evals.py
.venv/bin/python evals/run_trajectory_evals.py
.venv/bin/python evals/run_memory_evals.py
```

The three suites answer different questions. `run_evals.py` is the L0 pipeline
regression and RAG retrieval gate. `run_trajectory_evals.py` is the L1
trajectory layer: it grades the process against declared constraints (required
and forbidden tools, ordering, step ceilings) rather than a golden answer, and
separates proposed, accepted, executed, succeeded, failed, suppressed, denied,
and pending-approval calls. Only calls joined to a real `ToolResult` are
executed. It also builds a successful-read ledger and reports citation content
accuracy, answer-path grounding, and fully grounded cases separately.
`run_memory_evals.py` gates project-memory quality. All three run on the fake
provider, so a passing run is not evidence of answer quality.

The same L1 suite also runs **inside the app against a registered model**, from
the 评测 page: pick a registered provider/model pair, run it, and the page shows
lifecycle counts, citation metrics, token/time totals and averages, alerts,
history, and per-case evidence. The explicit evaluation context excludes real
profile/history, user/project memory, and global knowledge bases, then removes
temporary sessions, workspace state, and files. A real-model run is billed by
token and only one may run at a time. Baselines are manually pinned and keyed by
provider + model + suite + evaluator version; legacy rows are not compared with
evaluator v2. Critical runs require an explicit forced pin. The endpoints
are `/api/v1/evals/catalogue`, `/api/v1/evals/runs`,
`/api/v1/evals/runs/{run_id}` and `/api/v1/evals/runs/{run_id}/baseline`;
the settings are `EVAL_STORE`, `EVAL_FAULT_INJECTION_ENABLED` and
`EVAL_WORKSPACE_ROOT`.

The layered plan is in [evals/DESIGN.md](evals/DESIGN.md); metric definitions
and the compatibility boundary for legacy measurements are in
[evals/README.md](evals/README.md).
