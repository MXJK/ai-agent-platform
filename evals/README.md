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
- retrieval Recall@5 and mean reciprocal rank (MRR)
- approval pause behavior

The evals are intentionally small and deterministic. They are a regression
baseline, not a benchmark of model quality.
