# Agent Evals

This directory contains offline regression evals for the coding-agent backend.
They use the fake LLM provider, local deterministic embeddings, and an in-memory
vector store, so they do not need API keys or external services.

Run:

```bash
.venv/bin/python evals/run_evals.py
```

The runner ingests the fixture files from `agent_cases.json`, executes search
and agent cases, including a live-workspace project-overview regression, and
reports checks for:

- intent classification
- tool planning
- RAG retrieval hit rate
- code citation symbols
- RAG-only Recall@5, Precision@5, MRR@5, NDCG@5, and Hit Rate@5
- approval pause behavior

Agent repository navigation is reported per case but is deliberately excluded
from the RAG aggregate. `agent_cases.json` also defines minimum retrieval
quality gates; a metric below its configured threshold makes the command fail.
The corpus includes multi-document recall, an exact-token lexical rescue,
a hard negative, and an empty-knowledge-base/no-evidence case.

The evals remain deterministic regression tests, not a production benchmark of
model or answer quality.

Project-memory quality gates run separately and cover candidate precision,
Recall@6, and workspace isolation:

```bash
.venv/bin/python evals/run_memory_evals.py
```

The checked-in suite fails below 90% candidate precision or 85% Recall@6, and
requires exactly zero cross-workspace leaks.
