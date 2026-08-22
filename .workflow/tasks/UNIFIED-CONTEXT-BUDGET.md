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

## Result
