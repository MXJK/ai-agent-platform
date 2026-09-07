# Cogent

[简体中文](README.md) | **English**

Cogent is a local-first, self-hosted coding-agent platform. Its Web UI, terminal
client, and Python SDK share durable Runs, workspace tools, permissions, and model
management. Knowledge-base RAG remains independent from Agent context.

Version `0.2.0` currently targets single-user, single-instance deployments over
trusted code repositories.

## Highlights

- **Durable Agent Runs** with streamed events, approvals, questions, pause/resume,
  cancellation, compaction, and restart recovery.
- **Controlled code execution** with workspace-scoped read, write, search, patch,
  and command tools plus `default`, `acceptEdits`, `plan`, and `bypassPermissions` modes.
- **Central model management** for OpenAI, DeepSeek, Anthropic, Google, Zhipu GLM,
  MiniMax, and Doubao connections and models.
- **MCP and Skills** through a shared tool catalogue, central permission decisions,
  user/project Skill discovery, and slash commands.
- **Layered context** across project instructions, Markdown user/project memory,
  sessions, attachments, and Token budgets.
- **Independent knowledge base** with document ingestion, hybrid retrieval, citations,
  and evaluation outside the Coding Agent runtime.

## Quickstart

Docker Desktop is required. Set `WORKSPACE_HOST_PATH` to a trusted host directory,
then start the stack:

```bash
cp -n .env.example .env
mkdir -p workspaces
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml ps
```

Open <http://127.0.0.1:8000>, then:

1. Add a provider connection and register a model in Model Management.
2. Register a project below `/workspaces`.
3. Create a session, select the workspace, and send a task.
4. Review and approve workspace writes or commands when prompted.

For backup, migration, and recovery steps, see the
[full technical reference](docs/reference.en.md#storage-and-migration).

## Terminal client

The CLI connects to the same HTTP/SSE service as the Web UI; it does not assemble a
second Agent Runtime:

```bash
uv sync
uv run cogent
uv run cogent --workspace-id project
uv run cogent --workspace-id project --permission-mode plan
uv run cogent --workspace-id project --print "Explain this project's entrypoints"
```

The default API URL is `http://127.0.0.1:8000/api/v1`. Override it with
`COGENT_API_URL` or `--api-url`.

## Deployment boundary

The supported Compose stack runs the FastAPI/Web UI App, PostgreSQL, Qdrant, and a
one-shot migration service. The App is published on host loopback only.
`single_user` mode does not provide public-network authentication, so do not expose
it directly to a LAN or the Internet. Code tools are intended for repositories you
trust; review approval requests before allowing writes.

An optional single-process SQLite profile remains available. The old Go gateway,
Celery/Redis multi-Worker, database-memory, Chroma, and OS-keyring compatibility
implementations have been removed.

Execution-workspace copies, baselines, and file-history snapshots skip `.venv-*`
virtual-environment directories. FileHistory verifies content-addressed blobs with SHA-256
and reads this rewind data without the default 8 MB limit used for ordinary managed files.

## Documentation

- [Full technical reference](docs/reference.en.md) for configuration, protocols,
  permissions, storage, migrations, and evaluations.
- [中文 README](README.md) / [中文完整参考](docs/reference.zh-CN.md)
- [Architecture and evidence boundaries](INTERVIEW_NOTES.md)
- [End-to-end query flow](docs/architecture/query-full-chain.html)
- [Independent RAG architecture](docs/architecture/rag-architecture.html)
- [Agent evaluation guide](evals/README.md)

## Development and verification

Python 3.10+ is required. Common checks:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall ai_agent_platform tests evals
.venv/bin/python INTERVIEW_NOTES/validate.py
node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs
docker compose --env-file .env.example config --quiet
git diff --check
```

See the [full technical reference](docs/reference.en.md) for local configuration,
API, Provider protocol, and evaluation details.
