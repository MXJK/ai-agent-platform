# COGENT-RUN-HISTORY-PRESENTATION

## Goal

Restore each persisted Agent reply's displayable thinking summary and live
activity after a session reload or message refresh.

## Scope and constraints

- Preserve the active `COGENT-FILE-MEMORY-REFACTOR` work and unrelated files.
- Expose the already-persisted message-to-Run relationship through the message
  API.
- Rehydrate every assistant message from its own Run, while retaining latest-Run
  pause/resume and SSE recovery behavior.
- Reuse compact completed reasoning and activity entries already present in the
  Run result instead of fetching token-sized streaming deltas for history.
- Do not commit, push, migrate, deploy, or make external writes.

## Acceptance criteria

- [x] Message responses expose `source_run_id` without breaking messages that
      were not produced by an Agent Run.
- [x] Every persisted assistant message is bound to and hydrated from its own
      Run rather than only the latest Run.
- [x] Completed Run traces restore displayable thinking summaries and activity
      entries without loading all streaming deltas.
- [x] Missing/deleted historical Runs degrade to the persisted answer instead
      of failing the whole session.
- [x] Latest queued/running Runs still reconnect to SSE and existing controls.
- [ ] Focused backend/frontend tests, full verification, and real-browser
      reload checks pass.

## Decisions

- Add the domain's existing `source_run_id` to `MessageResponse`; keep it
  optional so historical and manually-created messages remain compatible.
- Bind each persisted assistant bubble to `source_run_id`, fetch historical
  Runs independently, and leave a missing/deleted Run as a plain saved answer.
- Render the latest Run before waiting for historical requests, so an old slow
  or unavailable Run cannot delay current status or SSE reconnection.
- Reconstruct presentation events from terminal Run result traces. The trace's
  persisted `node` identifies `thinking_completed` and activity event types,
  avoiding a token-delta event download for every historical turn.
- Documentation impact: updated README and the ignored local interview handbook
  capability/evidence records because reload behavior and the message response
  contract are user-visible.

## Verification

- `node --check ai_agent_platform/static/app.js`: passed.
- `node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs`:
  26 passed.
- Focused API tests for the static asset and Agent Run message stream: 2 passed,
  38 deselected.
- `.venv/bin/python -m compileall ai_agent_platform tests evals`: passed.
- `.venv/bin/python INTERVIEW_NOTES/validate.py`: validated 24 Markdown files
  and 45 capabilities; only the existing evidence-review warnings remained.
- `git diff --check`: passed.
- Real Compose API inspection of `sess_61b2645282ec`: persisted messages expose
  the correct Run IDs and terminal trace includes `thinking_completed` and
  tool activity needed by reload presentation.
- Full `.venv/bin/python -m pytest -q`: 872 passed, 12 skipped, 111 subtests
  passed; one unrelated pre-existing failure remains in
  `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`.
  That file-memory test expects `OSError`, while `ManagedFiles.read` currently
  normalizes `ELOOP`/`ENOTDIR` to `ValueError`; neither file is changed here.
- Real Chromium reload acceptance is pending because the Codex browser quota
  was exhausted; the app reported that it resets after 16:16 on 2026-09-06.

## Result

Implementation and scoped verification are complete. Historical assistant
replies now recover their own thinking summary and activity, while latest-Run
SSE behavior and missing-Run fallback are preserved. The task remains blocked
only on real-browser reload acceptance and the repository-wide pre-existing
file-memory test failure described above. No commit, push, migration, deploy,
or external write was performed.
