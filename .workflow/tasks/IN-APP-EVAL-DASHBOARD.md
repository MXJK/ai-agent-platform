# IN-APP-EVAL-DASHBOARD: Run trajectory evals inside the app against the registered model, and show them

## Goal

Make the L1 trajectory suite runnable against the user's **registered DeepSeek**
and visible in the product: a page that shows success rate, the five metrics,
threshold and regression alerts, run history, and per-case detail.

## Why it has to run inside the app

Investigated before planning:

- DeepSeek is registered and available: `deepseek-v4-flash`
  (`mdl_a6538c34fe83dc0b`), credential configured, breaker closed.
- The app runs in Docker with `MODEL_SECRET_BACKEND=encrypted_file`; the
  encrypted secret lives in the `app_state` volume, not on the host.
- Postgres publishes no host port, so the host cannot read the model registry.
- The image only contains `ai_agent_platform` and `migrations`
  (`Dockerfile:19-20`) — `evals/` is not in the container at all.

So a host-side script cannot reach the registered credential. The eval has to
execute in the app process, which is also what the frontend requirement needs.

## In scope

**Move the analysis layer into the package** so the container has it:

- `evals/trajectory.py` → `ai_agent_platform/evaluation/trajectory.py`
- `evals/citations.py` → `ai_agent_platform/evaluation/citations.py`
- `evals/trajectory_cases.json` → `ai_agent_platform/evaluation/trajectory_cases.json`
- `evals/run_trajectory_evals.py` stays as the offline CLI and imports from the
  package. No compatibility shims: the module is new and uncommitted.

**New `ai_agent_platform/evaluation/` package**

- `models.py` — `EvalRunRecord`, `EvalCaseRecord`, `EvalBaseline`, `EvalAlert`.
- `suite.py` — load and validate the case suite.
- `faults.py` — `ToolFaultController` + `FaultInjectingToolRegistry`, shared by
  the CLI and the service.
- `service.py` — `EvalService`: creates a session and a fixture workspace,
  submits one agent run per case through the existing `QueryService.submit_run`
  with `provider`/`model` pinned, polls, scores with `trajectory`/`citations`,
  persists, compares against the provider's baseline, emits alerts.

**Persistence** — new `eval_store` setting (`memory` | `postgres`), migration
`0023`, `InMemoryEvalRepository` + `PostgresEvalRepository`, following the
`change_set_store` pattern (`runtime.py:735`).

**API** — `api/routes/evals.py`: start a run, list history, read one run, read
and pin a baseline, and read the case catalogue.

**Frontend** — a new `evals` view: provider picker, run button with live
progress, metric cards, alert list, history table, per-case detail.

**Config** — `EVAL_STORE`, `EVAL_FAULT_INJECTION_ENABLED` (default `false`),
`EVAL_WORKSPACE_ROOT`.

## Out of scope

- Stage two and later of `evals/DESIGN.md`: the self-built 25-case dataset, L2
  programmatic ground truth, A/B matrix, LLM judge, SWE-bench subset.
- pass^k / multi-sample runs. The chosen baseline model handles run-to-run
  variance instead; multi-sample stays available as a later change.
- Scheduling. Runs are started by a person, never automatically.

## Decisions

- **Baselines are per provider.** A fake run and a DeepSeek run are not
  comparable: the real model actually enters the native tool loop, so its step
  counts, repeated calls and suppression counts are different in kind. Each
  provider carries its own baseline and its own alert thresholds.
- **The first real run becomes the baseline** (user's choice). Later runs are
  compared against it and alert on regression beyond a tolerance; the baseline
  can be re-pinned from the page.
- **Fault injection is off by default and gated by a setting.** The failure
  recovery metric needs a genuinely failed `ToolResult`. In-app that means the
  runtime's tool registry must be fault-capable, which is a test affordance, not
  production behavior — so `EVAL_FAULT_INJECTION_ENABLED` defaults to `false`
  and the plain registry is returned unless it is on. When off, the affected
  cases report `not_triggered` and the metric reads n/a rather than a false 1.0.
- **One eval run at a time**, guarded in the service. A run spends real money
  and takes minutes; concurrent runs would also interleave fault injection.
- **Runs are submitted through `QueryService.submit_run`**, the same path the
  API uses, with `provider`/`model` pinned. Evals must exercise the real code
  path, not a private one.
- **Fixtures are written under an allowed workspace root**
  (`/workspaces/.evals/<run_id>` in the container) and removed afterwards, so
  the workspace policy is not special-cased for evals.

## Acceptance criteria

- [x] `POST /api/v1/evals/runs` with `provider=deepseek` runs the suite against
      the registered model and persists the result.
- [x] `provider=fake` still works and stays free.
- [x] The frontend page shows success rate, all five metrics, alerts, history,
      and per-case constraint detail.
- [x] A second run against an existing baseline shows the delta per metric, and
      raises an alert when a metric regresses beyond tolerance.
- [x] The baseline can be re-pinned from the page.
- [x] A second concurrent start is rejected rather than queued.
- [x] Fixture workspaces are cleaned up, including after a failed run.
- [x] With `EVAL_FAULT_INJECTION_ENABLED=false` the failure-recovery metric
      reports n/a instead of a misleading 1.0.
- [x] `evals/run_trajectory_evals.py` still runs offline and still exits
      non-zero on a violation.
- [x] `.venv/bin/python -m pytest -q` passes.
- [x] `.venv/bin/python -m compileall ai_agent_platform tests evals` passes.
- [x] `node --check ai_agent_platform/static/app.js` passes.
- [x] Documentation impact assessed and applied; `INTERVIEW_NOTES/validate.py`
      passes.

## Verification

```
.venv/bin/python -m pytest -q
  532 passed, 60 subtests passed in 36.96s

.venv/bin/python -m compileall ai_agent_platform tests evals   clean
node --check ai_agent_platform/static/app.js                    clean
.venv/bin/python INTERVIEW_NOTES/validate.py                    exit=0, 42 capabilities
git diff --check                                                clean

evals/run_evals.py                exit=0
evals/run_trajectory_evals.py     exit=0  (8/8, unchanged baseline)
evals/run_memory_evals.py         exit=0

docker compose up -d --build app   migration 0023 applied; eval_runs and
                                   eval_baselines exist in Postgres
```

Live checks against the running stack on :8000

| Check | Result |
| --- | --- |
| `GET /evals/catalogue` | 200; `providers=[deepseek/deepseek-v4-flash]`, `fault_injection_enabled=true` |
| `POST /evals/runs {provider: fake}` in the container | 400 `no enabled model is registered for provider 'fake'` — correct, only DeepSeek is registered there |
| `POST /evals/runs {provider: deepseek}` | 202, completed 8/8 cases in 369s, 791,906 tokens |
| second concurrent start | 409 |
| frontend `评测` page | renders metric cards, 7 alerts, case list with per-case stats, history row marked 基线 |
| fixture workspaces | removed after the run |

## Result

The L1 suite now runs inside the app against the user's registered DeepSeek, and
the results are visible in the product.

**Moved into the package** so the container has the code at all:
`evals/trajectory.py`, `evals/citations.py` and `evals/trajectory_cases.json`
became `ai_agent_platform/evaluation/*`. `evals/run_trajectory_evals.py` remains
the offline CLI and imports from the package.

**Added**: `ai_agent_platform/evaluation/` (`models`, `suite`, `faults`,
`service`), `api/routes/evals.py`, `schemas/evals.py`,
`repositories/evals.py`, migration `20260822_0023_eval_runs`, an `evals` view in
the frontend, and `tests/test_eval_service.py` (15 tests).

**Changed**: `core/config.py` and `core/config_resolver.py` (three eval settings,
registered as process-level fields and in the runtime profiles),
`runtime.py` (eval store, fault controller, eval service),
`api/router.py` + `main.py` (route wiring), `docker-compose.yml`, `.env.example`,
`evaluation/trajectory.py` (`check_constraints` grew a `provider` keyword).

### Measured DeepSeek baseline

`deepseek-v4-flash`, 8 cases, 791,906 tokens, 369s:

| Metric | fake | deepseek |
| --- | --- | --- |
| Pass rate | 1.000 | 0.125 |
| Invalid action rate | 0.000 | 0.372 |
| Mean step efficiency | 2.273 | 5.94 |
| Budget cap rate | 0.125 | 0.125 |
| Failure recovery rate | 1.000 | 1.000 |
| Citation accuracy | 1.000 | 1.000 |

Two findings the fake provider structurally cannot produce:

1. `suppressed_calls` is 2–11 per case under DeepSeek and always 0 under fake,
   because only a real model enters the native tool loop. That is what puts the
   invalid-action rate at 37%.
2. Seven of eight cases fail on **ungrounded citations**, and they are true
   positives. Asked where `create_order` is implemented, the model read only
   `src/api/routes.py` and then asserted `OrderService` at
   `src/services/orders.py:1` and `submit` at `:4` — correct content, inferred
   from an import, for a file the run never opened.

### Problems the first real run exposed, and what was done

- **`provider=fake` silently produced garbage in the container.** No fake model
  is registered there, so the tool selection narrowed to nothing and every case
  failed with `permission_denied` that looked like an agent bug. Now the service
  resolves the provider against the registry and refuses with a 400, and the
  frontend picker is built from `catalogue.providers`.
- **The eval workspace had no membership.** `workspace_service.register` alone
  leaves the actor without a role, so every run was denied. The service now
  calls `ensure_workspace_admin` exactly as the workspace route does.
- **Step ceilings were fake-calibrated** and failed every real case. `max_steps`
  is now the fake ceiling and `max_steps_by_provider` overrides per provider,
  set from the measured run with roughly 35% headroom. Tool requirements and
  ordering stay provider-independent — those are correctness, not style.
- **`code_explainer` was in `required_tools`** for the navigation case. It is a
  rule-planner helper that a native tool loop never calls, so requiring it only
  asserted which planner ran. Removed; the ordering constraint still covers the
  real requirement and self-skips when the tool is absent.

### Deliberate limits

- One real-model run is one sample. Variance is not measured; `pass^k` remains a
  DESIGN.md item, and the page presents single-run numbers as such.
- Fault injection is off by default. When off, failure recovery reports n/a
  rather than a 1.0 that was never earned. When on, the fault is scoped to the
  eval's own workspace so a concurrent user run cannot be hit.
- Runs are started by a person only. No scheduling.

**Not committed.** The tree also carries the earlier L1-TRAJECTORY-EVALS work and
unrelated uncommitted files from a previous session; staging is left to a human.
