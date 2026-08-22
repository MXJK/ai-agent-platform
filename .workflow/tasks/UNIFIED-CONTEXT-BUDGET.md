# UNIFIED-CONTEXT-BUDGET: One budget authority, explicitly divided

## Goal

Make one place resolve the input allowance and divide it into named shares, instead of
four independent caps in three different units deciding what reaches the model. Keep
the two reduction pipelines separate; unify only the authority and the primitives.

This is step 4 of 4 and depends on CONTEXT-BUDGET-PRIMITIVES and
LAYERED-TRANSCRIPT-COMPACTION. It carries the highest regression risk of the four.

## Audit findings this task closes

On the agent path, conversation history passes four gates, each with its own unit:

| Gate | Unit | Model-aware | Value today |
| --- | --- | --- | --- |
| `services/session_service.py:416` `assemble_agent_context` | tokens | yes | window x `llm_context_input_token_ratio` (0.6) |
| `agents/coding/runtime_support.py:302` `recent_conversation_context` | messages + chars | no | 6 messages / 1800 chars |
| `agents/coding/context_nodes.py:344` RAG evidence | chars | no | `_max_rag_context_chars` |
| `agents/coding/tool_loop_nodes.py:839` `_compact_native_messages` | chars or tokens | partly | 48000 chars or window x `agent_native_context_token_ratio` (0.5) |

- The second gate makes the first largely inert on the agent path. The chat layer fits
  history to a model-derived token budget with three-stage overflow recovery, and
  `native_tool_messages` (`agents/coding/planner.py:441`) then cuts it to 1800
  characters — roughly 450 tokens — regardless of the model.
- Two ratios (0.6 and 0.5) are applied independently to the same window by two layers
  that both feed the same request.
- `_compact_native_messages` protects `seed = messages[:2]` unconditionally, and that
  seed is a single user message carrying `conversation_context`, `evidence`,
  `project_instructions`, `focus_files` and `context_warnings` as one JSON payload.
  Today the 1800-char cap keeps it small. Once evidence and history are sized against
  the model window instead of static character constants, the seed can exceed the whole
  native budget, and compaction becomes structurally unable to converge — every round
  measures over budget, folds the middle, and gains nothing.

## In scope

- A single resolved allowance divided into named shares: fixed overhead (system prompt
  and tool schemas), evidence, conversation history, and the remainder for the tool
  transcript.
- One ratio replacing `llm_context_input_token_ratio` and
  `agent_native_context_token_ratio`.
- `MAX_AGENT_HISTORY_CHARS`, `MAX_AGENT_HISTORY_MESSAGES`, `_max_rag_context_chars` and
  `agent_native_context_max_chars` demoted to fallbacks used only when no model
  information is available.
- Evidence and history sized against their share at seed assembly time, in
  `native_tool_messages` and `context_nodes.py`, rather than after the fact.
- The seed participates in reduction instead of being unconditionally protected.
- Configuration migration and documentation synchronization.

## Out of scope

- Merging the two reduction pipelines. See Decisions.
- Anything already delivered by the first three tasks.

## Acceptance criteria

- [ ] Exactly one place resolves the allowance and divides it; no layer re-derives a
      ratio from the window on its own.
- [ ] The seed is measured against its share before the loop starts, and can be reduced
      rather than being protected unconditionally.
- [ ] A small-window model produces a proportionally smaller seed, verified by a test
      that runs the same request against two window sizes.
- [ ] Setting the fallback constants to their current values restores today's behavior,
      giving an escape hatch equivalent to the `LLM_MAX_CONTEXT_MESSAGES` floor/ceiling
      pattern already used.
- [ ] The compaction breaker from LAYERED-TRANSCRIPT-COMPACTION is in place first, since
      this task is what makes seed overflow reachable.
- [ ] `.venv/bin/python -m pytest -q` passes.
- [ ] `.venv/bin/python -m compileall ai_agent_platform tests evals` passes.
- [ ] `README.md`, `INTERVIEW_NOTES.md`, the affected `INTERVIEW_NOTES/*.md` Parts and
      `INTERVIEW_NOTES/facts.json` updated; `.venv/bin/python INTERVIEW_NOTES/validate.py`
      passes. Stale claims about the old per-layer character caps are removed, not left
      alongside the new behavior.

## Decisions

- **The two pipelines are not merged.** Three reasons, each sufficient on its own:
  - *Data shape.* The chat layer handles `{role, content}` strings. The native layer
    handles `role="tool"`, `call_id`, `tool_calls` and `is_error` with a pairing
    invariant — an assistant tool call must be followed by its result or the provider
    rejects the request. Chat-layer drop-oldest would break that pairing.
  - *Side-effect profile.* Chat compaction is a service operation: it writes a versioned
    `ConversationSummary` under an optimistic lock and can be enqueued to a worker.
    Native compaction runs inside a LangGraph node that replays on checkpoint resume and
    must stay synchronous and effectively pure.
  - *Lifetime.* A session summary outlives the run and spans days; a native transcript
    dies with the run. One object cannot cleanly carry both "persistent and versioned"
    and "disposable" semantics.
- What is unified is the budget authority and the stateless primitives. Each layer keeps
  its own policy: the chat layer keeps summary persistence, version locking and
  `through_message_id` alignment; the native layer keeps pairing invariants, tool-result
  eviction, externalization and the compaction breaker.
- Sizing happens at assembly, not after. Assembling an oversized seed and compacting it
  afterwards wastes the retrieval work and, for the protected seed, does not even work.

## Verification

Run from the task worktree with the main checkout's interpreter, since a worktree does
not inherit `.venv`:

- `"…/ai-agent-platform/.venv/bin/python" -m pytest -q`
  - Baseline on the branch this was cut from: `503 passed, 60 subtests passed`.
  - PASS after the change: `528 passed, 63 subtests passed in 19.95s` (25 new tests).
- `"…/ai-agent-platform/.venv/bin/python" -m compileall ai_agent_platform tests evals`
  - PASS.
- `tests/test_context_budget.py`, `tests/test_context_budget_primitives.py`,
  `tests/test_context_budget_characterization.py` and
  `tests/test_native_compaction_characterization.py` pass **unmodified**, so the chat
  layer and the step-2/step-3 primitives are unchanged.
- `INTERVIEW_NOTES/` is gitignored by design: the handbook is a local-only artifact and
  never travels on a branch. It is therefore absent from the worktree, and
  `INTERVIEW_NOTES/validate.py` plus the affected Parts are run and updated in the root
  checkout after merge, as every task in this repository does.

### New coverage

- `tests/test_context_shares.py` (8 tests) — division boundaries: shares add back up to
  the allowance, transcript takes the remainder, shares scale with the window, overhead
  larger than the window leaves nothing, ratios that leave no transcript room are
  rejected.
- `tests/test_unified_context_budget.py` (8 tests) — seed groups are marked verbatim,
  truncating the seed returns it unchanged, the payload stays parseable, non-seed groups
  are still truncatable, schema overhead grows with the pool, and a window with no
  transcript room blocks with `context_budget_too_small` before any provider call.
- `tests/test_context_pipeline.py` — a smaller window produces a smaller seed, evidence
  over its share still serializes as valid JSON, low-ranked evidence is dropped before
  text is trimmed, the user's own request is never trimmed, and absent shares keep the
  static fallback behavior.
- `tests/test_agent_conversation_context.py` — a generous share keeps more than the
  static cap, a tight share keeps less, the excerpt is bounded in tokens, and the
  retrieval-query path keeps the static bound.

## Result

One place now resolves the input allowance and divides it. `_setup_workspace`
(`agents/coding/context_nodes.py`) resolves the budget once, calls the new pure
`divide_context_budget` in `services/context_budget.py`, and writes named shares into
`context_shares` on the run state and into its own trace. Every later layer reads its
share; no layer derives a second ratio from the window.

- `agent_native_context_token_ratio` (0.5) is gone. `llm_context_input_token_ratio`
  (0.6) is the single window ratio, split by the new `llm_context_evidence_ratio`
  (0.25) and `llm_context_history_ratio` (0.15); their sum must leave room for the
  transcript or `Settings` raises at startup.
- Shares apply at seed assembly, not afterwards. `native_tool_messages` drops whole
  low-ranked evidence sources before binary-searching a trim of the last survivor's
  `text`, measured against the serialized payload rather than the text alone.
  `recent_conversation_context` gained `max_tokens`, converting the share into a
  character bound using the conversation's own character-per-token density, and lets
  the share rather than a fixed count of 6 decide how many messages fit.
  `build_workspace_query` deliberately keeps the static bound: it builds a retrieval
  query, not model context.
- `_native_context_budget_tokens` returns the transcript share less the measured tool
  schema cost (`_tool_schema_tokens`), since schemas ride along on every request but
  never appear in the transcript the ladder measures.
- New terminal `blocked/context_budget_too_small` when schemas alone exhaust the
  window, carrying the configuration keys to change instead of looping.
- `MAX_AGENT_HISTORY_CHARS`, `MAX_AGENT_HISTORY_MESSAGES`, `_max_rag_context_chars` and
  `AGENT_NATIVE_CONTEXT_MAX_CHARS` are retained as fallbacks used only when budget
  resolution degraded, which is the escape hatch the acceptance criteria asked for.

### Defect found while reading the code and fixed here

`_native_message_groups` marked the seed `protected`, but protection only blocks
dropping — `fit_context_to_budget` truncates every item, protected or not. The seed's
user message is a single `json.dumps` payload, so the drop/truncate stage cut a
head/tail through it and sent the model malformed JSON, and it reached the seed first
because truncation runs oldest-first. Groups now carry `verbatim`, the seed sets it, and
the policy returns such groups unchanged. Sizing the seed field by field at assembly is
what makes that safe rather than merely deferring the problem.

### Deliberate behavior change to two step-3 tests

`test_provider_overflow_gets_one_forced_compaction_and_one_retry` and
`test_second_provider_overflow_blocks_instead_of_retrying_again` padded the seed through
`user_input`. Under the new invariant the request the user just sent is never trimmed to
buy room, so a seed made of the user's own text is legitimately irreducible and the run
blocks instead of retrying. The padding moved into conversation history — context the
harness chose to include and can drop again — which preserves both tests' intent. To keep
step 3's reactive contract intact, `_resize_native_seed` rebuilds the seed from halved
shares before the forced ladder runs, and `_reduce_native_messages` gained
`already_changed` so a forced pass is not judged exhausted when the caller already made
progress outside it.

### Not done

- The two reduction pipelines are still separate, as the Decisions section requires.
- The interview handbook is updated in the root checkout after merge, since it is
  gitignored by design and cannot travel on this branch. Part 02 (the context budget
  section now has one window ratio instead of two), Part 04 (seed assembly and the
  reduction ladder) and any `facts.json` evidence path naming
  `agent_native_context_token_ratio` need to follow.
