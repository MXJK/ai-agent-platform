# ARTIFACT-READBACK: Run-local recovery for compacted tool results

## Goal

Make tool-result bodies recoverable after native transcript reduction without
creating a cross-Run lookup oracle or eagerly externalizing every small result.
This is the Artifact phase after layered transcript compaction and the unified
context budget; structured compaction snapshots remain a later task.

## Scope and boundaries

- Add the read-only, idempotent `run.read_artifact` tool with strict arguments:
  required `artifact_id`, optional `view=page|head_tail`, `offset_chars=0`, and
  `max_tokens=800` constrained to `64..2000`.
- Resolve reads only against runtime-created, model-readable Artifacts in the
  current LangGraph checkpoint state. The model cannot supply Run, conversation,
  Workspace, or actor identity, and the reader performs no global hash or Run
  Store lookup.
- Treat the canonical JSON of the complete `ToolResult` entering the Agent
  Harness as the recoverable original. Provider/ToolRegistry truncation before
  that boundary is out of scope.
- Keep small results inline until eviction, fold, drop/truncate, or forced
  overflow recovery will actually transform them. Compute their content hash at
  the stateful `plan_tools` boundary before invoking the pure reducer, then
  persist Artifact additions and reduced messages in one state update.
- Keep readback pages ephemeral so they can never create nested Artifacts.
- Preserve checkpoint selection semantics for pause/resume, rollback, fork, and
  replay. Legacy v1/v2 `RunContextSnapshot` values do not gain the new tool.
- Do not add a database migration or a metadata-only `/compact` command.

## Security and observability

- Artifact IDs use `tool_result_` plus the first 20 lowercase hex characters of
  the full SHA-256; the full digest, canonical character count, estimated token
  count, top-level call/tool identity, and runtime/model-readable flags are
  verified before every read.
- Missing, cross-Run, wrong-type, forged, or corrupt Artifacts fail closed as
  `artifact_not_found`; an out-of-range offset returns
  `artifact_offset_out_of_range`.
- MCP output containing an `artifact_id` field does not create a readable
  Artifact.
- Trace/SSE/log/metric projections contain only ID, call/tool, view/range,
  character count, estimated tokens, hash, and error code. They do not contain
  Artifact body text.

## Acceptance criteria

- [x] Small built-in and MCP results receive an Artifact only when reduction
      actually evicts or otherwise transforms their bodies.
- [x] Page reads concatenate back to the exact canonical JSON; head/tail ranges
      and Unicode boundaries are deterministic.
- [x] Read responses obey both the model-requested maximum and the Harness
      per-result maximum, including their model-visible ToolResult envelope.
- [x] Integrity corruption, cross-Run reads, MCP-forged IDs, and legacy capability
      inheritance fail closed.
- [x] Readback is ephemeral, replay is idempotent, and duplicate/nested Artifacts
      are not produced.
- [x] Pause/resume and selected before/after rollback/fork checkpoints inherit
      exactly their Artifact state.
- [x] Existing AgentRun result serialization round-trips Unicode Artifacts through
      InMemory, SQLite, and the PostgreSQL JSON adapter without a migration.
- [x] Focused checkpoint, native-tool, compaction, local-store, and PostgreSQL
      repository suites pass.
- [x] Independent read-only security/checkpoint review has no unresolved blocker.
- [x] Final full pytest and compileall pass on the branch head prepared for review.

## Decisions

- Keep the pure transcript reducer independent of LangGraph state. The runtime
  resolves complete ToolResults by call identity immediately before reduction,
  builds deterministic content-addressed Artifacts only for bodies the reducer
  actually transforms, and returns the reduced messages and Artifact additions
  in the same state update.
- Treat duplicate `call_id` values with different or ambiguous results as unsafe
  for lazy association. Ambiguous identities do not receive a guessed Artifact
  ID, preventing a marker from pointing at another result body or at an
  unpersisted Artifact.
- Read only from the selected checkpoint's `artifacts` field. No global store,
  hash index, caller-supplied Run identity, or cross-Run existence probe is
  introduced.
- Define the recoverable boundary as the complete `ToolResult.to_response()`
  entering the Agent Harness. Provider or ToolRegistry truncation before that
  boundary remains outside this feature's promise.
- Budget the complete model-visible read envelope, not only the page body.
  Readback messages are ephemeral and excluded from subsequent externalization.
- Require both the persisted capability flag and the currently visible runtime
  tool before execution. Legal schema-v1 and schema-v2 Run context snapshots do
  not inherit `run.read_artifact`, including restored pending calls.
- Reuse existing AgentRun/checkpoint JSON persistence; no database migration is
  required. No metadata-only `/compact` behavior is added.
- Defer root-checkout `INTERVIEW_NOTES` synchronization until after an approved
  merge because those gitignored notes must describe the local `main` truth, not
  an unmerged worktree branch.

## Verification

- Independent checkpoint/security review matrix:
  `119 passed, 20 subtests passed`; no unresolved blocker.
- Independent test-design/behavior review matrix:
  `124 passed, 20 subtests passed`; no unresolved blocker.
- Coordinator full suite on the reviewed implementation:
  `.venv/bin/python -m pytest -q` ->
  `640 passed, 87 subtests passed`.
- Independent reviewer full suite on the same implementation:
  `.venv/bin/python -m pytest -q` ->
  `640 passed, 87 subtests passed`.
- Required bytecode verification:
  `.venv/bin/python -m compileall ai_agent_platform tests evals` -> passed.
- Patch hygiene: `git diff --check` -> passed.
- Focused verification additionally exercised real repository-tool execution,
  the real MCP provider adapter with a fake client, native model-loop pagination,
  forced Provider-overflow recovery, two executed LangGraph checkpoint forks,
  legal legacy Run context restoration, InMemory/SQLite persistence, and the
  PostgreSQL repository JSONB save/get path through `FakeConnection`.

## Result

Implementation, independent review, and required verification are complete with
no unresolved blocker. The Artifact branch is not merged and is waiting for the
user's explicit merge confirmation. The structured-compaction-snapshot second
wave has not started.

Non-blocking residual risks are confined to environment integration rather than
the validated feature contract:

- MCP behavior is covered through the real `MCPToolProvider` adapter with a fake
  client, not every live stdio/HTTP transport or remote server implementation.
- Provider overflow is covered through the runtime's real recovery path with a
  scripted `context_overflow`, not a live hosted model/provider response.
- PostgreSQL JSONB save/get behavior is covered through the real repository and
  `FakeConnection`; a live PostgreSQL service and production network failures
  were not exercised on this branch.
- SSE body isolation is covered through stored InMemory events and the real
  `AgentEventEncoder`; the HTTP streaming route, proxies, and external log/metric
  exporters were not exercised end to end.

Root-checkout, gitignored `INTERVIEW_NOTES` synchronization remains deferred
until an approved merge so it records the local `main` architecture accurately.
No production, test, README, workflow-state, merge, or second-wave change is part
of this closeout documentation commit.
