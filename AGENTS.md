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
4. After implementation, run verification and update the task result.
5. Update `.workflow/state.yaml`; only record a commit actually covered by verification.

### Safety

- Codex is the only agent allowed to modify source code by default.
- Require human confirmation for merge, release, migration, deployment, or external writes.
- Never store credentials or complete environment-variable values in workflow files.
<!-- personal-dev-workflow:end -->
