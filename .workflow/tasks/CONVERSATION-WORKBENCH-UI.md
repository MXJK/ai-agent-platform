# CONVERSATION-WORKBENCH-UI: Turn chat into a focused Agent workbench

## Goal

Reshape the conversation page from a persistent landing-style composition into a
focused workbench that keeps the active session, execution scope, messages, and
recovery actions visible across desktop and mobile layouts.

## In scope

- Keep the ASK → PLAN → ACT → VERIFY presentation as the empty-session state only.
- Add a compact active-session header with the session title, run status, Workspace,
  model, and direct session actions.
- Give the message list its own viewport on desktop while keeping the composer stable.
- Add a visible Workspace/context strip to the composer and reduce configuration noise.
- Add message copy/edit/retry affordances and an actionable failure card.
- Make the Inspector a modal drawer with a backdrop below the wide desktop breakpoint.
- Replace the seven-item mobile navigation row with four primary destinations plus More.
- Preserve the current static HTML/CSS/JavaScript architecture and backend contracts.

## Out of scope

- New attachment or file-upload backend APIs.
- Changes to Agent execution, approval, model routing, or persistence semantics.
- Replacing the static frontend with a component framework.
- Merge, deployment, release, or Docker-stack rebuilds.

## Acceptance criteria

- [x] Empty chat shows the existing welcome presentation; a loaded conversation hides it
      and shows the compact session header.
- [x] Desktop chat uses the available viewport for messages and does not scroll the whole
      document to follow streaming output.
- [x] The active-session composer always exposes the current Workspace, its
      availability/role, the selected model, and current context estimate when known.
- [x] User messages offer copy and edit/reuse; assistant messages offer copy; failed
      responses offer retry, model selection, and run-detail actions.
- [x] At widths at or below 1120px, opening the Inspector shows a backdrop, contains
      background scrolling, and closes with Escape or backdrop click.
- [x] Mobile keeps New session accessible, shows no unlabeled checkbox controls, and uses
      four primary navigation destinations plus More.
- [x] Keyboard focus remains visible; streaming output does not use the entire conversation
      as a continuously updating live region.
- [x] Frontend regression assertions cover the new workbench structure and behavior.
- [x] `.venv/bin/python -m pytest -q` passes.
- [x] `.venv/bin/python -m compileall ai_agent_platform tests evals` passes.
- [x] Documentation impact is assessed and recorded.

## Design decisions

- The existing dark engineering-console palette remains the product identity.
- The process rail is meaningful only before work begins; active work uses compact,
  information-dense controls instead of a marketing hero.
- Inline execution progress stays concise. The right rail remains the deep diagnostic view.
- Workspace scope is a safety signal and cannot disappear on mobile.
- Advanced model controls stay available but become secondary to task entry.

## Verification

- `node --check ai_agent_platform/static/app.js` — passed.
- `git diff --check` — passed.
- Root interpreter: `python -m pytest -q tests/test_api.py -k serves_unified_chat_and_workspace_agent_frontend`
  — 1 passed, 34 deselected.
- Root interpreter: `python -m pytest -q` — 485 passed and 56 subtests passed.
- Root interpreter: `python -m compileall ai_agent_platform tests evals` — passed.
- Browser QA against the isolated fake-provider server:
  - 1280×720: empty and active desktop states do not overlap; active messages scroll
    independently from the document.
  - 1024×768: Inspector opens as a 380 px drawer with backdrop and body scroll lock.
  - 390×844: no horizontal overflow, five visible navigation items (four primary + More),
    Workspace/model/context scope remains visible, advanced configuration stays collapsed,
    and message edit/reuse restores text and focus to the composer.

## Result

Implemented the focused conversation workbench without changing backend contracts. The
empty state keeps the existing Agent introduction, while active conversations use a compact
session header, bounded message viewport, execution-scope strip, secondary run
configuration, reusable message actions, and structured failure recovery. Responsive
navigation and Inspector behavior now preserve the same controls on narrow screens.

Updated `README.md` for the new user-visible behavior. The repository at this revision does
not contain the `INTERVIEW_NOTES` handbook named by project guidance, so there was no
handbook file to synchronize. Implementation verification was completed before integration;
the authorized commit and merge are recorded by Git history and the final workflow state.
