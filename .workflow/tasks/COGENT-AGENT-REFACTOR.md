# COGENT-AGENT-REFACTOR: replace the Coding Agent runtime with Cogent

## Goal

Replace the LangGraph Coding Agent with a durable Cogent loop adapted from the
provided local reference implementation while retaining the platform services,
model registry, authorization, workspaces, ChangeSets, MCP control plane, and
standalone RAG system.

## In scope

- Add `ai_agent_platform/cogent` for provider streaming, canonical conversation
  serialization, the Agent loop, prompts, permissions, tools, MCP exposure,
  slash commands, Skills, file memory, file history, and Textual CLI rendering.
- Keep `QueryService`, `AgentSDK`, Run/Event persistence, model selection,
  usage accounting, workspace execution, and ChangeSets as the application
  boundary around the new runtime.
- Persist versioned Cogent state and checkpoints in SQLite and PostgreSQL,
  including crash-safe approval and tool-call replay.
- Route the web application and CLI through the same QueryService; remove the
  fast-chat route and UI mode.
- Fully disconnect Agent context from RAG, ProjectMemory, and UserMemory while
  preserving their standalone APIs, storage, UI, and evaluation flows.
- Rebrand the distribution, scripts, user-facing UI, and new Agent data paths to
  Cogent while retaining the `ai_agent_platform` import namespace and existing
  platform data/API compatibility.
- Synchronize README and Interview Notes with the new runtime boundary.

## Out of scope

- Teams, user-visible subagents, Tasks, Hooks, reference worktree commands,
  Trace UI, or the reference remote/WebSocket server.
- RAG reindexing or migration of existing RAG/ProjectMemory data into Cogent
  file memory.
- Merge, release, deployment, applying database migrations to a live system,
  commit, or push.

## Acceptance criteria

- [x] Production Agent execution uses Cogent and has no LangGraph import or
  checkpointer dependency; legacy Run history remains readable but not resumable.
- [x] Provider streaming preserves provider-specific protocols, tool pairing,
  displayable thinking, fallback, prompt-cache fields, and raw token totals.
- [x] Permission decisions compose the existing hard authorization boundary
  with Cogent modes/rules/sandbox and are completed before any side effect.
- [x] Read tools may run concurrently while writes and commands remain ordered,
  approval-bound, durable, and idempotent across restart.
- [x] MCP, slash commands, inline Skills, file memory, automatic constrained
  consolidation, plan/review/rewind, and OS sandbox behavior are covered by tests.
- [x] Web and Textual CLI consume the same QueryService events; `/chat/stream`
  and the fast-chat UI are removed.
- [x] Cogent Agent has no RAG/KB/project-memory context dependency and standalone
  RAG APIs/evaluations continue to pass.
- [x] Package/UI/CLI/new data directories use Cogent and shipped code contains
  no reference-project branding or copied comments.
- [x] README, README.en, Interview Notes, and facts reflect the implemented
  architecture and all required verification commands pass.

## Decisions

- New Runs use `cogent-v1`; existing Runs are treated as `langgraph-v1` history.
- The internal import namespace, database identity, `/api/v1` routes, current
  model management, and existing `~/.ai-agent-platform` state remain compatible.
- New Cogent-owned project/user artifacts use `.cogent` and `~/.cogent`.
- Only provider-returned, user-displayable reasoning is emitted; protocol-private
  reasoning/signatures are neither displayed nor persisted as visible thinking.
- Inline Skills are supported; fork/subagent Skills fail with an explicit
  unsupported diagnostic.
- Automatic memory consolidation uses a restricted maintenance Run with no
  Bash, MCP, or ordinary workspace write capability.
- Reference executable logic may be adapted under the user's stated authority;
  source comments/docstrings and branding are not copied.

## Verification

Final verification on 2026-09-04, current uncommitted working tree:

- `.venv/bin/python -m pytest -q`: **819 passed, 111 subtests passed**.
  The run included opt-in real PostgreSQL and OS-sandbox tests through
  `COGENT_TEST_POSTGRES_URL` and `COGENT_TEST_OS_SANDBOX`; the database value
  was an isolated temporary PostgreSQL 16 instance, not the user's database.
  Log: `/tmp/cogent-verification-passed.txt`.
- `.venv/bin/python -m compileall ai_agent_platform tests evals`: passed.
- `node --check ai_agent_platform/static/app.js`: passed.
- `node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs`:
  **22 passed**.
- `.venv/bin/python INTERVIEW_NOTES/validate.py`: passed, 24 Markdown files and
  45 capabilities. Evidence-review warnings correctly remain relative to the
  old verified commit; no new commit was claimed.
- `docker compose -f docker-compose.yml --env-file .env.example config --quiet`,
  `bash -n scripts/start.sh scripts/start-local.sh`, `git diff --check`: passed.
- Editable install via `pip install --no-deps --no-build-isolation -e .` succeeded;
  `cogent --help` and `cogent-api --help` succeeded. Explicit package discovery
  fixes the previous multi-top-level-package installation error.
- Final Docker image `cogent-refactor-qa` built successfully. The image installs
  both CLI scripts and Textual dependencies. `cogent --workspace /workspaces
  --print ...` completed a fake-provider Run through QueryService inside the
  isolated container, emitting answer, usage, route, turn and Run events.
- A new temporary PostgreSQL 16 database successfully applied the complete
  migration chain through `20260903_0028`. Real-database tests covered atomic
  rollback, model/tool boundary restart, approval/input resume, compaction,
  duplicate ownership, completed-write replay and lease release after process kill.
  SQLite exercised the same durable resume and process-ownership behaviors.
- Actual macOS Seatbelt execution allowed an in-scope file write and rejected
  an out-of-scope write, denied-file read and network socket bind. Linux
  bubblewrap argument construction is covered; the official App image reports
  its absence and retains explicit command approval plus platform constraints.
  This is not a claim of malicious-repository isolation in the trusted App image.
- Real browser verification on the isolated Docker service: registered the empty
  `/workspaces` folder through the page's directory browser, submitted a normal
  composer message, observed `run_3de876a4f15d` complete, received Run SSE and
  usage/route events, and reloaded the same persisted session/answer. The old
  Chat/Agent mode selector was absent and Cogent permission controls were present.
  Fake was used, with no paid Provider request or real user-workspace write.
  ego-browser's screenshot endpoint timed out; the alternate in-app browser
  successfully captured and visually verified the page, including final Cogent
  branding. Browser timing/trace findings were fixed and included in the final
  automated run; protocol fixtures cover non-fake Providers.
- Handbook Parts 00–09 and associated current diagrams were synchronized. Draw.io
  sources and exported PNGs now agree; the runtime diagram was visually inspected.
  Retired graph/planner/completion-contract paths were removed from current
  narratives; general interview discussion of graph frameworks is labeled separately.
- Production scan: no LangGraph import/checkpointer dependency or setting remains.
  Historical `langgraph-v1` identifiers are intentionally retained.
- Final read-only check of the existing installation: PostgreSQL revision was
  already `20260903_0028`; the existing port-8000 Run-list endpoint returned 200.
  This task explicitly migrated only its temporary test database, and did not
  restart the user's existing App service. No commit, push or release was made.

The earlier baseline had eight failing legacy evaluation cases. Those cases were
migrated to current tool/event semantics, not suppressed: pending proposals are
separate from executed calls, actual reads supply evidence, actual compaction
supplies budget metrics, and standalone retrieval datasets and gates are preserved.

## Result

**Complete for the agreed local-development refactor scope.**

- Production runs use the canonical asynchronous Provider bridge and durable
  Cogent model/tool loop. Native messages, call/result pairing, public reasoning,
  fallback/cache fields and Provider-returned token totals are retained.
- Tools are preflighted before execution. EditFile rejects protected inputs
  before reading them. Atomic Run/Event/snapshot boundaries, persistent Worker
  leases, safe replay and startup recovery cover queued/running and suspended work.
  Legacy history is projected read-only without mutating the stored record.
- Approval/input resumes continue from committed authorization and answers.
  Final assistant-message projection is idempotent and repaired before authorized
  terminal reads, eliminating the transient missing-history race.
- MCP eager/native/dispatch selection and loaded-tool state are integrated.
  Native deferred search is restricted to supported official Anthropic endpoints;
  unsupported choices use dispatch. Existing MCP authorization and transport
  ownership remain in the platform.
- File memory has per-user roots, durable validated responses, maintenance leases,
  whole-plan preflight, write recovery, entry limits and stale/duplicate index-link
  pruning. Unreferenced topic files are retained. Pure conversation rewind now
  accepts a completed Run without requiring file-change history; file rewind
  retains approval, conflict checks and immutable history.
- Agent-only offline fixtures are separate from small platform RAG regression
  fixtures. The existing independent RAG pilot and answer labels were preserved.
- Web and CLI share QueryService. Removed fast-chat behavior remains removed.
  Packaging, installed scripts, Docker startup, persistent Cogent user-state volume,
  UI branding and actual elapsed/activity/retry statistics are complete.
- README, README.en, the handbook, diagrams and facts describe current behavior
  and startup. Handbook files remain ignored by existing Git configuration;
  their local edits were verified without altering ignore rules.

Operational handoff:

1. From the repository, use the explicit base Compose file to build `app migrate`,
   run the one-shot `migrate`, then start `app`; commands are in README.
2. Open the local Web UI, select a registered model and workspace, and send a task.
   New persistent installations require a Provider connection and an enabled,
   tool-capable model; Fake bootstrap belongs to memory test setups only.
3. Default permission mode asks before writes/commands. The official direct mode
   changes the selected source root, with ChangeSet audit and conflict-safe revert.

Remaining external boundaries are unchanged: no release/deployment or live-database
migration is authorized by this task; reference license verification is required
before external distribution. Real Provider answer quality and production capacity
are not inferred from deterministic tests. Teams, user-visible subagents, Tasks,
Hooks, /worktree and reference remote services remain explicitly out of scope.
