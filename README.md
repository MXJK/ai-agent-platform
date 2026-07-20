# AI Agent Platform

This is a learning project for Python AI backend engineering, LLM streaming,
RAG, and a repository QA / development assistant Agent.

## Python FastAPI Version

Project structure:

```text
ai_agent_platform/
  main.py                    # FastAPI application entrypoint
  api/
    router.py                # Versioned API composition root
    routes/                  # Resource routers: sessions, chat, runs, repos, RAG
  core/
    config.py                # Validated environment configuration
    observability.py         # JSON logging and request correlation middleware
    metrics.py               # Thread-safe counters and duration summaries
    task_queue.py            # Bounded background task execution boundary
  schemas/                   # Pydantic request and response models
  domain/models.py           # Core business entities
  repositories/memory.py     # In-memory storage boundary
  services/session_service.py # Session and message use cases
  agents/
    coding_agent.py          # LangGraph runtime and node orchestration
    coding/                  # State, planners, change loop, tools, formatting
  agents/game_agent.py       # Legacy rule-based demo runtime
  integrations/
    llm.py                   # LLM provider adapters and streaming client
    rag/                     # RAG models, errors, factory, and service implementations
    tools.py                 # Validated and auditable tool registry
tests/
  test_agent.py
  test_session_service.py
```

Module roles:

- `main.py`: starts the FastAPI app and wires dependencies together. In real AI
  backends, this is where config, repositories, model clients, and services are
  assembled.
- `api`: HTTP transport layer. It receives JSON requests, returns JSON
  responses, and converts service errors into HTTP status codes.
- `schemas`: Pydantic validation layer. It protects the backend from malformed
  input and documents the API contract.
- `domain`: business concepts that are not tied to HTTP or database libraries.
- `repositories`: data access boundary. This version uses memory storage; later
  it can be replaced with PostgreSQL, Redis, MongoDB, or a vector database.
- `services`: application use cases. It coordinates sessions, messages, and
  optional agent execution.
- `agents`: agent runtime boundary. The main runtime is now a repository-aware
  development assistant that classifies code questions, retrieves repository
  context, plans tool calls, and returns a trace. `game_agent.py` remains as a
  small legacy rule-based demo for the session-message exercise.
- `integrations`: LLM APIs, RAG retrieval, MCP providers, sandbox execution, and
  tool calling. These boundaries isolate external failures, timeouts, retries,
  and observability from the application layer.

Install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run the API:

```bash
.venv/bin/python -m uvicorn ai_agent_platform.main:app --reload
```

Open the lightweight frontend console:

```text
http://127.0.0.1:8000/
```

The console is served by FastAPI from `ai_agent_platform/static/`. It provides a
small debug UI for sessions, `POST /api/v1/chat/stream`, coding-agent runs, RAG
document ingest/ask, and repository indexing.

By default, local runs use the fake LLM provider and local deterministic
embeddings, so the basic API, RAG, and repository indexing flows work without
external API keys.

Configure a real Google Gemini model with Google AI Studio credentials by
creating a local `.env` file:

```bash
LLM_PROVIDER=google
LLM_MODEL=gemini-3.5-flash
GOOGLE_API_KEY=your_google_ai_studio_key
EMBEDDING_PROVIDER=local
MCP_ENABLED=false
MCP_CONFIG_PATH=mcp.json
MCP_REQUEST_TIMEOUT_SECONDS=10
SANDBOX_MODE=local
SANDBOX_DOCKER_IMAGE=python:3.11-slim
SANDBOX_COMMAND_TIMEOUT_SECONDS=30
LOG_LEVEL=INFO
LOG_FORMAT=json
```

The app reads `.env` automatically through `Settings.from_env()`, and `.env` is
ignored by git so local secrets do not get committed.

## Local Data Stores

This project is moving toward PostgreSQL as the structured source of truth and
Qdrant as the dedicated vector database.

PostgreSQL is intended to store structured application data:

- sessions, messages, and token usage
- agent runs, run events, traces, and LangGraph checkpoint metadata
- repositories, repository index jobs, repository file hashes, documents, and
  chunk metadata

Qdrant is intended to store vector embeddings for repository/document chunks.
PostgreSQL should still keep document and chunk metadata so Qdrant collections
can be rebuilt without losing the source-of-truth records.

Start the local databases:

```bash
docker compose up -d postgres qdrant
```

Check service health:

```bash
docker compose ps
curl http://localhost:6333/readyz
```

Create local configuration:

```bash
cp .env.example .env
```

Important local values:

```bash
DATABASE_URL=postgresql://ai_agent:ai_agent_password@localhost:5432/ai_agent_platform
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION_NAME=repo_chunks
```

Run PostgreSQL schema migrations before switching runtime stores to
PostgreSQL:

```bash
DATABASE_URL=postgresql://ai_agent:ai_agent_password@localhost:5432/ai_agent_platform \
  .venv/bin/python -m alembic upgrade head
```

If a local database was already initialized by the older repository auto-schema
code and the tables match this initial migration, mark that existing schema as
current once:

```bash
DATABASE_URL=postgresql://ai_agent:ai_agent_password@localhost:5432/ai_agent_platform \
  .venv/bin/python -m alembic stamp head
```

Useful migration commands:

```bash
.venv/bin/python -m alembic current
.venv/bin/python -m alembic history
.venv/bin/python -m alembic downgrade -1
```

By default, local tests and quick demos still use in-memory storage. To switch
structured application state to PostgreSQL, install dependencies, start
PostgreSQL, and set:

```bash
SESSION_REPOSITORY=postgres
AGENT_RUN_STORE=postgres
DOCUMENT_STORE=postgres
REPOSITORY_INDEX_STORE=postgres
LANGGRAPH_CHECKPOINTER=postgres
```

To switch vector search to Qdrant, start Qdrant and set:

```bash
RAG_VECTOR_STORE=qdrant
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION_NAME=repo_chunks
```

`SESSION_REPOSITORY=postgres` persists sessions, messages, and token usage.
`AGENT_RUN_STORE=postgres` persists product-level agent run status and trace
snapshots. `DOCUMENT_STORE=postgres` persists ingested document and chunk
metadata as the structured source of truth. `REPOSITORY_INDEX_STORE=postgres`
persists repository roots, index jobs, and per-file hashes. `LANGGRAPH_CHECKPOINTER=postgres`
uses LangGraph's PostgreSQL checkpointer for graph state. `RAG_VECTOR_STORE=qdrant`
stores chunk vectors in Qdrant while keeping the same RAG service interface.

The PostgreSQL schema currently includes these application-owned tables:

- `sessions`, `messages`, `token_usage_records`
- `agent_runs`
- `documents`, `document_chunks`
- `repositories`, `repository_index_jobs`, `repository_files`

The repository indexing tables are the metadata foundation for the next API
step: `repositories` stores the repo root, `repository_index_jobs` stores each
scan run and counters, and `repository_files` stores per-file path/hash/document
metadata so unchanged files can be skipped.

Endpoints:

```text
GET  /api/v1/health
GET  /api/v1/metrics
POST /api/v1/sessions
GET  /api/v1/sessions
GET  /api/v1/sessions/{session_id}
POST /api/v1/sessions/{session_id}/messages
GET  /api/v1/sessions/{session_id}/messages
POST /api/v1/chat/stream
POST /api/v1/agent/runs
POST /api/v1/agent/runs/{run_id}/resume
GET  /api/v1/agent/runs/{run_id}
GET  /api/v1/agent/runs/{run_id}/events
POST /api/v1/repositories/{repository_id}/index
POST /api/v1/knowledge-bases/{knowledge_base_id}/documents
POST /api/v1/knowledge-bases/{knowledge_base_id}/search
POST /api/v1/knowledge-bases/{knowledge_base_id}/ask
```

Example requests:

```bash
curl http://localhost:8000/api/v1/health

curl http://localhost:8000/api/v1/metrics

curl -X POST http://localhost:8000/api/v1/sessions \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"user_1"}'

curl http://localhost:8000/api/v1/sessions/sess_xxx/messages

curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"conversation_id":"sess_xxx","message":"你好，解释一下SSE"}'
```

## Observability and Configuration Safety

Every HTTP response includes an `X-Request-ID`. A valid incoming
`X-Request-ID` is propagated; otherwise the server generates one. Project logs
can use JSON or text output through `LOG_FORMAT`, and background Agent workers
bind `run_id`, `conversation_id`, and `repository_id` to their log context.
Request bodies and tool secrets are not written to access logs.

`GET /api/v1/metrics` exposes dependency-free process-local counters and timing
summaries for HTTP requests and Agent executions. Agent run responses also
contain a `metrics` object with elapsed time, node/tool counts, retries, and
recovered errors. This local registry is intentionally small; a production
deployment can replace the registry boundary with Prometheus or OpenTelemetry.

`Settings` validates storage backends, sandbox mode, logging options, positive
timeouts/limits, and RAG chunk overlap during startup. Invalid configuration
fails before repositories, model clients, or background workers are created.

## Asynchronous Tasks

Agent runs and repository indexing share a bounded `TaskQueue` protocol. The
default `InProcessTaskQueue` uses worker threads for local development, rejects
submissions with `503` when capacity is exhausted, records per-task counters and
durations, and drains accepted work during application shutdown. Services no
longer depend directly on `ThreadPoolExecutor`, so a durable Redis/Celery or
cloud-queue adapter can replace the local implementation without changing the
HTTP or business layers.

Repository indexing is asynchronous: `POST /repositories/{id}/index` returns
`202 Accepted` and a pending `job_id`; poll
`GET /repositories/{id}/index-jobs/{job_id}` for running counters and the final
status. Only one index job per repository runs in a process at a time, avoiding
concurrent writes to the same repository index.

Tune the local executor with `BACKGROUND_TASK_WORKERS` and
`BACKGROUND_TASK_QUEUE_CAPACITY`. The defaults are `4` workers and `100`
accepted waiting tasks beyond the active workers.

## Repository QA Agent

`POST /api/v1/agent/runs` is the coding-agent endpoint. It creates a background
Agent run and returns `202 Accepted` with a `run_id`. Call
`GET /api/v1/agent/runs/{run_id}` to poll status and
`GET /api/v1/agent/runs/{run_id}/events` to render a timeline from queued,
started, node-completed, approval-required, completed, or failed events. The run
itself is designed as a small OpenHands/Codex-style backend loop:

1. classify the user request as repository navigation, code explanation, change
   planning, bug investigation, test strategy, or general repository QA. The
   default planner asks the configured LLM for structured JSON
   (`intent`, `reason`, `confidence`) and falls back to deterministic rules when
   the model response is unavailable or invalid.
2. retrieve code context from the RAG index scoped by `repository_id`.
3. plan tool calls with structured JSON over the registered `ToolSpec` list,
   while rejecting unknown tools and falling back to the rule-based planner if
   the model output cannot be validated. Planned calls can include local
   repository search/read tools, file/symbol location, code explanation, change
   planning, bug investigation, and test design.
4. pause for human approval before writable Sandbox or external-side-effect
   tools execute.
5. when Sandbox lifecycle tools are present, separate repository inspection,
   code mutation, validation, and artifact collection into graph nodes. Failed
   validation can produce one bounded repair plan, which requires a second
   human approval before another write.
6. collect test reports and the final unified Diff in `artifacts`, with a
   `change_summary` that reports validation status, iterations, and changed
   files.
7. retry recoverable RAG/answer-generation failures, collect structured
   `errors`, and route unrecoverable failures through `handle_error` and
   `compose_error_answer`.
8. compose an answer with `rag_context`, `tool_calls`, `tool_results`, and a
   step-by-step `trace`.

The first LangGraph persistence layer is now wired in with an in-memory
checkpointer. Each run receives a `run_id`, uses that id as the LangGraph
`thread_id`, and returns the latest `checkpoint_id`. You can query the run later
to inspect `status`, `latest_node`, `next_nodes`, `trace`, timeline `events`,
and the final result.
Change-planning runs now return `status=waiting_approval` with a
`pending_approval` payload before tool execution. Resume the checkpoint with
`POST /api/v1/agent/runs/{run_id}/resume` and an approval decision.
Runs also expose `errors`, a structured list of `{node, code, message,
retryable, attempt, max_attempts, recovered}` records. Recoverable RAG provider
failures are retried before the graph continues. Unrecoverable RAG/configuration
or answer-generation failures route to `handle_error -> compose_error_answer`
so the API returns a diagnostic answer instead of an opaque crash.

Use `repository_id` as the code index id. For backward compatibility it maps to
the same storage path as `knowledge_base_id`.

### Tool Layer

The first tools phase standardizes local tool execution before MCP is added.
`ai_agent_platform.integrations.tools` defines:

- `ToolSpec`: name, description, JSON-style input/output schemas, provider,
  permission level, and approval requirement.
- `ToolCall`: the planned call name and arguments.
- `ToolResult`: normalized execution output with `ok`, `result`/`error`,
  provider, permission level, approval flag, duration, risk summary, sanitized
  argument summary, and output truncation flag.
- `ToolRegistry`: one registry that can execute local tools now and MCP-backed
  providers later through the same result contract.

Before a tool executes, `ToolRegistry` validates required JSON-schema arguments
and basic argument types. Tool results are capped by each tool's
`max_output_chars` so a single call cannot flood the Agent context. Tool
responses include sanitized `arguments_summary` values with sensitive names such
as tokens, passwords, secrets, and API keys redacted. Approval payloads include
each risky tool's permission level, risk summary, and sanitized arguments so the
human reviewer can audit what would run before approving it.

The local repository provider registers these read-only tools:

- `repo.list_files`: list text-oriented files under the configured repository
  root.
- `repo.read_file`: read a UTF-8 text file under the repository root.
- `repo.search_code`: search repository text files by symbol, path, or keyword.

All repository tools are scoped to the configured root path and reject paths
that escape that root. Higher-level planning tools such as `change_planner`
remain available, but now return standardized tool metadata in
`tool_results`.

The sandbox provider adds the code-modification loop tools. Every agent run gets
an isolated temporary workspace copied from the configured repository root. The
real repository is not modified directly; writable tools change only the
sandbox workspace, and `sandbox.git_diff` returns the patch for review.

- `sandbox.workspace_status`: inspect the current sandbox workspace and changed
  files.
- `sandbox.write_file`: write a UTF-8 file inside the sandbox workspace.
- `sandbox.apply_patch`: apply a unified diff inside the sandbox workspace.
- `sandbox.run_command`: run a command in the sandbox workspace.
- `sandbox.git_diff`: return the unified diff from the sandbox baseline.

Writable sandbox tools use `permission_level=write_safe` and
`requires_approval=true`, so they pause in the same
`waiting_approval -> resume` flow as risky MCP tools. Set `SANDBOX_MODE=docker`
to execute commands through Docker with no network access, CPU and memory
limits, and the sandbox workspace mounted at `/workspace`. The default
`SANDBOX_MODE=local` is intended for unit tests and lightweight development.

When a structured planner selects Sandbox lifecycle tools, the graph follows:

```text
inspect_repository -> execute_changes -> validate_changes
  -> collect_artifacts -> compose_answer
```

If validation fails and the planner can propose a minimal repair, the graph
pauses at `review_repair_plan`. Approval resumes another Sandbox mutation and
validation cycle. The loop is capped at two mutation iterations, and the real
repository is never modified; callers receive `test_report` and `code_diff`
artifacts for review.

The MCP provider boundary is available under `ai_agent_platform.integrations.mcp`.
Any client that implements:

```python
list_tools() -> list[MCPTool]
call_tool(name: str, arguments: dict[str, object]) -> object
```

can be wrapped by `MCPToolProvider` and registered into the same `ToolRegistry`.
MCP tools are namespaced as `mcp.<server_name>.<tool_name>` and return the same
`ToolResult` shape as local tools, including provider and approval metadata.
`MCPStdioClient` now implements the core stdio transport path: launch the MCP
server subprocess, send `initialize`, send `notifications/initialized`, call
`tools/list` with pagination, call `tools/call`, and close the child process.

Example MCP server config shape:

```json
{
  "mcp_servers": {
    "github": {
      "transport": "stdio",
      "command": "github-mcp-server",
      "args": ["--read-only"],
      "env": {"GITHUB_TOKEN": "token"}
    }
  }
}
```

Set these environment variables to enable MCP tools at app startup:

```bash
MCP_ENABLED=true
MCP_CONFIG_PATH=mcp.json
MCP_REQUEST_TIMEOUT_SECONDS=10
```

When `MCP_ENABLED=true`, `create_app()` loads the configured MCP servers,
registers their tools in the coding agent registry, and closes the providers on
FastAPI shutdown. The default remains disabled so local development does not
fail when optional MCP binaries are missing.

The coding agent planner can now inspect registered `ToolSpec` values and add
matching MCP tools to a run. Read-only MCP tools can execute automatically.
Tools with `requires_approval=true` or a non-`read_only` permission level are
routed through the existing `waiting_approval -> resume` flow before execution.

You can also create providers explicitly in tests or scripts:

```python
from ai_agent_platform.agents.coding_agent import create_coding_tool_registry
from ai_agent_platform.integrations.mcp import create_mcp_providers_from_config_file

providers = create_mcp_providers_from_config_file("mcp.json")
registry = create_coding_tool_registry(mcp_providers=providers)
```

```bash
curl -X POST http://localhost:8000/api/v1/repositories/repo_main/index \
  -H 'Content-Type: application/json' \
  -d '{
    "root_path": "/absolute/path/to/repo",
    "include_patterns": ["**/*.py", "**/*.md", "**/*.toml"],
    "exclude_patterns": [".git/**", ".venv/**", "__pycache__/**", "node_modules/**"],
    "max_file_size": 200000
  }'

curl http://localhost:8000/api/v1/repositories/repo_main/index-jobs/idxjob_xxx

curl -X POST http://localhost:8000/api/v1/knowledge-bases/repo_main/documents \
  -H 'Content-Type: application/json' \
  -d '{
    "filename": "ai_agent_platform/api/routes/chat.py",
    "content": "def chat_stream(...): ..."
  }'

curl -X POST http://localhost:8000/api/v1/agent/runs \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id": "sess_xxx",
    "repository_id": "repo_main",
    "focus_files": ["ai_agent_platform/api/routes/chat.py"],
    "message": "解释 chat stream 接口在哪里实现"
  }'

curl http://localhost:8000/api/v1/agent/runs/run_xxx

curl http://localhost:8000/api/v1/agent/runs/run_xxx/events

curl -X POST http://localhost:8000/api/v1/agent/runs/run_xxx/resume \
  -H 'Content-Type: application/json' \
  -d '{"approved":true,"feedback":"可以执行"}'
```

## Minimal RAG Flow

This project includes a small RAG pipeline for learning and local experiments.
It supports multiple `knowledge_base_id` values so one backend can isolate
repository indexes, product docs, customer FAQ, HR policies, project notes, or
other knowledge bases. The coding agent uses `repository_id`, which is the same
scoping concept with a code-oriented name.

Default settings use Google Gemini `gemini-embedding-001` for embeddings and an
in-memory vector store for storage. Configure your Google API key before running
RAG ingestion or search. The app reads `GOOGLE_API_KEY` from the shell
environment or from the repo `.env` file:

```bash
export GOOGLE_API_KEY=...
```

Tests explicitly use the deterministic local hash embedding provider so they do
not require network access or API keys. You can also enable it manually for
offline learning:

```bash
export EMBEDDING_PROVIDER=local
```

To use Qdrant instead of the in-memory vector store:

```bash
export RAG_VECTOR_STORE=qdrant
export QDRANT_URL=http://localhost:6333
export QDRANT_COLLECTION_NAME=repo_chunks
```

To use Chroma persistence instead:

```bash
export RAG_VECTOR_STORE=chroma
export CHROMA_PERSIST_DIRECTORY=.chroma
```

To enable two-stage retrieval with a Sentence Transformers cross-encoder
reranker:

```bash
export RAG_RECALL_LIMIT=20
export RAG_LEXICAL_WEIGHT=0.35
export RAG_RERANKER_PROVIDER=sentence_transformer
export SENTENCE_TRANSFORMER_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
```

Ingest a document:

```bash
curl -X POST http://localhost:8000/api/v1/knowledge-bases/customer_faq/documents \
  -H 'Content-Type: application/json' \
  -d '{
    "filename": "refund.md",
    "content": "# 退款规则\n\n用户可以在订单完成后 7 天内申请退款。超过 7 天需要人工客服审核。"
  }'
```

Search relevant chunks:

```bash
curl -X POST http://localhost:8000/api/v1/knowledge-bases/customer_faq/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"订单完成后多久可以退款？","recall_limit":20,"limit":3}'
```

Ask with retrieval-augmented generation:

```bash
curl -X POST http://localhost:8000/api/v1/knowledge-bases/customer_faq/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"退款期限是多久？","recall_limit":20,"limit":3}'
```

RAG implementation steps:

1. Document parsing accepts common text and source-code files such as `.txt`,
   `.md`, `.py`, `.ts`, `.tsx`, `.js`, `.go`, `.rs`, `.java`, `.json`, `.toml`,
   `.yaml`, `.html`, and `.css`. Real systems need extra parsers for PDF, Word,
   OCR, tables, and images.
2. Chunking uses code-aware boundaries for source files where possible. Python
   uses the standard-library AST so a class stays with its methods and exposes
   qualified symbols such as `ToolRegistry.execute`; incomplete source falls
   back to regex boundaries. Non-code documents still use character windows
   with natural breakpoints and overlap.
3. Embedding converts each chunk and query into vectors. The same embedding
   model must be used for ingestion and search.
4. Vector storage can use the local in-memory store for tests or Chroma for
   persisted local retrieval.
5. Search filters by `knowledge_base_id`, recalls a larger candidate set, blends
   vector similarity with exact content/symbol/path matches, and optionally runs
   a cross-encoder reranker. `RAG_LEXICAL_WEIGHT` controls the lexical share.
6. The top reranked chunks are formatted into the LLM prompt as numbered
   references.
7. `/search` and `/ask` return both snippets and precise citation metadata such
   as `start_line`, `end_line`, and `symbols` when the source is code, so callers
   can jump directly to the relevant implementation block.

Run tests that do not require FastAPI to be installed:

```bash
.venv/bin/python -m unittest discover -s tests
```

Run the offline Agent eval suite:

```bash
.venv/bin/python evals/run_evals.py
```

The eval suite ingests deterministic fixture files and checks intent
classification, tool planning, RAG retrieval, code citation symbols, and
approval pause behavior. Its report also includes retrieval Recall@5 and MRR so
ranking changes can be compared instead of relying only on pass/fail examples.

## Small Exercise

Add a new endpoint:

```text
GET /api/v1/sessions/{session_id}/summary
```

First version goal: return `session_id`, `message_count`, and `last_message`.
This practices the normal backend path: schema -> service method -> route ->
test.

## Go Backend Version

The older Go backend files are still present under `cmd/` and `internal/`, but
the current learning track uses Python + FastAPI.
