# Cogent workbench

React + TypeScript + Vite frontend, served by the existing FastAPI process.
Node is a development/build dependency, not an additional production service.

```bash
# From the repository root; Node >= 22.18
npm --prefix frontend ci
npm --prefix frontend run dev
# Open http://127.0.0.1:5173/static/; API requests proxy to 127.0.0.1:8000.
npm --prefix frontend run build
npm --prefix frontend test
node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs tests/test_trace_audit_ui.mjs
```

`build` typechecks TypeScript and replaces `ai_agent_platform/static` with the
production HTML and hashed assets. Commit those assets with source changes so a
Python-only checkout/install remains usable. Docker rebuilds them in a Node stage;
the Python wheel includes `static/assets/*`. Do not edit generated assets by hand.

## Ownership and migration boundary

- `app/Workbench.tsx` and `features/*-view.tsx` define the nine React page shells.
  `ViewPanel` subscribes to typed navigation state and keeps panels mounted to
  preserve form drafts and conversation nodes.
- `features/registry-lists.tsx`, `knowledge-lists.tsx`, and `session-lists.tsx`
  render keyed model/provider, Skill/MCP, knowledge/document and session lists.
  React escapes text. Unsaved provider credentials stay in component memory and
  survive background status refresh; they are cleared after a successful save.
- `components/ReactSlot.tsx` is an explicit migration boundary: domain controllers
  publish immutable render snapshots; React exclusively owns descendants of those
  slots. Controllers must not assign `innerHTML` or append/remove nodes inside a
  React slot. Delegated handlers on its parent are allowed; do not register both
  delegated and React handlers for the same action.
- `runtime/` contains the extracted JavaScript controllers, grouped by domain.
  Chat streaming, approval/question cards, ChangeSet/checkpoint details, memory,
  evaluation and audit rendering still include imperative DOM code. Their DOM
  islands are mounted once by the React shell. This is an incremental migration,
  **not a claim that all state and rendering have become typed React components**.
- `lib/api.ts` handles JSON/multipart identity headers, errors, cancellation and
  request telemetry. `lib/sse.ts` decodes UTF-8 and split LF/CRLF frames. Run/Event
  recovery and cursor/generation checks remain in `runtime/runs.js`.
- `lib/resources.ts` owns listener/timer cleanup; `app/lifecycle.ts` connects the
  committed shell to controller startup and disposal. No document-global boot
  script or production `/static/app.js` compatibility copy is maintained.
- `styles/tokens.css` defines dark/light semantic colors; domain styles map to those tokens.
  `styles/compact.css` is loaded last and owns the confirmed split-workbench layout,
  density and responsive overrides. `design.md` is the approved specification.
- `app/theme.tsx` and the pre-paint HTML script use `cogent.appearance` for local-only
  appearance. System changes are live in system mode; explicit choices override them.
- `lib/inspector.ts` publishes current-session/run snapshots to `RunInspector` without
  a separate fetch or polling loop. Terminal run restoration fetches its event history
  once; current Trace renders the latest 100 events, while full audit keeps its existing
  incremental cursor and page semantics. Session selection clears the projection.
- `SectionTabs` and `EditorDialog` keep domain islands mounted. Closing an editor hides
  it without discarding inputs. React actions must call controller-owned ChangeSet
  nodes, never pass React-owned nodes to imperative mutation helpers.

## Verification

Vitest/Testing Library checks the real ES module graph, page switching, draft
retention, registry safety, HTTP cancellation, multipart uploads and SSE framing.
The established Node tests continue to exercise controller behavior through a
source loader; they are supplemented by React tests rather than treated as proof
of browser integration. Some Python tests still retain explicit source contracts.
The API serving test follows generated asset URLs instead of assuming source-file
names. Backend and handbook checks remain in the root `AGENTS.md`.

Further migration should replace one controller-owned island at a time, move its
state and events into typed hooks/components, add user-behavior tests, and remove
the corresponding DOM renderer and native listeners in the same change.

Local migration results and remaining limits: [verification record](../docs/verification/frontend-react-2026-11-04.md).

Current layout and verification: [2026-11-06 redesign](../docs/verification/frontend-redesign-2026-11-06.md).
