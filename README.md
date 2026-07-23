# AI Agent Platform

FastAPI backend with streaming chat, a task-driven code Agent, an independent
document knowledge base, approval-aware sandbox execution, and optional
PostgreSQL/Celery/Qdrant infrastructure.

The code Agent does not index a repository and does not use embeddings. A run
captures a registered workspace root, searches the live filesystem for the
current task, reads only necessary source ranges, and places those original
snippets in the current model context.

## Local start

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn ai_agent_platform.main:app --reload
```

The web UI is available at `http://127.0.0.1:8000`. The default fake LLM and
local embedding provider require no API key.

## Code Agent flow

The LangGraph chain starts with:

```text
setup_workspace
→ load_project_instructions
→ classify_request
→ plan_exploration
→ execute_exploration
→ assess_context
```

`assess_context` either loops back to exploration or proceeds to tool/change
planning and final answer generation. Change runs retain human approval,
per-run sandbox copying, validation, one bounded repair attempt, and Diff/test
artifacts. The registered source workspace is never modified directly.

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

Responses expose `context_sources`, each containing `kind`, `path`,
`start_line`, `end_line`, `text`, `reason`, `content_hash`, and `truncated`.
The removed `repository_id`, `knowledge_base_id`, and `rag_context` Agent fields
are not accepted or returned.

### Live source tools

- `repo.find_files`: locate by filename/path fragment.
- `repo.list_files`: list paths below a workspace-relative directory.
- `repo.search_code`: use `rg` with `.gitignore`, falling back to Python.
- `repo.read_file`: read a UTF-8 line range with real line numbers and hash.

The tools reject absolute/traversal paths, escaping symlinks, binary or
oversized files, dependency/build directories, real `.env` files, private keys,
and common credential files.

## Independent knowledge base

Document RAG remains separate from the code Agent:

```text
POST /api/v1/knowledge-bases/{knowledge_base_id}/documents
POST /api/v1/knowledge-bases/{knowledge_base_id}/search
POST /api/v1/knowledge-bases/{knowledge_base_id}/ask
```

Only these document endpoints use chunking, embeddings, and the configured
vector store.

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

Historical migrations remain in the revision chain. The PostgreSQL result
loader alone adapts historical JSON containing `repository_id`/`rag_context`;
new APIs and runs expose only the workspace contract.

For Celery, configure shared storage and identical mounts/allowed roots in API
and workers:

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

Workers register only Agent run/resume tasks. An inaccessible captured root
fails with the structured `workspace_unavailable` message.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall ai_agent_platform tests evals
node --check ai_agent_platform/static/app.js
git diff --check
```

Run offline Agent/retrieval evals with:

```bash
.venv/bin/python evals/run_evals.py
```
