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
- [ ] Independent read-only security/checkpoint review has no unresolved blocker.
- [ ] Final full pytest and compileall pass on the branch head prepared for review.

## Verification so far

- Primitive/API compatibility: `5 passed`.
- Reader integration plus native and layered regression suites:
  `56 passed, 4 subtests passed`.
- Lazy eviction/readback/forced-recovery/security matrix plus native/layered suites:
  `59 passed, 6 subtests passed`.
- Checkpoint, execution-context, local-memory, and PostgreSQL repository matrix:
  `76 passed, 14 subtests passed`.
- The first full run reached `632 passed, 85 subtests passed` with one legacy
  characterization mismatch caused by emitting `artifact_id=-` when no Artifact
  existed. The compatibility output has been corrected; final full verification
  remains pending at this review checkpoint.

## Result

Implementation is complete and awaiting independent review plus final full
verification. The branch is not merged. `.workflow/state.yaml` intentionally
remains in `review`; `last_verified_commit` still identifies the previously
closed main task and is not advanced until this task's final verified commit is
known. Root-checkout, gitignored `INTERVIEW_NOTES` synchronization is deferred to
the coordinator after explicit merge approval and is not modified from this
worktree.
