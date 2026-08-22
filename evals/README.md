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

## L1 trajectory evals

```bash
.venv/bin/python evals/run_trajectory_evals.py
```

L0 answers "is the pipeline still working". L1 answers "did the run get there
the right way". It drives the same full stack over HTTP, then grades the
process.

**Constraints, not golden answers.** Each case in `trajectory_cases.json`
declares what must hold, and nothing more:

| Field | Meaning |
| --- | --- |
| `required_tools` | tools that must appear |
| `forbidden_tools` | tools that must not appear — a write tool in a read-only task is a hard failure |
| `order_constraints` | `[before, after]` pairs, e.g. read the file before explaining it |
| `max_steps` | upper bound on executed tool calls |
| `expected_status` | terminal run status |

The observed node sequence is printed under `trace (diagnostic)` and never
decides pass or fail. Exact-sequence matching is kept where it is actually
appropriate — `tests/test_agent_loop_characterization.py` drives the graph with
hand-written deterministic planners, so its goldens are pinned by this
repository's own code rather than by a model.

**Four process metrics**, all collected automatically:

1. **Invalid action rate** — `(repeated calls + suppressed calls) / (executed
   calls + suppressed calls)`. Each executed call is counted at most once even
   when it is both a repeat and a post-failure retry; the two are reported
   separately. Suppressed calls come from the `suppressed_tools` field that
   `plan_tools` writes into the run trace.
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

**Citation verification** (`verify_citations: true`) checks three things
programmatically, with no judge and no model:

1. every cited path exists in the workspace;
2. the content on disk at `start_line..end_line` equals the cited `text`,
   compared line by line with each line stripped, because `repo.search_code`
   strips matched lines while `repo.read_file` keeps them verbatim;
3. every file path in the answer was actually read. A path is grounded when it
   is a context-source path or appears inside the text of one — repeating a
   filename the README mentioned is reporting, not inventing.

`metric_thresholds` gates the suite. The current deterministic baseline is:

| Metric | Measured | Gate |
| --- | --- | --- |
| Invalid action rate | 0.000 | ≤ 0.05 |
| Mean step efficiency | 2.273 | ≤ 2.6 |
| Budget cap rate | 0.125 | ≤ 0.25 |
| Failure recovery rate | 1.000 | = 1.0 |
| Citation accuracy | 1.000 | = 1.0 |

## Running the same suite in the app, against a real model

The offline runner is deliberately limited to the fake provider. A real-model
run has to happen **inside the app process**, because that is where the
registered provider credential and the model registry live: the secret store is
in the app's own state volume, and the container image carries only
`ai_agent_platform`, not `evals/`. That is why the analysis modules live in
`ai_agent_platform/evaluation/` and this directory only holds the CLI.

Open the **评测** page in the UI, pick a registered provider, and run. The page
shows the pass rate, all five metrics, threshold and regression alerts, the run
history, and per-case detail down to which constraint broke.

- `POST /api/v1/evals/runs` starts a run in the background; one at a time.
- `GET /api/v1/evals/runs` and `/evals/runs/{id}` read history and detail.
- `POST /api/v1/evals/runs/{id}/baseline` pins a run as the provider's baseline.
- `GET /api/v1/evals/catalogue` describes the suite and the providers that
  actually have an enabled registered model. Anything else is refused with a
  400 rather than started — an unregistered provider produces an empty tool
  selection and every case fails with a permission denial that reads like an
  agent bug.

Each provider carries **its own baseline**. A real model and the fake provider
are not comparable: the real model actually enters the native tool loop, so its
step counts, repeated calls and suppression counts differ in kind. The first
completed run for a provider becomes its baseline; later runs report a signed
delta and alert on regression beyond `regression_tolerance`. Two things are
enforced for every provider regardless of baseline: a failed case, and citation
accuracy below 1.0.

Step ceilings follow the same logic. `max_steps` is the fake-provider ceiling;
`max_steps_by_provider` overrides it per provider. Which tools must and must not
run, and in what order, stay identical for every model — those are correctness,
not style.

`EVAL_FAULT_INJECTION_ENABLED` (default `false`) lets the eval arm one
deterministic tool failure so failure recovery can be measured. The fault is
scoped to the eval's own workspace, so a user's concurrent run can never be hit.
With it off, the metric reports n/a rather than a misleading 1.0.

### Measured DeepSeek baseline

`deepseek-v4-flash`, 8 cases, ~790k tokens, ~370s:

| Metric | fake | deepseek |
| --- | --- | --- |
| Pass rate | 1.000 | 0.125 |
| Invalid action rate | 0.000 | 0.372 |
| Mean step efficiency | 2.273 | 5.94 |
| Budget cap rate | 0.125 | 0.125 |
| Failure recovery rate | 1.000 | 1.000 |
| Citation accuracy | 1.000 | 1.000 |

The interesting column is the second one, and the interesting number is not the
pass rate. Under the fake provider `suppressed_calls` is always zero because the
native tool loop never runs; under DeepSeek it is 2–11 per case, which is what
puts the invalid-action rate at 37%.

Seven of eight cases failed on **ungrounded citations**, and they are true
positives. Asked where `create_order` is implemented, the model read only
`src/api/routes.py` and then wrote that `OrderService` is at
`src/services/orders.py:1` and `submit` at `:4` — correct, but inferred from an
import statement, for a file the run never opened. That is precisely the failure
the citation checker exists to catch, and it is invisible under the fake
provider, whose answer is an echo of its own prompt.

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

Two limits are worth stating explicitly:

- The fake provider's answer is an echo of its prompt, so it cannot fabricate a
  citation. The hallucinated-citation detector is proven by
  `tests/test_trajectory_evals.py` offline — and, as the DeepSeek baseline above
  shows, it fires on a real model.
- The fake provider never enters the native tool loop, so `suppressed_tools` is
  always zero offline. That field is covered end to end by
  `tests/test_native_tool_calling.py`, and is non-zero on every real-model run.
- A single real-model run is one sample. Run-to-run variance is not measured;
  `pass^k` is still a design item in DESIGN.md, not something these numbers
  account for.
