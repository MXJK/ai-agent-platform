# INLINE-CHECKPOINT-UI: Put Run checkpoints under the assistant response

## Goal

Move the checkpoint-facing UI out of the conversation header area and into the
bottom of the assistant response that owns the Agent Run, matching the interaction
model used by Codex.

## In scope

- Remove the global Run/checkpoint ribbon above the conversation.
- Keep approval, input, pause, steer, cancel, and checkpoint-history actions attached
  to the matching assistant message.
- Expose checkpoint history for running, suspended, and terminal Runs without moving
  the user's reading position away from the response.
- Preserve desktop/mobile layout, keyboard behavior, session restore, and ChangeSet
  ordering.
- Update user-facing documentation that describes the old persistent header ribbon.

## Out of scope

- Checkpoint API, restore semantics, or storage changes.
- Redesigning the checkpoint history dialog.
- Changes to non-Agent chat responses.

## Acceptance criteria

- [x] No Run/checkpoint control is rendered above the conversation.
- [x] The matching assistant response ends with a compact Run/checkpoint footer.
- [x] Running controls and suspended checkpoint decisions remain usable in-message.
- [x] Terminal Runs still open the Git-style checkpoint history.
- [x] ChangeSet review stays above the Run/checkpoint footer.
- [x] Narrow layouts keep the footer readable and controls keyboard accessible.
- [x] Focused frontend/API tests, JavaScript syntax checks, handbook validation, full
      pytest, compileall, and `git diff --check` pass.

## Test design

- Happy path: the static shell omits the old top ribbon and the assistant renderer
  creates the inline footer with a checkpoint-history action.
- State boundaries: queued Runs omit checkpoint history; running, suspended, and
  terminal Runs keep the response-owned footer while their state-specific controls
  remain available.
- Regression: ChangeSet review inserts before the footer, session restore resolves the
  footer by Run ID, and no legacy active-run DOM IDs or event handlers remain.

## Decisions

- Delete the global header ribbon instead of moving the same large card into the
  transcript. Running controls already belonged to the assistant message; a compact
  response-owned footer now carries status and checkpoint-history access.
- Re-append the footer on every Run render so it remains the bubble's last element
  across queued, running, suspended, and terminal state transitions.
- Insert ChangeSet review before an existing footer. This preserves the reading order:
  answer, metrics, change review, then Run/checkpoint status.
- Keep queued Runs status-only because no checkpoint history exists yet. Running,
  suspended, and terminal Runs expose the history action.
- Preserve cancellation for approval/input/pause boundaries by adding `取消 Run` to the
  inline checkpoint card after removing the global ribbon.
- Bump the static asset cache key so deployed browsers receive the new DOM and CSS
  together.

## Verification

- Focused frontend/API contract: `36 passed in 8.21s`.
- Full suite after the final suspended-cancel preservation change:
  `619 passed, 79 subtests passed in 57.90s`.
- `python -m compileall ai_agent_platform tests evals`: PASS.
- `node --check ai_agent_platform/static/app.js`: PASS.
- `git diff --check`: PASS.
- Interview handbook validation: `Validated 24 Markdown files and 43 capabilities`;
  existing evidence-review notices remain warnings.
- Real browser desktop check at 1280px: no top control, footer is the final assistant
  bubble child, appears after content, width 600px, and has no page-level horizontal
  overflow.
- Real browser narrow check at 390x844: footer is the final bubble child, width 308px,
  checkpoint button stays inside its bounds, and no page-level horizontal overflow is
  introduced.
- Impeccable detector ran in degraded regex mode because optional parser modules were
  unavailable. It reported only pre-existing side-accent and width-transition warnings;
  no finding pointed to the new footer.

## Documentation impact

- Updated `README.md` and `README.en.md` to remove the stale persistent-header-ribbon
  claim.
- Updated the gitignored project handbook root, Parts 04/07, and the
  `checkpoint_time_travel` fact title to describe response-owned controls.

## Result

The conversation header no longer renders a Run/checkpoint ribbon. Every Agent response
owns a compact bottom footer with its current state and checkpoint-history action; queued
responses omit the unavailable history action. Running steer/pause/cancel controls,
suspended approval/input actions, and terminal history access all remain attached to the
matching assistant message. ChangeSet review is kept immediately above the footer, and
the checkpoint history dialog/API semantics are unchanged.

No commit or merge was created. The verified implementation remains in the dedicated
`codex/inline-checkpoint-ui` worktree for review and integration.
