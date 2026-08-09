# COMPACT-CHAT-COMPOSER: Remove code context strip from chat composer

## Goal

Remove the code context block from the chat composer so the input area stays compact while preserving workspace selection and Agent execution behavior.

## In scope

- Remove the workspace/code-context strip from the unified chat composer.
- Remove frontend code and responsive styles used only by that strip.
- Keep the active workspace visible and editable through the existing sidebar/settings UI.
- Preserve Agent-mode workspace validation and execution behavior.
- Update the frontend smoke assertion to prevent the strip from returning.

## Out of scope

- Changing the dedicated Code Agent workspace context card.
- Changing workspace persistence, selection APIs, or Agent runtime behavior.
- Redesigning the rest of the composer or settings dialog.

## Acceptance criteria

- [x] The chat composer no longer renders the `代码上下文` strip in either response mode.
- [x] Switching between Chat and Agent modes does not depend on removed DOM elements.
- [x] Agent mode still requires a ready workspace and uses the session/default workspace selected in settings.
- [x] The dedicated Code Agent page and sidebar still show the current workspace.
- [x] Frontend syntax and repository verification commands pass.

## Decisions

- Keep workspace selection in the existing sidebar/settings manager instead of
  replacing the removed strip with another composer control.
- Keep the dedicated Code Agent workspace card because it is part of that page's
  task setup rather than duplicated composer chrome.
- Remove the strip's DOM-dependent JavaScript and responsive CSS instead of
  leaving a permanently hidden compatibility block.

## Verification

- `.venv/bin/python -m pytest -q` — 245 passed, 1 dependency deprecation
  warning, 4 subtests passed.
- `.venv/bin/python -m compileall ai_agent_platform tests evals` — passed.
- `node --check ai_agent_platform/static/app.js` — passed.
- `.venv/bin/python INTERVIEW_NOTES/validate.py` — validated 12 Markdown files
  and 29 capabilities; evidence-review notices only, no validation errors.
- `git diff --check` — passed.
- In-app browser smoke test — Chat and Agent composer modes both omitted the
  context strip and retained the same compact height; Agent submission remained
  enabled with the selected workspace, settings opened the workspace manager,
  the dedicated Agent page retained workspace path/role, and the console had no
  errors.

## Result

Removed the duplicated code-context strip from the unified chat composer,
including its workspace selector, settings shortcut, DOM updates, event binding,
and responsive CSS. Workspace selection continues through the existing sidebar
and settings manager, while the dedicated Code Agent page still shows detailed
workspace availability and role. Added a frontend regression assertion and
synchronized README/interview-handbook documentation. Existing unrelated Agent
runtime changes were preserved and landed separately. No migration or
deployment was performed.
