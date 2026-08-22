# Project Guidance

<!-- personal-dev-workflow:start -->
## Personal Development Workflow

- Project ID: `ai-agent-platform`
- Source code and task specifications in this repository are authoritative.
- Obsidian is a cross-project summary, not the task source of truth.

### Verification

Run all relevant commands before marking a task done:

- `.venv/bin/python -m pytest -q`
- `.venv/bin/python -m compileall ai_agent_platform tests evals`

### Workflow

1. Read `.workflow/state.yaml` and the active task file before editing.
2. Confirm the goal, acceptance criteria, and permitted scope.
3. Keep complex task specifications in `.workflow/tasks/<task-id>.md`.
4. Before closing any task, assess documentation impact.
5. After implementation, run verification and update the task result.
6. Update `.workflow/state.yaml`; only record a commit actually covered by verification.

### Documentation synchronization

- Treat `README.md` and the modular interview handbook rooted at
  `INTERVIEW_NOTES.md` as part of the feature definition, not optional follow-up
  work.
- When a task changes user-visible behavior, API contracts, configuration,
  architecture, data flow, operations, verification commands, or capability
  boundaries, update `README.md`, `INTERVIEW_NOTES.md`, the affected
  `INTERVIEW_NOTES/*.md` Parts, and `INTERVIEW_NOTES/facts.json` in the same
  task.
- Remove or correct stale claims when a capability is replaced or deleted; do
  not leave legacy architecture described as current behavior.
- If a task has no documentation impact, record that conclusion and the reason
  in the task Result instead of making cosmetic documentation edits.
- Run `.venv/bin/python INTERVIEW_NOTES/validate.py` whenever the interview
  handbook or any evidence path mapped by `INTERVIEW_NOTES/facts.json` changes.

### Safety

- Codex and Claude Code are the agents allowed to modify source code by default.
- Require human confirmation for merge, release, migration, deployment, or external writes.
- Never store credentials or complete environment-variable values in workflow files.
<!-- personal-dev-workflow:end -->

## Parallel agents

<!-- Kept outside the personal-dev-workflow markers so a regeneration of that
     block cannot drop these rules. -->

- The checkout at the repository root stays on `main`. Never `git checkout` a
  task branch there; another agent may be reading or serving those files.
- Do task branches in a dedicated worktree:
  `git worktree add ../<name> -b <branch>`.
- Only the root checkout runs the docker dev stack on port 8000. Frontend
  verification happens there, after the branch is merged. A second stack started
  from a worktree will fail to bind the port.
- Worktrees do not inherit gitignored files: `.venv`, `.env`, and
  `docker-compose.override.yml` are absent. Use the root checkout's interpreter
  at `<root>/.venv/bin/python`.
- Tests must not depend on the process working directory. `Settings`
  defaults `workspace_allowed_roots` to `$HOME`, so a test that requests a
  workspace at `"."` passes in the root checkout and fails in a worktree outside
  the home directory. Pin both `workspace_allowed_roots` and the workspace
  `root_path` to the test's own temporary directory, as `tests/test_mcp_lifecycle.py`
  and `tests/test_mcp_provider.py` do.
