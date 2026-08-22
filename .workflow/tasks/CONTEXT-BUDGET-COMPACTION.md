# CONTEXT-BUDGET-COMPACTION

## Goal

Close the seven context-window gaps found in the context/compaction audit: make
conversation context token-budgeted and model-adaptive, align rolling summaries by
message identity, give the native tool loop semantic compaction, stabilize the
prompt prefix for provider caching, and make compaction observable to the user.

## Acceptance criteria

- Chat context assembly enforces a token budget derived from the routed model's
  context window, not only a message count.
- Overflow is recovered (synchronous compaction, then oldest-first trimming, then
  bounded per-message truncation) instead of failing with `context_window_too_small`.
- Rolling summaries align on `through_message_id` and survive message deletion.
- Context ceilings scale with the routed model's context window.
- Native tool-loop compaction uses the same LLM compressor as the chat layer with
  the existing rule-based fallback.
- The system prefix stays stable across turns so provider prefix caches can hit.
- Compaction and context pressure are visible over SSE and in the workbench.
- Focused tests, the full suite, compile checks, and documentation validation pass.

## Scope

- `ai_agent_platform/services/session_service.py`, `conversation_compression.py`
- `ai_agent_platform/integrations/llm.py` context-budget resolution
- `ai_agent_platform/agents/coding/tool_loop_nodes.py`, `coding_agent.py`, `runtime.py`
- `ai_agent_platform/api/routes/chat.py`, `ai_agent_platform/static/app.js`
- `ai_agent_platform/core/config.py`, `domain/models.py`, schemas
- README and interview handbook synchronization.

## Decisions

- The token budget is derived from the context window of the model the router
  would pick for this turn (`LLMClient.resolve_context_budget`), not from a
  static constant. Budget resolution never raises: a routing failure degrades to
  `LLM_MODEL_CONTEXT_WINDOW_TOKENS`, because a cost guardrail must not be the
  reason a turn cannot start.
- `LLM_MAX_CONTEXT_MESSAGES` becomes a floor and `LLM_MAX_CONTEXT_MESSAGES_CEILING`
  an upper bound reached only while the token budget holds. Setting them equal
  restores the previous fixed-length window.
- Overflow recovery is ordered by what it costs the conversation: synchronous
  compaction, then oldest-first dropping, then head/tail truncation of message
  bodies. Truncation reaches the summary before the live turn on purpose.
- Context reporting stays side-effect free: `get_context_token_usage` never
  triggers the synchronous compaction path, so a GET cannot spend a model call.
- Summary boundaries align on `through_message_id` first and fall back to
  `summarized_message_count` only when that id is gone, which keeps a deleted
  message from shifting the whole window.
- The native tool loop reuses the conversation compressor rather than growing a
  second summarization path; its rule-based fallback stays the failure mode.
- Prompt prefix order is stability-ordered (profile, summary, history, then
  per-query memories next to the live turn) so provider prefix caches can hit.

## Verification

- `.venv/bin/python -m pytest -q` — passed: 485 tests and 56 subtests.
- `.venv/bin/python -m compileall ai_agent_platform tests evals` — passed.
- `.venv/bin/python INTERVIEW_NOTES/validate.py` — passed: 24 Markdown files and
  40 capabilities; evidence-review warnings only.
- `node --check ai_agent_platform/static/app.js` — passed.
- `git diff --check` — passed.
- New focused suites: `tests/test_context_budget.py`, `tests/test_context_pipeline.py`.

## Result

Implemented all seven fixes: model-derived token budgets with ordered overflow
recovery, identity-aligned summary boundaries, window-adaptive ceilings for both
the chat window and the native transcript, semantic transcript compaction with a
rule-based fallback, a stability-ordered prompt prefix, an SSE `context` event
plus workbench display, and a sectioned rolling-summary prompt with a larger
budget.

Note: the `ContextBudget` / `resolve_context_budget` part of this task was swept
into commit `491d5edb` by a concurrent session; the rest of the change is still
uncommitted in the working tree.
