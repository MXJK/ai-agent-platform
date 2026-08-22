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
- `native_context_compactions` is written but never read as a stop condition. It is a
  counter, not a breaker, so a transcript that cannot converge folds again every round,
  spending a model call each time and losing information each time.
- There is no reactive path. Provider context-length errors terminate the round, while
  preflight relies on a token estimator that can under-count.
- The CLI exposes `/skills`, `/tools`, `/mcp`, `/permissions` and `/resume` but has no
  manual `/compact`.

## In scope

- A microcompact stage before folding: evict the bodies of older tool results, keeping
  the last K complete (`AGENT_TOOL_RESULT_KEEP_RECENT`, default 6), replacing the rest
  with a one-line marker. No model call.
- Post-fold recheck: re-measure after folding and fall through to the shared
  drop/truncate primitives when still over budget.
- A circuit breaker on `AGENT_NATIVE_MAX_COMPACTIONS` (default 3) that ends the run with
  `terminal_status="blocked"` and
  `terminal_reason="context_compaction_exhausted"`.
- A `context_overflow` error code for provider context-length errors, and exactly one
  forced compaction plus retry on it.
- CLI `/compact [instruction]`.
- Stage reporting through metrics and the existing SSE context event.

## Out of scope

- Budget derivation and allocation (UNIFIED-CONTEXT-BUDGET).
- Chat-layer compaction behavior.
- The tool-result entry cap (TOOL-RESULT-BUDGET), which is already in place.

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
      retry -- never a loop.
- [x] `/compact [instruction]` works in the CLI REPL.
- [x] `.venv/bin/python -m pytest -q` passes.
- [x] `.venv/bin/python -m compileall ai_agent_platform tests evals` passes.
- [x] Documentation updated: new configuration keys, the new terminal reason, the CLI
      command and the SSE stage field are user-visible. Run
      `.venv/bin/python INTERVIEW_NOTES/validate.py` when the handbook is present.

## Decisions

- Order is by cost to the conversation: evict the largest and most safely discardable
  content first, restructure the conversation last.
- Microcompact makes no model call. Most overruns are tool output, so the common case
  should cost nothing and preserve the transcript structure exactly.
- The breaker terminates rather than degrading silently. A run that folds every round
  is burning model calls and losing information; failing with a named reason is more
  honest and easier to diagnose than a slowly emptying context.
- The reactive retry is capped at one attempt. Retrying a context error more than once
  without new information is a loop, not a recovery.
- `context_overflow` bypasses model-router fallback and returns directly to the native
  Harness. Otherwise one high-level retry could hide several provider attempts.
- SSE context-stage events are derived deterministically from the saved `plan_tools`
  trace, so memory, SQLite, PostgreSQL and query stores share one cursor-stable path.
- Body truncation fits the cost of the final native message group rather than the raw
  body alone, and preserves the transcript-summary prefix plus tool artifact metadata.

## Verification

- Characterization before behavior changes:
  `tests/test_native_compaction_characterization.py` -- `1 passed`.
- Focused compaction, router, native-provider, streaming, CLI and configuration suite:
  `91 passed, 10 subtests passed`.
- Required full suite:
  `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m pytest -q`
  -- `503 passed, 60 subtests passed in 19.58s`.
- Required bytecode verification:
  `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m compileall ai_agent_platform tests evals`
  -- passed.
- Handbook validation: `.venv/bin/python INTERVIEW_NOTES/validate.py` -- validated 24
  Markdown files and 40 capabilities; the reported evidence-review warnings are
  informational and pre-existing.
- `git diff --check` -- passed.

## Result

Implemented the ordered native transcript reduction ladder, a bounded folding breaker,
provider-overflow recovery, `/compact [instruction]`, deterministic SSE stage events,
metrics and the two new environment settings. Regression coverage includes structural
assistant/tool pairing, post-fold remeasurement, metadata-preserving truncation,
compaction-limit exhaustion, router fallback suppression and the one-retry boundary.

Updated `README.md`, `README.en.md`, environment examples and Compose defaults. The
ignored local interview handbook was updated in the root checkout and validated because
worktrees do not inherit those files. Verification covered the task worktree before
commit; after commit authorization, the workflow state records the verified
implementation commit separately from its closeout metadata commit.
