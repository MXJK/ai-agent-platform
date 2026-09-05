# Cogent

[简体中文](README.md) | **English**

Cogent 0.2.0 replaces the Agent kernel while retaining the platform's model
registry, authentication, workspaces, Run/Event storage, ChangeSets, MCP management
and independent RAG. The internal Python namespace remains `ai_agent_platform`.

The refactor supports local use. See the [refactor task](.workflow/tasks/COGENT-AGENT-REFACTOR.md)
for verification evidence and limits. Existing installations need the schema upgrade below.
This task has not released the project or migrated the existing user database.

## Single-node Docker start

The only supported product path is single-user, single-instance Docker Compose
self-hosting. The persistent services are the FastAPI/Web UI App, PostgreSQL, and
Qdrant; a one-shot `migrate` service applies the existing Alembic chain. Agent work
uses the in-process bounded queue, so no Go gateway, Redis, Celery Worker, or
Adminer is started.
The App image installs `requirements.self-hosted.txt`, excluding Celery/Redis,
Chroma and OS keyring. It includes SentenceTransformer/Torch for the default BGE-M3
document embeddings. Optional adapters remain in the full development set.

```bash
cp -n .env.example .env
mkdir -p workspaces
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml ps
```

Open `http://127.0.0.1:8000`. Only the App is published to host loopback;
PostgreSQL and Qdrant remain private to the Compose network. Set
`WORKSPACE_HOST_PATH` to one trusted host directory, mounted as `/workspaces`; the
existing web directory browser registers projects below that root without trying
to open the host Finder from a container.

The official `RUNTIME_PROFILE=custom` combination reuses the existing PostgreSQL
repositories, Qdrant vector stores, and `in_process` task queue. Project memory is
enabled, and independent user memory is enabled. Cogent does not inject either service. Provider API keys are entered only in Model
Management; Compose encrypts them in the private persistent `app_state` volume while
PostgreSQL stores only opaque references. Its model catalog starts empty; register and
enable a Provider connection and a tool-capable model in the UI before sending a task.

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

For an existing installation, back up PostgreSQL, then run:

```bash
docker compose -f docker-compose.yml build app migrate
docker compose -f docker-compose.yml run --rm migrate
docker compose -f docker-compose.yml up -d app
```

Migration must reach `20260903_0028`; health alone does not verify schema compatibility.
Open http://127.0.0.1:8000, add a Provider connection and register/enable a tool-capable model,
register your mounted project under `/workspaces`, create a session and select that workspace.
Persistent model catalogs start empty; Fake bootstrap is limited to memory tests.
Try `/help`, `/status` or `/plan` before a coding task. The default permission mode asks before writes.
User Cogent settings/memory survive container recreation in the `cogent_user_state` volume.
Explicit `-f docker-compose.yml` avoids machine-specific development overrides.

## CLI, REPL, SDK, and process entrypoints

Install locally with `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.txt`
and `.venv/bin/python -m pip install --no-deps -e .`. Activate `.venv` before using the commands below.
Local configuration does not automatically connect to Compose stores. To share the container setup,
use `docker compose -f docker-compose.yml exec app cogent --workspace /workspaces/your-project`.

The published commands are `cogent` and `cogent-api`; old script names are removed.

```bash
cogent --workspace /absolute/path/to/project
cogent --workspace /absolute/path/to/project --print "Explain the entrypoints"
cogent-api --host 127.0.0.1 --port 8000
```

The default interface is Textual. Noninteractive output, the compatibility REPL,
AgentSDK.query(), and the web API all use QueryService. Web clients submit to
POST /api/v1/agent/runs and subscribe to Run SSE. /api/v1/chat/stream returns 404.

Shared commands: /help, /status, /clear, /compact, /mcp, /memory, /session,
/skill (also /skills), /tools, /permissions, /resume, /plan, /review, /rewind,
/sandbox. /exit is local to the CLI.

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

Priority: project .cogent/skills > ~/.cogent/skills >
read-only compatibility with ~/.ai-agent-platform/skills > built-ins.
The existing CRUD API/UI remains; new files target ~/.cogent/skills.
Existing Skills are not moved automatically.

.md, SKILL.md, skill.yaml + prompt.md, argument substitution, hot reload and
slash commands are supported. Only inline execution is supported; fork requests
are explicitly rejected. .agents/skills is not read, keeping Codex Skills separate
from runtime Skills. Skill content never grants tools or overrides authorization.

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

Low-level network failures are persisted with safe messages and stable granular
codes without exposing hosts, certificates, proxies, or request content. Connect,
read, write, and pool-wait timeouts use `llm_connect_timeout`, `llm_read_timeout`,
`llm_write_timeout`, and `llm_pool_timeout`. DNS, TLS, certificate verification,
connection, and proxy failures use `llm_dns_error`, `llm_tls_error`,
`llm_tls_certificate_error`, `llm_connection_error`, and `llm_proxy_error`.
Post-connect read, write, close, remote/local protocol, and response decoding
failures use `llm_read_error`, `llm_write_error`, `llm_close_error`,
`llm_remote_protocol_error`, `llm_local_protocol_error`, and
`llm_decoding_error`. Certificate verification and local protocol errors are not
retryable; the other errors above are retryable by default.

Each granular code is accepted as an exact `LLM_RETRY_POLICY_JSON` override. For
backward compatibility, granular timeout codes fall back to `llm_timeout`, while
the other network codes fall back to `llm_transport_error`, before `default` /
`LLM_MAX_RETRIES`. The map also supports `rate_limit`, `llm_server_error`,
`token_count_failed`, the three tool-output correction errors, and
`llm_provider_error`. Unknown keys, negative values, and non-integers fail during
startup. Both normal HTTP and SSE 429/5xx responses accept delta-seconds or
HTTP-date `Retry-After` values.
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

Registry rows store the context window and output limit separately. Normal Cogent calls
request LLM_MAX_OUTPUT_TOKENS; output-truncation recovery requests up to the model limit,
capped at 64K. Remaining context and the usage ledger can reduce that request. Retired
plan/mutation/final phase budgets no longer drive the Cogent loop.

Session and workspace budgets count every ledger record attributed to that
scope:

```dotenv
SESSION_TOKEN_BUDGET=100000
WORKSPACE_TOKEN_BUDGET=1000000
TOKEN_BUDGET_ACTION=reject
```

`0` disables a scope. With `reject`, a request that cannot leave at least one
output token is rejected before generation. An accepted async Run becomes failed with a
budget error; its user message remains. Allowed calls cap output to the remaining budget. With `downgrade`,
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

QueryService freezes identity, model selection, the execution root and tool pool. CogentRuntime
uses the canonical RegistryClient async stream over existing Provider adapters. Each complete
response is persisted before batch preflight. Adjacent reads may run concurrently; writes and
commands remain ordered. Model output ends the turn when no tools remain. The retired graph
planner, automatic RAG injection and completion contract are no longer part of the runtime.

Modes are default, acceptEdits, plan and bypassPermissions. Platform hard denials always apply;
approvals bind Run/call/tool/argument hashes and are rechecked before execution. Uncertain
side effects are blocked rather than automatically repeated. OS sandbox support attempts
Seatbelt or bubblewrap with networking disabled. The official trusted-repository container
reports unavailable if bubblewrap is absent and still requires command approval.

MCP chooses eager for small catalogs, native deferred search for supported official Anthropic
models, and dispatch/ToolSearch otherwise. COGENT_MCP_LOADING can select a strategy; unsupported
native combinations fall back to dispatch. Loaded tools persist across checkpoints. MCP transport,
Secret and permission metadata remain owned by the platform.

Provider-owned public thinking is streamed separately from answer text; opaque signatures and
encrypted data never become visible reasoning. Canonical messages retain tool pairing, source
provider metadata, cache fields and raw usage. Output truncation retries at most three times up
to the registered output limit capped at 64K. Context overflow may compact and retry; cumulative
Token budget rejection remains an error. Large results are registered hash-checked files.

Run/Event and runtime snapshots commit atomically. SQLite uses file leases and PostgreSQL uses
advisory leases; process death releases ownership. Complete tool results replay once. Startup
requeues durable work, preserves approval/input/pause waits and reconciles final message projection.
Legacy langgraph-v1 history stays read-only, with unfinished records projected as blocked.

Cogent file memory is independent of RAG/ProjectMemory/UserMemory. Project memory files live in
.cogent/memory; user files are isolated under ~/.cogent/memory/.users/<identity hash>. MEMORY.md
is capped at 200 lines/25KB. Restricted maintenance persists validated responses and write plans,
recovers without repeating completed requests, and removes duplicate/stale index links. Unreferenced
topic files are retained. No Bash, MCP or ordinary workspace tools are exposed to maintenance.

/plan restricts writes to the plan file; /review is read-only. /rewind previews and requires approval,
rejects hash conflicts, and appends a logical conversation branch. Use a file snapshot ID for files/all,
or `/rewind <completed-run-id> conversation` for a pure conversation branch. History is not deleted.

Agent-only and independent RAG evaluations are separated; existing retrieval data and gates remain.
SQLite and isolated PostgreSQL crash/resume/compaction/lease tests cover the persisted boundaries.
Real Provider quality and production capacity are separate from deterministic protocol tests.

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

The event stream uses resumable cursors. Graph-node wrappers append
`node_started` and `node_completed` at execution boundaries and persist a safe
`reasoning_summary`. Tool calls append idempotent `tool_selected`, `tool_started`,
and `tool_result` or `tool_error` events by call ID. Provider text is stored in
bounded `answer_delta` batches while it is generated, tentative tool-turn text
is cleared with `answer_reset`, and the accepted answer ends with
`answer_completed`. Tool arguments execute only after complete JSON aggregation
and validation. Once public text has been emitted, the request is not silently
retried or moved to another model. Stable event keys prevent worker replay and
terminal projection from duplicating execution facts.
The browser reduces each event type into the current stage, live activity list,
and answer body. The Code Agent in-message card renders only that live activity
list and clears an activity's running marker when `node_completed`, `tool_result`,
or `tool_error` arrives. It fetches one complete Run snapshot at a terminal state and
falls back to status polling if the stream fails or ends early. Provider-private
chain-of-thought never enters the event protocol. Artifact-read result events
reuse the body-free observability projection for `run.read_artifact`, preserving
artifact and integrity metadata without leaking page content.
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

New Cogent results omit context_route, selected_knowledge_base_ids and
context_sources. Agent requests cannot select knowledge bases. Historical
payloads remain readable. Checkpoints are inspectable; file/conversation rewind
uses /rewind with approval and hash verification rather than old graph restore.

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

This is the retained standalone platform memory subsystem, not Cogent file memory.
Its APIs, data, indexes and evaluation semantics remain. Cogent neither injects
these facts/profiles nor triggers the retired Chat/Agent extraction hooks.

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

Extraction deduplicates additional evidence by source kind, source ID, and path
before selecting at most five sources. The first complete span/hash is retained;
different hits in the same file are not spliced together. PostgreSQL ignores
duplicate evidence IDs and existing source unique-key conflicts while other
errors still roll back the transaction. This does not automatically replay old
failed jobs; a completed job with zero stored candidates added no memories.

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
six-result/3,000-character budget is applied by this independent retrieval service.

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

Cogent does not invoke this retrieval/extraction chain; /chat/stream is removed.

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
configured character budget. These profiles remain independent platform data and are not injected into Cogent.

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
stored by the independent service (not read by Cogent), while conversation search pairs user-scoped hits with a
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

Cogent compaction is independent of this platform memory subsystem.

## Independent knowledge base

RAG answer, indexing and evaluation use the independent RAG service and model
registry, never the Cogent loop. Agent requests selecting knowledge bases are
rejected. Existing RAG data and indexes are not rebuilt or migrated.

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

Revision 20260903_0028 adds versioned Cogent state and snapshots; SQLite uses schema v4.
No live migration was applied in this task. Review and back up the database before approval.

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

Revision `20260825_0025` adds `model_probe_stats`, persistently separating manual
and periodic fixed-prompt measurements from live-request latency samples. The
revision ships with this change and was not applied to the current database.

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
| PostgreSQL | Sessions/messages, user defaults, per-session configuration and rolling summaries, Agent runs/events/tool ledger/ChangeSets plus immutable model and Run-context snapshots, workspace/knowledge-base catalogs, project-memory facts/evidence/jobs/outbox/audit, document/chunk metadata, lexical search, and Cogent runtime snapshots |
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
registry, Workspace, RAG, MCP, Tool Registry, Cogent Agent
runtime, and business services therefore share one dependency graph. CLI print,
REPL, and SDK are active thin Query Kernel adapters. A shared non-owning
`ToolPoolBuilder` is injected into context creation, Query recovery, and the
Cogent loop; RuntimeContainer still owns and closes Registry/MCP resources.

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
