# Agent Evals

This directory contains offline regression evals for the coding-agent backend.
They use the fake LLM provider, local deterministic embeddings, and an in-memory
vector store, so they do not need API keys or external services.

The layered plan these suites belong to is in [DESIGN.md](DESIGN.md). L0 and L1
are implemented; L2 and L3 are still design only.

## L0 pipeline regression

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

## Graded RAG pilot diagnostics

```bash
.venv/bin/python evals/run_rag_evals.py
.venv/bin/python evals/run_rag_evals.py --profile current
```

`rag_cases.json` is a versioned 30-case pilot for checking the annotation
contract before collecting a 100–300 query dataset from sanitized real usage.
It uses 0–3 file relevance, exact category quotas, and a fixed synthetic
AuroraDesk snapshot. Rankings collapse repeated chunks from the same file.

- Recall, precision, and hit rate treat grades 2–3 as relevant.
- Core MRR uses the first grade-3 document.
- NDCG preserves all graded judgements with exponential gain.
- Hard-negative violations, non-empty unanswerable searches, conflict-source
  preference, and search p50/p95 are reported separately.

The default deterministic profile fixes local hashing, chunk 800/overlap 120,
recall 20, lexical weight 0.35, RRF k=60, no reranker, and an in-memory index.
`--profile current` reads the current retrieval settings but still forces an
isolated in-memory index. Draft quality gates are diagnostic unless
`--enforce-gates` is passed. This corpus is synthetic pilot data, not production
traffic, a final holdout, Prompt-cost evidence, or answer-quality evidence.

## L1 trajectory evals

```bash
.venv/bin/python evals/run_trajectory_evals.py
```

L0 answers "is the pipeline still working". L1 answers "did the run get there
the right way". It creates and observes the run through the throwaway app and
submits through the same `QueryService` isolated-Eval path as the in-app suite,
then grades the process.

**Constraints, not golden answers.** Each case in `trajectory_cases.json`
declares what must hold, and nothing more:

| Field | Meaning |
| --- | --- |
| `required_tools` | tools that must have a matching real `ToolResult` |
| `forbidden_tools` | proposed forbidden tools are planning warnings; executed forbidden tools are critical platform-safety failures |
| `order_constraints` | `[before, after]` pairs, e.g. read the file before explaining it |
| `max_steps` | upper bound on executed tool calls |
| `expected_status` | terminal run status |

The observed node sequence is printed under `trace (diagnostic)` and never
decides pass or fail. Exact-sequence matching is kept where it is actually
appropriate — `tests/test_agent_loop_characterization.py` drives the graph with
hand-written deterministic planners, so its goldens are pinned by this
repository's own code rather than by a model.

**Tool-call lifecycle.** A provider proposal is not an execution. Every case
retains `proposed`, `accepted`, `executed`, `succeeded`, `failed`, `suppressed`,
`denied`, and `pending approval` calls with call IDs, arguments, source, reason,
and outcome where available. `executed` requires a `ToolResult` joined by call
ID. Required tools, order constraints, step ceilings, and recovery use only this
executed sequence; a proposal without a result is never assumed successful.

**Four process metrics**, all collected automatically:

1. **Invalid action rate** — `(exact repeated executed calls + suppressed calls)
   / (executed calls + suppressed calls)`. Failed calls and post-failure retries
   are reported separately, not double-counted as invalid. With no denominator
   the value is `n/a`, not zero. Suppressed calls come from the structured
   `suppressed_tools` trace field.
2. **Step efficiency** — executed tool calls divided by the case's declared
   `reference_steps`, the number of calls a minimal correct plan needs. Graph
   nodes are not used: fixed pipeline stages would wash out the signal.
3. **Budget cap rate** — the share of cases that exhaust the exploration budget
   or a hard tool-loop budget. Running out of budget is distinguished from
   giving up (`max_consecutive_tool_failures`, `no_progress`).
4. **Failure recovery** — a case may declare `fault_injection`, which makes one
   named tool return a genuinely failed `ToolResult` on the real execution path,
   installed through the `ApplicationFactory` seam. The run is then classified as
   `recovered` (a different call succeeded afterwards), `retry_loop` (only the
   failing call was re-issued) or `gave_up` (no calls afterwards).

**Read evidence and citation verification** (`verify_citations: true`) uses one
successful-read ledger. Initialization `ContextSource` records are merged with
successful native `repo.read_file` results; the latter retain normalized
workspace-relative path, normalized line range, exact content, SHA-256,
truncation status, and call ID. Failed, suppressed, denied, or pending calls do
not create evidence. `repo.search_code` can prove that a returned matching line
exists, but cannot prove the whole file was read.

The verifier checks three things programmatically, with no judge and no model:

1. every cited path exists in the workspace;
2. the content on disk at `start_line..end_line` equals the cited `text`,
   compared line by line with each line stripped, because `repo.search_code`
   strips matched lines while `repo.read_file` keeps them verbatim;
3. every file path in the answer resolves to successful read evidence. A unique
   basename may resolve to its read full path; duplicate basenames remain
   ambiguous. Merely quoting a filename from README or a search result does not
   ground the target file.

This produces three independent metrics: `citation_content_accuracy` for
scoreable path/range/content records, `answer_path_grounding_rate` for paths in
the answer, and `fully_grounded_case_rate` for cases passing both checks. Each is
`n/a` when it has no scoreable sample.

`metric_thresholds` gates the suite. The current deterministic baseline is:

| Metric | Measured | Gate |
| --- | --- | --- |
| Invalid action rate | 0.000 | ≤ 0.05 |
| Mean step efficiency | 2.273 | ≤ 2.6 |
| Budget cap rate | 0.125 | ≤ 0.25 |
| Failure recovery rate | 1.000 | = 1.0 |
| Citation content accuracy | 1.000 | = 1.0 |
| Answer path grounding rate | 1.000 | = 1.0 |
| Fully grounded case rate | 1.000 | = 1.0 |

## Running the same suite in the app, against a real model

The offline runner is deliberately limited to the fake provider. A real-model
run has to happen **inside the app process**, because that is where the
registered provider credential and the model registry live: the secret store is
in the app's own state volume, and the container image carries only
`ai_agent_platform`, not `evals/`. That is why the analysis modules live in
`ai_agent_platform/evaluation/` and this directory only holds the CLI.

Open the **评测** page in the UI, pick an enabled provider/model pair, and run.
The page shows metric direction and denominator hints, token/time totals and
per-case averages, proposed/executed/suppressed counts, the three citation
metrics, alerts, history, and per-case call/evidence details.

- `POST /api/v1/evals/runs` starts a run in the background; one at a time.
- `GET /api/v1/evals/runs` and `/evals/runs/{id}` read history and detail.
- `POST /api/v1/evals/runs/{id}/baseline` manually pins a compatible baseline;
  `force=true` is required for a run with critical alerts.
- `GET /api/v1/evals/catalogue` describes the suite and the providers that
  actually have an enabled registered model. Anything else is refused with a
  400 rather than started — an unregistered provider produces an empty tool
  selection and every case fails with a permission denial that reads like an
  agent bug.

An in-app Eval Run carries an explicit `evaluation=true` execution context. It
still resolves the selected registered model and its secret inside the app, but
the secret is never serialized into eval records. The context factory supplies
no real profile, summary, or conversation history; retrieval skips project
memory and the global knowledge-base catalogue, except for fixture KB IDs the
suite explicitly allows. Query completion skips user/project-memory writes and
background extraction. Every case session is deleted, then the run removes its
workspace, temporary member/settings/memory rows, vector rows, and files. A
generated temporary principal is used only as an authorization subject; the
explicit evaluation flag—not its name—controls isolation.

Baselines are keyed by **provider + model + suite ID + evaluator version**. A
completed run is not trusted automatically: pinning is manual, requires all
cases and complete metrics, and normally rejects critical alerts. The UI asks
twice before sending an explicit forced pin. Eval runs and baselines also store
an evaluator version and schema version; migration labels existing records as
`legacy`, so they stay readable but cannot silently compare with evaluator 2.0
and suite `l1_trajectory_v2`. Token and elapsed-time metrics generate relative
regression warnings only; they are not uncalibrated hard failure gates.

Step ceilings follow the same logic. `max_steps` is the fake-provider ceiling;
`max_steps_by_provider` overrides it per provider. Which tools must and must not
run, and in what order, stay identical for every model — those are correctness,
not style.

`EVAL_FAULT_INJECTION_ENABLED` (default `false`) lets the eval arm one
deterministic tool failure so failure recovery can be measured. The fault is
scoped to the eval's own workspace, so a user's concurrent run can never be hit.
With it off, the metric reports n/a rather than a misleading 1.0.

### Legacy DeepSeek measurement (not a v2 baseline)

The earlier `deepseek-v4-flash` run (8 cases, ~790k tokens, ~370s) used the old
evaluator. Its `tool_calls` array was counted as executed even when no
`ToolResult` existed, and native `read_file` results were missing from citation
evidence. These historical numbers are retained only to explain the bug; they
are explicitly `legacy` after migration and must not be compared with v2:

| Metric | fake | deepseek |
| --- | --- | --- |
| Pass rate | 1.000 | 0.125 |
| Invalid action rate | 0.000 | 0.372 |
| Mean step efficiency | 2.273 | 5.94 |
| Budget cap rate | 0.125 | 0.125 |
| Failure recovery rate | 1.000 | 1.000 |
| Legacy combined citation accuracy | 1.000 | 1.000 |

The original raw run contained 170 proposals, 139 matching ToolResults, 30
suppressed calls, and one pending approval. Re-running it under v2 would require
a paid provider call and is intentionally not part of automated verification.

## Project-memory quality gates

```bash
.venv/bin/python evals/run_memory_evals.py
```

The checked-in suite fails below 90% candidate precision or 85% Recall@6, and
requires exactly zero cross-workspace leaks. It covers candidate precision,
Recall@6, and workspace isolation.

## What these suites do not prove

All three run on the fake LLM provider. They are deterministic regression tests
of **this system's** logic — routing, exploration, suppression, budgets,
citation bookkeeping — not a benchmark of model or answer quality. A passing
run says nothing about how good the answers are.

Three limits are worth stating explicitly:

- The fake provider's answer is an echo of its prompt, so it cannot fabricate a
  citation. The hallucinated-citation detector is proven by
  `tests/test_trajectory_evals.py` with explicit positive, search-only, failed
  read, and ambiguous-basename cases. The legacy DeepSeek run motivated those
  cases but is not valid v2 accuracy evidence.
- The fake provider never enters the native tool loop, so `suppressed_tools` is
  always zero offline. That field is covered end to end by
  `tests/test_native_tool_calling.py`; no paid real-model rerun was performed for
  evaluator v2.
- A single real-model run is one sample. Run-to-run variance is not measured;
  `pass^k` is still a design item in DESIGN.md, not something these numbers
  account for.
