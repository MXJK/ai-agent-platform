# SHARED-BACKEND-TERMINAL-UI

## Goal

Make `uv run cogent` open the Cogent terminal UI while using the same server-side
configuration and persisted state as the web UI.

## Scope and constraints

- The public CLI always connects to the existing Cogent HTTP API; it does not
  assemble a second local Runtime.
- Provider credentials remain in the server Secret Store. The TUI may display
  configured/healthy status but must never retrieve plaintext credentials.
- Reuse the established mewcode terminal interaction structure while retaining
  Cogent naming, Query contracts, permission boundaries, and server runtime.
- Preserve the embedded Python SDK and in-process test adapters.
- Do not migrate databases, deploy, commit, or push.

## Acceptance criteria

- [x] `uv run cogent` connects to the default loopback API and opens the TUI.
- [x] CLI sessions, workspaces, models, credential status, Runs, commands, and
      SSE events come from the same backend used by the web UI.
- [x] The terminal UI uses the mewcode-style banner/chat/input/status layout,
      streaming answers, collapsible tools, completion, and approval controls.
- [x] Server unavailability and ambiguous/missing Workspaces produce actionable
      errors without falling back to an in-memory Runtime.
- [x] Provider secrets are never returned or persisted by the CLI.
- [ ] Focused tests, full pytest, compileall, documentation validation, and diff
      checks pass.

## Decisions

- Keep the embedded `CliApplication` for SDK and test compatibility, but make
  the public console entrypoint instantiate only `RemoteCliApplication`.
- Resolve Workspace and Session state through the public API and fail closed on
  missing or ambiguous Workspaces; never assemble an implicit local Runtime.
- Reuse mewcode's interaction language (full-screen chat, bottom composer and
  status, Markdown streaming, collapsible tool calls, slash completion) without
  copying its branding, comments, or Agent loop.
- Display only the model registry's `credential_configured` and health/status
  fields. Credential plaintext remains server-only.

## Verification

- `54 passed, 1 warning, 11 subtests passed` for the focused HTTP client, TUI,
  public CLI entrypoint, API, Session, and model-registry tests.
- Isolated live server: `uv run --frozen --no-sync cogent --api-url
  http://127.0.0.1:8877 --workspace-id project --print ...` completed from Run
  SSE, and the Textual UI opened with the server model, Workspace, and API URL.
- Unavailable-server invocation returned exit 2 with an actionable Compose
  startup message and no local fallback.
- `uv lock --check`, frozen `cogent --help`, `compileall`, handbook validation,
  and `git diff --check` passed.
- Full pytest was executed twice. The final shared-worktree snapshot reached
  `819 passed, 12 skipped, 111 subtests passed`, with eleven failures confined
  to the concurrently active `COGENT-FILE-MEMORY-REFACTOR` instruction and
  legacy-memory replacement work. That task continued changing during
  verification, so the repository-wide green check is intentionally left open.

## Result

Implementation and scope-local acceptance are complete. Repository-wide
closure remains pending only because the pre-existing active file-memory task
is concurrently modifying shared runtime and test files; `.workflow/state.yaml`
continues to describe that task and was not overwritten. Documentation impact
was material and is covered in both READMEs and the architecture, runtime,
reliability, and facts handbook entries.
