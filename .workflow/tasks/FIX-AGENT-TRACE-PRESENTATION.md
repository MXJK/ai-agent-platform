# FIX-AGENT-TRACE-PRESENTATION: Stabilize frontend Agent trace completion

## Goal

Keep the chat Agent execution trace accurate through terminal states, consume
the cursor SSE as the live progress source without refetching the full run for
every event, and make asynchronous trace updates accessible.

## In scope

- Preserve the final Agent run snapshot returned from the SSE watcher.
- Render live node progress from SSE events and fetch the full run at terminal
  or polling-fallback boundaries.
- Keep trace playback readable without delaying large completed traces for many
  seconds.
- Mark the Agent timeline and inspector trace as polite live regions.
- Add regression coverage for the frontend contract and verify in a real browser.

## Out of scope

- Exposing private model chain-of-thought.
- Changing Agent runtime, event persistence, tool permissions, or API schemas.
- Applying migrations, deploying, releasing, or committing.
- Reworking unrelated composer layout changes already present in the worktree.

## Acceptance criteria

- [x] A completed Agent run keeps its final answer and a collapsed completed
  execution process in the chat bubble.
- [x] Waiting, paused, failed, partial, blocked, and cancelled terminal or
  suspended states are not overwritten by the original queued response.
- [x] Node progress is rendered from SSE payloads without one full run GET per
  event; polling remains available when streaming fails or ends early.
- [x] Trace playback has a bounded catch-up delay for large or backfilled traces.
- [x] Agent event and inspector trace updates are exposed as polite live regions.
- [x] Required repository verification and a browser regression pass.

## Decisions

- Treat durable `node_completed` SSE events as the live trace source. Rebuild
  the frontend trace from those payloads and fetch the complete Run only for a
  terminal or suspended event.
- Preserve the last complete SSE snapshot through polling cleanup. If a stream
  reports a terminal state without a usable snapshot, polling performs one
  defensive final refresh instead of falling back to the original queued body.
- Keep ordered playback but cap batch replay at 1.2 seconds with a 16ms maximum
  per-step delay; skip the delay when reduced motion is requested.
- Keep polling unchanged as the transport fallback when SSE fails or ends
  before a terminal state.
- Use non-atomic polite live regions for the timeline and inspector trace so
  asynchronous progress remains available to assistive technology.

## Verification

- `.venv/bin/python -m pytest -q` — 245 passed, 1 dependency deprecation
  warning, 4 subtests passed.
- `.venv/bin/python -m compileall ai_agent_platform tests evals` — passed.
- `node --check ai_agent_platform/static/app.js` — passed.
- `git diff --check` — passed.
- `.venv/bin/python INTERVIEW_NOTES/validate.py` — validated 12 Markdown files
  and 29 capabilities; evidence-review notices only, no validation errors.
- In-app browser completed-run regression — 15 streamed trace nodes and 18
  events rendered; final answer remained visible; execution process had the
  `complete` class, was collapsed, and no response placeholder remained.
- In-app browser suspended-run regression — `waiting_approval` remained visible
  in both Agent and Chat status; the chat bubble showed the approval guidance
  and did not regress to queued/running.
- Request-log verification — each browser run used one full
  `GET /api/v1/agent/runs/{run_id}` at its terminal/suspended event instead of
  one GET per streamed node; browser console error/warn count was zero.

## Result

The browser now renders live Agent trace nodes directly from cursor SSE event
payloads and reads one complete Run snapshot at terminal or suspended states.
The watcher returns that snapshot through cleanup, so the composer no longer
replaces completed answers or approval guidance with the initial queued state.
Ordered trace playback is short and reduced-motion aware, and both asynchronous
trace surfaces are polite live regions. README and interview-handbook claims
were synchronized. No commit, migration, deployment, or external write was
performed.
