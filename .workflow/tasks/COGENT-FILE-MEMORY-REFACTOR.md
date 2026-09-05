# COGENT-FILE-MEMORY-REFACTOR

## Goal

Replace the platform, project, and Cogent memory paths with one mewcode-style
file memory system while retaining database-backed sessions and Runs.

## Scope and constraints

- Port layered Cogent instruction files, recursive `@` includes, two-level
  Markdown memory, incremental extraction, recall, and governed consolidation.
- Keep session messages and Run state in their configured SQLite/PostgreSQL
  stores. Store only session attachments below `.cogent/sessions/`.
- Preserve independent RAG and extract workspace authorization from the retired
  project-memory service.
- Start the new file memory empty. Preserve legacy tables without importing or
  mutating their rows; return HTTP 410 from retired memory endpoints.
- Preserve unrelated working-tree changes. Do not migrate a live database,
  deploy, commit, or push.

## Acceptance criteria

- [ ] Layered COGENT/AGENTS instructions and bounded `@` includes are captured
      with provenance and fail closed at filesystem and workspace boundaries.
- [ ] User/project Markdown memories support safe CRUD, index limits, recall,
      incremental extraction, and recoverable consolidation.
- [ ] Session continuation restores every safe Cogent terminal state and stores
      new tool-result attachments by session and Run without JSONL.
- [ ] Workspace authorization no longer depends on ProjectMemoryService; legacy
      memory APIs return 410 and old background pipelines are not scheduled.
- [ ] Web and CLI expose the new file memory and session controls.
- [ ] Focused, full, frontend, documentation, and browser checks pass.

## Decisions

- Implementation in progress.

## Verification

- Pending.

## Result

Pending.
