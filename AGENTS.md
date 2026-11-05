# Project Guidance

## Verification

Run all relevant commands before marking a task done:

- `.venv/bin/python -m pytest -q`
- `.venv/bin/python -m compileall ai_agent_platform tests evals`

## Documentation synchronization

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
  in the delivery summary instead of making cosmetic documentation edits.
- Run `.venv/bin/python INTERVIEW_NOTES/validate.py` whenever the interview
  handbook or any evidence path mapped by `INTERVIEW_NOTES/facts.json` changes.

## Safety

- Codex and Claude Code are the agents allowed to modify source code by default.
- Require human confirmation for merge, release, migration, deployment, or external writes.
- Never store credentials or complete environment-variable values in workflow files.
