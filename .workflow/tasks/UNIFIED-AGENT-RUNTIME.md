# UNIFIED-AGENT-RUNTIME: completion-driven persistent Agent loop

## Goal

Replace the fixed post-retrieval coding workflow with a Codex/Claude Code-style
observe-decide-act loop that continues until a verifiable completion condition,
approval/input boundary, cancellation, or explicit multi-dimensional budget is
reached, while always preserving a grounded finalization path.

## In scope

- Unify native read, mutation, validation, and artifact tool results in one
  model-observed loop.
- Add soft and hard round/call budgets, elapsed-time, no-progress, consecutive
  failure, finalization, graph-step, and run-context compaction policies.
- Preserve provider-native call IDs and tool-result messages; force a no-tools
  final response when a hard stop is reached.
- Distinguish completed, partial, blocked, paused, cancelled, waiting-input, and
  waiting-approval lifecycle outcomes.
- Add cooperative pause/cancel/steer controls, durable run events/tool execution
  records, cursor-based event streaming, and corresponding frontend controls.
- Preserve workspace sandbox and approval boundaries, including explicit user
  review for side-effecting tools under the existing safe policy.
- Add unit, integration, API, persistence, frontend syntax, and offline-eval
  coverage for the new lifecycle.
- Synchronize README and interview-handbook documentation when present.

## Out of scope

- Deploying the service, executing database migrations, merging, releasing, or
  performing external writes.
- Replacing the multi-provider LLM layer with a vendor-specific Agent SDK.
- Exposing hidden chain-of-thought; progress events contain concise activity and
  evidence summaries only.

## Acceptance criteria

- [x] Every successful tool result is fed back to the selected provider before
  the model chooses another tool or emits a final answer.
- [x] Read, mutation, validation, and artifact tools can participate in the same
  bounded observe-decide-act loop without a fixed one-pass phase boundary.
- [x] A text-only provider finalization call is reserved after hard budget or
  no-progress termination and uses the compacted native transcript.
- [x] Hard-budget termination is reported as partial/blocked rather than normal
  completion; finalization/provider errors remain observable.
- [x] Soft/hard rounds and calls, elapsed time, consecutive failures,
  no-progress, context compaction, final-output reserve, and graph execution
  limits are configurable and validated.
- [x] Runs can be paused, resumed, cancelled, and steered at safe execution
  boundaries; approvals remain resumable from a LangGraph checkpoint.
- [x] Run events and tool execution outcomes are durably queryable, and the UI
  consumes a cursor-aware server event stream with a polling fallback.
- [x] Durable `(run_id, call_id)` execution identity prevents replay of a known
  completed tool call after worker redelivery or resume.
- [x] Existing repository/RAG/memory/model-routing behavior remains compatible.
- [x] Required repository verification, frontend syntax, migration-head, and
  documentation validation checks pass.

## Decisions

- Keep the provider-neutral LangGraph runtime and existing ToolRegistry; evolve
  their state and policy boundaries instead of embedding Codex or Claude SDKs.
- Treat LangGraph recursion as an internal execution allowance, never as the
  business stop condition.
- Treat a provider response with no tool calls as the normal completion signal;
  deterministic completion checks can return the loop to the model when a
  requested code change still lacks validation evidence.
- Use soft budgets to request convergence and hard budgets to force a final
  no-tools answer. The forced answer does not consume a tool round.
- Preserve canonical run messages alongside provider-native payloads so compacted
  runs remain inspectable and provider adapters can render their own protocol.
- Default to soft limits of 12 model rounds / 36 tool calls and hard limits of
  24 rounds / 72 calls, plus 900 seconds elapsed time, 3 no-progress rounds, 3
  consecutive failures, 48,000 native transcript characters, 10 retained
  messages, and a LangGraph recursion allowance of 128.
- Represent suspension and termination explicitly with `waiting_input`,
  `waiting_approval`, `paused`, `partial`, `blocked`, and `cancelled`; keep
  `completed` only for verified normal completion.
- Persist append-only run events and `(run_id, call_id)` tool executions in
  PostgreSQL, expose cursor-based SSE, and retain polling as a frontend fallback.
- Add `always`, `on_request`, and `never` approval profiles; keep `on_request`
  as the compatibility-preserving default.

## Verification

- `.venv/bin/python -m pytest -q` — 245 passed, 1 dependency deprecation
  warning, 4 subtests passed.
- `.venv/bin/python -m compileall ai_agent_platform tests evals` — passed.
- `node --check ai_agent_platform/static/app.js` — passed.
- `.venv/bin/alembic heads` — `20260808_0018 (head)`.
- `git diff --check` — passed.
- `.venv/bin/python INTERVIEW_NOTES/validate.py` — validated 12 Markdown files
  and 29 capabilities; evidence-review notices only, no validation errors.
- In-app browser smoke test against the fake-provider local runtime — selected
  the repository workspace, submitted an Agent run, consumed the SSE event
  stream, rendered all progress events, and reached `completed`. The smoke test
  exposed an undefined status variable in `renderAgentRun`; it was fixed and the
  same flow passed on rerun.

## Result

Implemented the completion-driven Agent framework across runtime, provider
adapters, storage, HTTP/SSE API, worker resumption, and the native web console.
The native tool loop now observes every tool result, supports model-requested
input, safe-boundary pause/cancel/steer controls, context compaction, explicit
completion policy, multidimensional budgets, and a reserved text-only finalizer.
Durable event and tool-execution tables are defined in Alembic revision
`20260808_0018`; the migration was created but not applied because migration
execution requires human confirmation. README and the locally ignored modular
interview handbook were synchronized with the new behavior. Commit and push
were explicitly authorized on 2026-08-09; this task document and implementation
form the verified delivery.
