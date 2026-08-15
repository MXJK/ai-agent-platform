# LOCAL-DIRECT-WRITES

## Goal

Make the repository's local development entry point usable with authenticated change-set review and explicit application to a real workspace, without exposing unauthenticated write APIs.

## Acceptance criteria

- The local gateway supplies a trusted development identity only on a loopback listener and strips caller-supplied identity headers.
- FastAPI runs in `trusted_header` mode with live workspace writes enabled and change sets created in `direct` apply mode.
- The standard start script launches the gateway, API, and worker with one browser-facing URL.
- The chat workbench can create a code change, show the changed files/diff, and apply it to a selected real workspace after the user clicks the apply control.
- Automated tests, compilation checks, gateway tests, and a real-browser smoke test pass.
- README and interview handbook claims match the enabled local workflow and its security boundary.

## Permitted scope

- Local runtime configuration and examples.
- Gateway authentication/configuration and tests.
- Startup scripts and the minimum API/UI fixes discovered by end-to-end verification.
- User-facing and interview documentation required by the repository contract.

## Decisions

- Keep FastAPI's existing `trusted_header` boundary. Add a passwordless `local`
  mode to the Go gateway instead of allowing anonymous direct writes in the API.
- The standard Compose publish rule remains `127.0.0.1:8080:8080`; local mode
  strips caller identity and Authorization headers, then injects the configured
  local user and shared gateway trust secret.
- Keep ChangeSet permissions immutable. A `patch_only` ChangeSet created before
  the configuration change remains non-applicable; a fresh Run must create a
  `direct` ChangeSet.
- The untracked `.env` uses `trusted_header`, live workspace writes, `direct`, a
  `demo_user` local gateway identity, and a generated random trust secret. No
  credential value is stored in this task or tracked documentation.

## Verification

- `.venv/bin/python -m pytest -q`: `386 passed, 49 subtests passed`.
- `.venv/bin/python -m compileall ai_agent_platform tests evals`: passed.
- Dockerized `go test ./gateway/...`: passed.
- `node --check ai_agent_platform/static/app.js`: passed.
- `bash -n scripts/start.sh`: passed.
- `./scripts/start.sh --check`: passed with the enabled local direct-write profile.
- `docker compose --profile gateway config --quiet`: passed.
- `.venv/bin/python INTERVIEW_NOTES/validate.py`: validated 12 Markdown files and
  37 capabilities; existing dirty-worktree evidence review warnings were reported.
- `git diff --check`: passed.
- HTTP boundary smoke test: direct FastAPI workspace request returned 401;
  gateway readiness and authenticated workspace requests returned 200.
- Real in-app browser smoke test at `http://127.0.0.1:8080`: submitted a one-file
  Agent task, approved its Sandbox write, observed `direct · changes_ready`, the
  changed-file ledger and Apply UI, accepted the final confirmation, and observed
  `已应用到真实工作区`.
- Filesystem verification: `/Users/mxjk/programming/vs code project/test-workspace/local-write-smoke-test.txt`
  exists with the single line `LOCAL_DIRECT_WRITE_OK`.

## Result

Complete. The local runtime now has a usable trusted identity chain and explicit
real-workspace promotion flow through the conversation workbench. The standard
startup path builds/starts the gateway and prints the browser-facing port; startup
validation rejects mismatched gateway/FastAPI auth modes. README, README.en, the
interview handbook, and the facts index describe the local security boundary and
the immutable `patch_only` behavior. No schema migration was added or executed,
and no commit was created.
