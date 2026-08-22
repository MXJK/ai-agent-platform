# L1-TRAJECTORY-EVALS: Grade the agent's process, not just its pipeline

## Goal

Land stage one of `evals/DESIGN.md`: a constraint-based trajectory layer (L1) that
grades **how** a run reached its answer, plus programmatic citation verification.
Everything runs on the fake LLM provider, so it costs nothing per API call and can
gate every commit alongside L0.

L0 (`evals/run_evals.py`) proves the pipeline is not broken. It cannot tell a run
that read the right file before explaining it from a run that guessed. L1 closes
that gap.

## In scope

- `evals/trajectory.py` — pure analysis over one run observation:
  constraint verdicts (`required_tools`, `forbidden_tools`, `order_constraints`,
  `max_steps`, `expected_status`) and the four metrics from DESIGN.md
  (invalid-action rate, step efficiency, budget-cap rate, failure recovery).
- `evals/citations.py` — the three programmatic citation checks:
  cited path exists, cited line range equals the file's real content, and answer
  citations are a subset of the paths actually read.
- `evals/trajectory_cases.json` — L1 fixtures, cases and metric thresholds.
- `evals/run_trajectory_evals.py` — HTTP harness (same `TestClient` →
  `POST /api/v1/agent/runs` path as `run_evals.py`), report formatting, exit code.
- Deterministic tool-fault injection through the existing `ApplicationFactory`
  seam (`runtime.py:215`, "Creates runtime components behind overridable test
  seams"), so the failure-recovery metric observes a real failed `ToolResult`
  travelling the real execution path.
- One additive source change: `plan_tools` trace output in
  `agents/coding/tool_loop_nodes.py` carries the suppressed calls it already
  computes, so the invalid-action rate can count them instead of parsing a
  warning string.
- `tests/test_trajectory_evals.py` — unit coverage for the analysis functions,
  including a hallucinated citation and a retry-loop trajectory that the fake
  provider cannot produce on its own.
- Documentation: `evals/README.md`, `evals/DESIGN.md` status, `README.md`,
  `INTERVIEW_NOTES` Parts and `facts.json` as the handbook rules require.

## Out of scope

- Stages two to four of `evals/DESIGN.md`: the self-built 25-case dataset, real
  model runs, L2 programmatic ground truth, A/B matrix, pass^k, cost and latency
  accounting, LLM judge, SWE-bench subset.
- Any change to `evals/run_evals.py` or `evals/agent_cases.json`. L0 stays as is.
- Any change to how the agent plans, retries or suppresses calls. This task only
  reports what the loop already does.

## Decisions

- **The characterization goldens keep their exact-equality assertions.**
  DESIGN.md proposes relaxing `tests/golden/agent_loop_trajectories.json` into
  constraints because equality is brittle under model drift. That argument does
  not apply there: `tests/test_agent_loop_characterization.py` drives the graph
  with hand-written deterministic planners, never a model, so its sequences are
  pinned by our own code and are exactly the refactor safety net `AGENTS.md` and
  `CLAUDE.md` ask for. The constraint layer belongs in the new L1 suite, which
  runs the real rule-based planner whose heuristics legitimately evolve.
- The L1 suite is a separate runner rather than new case types inside
  `run_evals.py`. It has a different case schema, different gates and a different
  failure meaning; `run_memory_evals.py` sets the precedent.
- Fault injection goes through `ApplicationFactory.create_tool_registry` rather
  than reaching into `ToolRegistry._tools`. `ToolRegistryView` re-dispatches to
  its source registry, so a delegating wrapper would be bypassed; the injector is
  therefore a real `ToolRegistry` subclass that adopts the built registry's state.
- Invalid-action rate counts each executed call at most once across its three
  causes, with suppressed calls added to both numerator and denominator, so the
  rate stays inside `[0, 1]`.
- Step efficiency counts executed tool calls, not graph nodes. Node count is
  dominated by fixed pipeline stages and would wash out the signal.
- Citation content is compared line by line with each line stripped. The
  repository tools strip matched lines for `search_match` sources but preserve
  them verbatim for `file` sources; stripping per line tolerates that difference
  while still catching a wrong line number or fabricated content.

## Acceptance criteria

- [x] `.venv/bin/python evals/run_trajectory_evals.py` runs on the fake provider
      with no API key and exits non-zero when a constraint or a metric gate fails.
- [x] A case declaring `forbidden_tools` fails the suite when that tool is called.
- [x] `order_constraints` fails when the "after" tool runs with no preceding
      "before" tool.
- [x] The reference node sequence is reported as diagnostics only and never
      decides pass or fail.
- [x] All four DESIGN.md metrics are computed and printed per case and per suite.
- [x] The failure-recovery case observes a genuinely failed `ToolResult` produced
      by the real execution path, and distinguishes recovery from a retry loop.
- [x] Citation verification flags a fabricated line range and an answer citing a
      path that was never read; both are covered by unit tests, since the fake
      provider echoes its prompt and cannot fabricate on its own.
- [x] The suppressed-call trace field is asserted end to end by a native-tool-loop
      test, not only by a synthetic fixture.
- [x] Thresholds in `trajectory_cases.json` are set from measured values and the
      measured baseline is recorded in this file's Result.
- [x] `.venv/bin/python -m pytest -q` passes.
- [x] `.venv/bin/python -m compileall ai_agent_platform tests evals` passes.
- [x] Documentation impact assessed and applied; if the handbook or a mapped
      evidence path changes, `.venv/bin/python INTERVIEW_NOTES/validate.py` passes.

## Verification

```
.venv/bin/python -m pytest -q
  517 passed, 60 subtests passed in 20.87s

.venv/bin/python -m compileall ai_agent_platform tests evals
  clean

.venv/bin/python INTERVIEW_NOTES/validate.py
  Validated 24 Markdown files and 41 capabilities. exit=0
  (the "Evidence review" lines are the pre-existing per-capability drift notices
  against last_verified_commit, not errors, and appear for all 41 capabilities)

.venv/bin/python evals/run_evals.py
  Passed: 9/9 (100%)  Recall@5=1.000 Precision@5=0.250 MRR=1.000 NDCG@5=1.000

.venv/bin/python evals/run_trajectory_evals.py
  Passed: 8/8  exit=0
  InvalidActionRate=0.000 MeanStepEfficiency=2.273 BudgetCapRate=0.125
  FailureRecoveryRate=1.000 CitationAccuracy=1.000
  Identical across repeated runs.

.venv/bin/python evals/run_memory_evals.py
  PASS  precision=1.000 Recall@6=1.000 leaks=0
```

Negative controls, run against copies of the case file, to show the suite is not
vacuous:

| Mutation | Result |
| --- | --- |
| add `repo.read_file` to a case's `forbidden_tools` | `miss forbidden_tools: called_forbidden=['repo.read_file']`, exit=1 |
| invert an `order_constraints` pair | `miss order_constraints: violations=['code_explainer->repo.search_code']`, exit=1 |
| set `max_mean_step_efficiency` to 0.1 | `FAIL metric_gate ... actual=2.273`, exit=1 |

## Result

Stage one of `evals/DESIGN.md` is implemented and verified.

**Added**

- `evals/trajectory.py` — constraint verdicts and the four metrics as pure
  functions over one run's API payload.
- `evals/citations.py` — the three citation checks.
- `evals/trajectory_cases.json` — 23 fixtures, 8 cases, calibrated gates.
- `evals/run_trajectory_evals.py` — HTTP harness, fault injection, report.
- `tests/test_trajectory_evals.py` — 31 tests.

**Changed**

- `agents/coding/tool_loop_nodes.py` — the `plan_tools` trace output now carries
  `suppressed_tools`. Additive only; the frontend reads a fixed key list
  (`static/app.js:334`) that this field is not part of, so nothing downstream
  changes. Covered end to end by a new native-tool-loop test.
- `tests/test_native_tool_calling.py` — `RepeatedCallNativePlanner` plus
  `test_plan_tools_trace_records_suppressed_calls_for_trajectory_evals`.

**Measured baseline** (fake provider, deterministic across runs)

| Metric | Measured | Gate |
| --- | --- | --- |
| Invalid action rate | 0.000 | ≤ 0.05 |
| Mean step efficiency | 2.273 | ≤ 2.6 |
| Budget cap rate | 0.125 | ≤ 0.25 |
| Failure recovery rate | 1.000 | = 1.0 |
| Citation accuracy | 1.000 | = 1.0 |

The one case that reaches a budget is
`l1_oversized_evidence_stops_at_the_context_budget`; without it the budget
metric would be implemented but never exercised.

**Deviation from DESIGN.md**: the constraint layer went into the new L1 suite
and `tests/golden/agent_loop_trajectories.json` kept its exact-equality
assertions. Reasoning is recorded under Decisions and now also in
`evals/DESIGN.md` and Part 08, so the document no longer proposes something the
code deliberately does not do.

**Honest limits, recorded in `evals/README.md` and Part 08's boundaries**

- The fake provider echoes its prompt, so it cannot fabricate a citation. The
  hallucinated-citation detector is proven by unit tests, not by suite cases.
- The fake provider never enters the native tool loop, so `suppressed_tools` is
  always zero in the suite; the field is proven by
  `tests/test_native_tool_calling.py`.
- A passing L1 run is evidence about this system's loop, not about answer
  quality.

**Documentation**: `README.md`, `README.en.md`, `evals/README.md`,
`evals/DESIGN.md`, `INTERVIEW_NOTES/08-可观测性、评测与测试.md` and
`INTERVIEW_NOTES/facts.json` (new `trajectory_evaluation` capability) updated;
`validate.py` passes. `INTERVIEW_NOTES/` is gitignored, so those edits are
local-only by design.

**Not committed.** The working tree also carries unrelated uncommitted work from
an earlier session (`.workflow/state.yaml`, four context-budget task specs);
staging is left to a human so the two are not mixed into one commit.
