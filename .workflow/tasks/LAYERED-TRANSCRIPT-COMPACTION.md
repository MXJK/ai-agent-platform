# LAYERED-TRANSCRIPT-COMPACTION: Ordered reduction ladder for the native transcript

## Goal

Replace the single fold in the native tool loop with an ordered ladder that spends the
cheapest reduction first, verifies that folding actually worked, and stops folding
forever when it cannot converge. Add the reactive path that covers a wrong token
estimate.

This is step 3 of 4 and depends on CONTEXT-BUDGET-PRIMITIVES.

## Audit findings this task closes

- `_compact_native_messages` (`agents/coding/tool_loop_nodes.py:839`) has exactly one
  action: keep `messages[:2]` plus the last `keep_messages` items and fold everything
  between into a single summary. There is no eviction stage and no truncation stage, so
  one oversized tool result costs the entire middle of the transcript, including the
  model's reasoning and any user steering.
- `keep_messages` (default 10, `agent_native_context_keep_messages`) is a message count,
  not a token count. The kept window can itself exceed the budget, and the function
  returns the result without re-measuring.
- `native_context_compactions` is written at `tool_loop_nodes.py:392` and `:440` and is
  never read as a stop condition anywhere in the tree. It is a counter, not a breaker,
  so a transcript that cannot converge folds again every single round, spending a model
  call each time and losing information each time.
- There is no reactive path. `integrations/llm.py:2216` treats only 408, 409 and 429 as
  retryable; a provider context-length error terminates the round. The preflight check
  at `integrations/llm.py:1737` relies on `estimate_text_tokens`
  (`token_counting.py:13`, `unicode_heuristic_v1`, ASCII at 4 chars per token), which
  can under-count and let a request through that the provider then rejects.
- The CLI exposes `/skills`, `/tools`, `/mcp`, `/permissions` and `/resume`
  (`ai_agent_platform/cli.py:278`) but has no manual `/compact`.

## In scope

- A microcompact stage before folding: evict the bodies of older tool results, keeping
  the last K complete (proposed `AGENT_TOOL_RESULT_KEEP_RECENT`, default 6), replacing
  the rest with a one-line marker. No model call.
- Post-fold recheck: re-measure after folding and fall through to the drop/truncate
  primitives from CONTEXT-BUDGET-PRIMITIVES when still over budget.
- A circuit breaker on `AGENT_NATIVE_MAX_COMPACTIONS` (proposed default 3) that ends the
  run with `terminal_status="blocked"` and `terminal_reason="context_compaction_exhausted"`.
- A `context_overflow` error code for provider context-length errors, and exactly one
  forced compaction plus retry on it.
- CLI `/compact [instruction]`.
- Stage reporting through metrics and the existing SSE `context` event.

## Out of scope

- Budget derivation and allocation (UNIFIED-CONTEXT-BUDGET).
- Chat-layer compaction behavior.
- The tool-result entry cap (TOOL-RESULT-BUDGET), which should already be in place.

## Acceptance criteria

- [x] Characterization tests lock today's `_compact_native_messages` behavior before any
      change, following `tests/test_agent_loop_characterization.py`.
- [x] Reduction runs in order: tool-result eviction, then fold, then drop/truncate.
- [x] Microcompact never splits an assistant/tool pair and never leaves a `tool_calls`
      entry without a matching result; assistant text and message structure survive.
- [x] Folding re-measures its own output; a still-over-budget transcript falls through
      instead of being returned as if it fit.
- [x] Exceeding the compaction limit terminates the run with the documented reason and a
      final answer that says where it stopped, instead of folding again.
- [x] A provider context-length error triggers exactly one forced compaction and one
      retry — never a loop.
- [x] `/compact [instruction]` works in the CLI REPL.
- [x] `.venv/bin/python -m pytest -q` passes.
- [x] `.venv/bin/python -m compileall ai_agent_platform tests evals` passes.
- [x] Documentation updated: new configuration keys, the new terminal reason, the CLI
      command and the SSE stage field are user-visible. Run
      `.venv/bin/python INTERVIEW_NOTES/validate.py`.

## Decisions

- Order is by cost to the conversation, matching the harness reference material:
  evict the largest and most safely discardable content first, restructure the
  conversation last.
- Microcompact makes no model call. Most overruns are tool output, so the common case
  should cost nothing and preserve the transcript structure exactly.
- The breaker terminates rather than degrading silently. A run that folds every round is
  burning model calls and losing information; failing with a named reason is more honest
  and easier to diagnose than a slowly emptying context.
- The reactive retry is capped at one attempt. Retrying a context error more than once
  without new information is a loop, not a recovery.
- Initial user requests, checkpoint restore directions, and pause/resume steering are
  verbatim/non-truncatable. If those immutable instructions cannot fit, the Run blocks
  instead of silently continuing with a partial direction.
- Restored transcripts validate assistant/tool call boundaries before any reduction.
  Current multi-call turns require complete call IDs; the single positional pair written
  by legacy checkpoints remains accepted when both sides omit IDs.
- Checkpoint clones normalize missing compaction channels to `0`, `[]`, and `false`,
  preserve channels that are present, clear the consumed `/compact` command metadata,
  and mark inherited context-stage events as replayed with their source identity.

## Verification

- Focused compaction/checkpoint/router/CLI/config suite:
  `77 passed, 14 subtests passed`.
- Full suite: `586 passed, 68 subtests passed`.
- `.venv/bin/python -m compileall ai_agent_platform tests evals`: passed.
- `git diff --check`: passed.
- `INTERVIEW_NOTES/validate.py` was not applicable: the current `main` stopped tracking
  the interview handbook in commit `ca71b1e6`; this task changed both `README.md` and
  `README.en.md` instead.

## Result

Implemented the ordered native-transcript reduction ladder, post-fold remeasurement,
bounded fold breaker, provider `context_overflow` recovery, CLI `/compact`, stage metrics
and SSE events, and environment configuration. Added checkpoint/time-travel compatibility
for missing or consumed compaction state, verbatim steering under pressure, replay-labeled
inherited events, and invalid assistant/tool boundary rejection. Documentation describes
the user-visible terminal reason, event contract, settings, and manual command.
