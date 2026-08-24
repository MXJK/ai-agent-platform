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

- [x] Exactly one place resolves the allowance and divides it; no layer re-derives a
      ratio from the window on its own.
- [x] The seed is measured against its share before the loop starts, and can be reduced
      rather than being protected unconditionally.
- [x] A small-window model produces a proportionally smaller seed, verified by a test
      that runs the same request against two window sizes.
- [x] Setting the fallback constants to their current values restores today's behavior,
      giving an escape hatch equivalent to the `LLM_MAX_CONTEXT_MESSAGES` floor/ceiling
      pattern already used.
- [x] The compaction breaker from LAYERED-TRANSCRIPT-COMPACTION is in place first, since
      this task is what makes seed overflow reachable.
- [x] `.venv/bin/python -m pytest -q` passes.
- [x] `.venv/bin/python -m compileall ai_agent_platform tests evals` passes.
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
- Fixed overhead is explicit rather than hidden inside the transcript share. The
  authority records `system_tokens` and `tool_schema_tokens`; evidence and history split
  what remains, and transcript receives the exact remainder. The native ladder measures
  all messages against `total_tokens - tool_schema_tokens`, because system and seed are
  already present in that serialized message list.
- User messages are hard chronological barriers for semantic folding. Each contiguous
  non-user segment is summarized in place, so older, interleaved, checkpoint, queued and
  pause/resume steering remain byte-exact and in their original timeline. If system seed
  plus verbatim user messages alone exceed the unified allowance, the run blocks as
  `context_budget_too_small` rather than weakening those instructions.
- Production history remains bounded at the submission boundary: Session assembly
  freezes `RunContextSnapshot.controlled_history` under its message ceiling and token
  budget. Coding Runtime persists that already-controlled list without a second 12-item
  slice, then history share controls the native model projection. A legacy checkpoint or
  unavailable model budget uses the static message/character fallbacks.

## Verification

Run from `/private/tmp/aap-unified-wave2` using the root checkout interpreter:

- Characterization before implementation: existing context/layered/checkpoint suite
  passed with `53 passed, 12 subtests passed`; the newly added unified-share tests then
  failed at collection because `ContextShares` did not exist.
- Focused final regression suite covering unified shares, configuration migration,
  Layered fold/overflow behavior, checkpoint cloning and legacy restore:
  `88 passed, 12 subtests passed`.
- Full suite: `607 passed, 68 subtests passed in 46.60s`.
- `.venv/bin/python -m compileall ai_agent_platform tests evals`: PASS.
- `git diff --check`: PASS.
- `README.md`, `README.en.md` and `.env.example` are synchronized. The gitignored
  `INTERVIEW_NOTES/` tree is absent from worktrees; its affected Parts, facts and
  validator remain a post-merge root-checkout action, so the combined documentation
  acceptance item intentionally remains unchecked.

## Result

The Agent path now has one model-aware input-budget authority. `setup_workspace`
resolves the serving model allowance once, measures system prompt and visible tool
schemas, divides the remainder into evidence/history/transcript shares, and persists the
named values in LangGraph state and trace. Later layers only read those values; the
independent `agent_native_context_token_ratio` and Runtime's duplicate 12-message history
gate are removed.

Seed assembly is field-aware and JSON-safe. Evidence drops low-ranked sources before
trimming one source's `text`; history uses its token share and may retain more than the
legacy fixed message/character caps; a zero share removes optional evidence/history.
RAG and transcript character limits are active only when no model shares exist. The
current request, initial seed, every user steering message and checkpoint direction stay
verbatim. Provider overflow may rebuild optional seed fields at half shares, then still
receives exactly the existing one forced reduction and one retry.

The Layered ladder remains pure and keeps assistant/multi-tool/result groups atomic.
During this task a Wave-1 regression was found and fixed: folding used to summarize old
user messages before drop/truncate protections ran. Folding now treats user groups as
in-place chronological barriers and summarizes only contiguous non-user segments. This
fix is a merge blocker for the Unified branch.

Small windows stop before a Provider call as `blocked/context_budget_too_small`, with
separate system/tool-schema evidence in the message. Legacy checkpoints normalize a
missing `context_shares` channel to `{}` and keep static fallback behavior; new rollback
and fork branches preserve the resolved shares and the existing replayed compaction-stage
semantics.

No manual or metadata-only `/compact` path was added. Artifact persistence/readback is
also intentionally absent: the later Artifact wave should externalize eviction candidates
at the stateful `_plan_tools` boundary before invoking `_reduce_native_messages`, while
`_reduce_native_messages` and `_evict_old_tool_results` remain side-effect-free budget
primitives.
