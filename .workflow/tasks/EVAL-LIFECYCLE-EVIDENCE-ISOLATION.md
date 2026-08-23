# EVAL-LIFECYCLE-EVIDENCE-ISOLATION: Make in-app Agent evaluation trustworthy

## Goal

Make the in-app Agent evaluation lifecycle accurate, reproducible, explainable,
and isolated from the owner's normal chat, memory, project-scene, and knowledge
base state.

## Scope

- Distinguish proposed, executed, succeeded, failed, suppressed, denied, and
  pending-approval tool calls from the persisted run payload.
- Grade required tools, ordering, step ceilings, repetition, and recovery only
  from calls joined to real `ToolResult` records.
- Build a successful-read evidence ledger from initialization context plus
  successful native `repo.read_file` results; do not promote search matches or
  unsuccessful calls into file-read evidence.
- Split citation content accuracy, answer-path grounding, and fully grounded
  case rate, preserving `None`/`n/a` when no sample is scoreable.
- Add an explicit Eval execution context that preserves provider/model registry
  selection while excluding owner profile, ordinary history, user/project
  memory, project-scene refresh, global KB routing, and background extraction.
- Version eval records and baselines, key baselines by provider, model, suite,
  and evaluator version, remove first-completed auto-pinning, and protect
  critical runs from accidental baseline pinning.
- Show model selection, token/time quality data, and lifecycle counts in the
  Eval UI without turning token/time regressions into hard gates.
- Keep persistence/API reads backward compatible; no destructive database
  migration and no reinterpretation of existing rows as current-version data.
- Synchronize the requested README, eval design, and interview-handbook docs.

## Acceptance criteria

- [x] Missing `ToolResult` never counts as executed or successful.
- [x] Required tools, order, `max_steps`, repeated-call rate, and recovery use
      only actually executed calls.
- [x] Invalid action rate is `(executed exact repeats + suppressed) /
      (executed + suppressed)`; denied/pending remain separate.
- [x] Forbidden proposed calls raise an Agent-planning warning; forbidden
      executed calls raise a platform-safety critical alert.
- [x] Per-case proposed/executed/suppressed/denied/pending details are retained.
- [x] Only successful `repo.read_file` results create native read evidence with
      normalized path, range, content/hash, truncation, and call ID.
- [x] Unique basenames can ground answers; ambiguous basenames cannot.
- [x] Search-only, failed, suppressed, denied, and pending calls do not prove a
      file was read.
- [x] Citation content accuracy, answer-path grounding rate, and fully grounded
      case rate are separate and preserve `n/a` for no scoreable samples.
- [x] Eval runs use registered provider/model configuration but inject no owner
      profile/history/global KB/memory and schedule no memory extraction.
- [x] Ordinary Agent runs retain their current profile, memory, and KB behavior.
- [x] Baselines are explicitly pinned and compatible only on provider + model +
      suite + evaluator version; critical runs require explicit force.
- [x] Existing baselines are treated as legacy evaluator data and never silently
      compared with current runs.
- [x] Run detail/baseline comparison expose total/per-case token/time figures and
      proposed/executed/suppressed totals with metric denominator/direction notes.
- [x] The deterministic fake L1 suite stays green without changing provider
      `max_steps` overrides and no paid provider is executed.
- [x] Requested focused, full, compile, documentation, JavaScript, and diff
      verification commands pass.

## Non-goals

- Do not run paid DeepSeek/OpenAI or other real-provider evaluations.
- Do not loosen step ceilings or citation truthfulness rules.
- Do not implement LLM Judge, SWE-bench, pass^k, prompt optimization, or
  unrelated tool-loop/business changes.
- Do not add a global switch that changes ordinary Agent runs.

## Decisions

- Use a new evaluator/schema version and compatibility-aware deserialization so
  old persisted records remain readable but are not eligible as current
  baselines.
- Prefer deterministic unit/service tests with fakes and spies; real-provider
  verification requires later human approval because it costs tokens.
- Treat one `ToolResult` as one execution even if malformed input repeats a
  call ID; missing `ok` is failed/unknown-safe rather than successful.
- Keep `repo.search_code` scoreable for its returned line content while never
  treating it as whole-file read grounding. Require exact whitespace for read
  evidence; only search matches use stripped-line comparison.
- Drive isolation from persisted RunContext entrypoint metadata populated by
  the explicit Query flag. A generated Eval principal is only an authorization
  subject and is not used as the isolation convention.
- Hard-purge Eval workspaces after deleting scoped project-memory rows and
  vectors; normal workspace deletion remains the existing reversible soft
  delete. Eval Query persistence does not create temporary user preferences.
- Baseline comparison also checks schema version even though the required
  persisted key is provider/model/suite/evaluator. Unsafe downgrade with more
  than one model/suite baseline per provider aborts instead of deleting rows.

## Documentation impact

This changes user-visible metrics, API/domain contracts, persistence
compatibility, execution isolation, and the documented evaluation data flow.
Update `README.md`, `evals/README.md`, `evals/DESIGN.md`, `INTERVIEW_NOTES.md`,
`INTERVIEW_NOTES/08-可观测性、评测与测试.md`, and
`INTERVIEW_NOTES/facts.json`.

## Verification

- Focused required command:
  `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m pytest -q tests/test_trajectory_evals.py tests/test_eval_service.py tests/test_native_tool_calling.py`
  — 88 passed, 4 subtests passed in 20.66s.
- Expanded targeted regression (trajectory, Eval, native tools, context,
  Query, project memory, workspace/local cleanup, API UI contract) — 205
  passed, 4 subtests passed in 28.50s.
- Full suite: `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m pytest -q`
  — 562 passed, 60 subtests passed in 40.70s.
- Compile: `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m compileall ai_agent_platform tests evals`
  — passed.
- Interview handbook: `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python INTERVIEW_NOTES/validate.py`
  from the root checkout — validated 24 Markdown files and 42 capabilities;
  exit 0, with the validator's expected evidence-review warnings for changes
  newer than its recorded commit baseline.
- Frontend syntax: `node --check ai_agent_platform/static/app.js` — passed.
- Diff hygiene: `git diff --check` — passed.
- Migration graph: `alembic heads` — one head, `20260823_0024`.
- No DeepSeek, OpenAI, or other paid provider evaluation was run.

## Result

Implemented evaluator/schema v2 and suite `l1_trajectory_v2`. Lifecycle metrics
now join proposals to ToolResults by call ID, keep security outcomes separate,
and expose auditable per-case details. Successful-read evidence merges initial
file context with native read results, while citation content and answer-path
grounding are scored independently. Application Eval runs keep registered model
selection but are isolated from owner state and clean up sessions, hard-purged
workspaces, project-memory rows, and vectors.

Baselines are manual, compatibility-keyed, schema-checked, and protected from
critical runs unless explicitly forced. Migration `20260823_0024` labels all
existing rows as legacy without rewriting their metrics. The UI supports every
enabled model under a provider, explains metric direction/denominators, renders
lifecycle/evidence/errors, and compares token/time only as regression warnings.

Documentation was synchronized in `README.md`, `README.en.md`, `evals/README.md`,
and `evals/DESIGN.md`. The gitignored local interview handbook was updated in
the root checkout (`INTERVIEW_NOTES.md`, Part 08, and `facts.json`) and validated
there without changing the root checkout branch or tracked Git status.

Remaining limits: no paid v2 real-model sample exists; LLM Judge, pass^k,
SWE-bench, cost-in-currency, and distribution/variance analysis remain out of
scope. Cleanup failures are logged and do not overwrite the primary Eval
result. Database migration execution against a live PostgreSQL instance and UI
browser interaction still require human-controlled deployment/merge context;
only the migration chain, repository behavior, static frontend contract, and
offline tests were verified here. No commit was created because none was
authorized.
