# CONTEXT-BUDGET-PRIMITIVES: Extract shared, side-effect-free budget primitives

## Goal

Stop implementing token accounting and budget fitting twice. Extract the reduction
primitives the chat layer already has into a module both layers can use, without
changing any observable behavior.

This is step 2 of 4. It unblocks LAYERED-TRANSCRIPT-COMPACTION and
UNIFIED-CONTEXT-BUDGET. It is deliberately behavior-preserving so it can land and be
verified on its own.

## Audit findings this task closes

- `_fit_context_to_budget` (`ai_agent_platform/services/session_service.py:517`) and its
  helpers `_fit_text_to_tokens` / `_head_tail` are private to the chat layer, so the
  native tool loop has no drop or truncate stage at all — its only reduction action is
  a full fold (`agents/coding/tool_loop_nodes.py:839`).
- The two layers report reduction through different vocabularies, so context pressure
  cannot be read as one picture across a run.

## In scope

- New `ai_agent_platform/services/context_budget.py` holding pure functions only.
- Move `_fit_context_to_budget`, `_fit_text_to_tokens` and `_head_tail` there, made
  generic over a Protocol describing how to cost an item, how to truncate its content,
  and which items are protected from dropping.
- One `ContextReduction` result type with `dropped`, `truncated`, `compacted` and
  `evicted` counters, which `ConversationContextUsage` reports through unchanged fields.
- `session_service.py` adapted to call the extracted module.

## Out of scope

- Adoption by the native tool loop (LAYERED-TRANSCRIPT-COMPACTION).
- Any change to how the budget number is derived or divided (UNIFIED-CONTEXT-BUDGET).
- Any change to summary persistence, versioning or `through_message_id` alignment.

## Acceptance criteria

- [x] Characterization tests are written and passing **before** the move, locking the
      current chat-layer reduction behavior, following the pattern in
      `tests/test_agent_loop_characterization.py`.
- [x] No observable behavior change: `tests/test_context_budget.py` and
      `tests/test_context_pipeline.py` pass unmodified.
- [x] `context_budget.py` imports no repository, no LLM client and no metrics registry.
- [x] The Protocol supports the native message shape (`call_id`, `tool_calls`,
      `is_error`) without normalizing it to `{role, content}`.
- [x] `.venv/bin/python -m pytest -q` passes.
- [x] `.venv/bin/python -m compileall ai_agent_platform tests evals` passes.
- [x] Documentation impact assessed. If behavior is genuinely unchanged, record that
      conclusion and the reason in Result instead of making cosmetic doc edits.

## Decisions

- Pure functions only. Callers own every side effect. This is not stylistic: chat-layer
  compaction writes a versioned `ConversationSummary` row and can be enqueued to a
  worker, while native compaction runs inside a LangGraph node that replays on
  checkpoint resume — a shared module that wrote to a repository would double-write on
  replay.
- The Protocol adapts to each message shape rather than normalizing both layers onto
  one message type. The native shape carries tool-call pairing invariants that a
  `{role, content}` normalization would destroy, and a broken pair is a provider 400.
- Scope stays at extraction. Mixing a refactor with a behavior change would remove the
  one property that makes this step cheap to verify.
- `ContextBudgetPolicy` owns item cost, truncation and drop protection. The generic
  fitter copies the input sequence and never projects a native tool message onto a
  chat-only schema, so provider-critical metadata survives by construction.
- `SessionService` remains the owner of summary persistence and metrics. It maps the
  shared `ContextReduction` counters back to the existing
  `ConversationContextUsage` fields and counter names.

## Verification

- Before extraction:
  `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m pytest -q tests/test_context_budget_characterization.py`
  - PASS: `1 passed in 0.56s` against the original private implementation.
- Focused after extraction:
  `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m pytest -q tests/test_context_budget_primitives.py tests/test_context_budget_characterization.py tests/test_context_budget.py tests/test_context_pipeline.py`
  - PASS: `21 passed, 4 subtests passed in 0.65s`.
  - `tests/test_context_budget.py` and `tests/test_context_pipeline.py` were not edited.
- Required full suite:
  `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m pytest -q`
  - PASS: `492 passed, 56 subtests passed in 18.90s`.
- Required bytecode verification:
  `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m compileall ai_agent_platform tests evals`
  - PASS.

## Result

- Added `services/context_budget.py` with a generic `ContextBudgetPolicy`, frozen
  `ContextReduction` counter bookkeeping, drop-then-truncate fitting, and the extracted
  head/tail text primitives. The module imports only the Python standard library.
- Adapted chat context assembly to the shared result while preserving its exact drop
  order, truncation output, protected summary/latest-message rules, usage fields and
  metrics.
- Added a pre-move golden characterization and focused primitive tests. The native
  shape test keeps `tool_calls`, `call_id` and `is_error` intact and proves the input
  transcript is not mutated.
- Documentation impact: none. This task deliberately changes no API, configuration,
  architecture boundary visible to operators, data flow, or model-observed context;
  editing README or the interview handbook would only restate existing behavior.
- Work is isolated on stacked branch `codex/context-budget-primitives`, based on
  `codex/tool-result-budget`. The user authorized commit, push and merge on
  2026-08-22; no deployment, release or migration is part of this task.
