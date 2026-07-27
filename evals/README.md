# Agent Evals

This directory contains offline regression evals for the coding-agent backend.
They use the fake LLM provider, local deterministic embeddings, and an in-memory
vector store, so they do not need API keys or external services.

Run:

```bash
.venv/bin/python evals/run_evals.py
```

The runner ingests the fixture files from `agent_cases.json`, executes search
and agent cases, and reports checks for:

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
