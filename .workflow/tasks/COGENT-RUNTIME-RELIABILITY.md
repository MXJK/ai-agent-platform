# COGENT-RUNTIME-RELIABILITY

## Goal

Repair the post-refactor Agent execution defects confirmed by the latest
DeepSeek Runs and audit adjacent model/tool/resume boundaries so normal Web
and CLI Agent conversations can finish reliably.

## Scope and constraints

- Fix tool-call/result ordering across approval, pause, steering, user input,
  persisted recovery, and later conversation turns.
- Validate outbound tool pairing before contacting a Provider. Preserve raw
  Provider reasoning, model registry selection, exact approvals, tool result
  evidence, and durable execution deduplication.
- Correct misleading command/tool capabilities if confirmed by live evidence;
  preserve the configured command allowlist and authorization boundaries.
- Add meaningful regression coverage, protocol transport coverage, isolated
  persistent/API/SSE and real browser acceptance, and synchronize affected docs.
- Do not alter independent RAG/model management, resume historical failed Runs,
  mutate the user's test-workspace, commit, push, migrate the user database, or redeploy services.
- The user confirmed the separately described real DeepSeek API smoke test with
  '继续'; each model was limited to five requests and synthetic temporary files.

## Acceptance criteria

- [x] Approval feedback and mid-batch control messages cannot separate calls/results.
- [x] Recovery preserves user feedback and completed results without executing tools twice.
- [x] Incomplete/duplicate/orphan results are rejected locally before Provider requests.
- [x] Web approval with the actual default feedback completes through a strict protocol transport.
- [x] Adjacent input/pause/steering/multiple-turn cases and configured tool restrictions pass.
- [x] Full pytest, compileall, frontend tests, handbook validation, diff review and browser QA pass.

## Decisions

- The four latest failed Runs have both result IDs but place approval feedback
  between assistant calls and results. The latest is run_ec33ddf44da7.
- Both enabled DeepSeek models advertise tool calling; the latest Run's first
  deepseek-v4-flash response succeeded. Registration is not the failure cause.
- Earlier refactor acceptance used fake Provider browser traffic; the new
  regression must enforce actual chat-completion pairing at the HTTP boundary.

## Verification

- Full suite with an isolated PostgreSQL 16 database and real macOS OS-sandbox
  tests enabled: **833 passed, 111 subtests passed**, 46.72 seconds.
  Log: `/tmp/cogent-reliability-full.log`. Only the disposable test database was
  migrated through `20260903_0028`; the user's database was read-only.
- Baseline before edits: 806 passed, 12 skipped, 111 subtests, one unrelated
  local-memory profile timing failure. That exact case passed alone, and the
  final full suite passed without any changes to independent memory code.
- Added 14 tests covering approval feedback, pause, steering, SQLite restart,
  input siblings, later turns, old interleaved snapshots, invalid pairing,
  duplicate call IDs and the real DeepSeek HTTP adapter/API/SSE path. Existing
  SQLite/PostgreSQL resume tests now include the page's actual approval feedback.
- `.venv/bin/python -m compileall -q ai_agent_platform tests evals`: passed.
- `node --check ai_agent_platform/static/app.js`: passed.
- Node UI tests: **22 passed**; `/tmp/cogent-reliability-node.log`.
- `.venv/bin/python INTERVIEW_NOTES/validate.py`: passed, 24 Markdown files and
  45 capabilities. Old verified-commit evidence warnings remain correctly
  scoped; no new verified commit is claimed.
- `git diff --check`: passed. Initial worktree was clean; no commit/push.
- Browser acceptance used a real page at local port 18080, temporary SQLite and
  the actual DeepSeek adapter with strict local HTTP protocol responses. Added
  the temporary workspace via the folder picker, entered the project check,
  clicked '确认并继续' (including its default feedback), observed 3 model requests
  and completed Bash/Glob/ReadFile, then sent a follow-up (4 total requests).
  Zero protocol rejections; role sequence after approval was
  `system,user,assistant,tool,tool,user`. Reload in another browser retained
  both answers and completed state. ego-browser screenshot timed out; the
  in-app browser screenshot was successfully captured and visually inspected.
- After explicit user confirmation, **real DeepSeek API calls** were made using
  existing registered credentials in-process, with synthetic temporary files,
  an in-memory Run Store and a tool wrapper allowing only reads and the exact
  command `python3 --version`. Both models completed with 3 requests and 1
  approval each; Bash, Glob and ReadFile all succeeded:
  - deepseek-v4-flash: `run_346bef6ffc8d`.
  - deepseek-v4-pro: `run_3e9b3e3ea994`.
  Both correctly reported Python 3.11.15 and Project status demo-ready.
  `/tmp/cogent-real-provider-smoke.log` contains safe summaries. No user Run was
  resumed or user-workspace file changed. These were live protocol smoke tests,
  not a model quality or production-capacity evaluation.
- Existing local app remained healthy. WatchFiles logs confirm it loaded the
  changed files and restarted its worker automatically; no deployment command
  or explicit restart was performed.

## Result

Complete for the confirmed runtime reliability scope.

Approval, pause and steering text is durably deferred until every recorded tool
result is present. Outbound transcripts reject missing, orphan, duplicate or
invalid call/result IDs before Provider traffic. Recovery may reorder already
recorded results around user feedback without fabricating evidence or replaying
side effects. Duplicate calls are rejected before execution. Bash now exposes
the actual configured allowlist and single-program execution semantics.

Documentation impact: README (Chinese/English), Interview Notes index, Parts 04
and 05 and facts.json were synchronized. Interview handbook files remain ignored
by the repository's existing rules; no ignore configuration was changed.

The local installation already uses the patched mounted code. Start a new task
or continue a normally suspended task through its controls; historical failed
Runs were preserved for audit. No blanket guarantee is made for every possible
model/provider task.
