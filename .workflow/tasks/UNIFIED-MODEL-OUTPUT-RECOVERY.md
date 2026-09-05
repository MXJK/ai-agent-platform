# UNIFIED-MODEL-OUTPUT-RECOVERY

## Goal

Adopt one provider-independent output allocation policy for every registered model
and make output-limited Cogent tool turns recover instead of failing before the
runtime recovery boundary.

## Scope and constraints

- Use 8192 tokens for ordinary model turns and escalate the first
  output-limited turn to 64000; afterward allow at most three additional
  recoveries.
- Stop accepting or presenting manually authored per-model output limits.
  Preserve the existing database column and response field for compatibility,
  but normalize them to the backend-owned 64000 recovery ceiling.
- Keep context-window headroom and Usage Ledger authorization as effective
  request clamps.
- Treat `tool_output_truncated` as a recoverable Cogent condition, persist the
  retry boundary, retain failed-request usage, and never execute incomplete
  tool arguments.
- Update user-visible documentation and regression coverage.
- Do not call a paid Provider, migrate the live database, deploy, commit, or push.

## Acceptance criteria

- [x] New and existing registered models expose and route with the fixed 64000
      recovery ceiling; create/update APIs and the UI no longer accept a manual
      output-limit value.
- [x] Ordinary requests default to 8192; explicit thinking and output-limit
      recovery requests use 64000 subject to context and Usage Ledger clamps.
- [x] A truncated tool turn enters the same persisted 64K escalation plus
      three-additional-recoveries path as a completed response with
      `finish_reason=length`.
- [x] Recovery emits an explicit event and adds a focused one-tool correction;
      exhaustion returns a partial terminal result instead of a failed Run.
- [x] Focused tests, full pytest, compileall, frontend checks, handbook
      validation, and diff checks pass.

## Decisions

- Follow mewcode's numeric policy: ordinary model turns default to 8192;
  explicit thinking turns and the first output-limit recovery use 64000; no
  model may request more than the shared 64000 ceiling.
- Keep the persisted `max_output_tokens` column and response fields only for
  compatibility. Normalize new and existing registry views/runtime candidates
  to `min(context_window_tokens, 64000)` and reject manual API values.
- Let Cogent, rather than the Provider retry/fallback layer, own output-limit
  recovery. Both an output-limited turn without a tool call and truncated JSON
  tool arguments cross the persisted recovery boundary immediately.
- Keep incomplete tool arguments non-executable. Each recovery prompt requests
  exactly one smaller tool call and one focused file/patch; after the first
  64K escalation and three additional recoveries, terminate as
  `partial/output_limit_exhausted`.
- Preserve context-window headroom and Usage Ledger authorization as lower
  effective clamps. No database migration is needed because storage remains
  backward compatible.

## Verification

- `.venv/bin/python -m pytest -q` -> `829 passed, 12 skipped, 111 subtests passed`.
- Focused post-adjustment regression over Cogent, Provider mappings, router,
  registry, config, API, streaming, and context pipeline -> `191 passed, 50
  subtests passed`.
- `.venv/bin/python -m compileall -q ai_agent_platform tests evals` -> passed.
- `node --check ai_agent_platform/static/app.js` -> passed.
- `node --test tests/test_model_config_dismiss.mjs tests/test_chat_message_ui.mjs`
  -> `22 passed`.
- `.venv/bin/python INTERVIEW_NOTES/validate.py` -> validated 24 Markdown files
  and 45 capabilities; evidence-review reminders only.
- `git diff --check` -> passed.
- Running-container read-only HTTP verification showed all three registered
  models normalized to 64000, the page policy text present, and the removed
  manual-limit input absent.
- ego-browser opened the live `#models` page: zero manual-limit controls; all
  model cards displayed `8,192 普通 / 64,000 thinking 与恢复`.

## Result

Completed in the working tree. Model registration no longer exposes a
hand-authored output limit. The runtime uses the mewcode 8192/64000 allocation
scheme, and output-limited or argument-truncated tool turns now persist and
resume through Cogent for the 64K escalation plus at most three additional
recoveries before returning a partial result.

No paid Provider request, database migration, deployment, commit, or push was
performed. The full test suite's installation-flow coverage recreated `.venv`;
pytest was restored afterward and the affected regression set was rerun.
