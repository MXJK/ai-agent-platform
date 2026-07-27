# AI Agent Platform

FastAPI backend with streaming chat, a task-driven code Agent, an independent
document knowledge base, approval-aware sandbox execution, and optional
PostgreSQL, Celery, Redis, and Qdrant infrastructure.

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
APP_HOST=0.0.0.0 APP_PORT=8000 ./scripts/start.sh
```

The web UI is available at `http://127.0.0.1:8000`. It is served directly by
FastAPI and requires no separate frontend build. The example configuration uses
the fake LLM and local embedding provider, which require no API key.

The shared composer offers:

- `快速对话` for direct SSE model responses;
- `代码 Agent` for task-driven workspace exploration, approvals, progress, and
  artifacts;
- a common conversation history, so bounded recent messages can inform Agent
  exploration and structured tool planning.

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

## Code Agent flow

The LangGraph chain starts with:

```text
setup_workspace
→ load_project_instructions
→ classify_request
→ decide_context_source
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
`merge_evidence` preserves both provenance types before tool/change planning or
answer generation. Change runs retain human approval, per-run sandbox copying,
validation, one bounded repair attempt, and Diff/test artifacts. The registered
source workspace is never modified directly.

Default exploration budgets:

- 4 exploration rounds
- 6 read-only tools per round
- 12 distinct source files
- 32,000 source-evidence characters
- 16,000 project-instruction characters

Repeated tool calls, identical line segments, and duplicate content do not
consume the evidence budget again. When a budget is exhausted the Agent answers
from collected evidence and marks uncertainty.

### Project instructions

The Agent loads `AGENTS.md` from the workspace root toward focused file
directories. `AGENTS.override.md` replaces `AGENTS.md` in the same directory;
nearer directories are later and therefore more specific. Multi-directory
tasks retain each rule's applicable path.

README files and directories are not injected automatically. They are read only
when task-driven search selects them.

Recent conversation context is bounded to six messages and 1,800 characters.
It is included in deterministic workspace queries and structured tool planning.

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
```

There is intentionally no workspace deletion endpoint in v1. Updating a root
only affects new runs; every `agent_runs` row stores its captured
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
WORKSPACE_ALLOWED_ROOTS=/srv/workspaces
```

The persistent runtime assigns one responsibility to each database:

| Component | Responsibility |
| --- | --- |
| PostgreSQL | Sessions/messages, Agent runs, workspace and knowledge-base catalogs, document/chunk metadata, and LangGraph checkpoints |
| Qdrant | RAG embeddings, vector similarity search, knowledge-base filtering, and retrieval payloads |
| Redis | Celery broker and result backend; it is not the source of truth for business records |
| Chroma | Optional embedded/single-node vector-store alternative to Qdrant |

`RAG_VECTOR_STORE` selects either Qdrant or Chroma. They implement the same
vector-store boundary and are not written simultaneously. The checked-in
example and current persistent runtime select Qdrant; in-memory repositories
remain available only as explicit test doubles.

Before starting the API and Celery worker, start the backing services and apply
the schema migration:

```bash
docker compose up -d postgres qdrant redis
.venv/bin/alembic upgrade head
.venv/bin/celery -A ai_agent_platform.workers.celery_app:celery_app worker
```

Workers register only Agent run/resume tasks. An inaccessible captured root
fails with the structured `workspace_unavailable` message.

## Optional Go gateway

The `gateway/` service provides request admission, request-ID propagation,
health/readiness probes, SSE-safe proxying, and graceful shutdown:

```bash
go run ./gateway/cmd/gateway
go test ./gateway/...
go vet ./gateway/...
```

It remains a transport boundary and does not own Agent, RAG, LLM, or database
logic.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall ai_agent_platform tests evals
node --check ai_agent_platform/static/app.js
git diff --check
```

Run offline Agent evaluations with:

```bash
.venv/bin/python evals/run_evals.py
```
