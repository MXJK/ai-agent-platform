# TOOL-RESULT-BUDGET: Cap tool results at the harness boundary

## Goal

Stop a single oversized tool result from entering the native transcript unbounded.
Apply one harness-level budget to every tool result — built-in and MCP alike —
and externalize the original so the model can still reach it.

This is step 1 of 4 in the context/compaction rework. It has no dependency on the
other three and delivers the largest behavior improvement on its own.

## Audit findings this task closes

- `_native_tool_result_message` (`ai_agent_platform/agents/coding/tool_loop_nodes.py:789`)
  embeds the entire result dict into the message with no size check.
- Per-tool caps exist but are inconsistent in unit and value:
  `tools/repository.py:136` caps by line range, `integrations/execution_workspace.py:531`
  by `max_chars`, `integrations/sandbox.py:745` by remaining capacity.
- MCP tool results have no cap at all. `integrations/mcp/provider.py` and
  `integrations/mcp/client.py` contain no truncation or size limit, so a third-party
  server can return an arbitrarily large payload straight into the context.
- This is the only path where context growth is unbounded; the compaction problems
  in LAYERED-TRANSCRIPT-COMPACTION are mostly triggered by it.

## In scope

- A size cap applied between the `EffectiveToolPool.execute` result and
  `_native_tool_result_message`, so built-in and MCP tools share one rule.
- New setting `AGENT_TOOL_RESULT_MAX_TOKENS` (proposed default 2000) in
  `core/config.py`, resolved through the existing layered config surface.
- Placeholder result shape carrying `truncated`, `truncated_from_tokens` and
  `artifact_id`, with head and tail of the original preserved.
- Externalizing the full result through the existing run artifact path
  (`agents/coding/run_recorder.py`) so the original stays retrievable.
- Metric `agent_tool_results_truncated_total`.

## Out of scope

- Removing or lowering the existing per-tool caps. They stay as a second line of
  defense; a later task may lower them once the harness cap is proven.
- Any change to `_compact_native_messages` (see LAYERED-TRANSCRIPT-COMPACTION).
- Any change to the chat layer or to budget allocation (see UNIFIED-CONTEXT-BUDGET).

## Acceptance criteria

- [ ] A tool result over the budget enters the transcript as a head/tail placeholder
      carrying `artifact_id`; the full original is written to run artifacts and can
      be read back.
- [ ] An oversized MCP tool result is capped by the same code path as a built-in one.
- [ ] The placeholder serializes correctly for every provider adapter's tool-result
      shape (OpenAI, Anthropic, DeepSeek, Google) in `integrations/llm.py`.
- [ ] `call_id` pairing is preserved: capping never drops or renames a tool message.
- [ ] The cap is unconditional — it does not depend on current context occupancy.
- [ ] New test: an oversized tool result does not grow the transcript beyond the
      configured budget, asserted for both a built-in and an MCP-backed tool.
- [ ] `.venv/bin/python -m pytest -q` passes.
- [ ] `.venv/bin/python -m compileall ai_agent_platform tests evals` passes.
- [ ] Documentation impact assessed: this changes what the model observes and adds a
      configuration key, so `README.md`, the affected `INTERVIEW_NOTES/*.md` Parts and
      `INTERVIEW_NOTES/facts.json` are updated and
      `.venv/bin/python INTERVIEW_NOTES/validate.py` passes.

## Decisions

- The cap belongs at the harness boundary, not inside each tool. Tools cannot be
  trusted to bound their own output, and MCP tools are not ours to change.
- Head and tail are both preserved rather than head only: the tail usually carries
  the error line, exit status or summary that makes the result actionable.
- The original is externalized rather than discarded, and the placeholder names the
  artifact, so the model knows the data still exists instead of concluding the tool
  returned little.
- Existing per-tool caps stay in place. The harness cap is an upper bound over them,
  not a replacement, so this task cannot regress a tool that already behaves.

## Verification

## Result
