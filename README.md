# AI Agent Platform

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
output, thinking, and total tokens per response. Agent polling also merges live
LangGraph checkpoint trace so fast runs still play completed stages in order
before the final answer appears.

Token usage is also persisted across both response modes. The sessions page
shows cumulative input, output, thinking, and total tokens for every
conversation plus the estimated size of the bounded conversation context that
would be injected into the next request. The operations page aggregates
explicitly attributed Chat and Agent usage for every registered workspace.
Context size uses the documented local `unicode_heuristic_v1` estimate; provider
usage remains the authoritative actual-request usage signal.

The browser workspace also includes:

- managed knowledge-base catalog, multi-file upload, hybrid search, answers,
  citations, and index-job status;
- a project-memory governance page with mode/status/type filters, evidence,
  confidence, optimistic edits, confirm/reject/forget, and index repair;
- local workspace folder selection constrained by `WORKSPACE_ALLOWED_ROOTS`;
- Agent run details, approval risk, validation artifacts, errors, and metrics;
- safe Markdown rendering, response cancellation, responsive navigation, and
  accessible textual status indicators.

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
`thinking_level`. The UI can override the server default. SSE responses emit
heartbeats while the provider is idle, report thinking tokens separately, and
return an explicit `max_output_tokens` error instead of a normal completion
when Gemini finishes with `MAX_TOKENS`.

## Model routing

`LLMClient` now delegates model choice to an independent `ModelRouter`. Every
request is processed in this order:

```text
request requirements
→ capability filter (tool calling, structured output, context window)
→ quality / cost / latency ranking
→ provider health and circuit-breaker filter
→ selected model + route trace
→ provider call; pre-delta failure may try the next cross-provider candidate
```

The model table is supplied through `LLM_MODEL_CATALOG_JSON`. Without it, the
application derives one conservative entry from `LLM_PROVIDER`, `LLM_MODEL`,
and `LLM_MODEL_CONTEXT_WINDOW_TOKENS`, preserving single-model local startup.
A deployment that wants actual routing must configure at least two entries.
This abbreviated example is formatted for readability; `.env` values must keep
the JSON array on one line:

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

Prices, quality scores, and latency estimates are operator-maintained routing
inputs, not live provider facts. `quality` maximizes configured quality,
`cost` minimizes estimated input/output cost, and `latency` minimizes configured
latency; deterministic tie-breakers keep tests reproducible. Explicit request
`provider`/`model` values remain hard filters and must match the catalog.

Provider health is process-local. A bounded recent-outcome window and consecutive
failure count open the circuit; after the recovery timeout, the provider becomes
`half_open` and a successful probe closes it. Chat emits a `route` SSE event and
shows a `model_route` Trace node containing every candidate, rejection reasons,
health snapshot, selection reason, failures, and final model. Events before the
first non-empty text `delta` are buffered, so a 429, timeout, or transport failure
can safely fall back across providers. After the first text delta, failures are
returned with `partial_response=true` and never replayed on another model.

## Code Agent flow

The LangGraph chain starts with:

```text
setup_workspace
→ load_project_instructions
→ classify_request
→ decide_context_source
→ retrieve_project_memory (orthogonal to repo/RAG routing)
   ├─ repo   → plan_exploration → execute_exploration → assess_context
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
3,000-character budget. `merge_evidence` preserves all provenance types before
tool/change planning or answer generation. Change runs retain human approval,
per-run sandbox copying, validation, one bounded repair attempt, and Diff/test
artifacts. The registered source workspace is never modified directly.

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

Repeated tool calls, identical line segments, and duplicate content do not
consume the evidence budget again. When a budget is exhausted the Agent answers
from collected evidence and marks uncertainty.

### Native tool-calling loop

OpenAI, Anthropic, and Google adapters send `ToolSpec` definitions through each
provider's native Function/Tool Calling API. The Agent no longer asks production
models to manufacture a JSON tool plan in prompt text. Provider-specific
function calls are normalized to `LLMToolDecision`, including a stable tool-call
ID; dotted registry names receive provider-safe aliases and are mapped back
before execution. The fake provider retains deterministic rule planning for
offline tests.

Read-only analysis follows a bounded observe/replan loop:

```text
native tool call
→ ToolRegistry validation and execution
→ result/error linked by call ID
→ provider-native tool result message
→ model observes and either calls another tool or answers
```

The default limits are four model tool rounds and twelve calls per run,
configured by `AGENT_MAX_TOOL_ROUNDS` and `AGENT_MAX_TOOL_CALLS`. Repeating an
identical tool name and arguments is treated as no progress.

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

README files and directories are not injected automatically. They are read only
when task-driven search selects them.

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
symbolic links are resolved.

```bash
curl -X PUT http://localhost:8000/api/v1/workspaces/project \
  -H 'content-type: application/json' \
  -d '{"root_path":"/absolute/path/to/project"}'

curl http://localhost:8000/api/v1/workspaces
curl http://localhost:8000/api/v1/workspaces/project
curl http://localhost:8000/api/v1/workspaces/project/token-usage
curl http://localhost:8000/api/v1/sessions/{session_id}/token-usage
```

There is intentionally no workspace deletion endpoint in v1. Updating a root
increments `workspaces.revision`; old-revision memories stop participating in
retrieval. An administrator can explicitly confirm an old record to copy it
into the current revision; the historical record remains unchanged. Every
`agent_runs` row keeps its captured `workspace_root`.

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

Responses expose `context_route`, `selected_knowledge_base_ids`, and
`context_sources`. Knowledge chunks use `kind=knowledge_chunk` and include
optional `knowledge_base_id`, `document_id`, and `score` provenance fields.
The removed `repository_id` and `rag_context` Agent fields are not accepted or
returned.

### Live source tools

- `repo.find_files`: locate by filename or path fragment.
- `repo.list_files`: list paths below a workspace-relative directory.
- `repo.search_code`: use `rg` with `.gitignore`, falling back to Python.
- `repo.read_file`: read a UTF-8 line range with real line numbers and hash.

The tools reject absolute or traversal paths, escaping symlinks, binary or
oversized files, dependency/build directories, real `.env` files, private keys,
and common credential files.

### Sandbox boundary

Change runs copy regular, non-sensitive workspace files into a per-run
directory. Real `.env` files, credentials, private keys, symbolic links,
unreadable paths, sockets, FIFOs, and other special files are skipped and
reported in `copy_warnings`. Completed, failed, or rejected runs remove their
Sandbox; startup also prunes directories older than
`SANDBOX_WORKSPACE_TTL_SECONDS`.

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
tags. Deletion removes vectors first and then cascades PostgreSQL documents and
chunks. Document RAG endpoints remain available independently:

```text
POST /api/v1/knowledge-bases/{knowledge_base_id}/documents
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

Only these document endpoints use chunking, embeddings, and the configured
vector store. Agent RAG routing calls the same search implementation and
degrades to an evidence warning when retrieval is unavailable.

Each upload records a queryable index job with the state sequence
`pending → parsing → embedding → vector_written → active`; a failure from any
non-terminal state is recorded as `failed` with a bounded error message.
Vector replacement writes the new points before removing stale points, so an
embedding failure does not delete the previous searchable version first.

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
thinking tokens to `token_usage_records`. Chat writes a stable request record;
Agent upserts one stable record per run so worker redelivery and approval resume
do not double count cumulative usage. Historical records without workspace
attribution remain visible in session totals and are not guessed into a
workspace.

Historical migrations remain in the revision chain. The PostgreSQL result
loader alone adapts historical JSON containing `repository_id`/`rag_context`;
new APIs and runs expose only the workspace contract.

For Celery, configure shared storage and identical mounts and allowed roots in
API and workers:

```dotenv
TASK_QUEUE_BACKEND=celery
SESSION_REPOSITORY=postgres
AGENT_RUN_STORE=postgres
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
| PostgreSQL | Sessions/messages and rolling summaries, Agent runs, workspace/knowledge-base catalogs, project-memory facts/evidence/jobs/outbox/audit, document/chunk metadata, lexical search, and LangGraph checkpoints |
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
.venv/bin/python INTERVIEW_NOTES/validate.py
node --check ai_agent_platform/static/app.js
go test ./gateway/...
git diff --check
```

Run offline Agent evaluations with:

```bash
.venv/bin/python evals/run_evals.py
.venv/bin/python evals/run_memory_evals.py
```
