# COGENT-PERMISSION-MODE-PARITY

## Goal

Make Cogent's four permission modes match the reference implementation end to
end across runtime, CLI, and Web while preserving platform hard denies.

## Scope and constraints

- Keep the four public modes: `default`, `acceptEdits`, `plan`, and
  `bypassPermissions`.
- Carry a mode-level allow through the central PermissionResolver without
  bypassing process, Workspace/RBAC, project, Secret, protected-path, command
  allowlist, or dangerous-command hard denies.
- Match the reference plan behavior: prompt-level read-only discipline, automatic
  plan-file writes, and HITL fallback for other writes/commands.
- Expose the same modes in CLI startup, CLI/TUI interaction, and the Web
  composer.
- Preserve the previously active `COGENT-FILE-MEMORY-REFACTOR` task and resume
  it after this focused repair.
- Do not migrate, deploy, commit, push, create a PR, or merge.

## Acceptance criteria

- [x] The mode matrix is tested for read, write, and command tools.
- [x] `acceptEdits` executes permitted file edits without HITL but still asks
      for commands.
- [x] `bypassPermissions` executes permitted writes and commands without HITL;
      hard denies remain effective.
- [x] `plan` receives an explicit read-only system reminder, may write only the
      current plan file without HITL, and asks before other writes/commands.
- [x] Strict central `always` and `never` policies cannot be weakened by a
      permission mode.
- [x] CLI supports `--permission-mode`, `/permissions`, and TUI mode cycling and
      displays the effective selection.
- [x] Web exposes all four modes and submits the selected mode to the Run API.
- [x] Focused, frontend, documentation, compile, CLI, and real-browser checks
      pass; the full suite was run and its one unrelated file-memory failure is
      documented below.

## Decisions

- A Cogent `allow` decision bridges the central `on_request` policy as
  `auto_approve`, but does not weaken `always`, `never`, process capability,
  Workspace/RBAC, project selection, protected-path, or dangerous-command
  denies.
- Safe read-only shell commands remain auto-allowed, matching the reference
  checker. Other commands use the four-mode matrix.
- MCP invocations are classified as commands even when remote annotations say
  read-only. The first three modes therefore confirm them; bypass skips only
  their ordinary confirmation while central hard boundaries remain active.
- Plan mode is enforced by both its system prompt and the checker: only its
  exact plan file is auto-writable; other writes and commands fall back to
  HITL, and leaving plan mode still requires confirmation.
- `/permissions` remains a shared Run command. CLI/TUI state changes only after
  the server reports command completion, preventing client/server divergence.

## Verification

- Focused permission/runtime/CLI/API/MCP/acceptance suite:
  `110 passed`, `12 skipped`, `2 subtests passed`.
- `node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs`:
  `23 passed`.
- `.venv/bin/python -m compileall ai_agent_platform tests evals`: passed.
- `.venv/bin/python INTERVIEW_NOTES/validate.py`: validated 24 Markdown files
  and 45 capabilities; evidence-review warnings only.
- `git diff --check`: passed.
- Real Web QA on `127.0.0.1:8877`: all four selector labels and dynamic hints
  rendered; an `acceptEdits` submission carried
  `permission_mode=acceptEdits`, `workspace_id=permission-qa`, completed through
  the fake Provider, and displayed `Agent 已完成`.
- Real public CLI HTTP/SSE smoke with
  `--permission-mode bypassPermissions`: Run `run_7be1bc7c9047` reached
  `run_completed`.
- Full `.venv/bin/python -m pytest -q`: `872 passed`, `12 skipped`, `111`
  subtests passed; one unrelated pre-existing failure remains in
  `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`
  because the test expects `OSError` while the current file-memory code safely
  normalizes the symlink rejection to `ValueError`. Neither file is part of
  this permission-mode change.

## Result

Completed the permission-mode parity repair across runtime, API, CLI/TUI, and
Web. The focused task is ready for handoff without a commit; resume the prior
`COGENT-FILE-MEMORY-REFACTOR` task, which owns the remaining unrelated full-suite
failure.
