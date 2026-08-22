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

- [ ] Characterization tests are written and passing **before** the move, locking the
      current chat-layer reduction behavior, following the pattern in
      `tests/test_agent_loop_characterization.py`.
- [ ] No observable behavior change: `tests/test_context_budget.py` and
      `tests/test_context_pipeline.py` pass unmodified.
- [ ] `context_budget.py` imports no repository, no LLM client and no metrics registry.
- [ ] The Protocol supports the native message shape (`call_id`, `tool_calls`,
      `is_error`) without normalizing it to `{role, content}`.
- [ ] `.venv/bin/python -m pytest -q` passes.
- [ ] `.venv/bin/python -m compileall ai_agent_platform tests evals` passes.
- [ ] Documentation impact assessed. If behavior is genuinely unchanged, record that
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

## Verification

## Result
