# PARALLEL-READ-TOOL-CALLS: Batch safe read-only tool calls

## Goal

Reduce avoidable Agent tool rounds by accepting and executing independent,
idempotent read-only calls as one bounded parallel batch while preserving the
existing single-call boundary for mutations, validations, approvals, and user
input.

## Acceptance criteria

- [x] The production native planner may return multiple independent read-only
      calls in one turn.
- [x] A read batch is bounded by `AGENT_MAX_READ_TOOLS_PER_ROUND` and the
      remaining hard tool-call budget.
- [x] Only tools declared `read_only`, idempotent, and approval-free may execute
      in parallel.
- [x] Batch execution preserves the model-proposed result order.
- [x] Mutations, validations, approval-requiring tools, user-input requests, and
      mixed read/write proposals remain serialized at one accepted call per turn.
- [x] Duplicate call IDs or unsafe batches cannot race through the executor.
- [x] Provider requests allow parallel native calls where their API exposes the
      switch; finalization keeps tool calling disabled.
- [x] Trace output distinguishes parallel read batches and suppressed calls.
- [x] Focused and full repository verification pass.

## Non-goals

- Do not parallelize workspace mutations, validation commands, approval flows,
  or interactive user-input requests.
- Do not add a new bulk-read tool or change repository-tool response schemas.
- Do not increase hard tool-round or tool-call limits.
- Do not deploy, merge, or invoke a paid model.

## Documentation impact

This changes Agent execution behavior and provider request configuration. Update
the bilingual README and the interview handbook's native tool-calling contract.

## Decisions

- Keep `single_tool_per_turn` as the mutation and control-flow boundary; add a
  separate production-planner capability for safe parallel reads.
- Accept only a consecutive read prefix whose ToolSpecs are `read_only`,
  idempotent, approval-free, and effectively allowed. Do not reorder a mixed
  proposal to manufacture a parallel batch.
- Recheck executor safety and unique call IDs before creating worker threads,
  then replay results in model-proposed order.
- Reuse `AGENT_MAX_READ_TOOLS_PER_ROUND` and the existing hard call budget
  instead of adding a new concurrency setting.

## Verification

- `tests/test_native_tool_calling.py`: 31 passed.
- Agent runtime, loop characterization, workspace, model registry, tool
  execution, and permission regression set: 73 passed, 12 subtests passed.
- `.venv/bin/python -m pytest -q`: 571 passed, 60 subtests passed.
- `.venv/bin/python -m compileall ai_agent_platform tests evals`: passed.
- `.venv/bin/python INTERVIEW_NOTES/validate.py`: validated 24 Markdown files
  and 43 capabilities; existing evidence-review warnings remain informational.
- `git diff --check`: passed.

## Result

Implemented bounded parallel execution for safe read-only native tool batches.
OpenAI and Anthropic provider requests now permit parallel tool proposals, while
the harness preserves single-call execution for mutations, validations,
approval flows, user input, and mixed plans. Added concurrency, ordering, batch
limit, mutation-boundary, approval-boundary, duplicate-call-ID, provider payload,
trace, and suppression coverage. Updated the bilingual README and local interview
handbook contract; no migration, deployment, external write, or paid-model call
was performed.
