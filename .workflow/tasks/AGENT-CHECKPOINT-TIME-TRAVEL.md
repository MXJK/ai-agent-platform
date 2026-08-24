# AGENT-CHECKPOINT-TIME-TRAVEL: Pause, continue, and branch Agent runs

## Goal

Make long-running Agent work visibly controllable in the conversation UI and
allow a user to inspect any durable LangGraph checkpoint, continue from an old
execution point in the current conversation, or fork that point into a new
conversation without deleting the original execution history.

## Scope

- Add a persistent active-Run control strip for pause, continue, cancel, and
  checkpoint history instead of relying only on controls inside one message.
- Expose normalized, actor-authorized checkpoint history from the existing
  LangGraph checkpointer.
- Restore a selected safe checkpoint into a new Run with a fresh thread and
  tool-call identity while preserving the selected graph state.
- Support two audited restore modes:
  - `rollback`: create the restored Run in the current conversation and make it
    the latest execution path.
  - `fork`: copy the visible conversation prefix into a new conversation and
    run the restored branch there.
- Preserve the source Run and checkpoint history for auditability.
- Make the UI explicit that graph-state restoration does not silently revert
  workspace files; existing ChangeSet rollback remains the file rollback path.
- Synchronize README and interview-handbook capability descriptions.

## Acceptance criteria

- [x] A queued/running Run exposes pause and cancel in a persistent control strip.
- [x] A paused or waiting-input Run exposes continue with optional direction.
- [x] Waiting-approval still uses its argument-bound approve/deny flow.
- [x] Checkpoint history returns ordered IDs, parents, timestamps, graph steps,
      next nodes, summaries, interrupt state, and restore eligibility.
- [x] A checkpoint is verified as belonging to the actor-authorized source Run.
- [x] Restoring never mutates or deletes the source Run.
- [x] Restored branches use a fresh `run_id`/`thread_id`, fresh elapsed-time
      budget, cloned immutable Run context, and restored Effective Tool Pool.
- [x] `rollback` adds a new auditable user turn and Run to the same conversation.
- [x] `fork` creates a new conversation containing the source prefix and opens it.
- [x] Completed checkpoint history remains inspectable; checkpoints with no next
      node are visible but cannot be resumed.
- [x] The UI shows a version-control-style checkpoint rail with current,
      restorable, interrupted, and terminal states plus rollback/fork actions.
- [x] The UI warns that filesystem state remains current and points users to the
      ChangeSet rollback control for file reversal.
- [x] Memory, SQLite, and PostgreSQL-compatible Run/session paths retain their
      existing behavior.
- [x] Focused backend, API, JavaScript, visual, full-suite, compile, docs, and
      diff-hygiene verification pass.

## Non-goals

- Do not destructively rewrite an existing Run or LangGraph thread.
- Do not reconstruct arbitrary historical filesystem snapshots from graph state.
- Do not automatically replay a worker-lost running task.
- Do not merge, deploy, or execute paid model requests.

## Design direction

- Subject: a local developer operating a long-running code Agent.
- Single job: retain control of the active Run and choose a causal restart point.
- Palette: reuse the product's existing ink/surface/signal/amber/red tokens.
- Type: retain the existing display/body/mono roles; checkpoint IDs and graph
  nodes use the utility mono face.
- Layout: a compact active-Run ribbon below the conversation header and a wide
  modal with a branch rail on the left and restore guidance/actions on the right.
- Signature: a Git-like checkpoint spine whose nodes encode state and whose fork
  action visibly peels into a new conversation path.

## Documentation impact

This adds user-visible lifecycle controls, API contracts, checkpoint semantics,
and a new capability boundary. Update `README.md`, `README.en.md`,
`INTERVIEW_NOTES.md`, the relevant Agent/persistence/frontend Parts, and
`INTERVIEW_NOTES/facts.json`.

## Decisions

- Treat both restore modes as append-only branching operations: `rollback`
  appends a new Run to the current conversation, while `fork` copies the visible
  prefix before the source Run into a new conversation. Neither rewrites the
  source Run or its LangGraph thread.
- Clone the selected LangGraph snapshot into a fresh thread and strip stale
  interrupt/resume control writes before invoking its recorded next node.
- Freeze an independent execution workspace for every restored Run. A cleaned
  `patch_only` sandbox is rebuilt from the currently registered source root;
  checkpoint restoration therefore restores graph causality, not historical
  filesystem bytes. ChangeSet revert remains the file rollback boundary.
- Require an active source Run to reach a safe suspended boundary before time
  travel. Terminal checkpoints without a next node remain inspectable but are
  not restorable.
- Keep the persistent Run ribbon available after message scrolling and use a
  responsive Git-like rail/detail dialog for history. A successful restore that
  later fails to switch the UI reports that the new path was still created.

## Verification

- `.venv/bin/python -m pytest -q` — `566 passed, 60 subtests passed`.
  After rebasing onto `main`, the first full run had one transient
  `test_history_and_baseline_pinning` timing failure; its isolated rerun and the
  subsequent full run both passed.
- `.venv/bin/python -m compileall ai_agent_platform tests evals` — passed.
- Focused checkpoint/API/session/queue/Celery suite — `84 passed`.
- `node --check ai_agent_platform/static/app.js` — passed.
- `python INTERVIEW_NOTES/validate.py` — validated 24 Markdown files and 43
  capabilities; evidence-review notices remain advisory.
- `git diff --check` — passed.
- Real in-app-browser QA on the local fake-provider server verified a completed
  `patch_only` Run whose original sandbox had been cleaned, historical
  checkpoint selection, fork creation, automatic navigation to the new
  conversation, independent execution root, and completed restored Run.
- Visual QA covered the persistent controls and checkpoint dialog at desktop
  size and at a 390 x 844 mobile viewport.

## Result

Implemented persistent pause/continue/cancel/checkpoint controls, normalized and
authorized checkpoint history APIs, append-only rollback/fork restoration,
independent execution-workspace rehydration, in-process and Celery dispatch, and
the responsive time-travel UI. The source Run remains auditable, terminal-only
boundaries are read-only, and filesystem reversal remains explicitly separated
behind ChangeSet revert.
