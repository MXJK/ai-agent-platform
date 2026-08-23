# FIX-MODEL-CONFIG-DISMISS: Close the conversation model UI on outside click

## Goal

Make the conversation workbench's model/run-configuration popover dismissible
without requiring another click on its summary control.

## Scope

- Close the run-configuration popover when the user clicks outside both the
  popover and the Model scope chip.
- Close it with Escape and return focus to the Model scope chip.
- Keep clicks inside the popover and on the Model scope chip from immediately
  dismissing it.
- Keep the nested manual-model picker independently dismissible.
- Add a deterministic JavaScript regression test for these interactions.

## Acceptance criteria

- [x] Clicking the Model scope chip opens the run-configuration popover.
- [x] Clicking inside the popover leaves it open.
- [x] Clicking elsewhere in the workbench closes it.
- [x] Pressing Escape closes it and restores focus to the Model scope chip.
- [x] Escape closes the nested model picker before the outer popover.
- [x] Focused regression tests and the project verification commands pass.

## Decisions

- Keep the native `<details>` control and add explicit dismissal behavior around
  it; the bug did not require a replacement dialog/popover implementation.
- Treat the manual-model listbox as the inner dismissal layer. Its Escape
  handler stops propagation so one key press closes only that listbox; a second
  Escape closes the outer run-configuration popover.
- Outside-click dismissal does not move focus. Escape dismissal restores focus
  to the Model scope chip for keyboard continuity.
- Bump the `app.js` cache key so deployed browsers fetch the corrected event
  handlers instead of retaining the previous static asset.

## Documentation impact

No README or interview-handbook update is expected. This corrects an existing
popover dismissal behavior without changing an API, configuration, architecture,
or documented capability boundary.

## Verification

```text
node --check ai_agent_platform/static/app.js
  passed

node --test tests/test_model_config_dismiss.mjs
  4 passed

<root>/.venv/bin/python -m pytest -q
  539 passed, 60 subtests passed in 37.61s

<root>/.venv/bin/python -m compileall ai_agent_platform tests evals
  passed

git diff --check
  passed
```

Browser QA against the worktree on `127.0.0.1:8765` also confirmed:

- Clicking inside the configuration left it open.
- Clicking the conversation heading closed the outer configuration and nested
  model listbox.
- Escape closed the outer configuration and focused `composer-model-btn`.
- With the nested listbox open, Escape closed only that listbox, left the outer
  configuration open, and focused `model-picker-trigger`.

The first two full-suite attempts each exposed a different pre-existing async
timing failure; each failed test passed immediately in isolation, and the final
required full-suite run passed cleanly.

## Result

The conversation Model configuration now dismisses on an outside click and on
Escape. The nested manual-model picker retains its own dismissal layer, so
keyboard users can back out one level at a time. Four deterministic Node tests
cover outside, inside, outer-Escape, and nested-Escape behavior, and the static
asset cache key was updated.

No README or interview-handbook files were changed because this is a focused
correction to an already documented UI capability, with no contract or
architecture change.
