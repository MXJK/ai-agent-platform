# AI Agent Platform

This is a learning project for Python AI backend engineering, LLM streaming,
RAG, and a repository QA / development assistant Agent.

## Python FastAPI Version

Project structure:

```text
ai_agent_platform/
  main.py                    # FastAPI application entrypoint
  api/router.py              # HTTP routes
  schemas/                   # Pydantic request and response models
  domain/models.py           # Core business entities
  repositories/memory.py     # In-memory storage boundary
  services/session_service.py # Session and message use cases
  agents/coding_agent.py     # Repository QA and development assistant runtime
  agents/game_agent.py       # Legacy rule-based demo runtime
  integrations/
    llm.py                   # Future LLM API client
    rag.py                   # RAG parsing, chunking, embedding, vector search
    tools.py                 # Future tool-calling registry
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
- `integrations`: placeholders for LLM API calls, RAG retrieval, and tool
  calling. These are separate because external systems fail, timeout, and need
  retries/observability.

Install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run the API:

```bash
.venv/bin/python -m uvicorn ai_agent_platform.main:app --reload
```

Configure a real Google Gemini model with Google AI Studio credentials by
creating a local `.env` file:

```bash
LLM_PROVIDER=google
LLM_MODEL=gemini-3.5-flash
GOOGLE_API_KEY=your_google_ai_studio_key
EMBEDDING_PROVIDER=local
```

The app reads `.env` automatically through `Settings.from_env()`, and `.env` is
ignored by git so local secrets do not get committed.

Endpoints:

```text
GET  /api/v1/health
POST /api/v1/sessions
GET  /api/v1/sessions
GET  /api/v1/sessions/{session_id}
POST /api/v1/sessions/{session_id}/messages
GET  /api/v1/sessions/{session_id}/messages
POST /api/v1/chat/stream
POST /api/v1/agent/runs
POST /api/v1/knowledge-bases/{knowledge_base_id}/documents
POST /api/v1/knowledge-bases/{knowledge_base_id}/search
POST /api/v1/knowledge-bases/{knowledge_base_id}/ask
```

Example requests:

```bash
curl http://localhost:8000/api/v1/health

curl -X POST http://localhost:8000/api/v1/sessions \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"user_1"}'

curl http://localhost:8000/api/v1/sessions/sess_xxx/messages

curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"conversation_id":"sess_xxx","message":"你好，解释一下SSE"}'
```

## Repository QA Agent

`POST /api/v1/agent/runs` is the coding-agent endpoint. It is designed as a
small OpenHands/Codex-style backend loop:

1. classify the user request as repository navigation, code explanation, change
   planning, bug investigation, test strategy, or general repository QA.
2. retrieve code context from the RAG index scoped by `repository_id`.
3. plan tool calls such as repository search, file/symbol location, code
   explanation, change planning, bug investigation, and test design.
4. compose an answer with `rag_context`, `tool_calls`, `tool_results`, and a
   step-by-step `trace`.

Use `repository_id` as the code index id. For backward compatibility it maps to
the same storage path as `knowledge_base_id`.

```bash
curl -X POST http://localhost:8000/api/v1/knowledge-bases/repo_main/documents \
  -H 'Content-Type: application/json' \
  -d '{
    "filename": "ai_agent_platform/api/router.py",
    "content": "def chat_stream(...): ...\ndef run_agent(...): ..."
  }'

curl -X POST http://localhost:8000/api/v1/agent/runs \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id": "sess_xxx",
    "repository_id": "repo_main",
    "focus_files": ["ai_agent_platform/api/router.py"],
    "message": "解释 chat stream 接口在哪里实现"
  }'
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

To use Chroma persistence instead of the in-memory vector store:

```bash
export RAG_VECTOR_STORE=chroma
export CHROMA_PERSIST_DIRECTORY=.chroma
```

To enable two-stage retrieval with a Sentence Transformers cross-encoder
reranker:

```bash
export RAG_RECALL_LIMIT=20
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
2. Chunking uses character windows with natural breakpoints and overlap. Chunks
   that are too large waste prompt space; chunks that are too small lose context.
3. Embedding converts each chunk and query into vectors. The same embedding
   model must be used for ingestion and search.
4. Vector storage can use the local in-memory store for tests or Chroma for
   persisted local retrieval.
5. Search filters by `knowledge_base_id`, recalls a larger candidate set, and
   optionally reranks candidates before selecting the final chunks.
6. The top reranked chunks are formatted into the LLM prompt as numbered
   references.
7. `/ask` returns both the answer and `citations`, so callers can show the
   source snippets behind the answer.

Run tests that do not require FastAPI to be installed:

```bash
.venv/bin/python -m unittest discover -s tests
```

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
