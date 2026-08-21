# TOOL-CALL-EXECUTION-HARDENING

## Goal

Repair four verified defects in the tool-calling path: abandoned timeout
workers, provider-rejected text finalization, context-parameter collisions, and
schema errors that echo the rejected value.

## Acceptance criteria

- A timed-out tool reports that its call may still be running, and a second
  side-effecting call in the same Run cannot race the abandoned worker.
- Text finalization keeps the replayed tool transcript valid for every provider.
- A tool or MCP server may declare an argument named `context` without breaking
  execution.
- Schema validation errors never contain the rejected argument or output value.
- Focused tests, the full suite, compile checks, and documentation validation
  pass.

## Scope

- `ToolRegistry` execution, timeout, and validation reporting.
- MCP provider tool registration and invocation.
- `LLMClient` tool finalization across OpenAI, Anthropic, DeepSeek, and Google.
- README, README.en, and interview handbook Part 05 synchronization.

## Decisions

- A Python callable cannot be preempted, so a timeout abandons a daemon worker
  and says so; the Run-scoped guard (`tool_timeout_in_flight`) prevents a racing
  write instead of pretending the abandoned call did not happen. Only
  non-idempotent calls are tracked, and finished workers are pruned on access.
- Finalization declares the same tools and disables calls through each
  provider's tool choice. Providers reject replayed `tool_use`/`tool_result`
  content when a request declares no tools, and the previous request omitted the
  tools entirely. When no definitions exist at all, the transcript is flattened
  to text so the request stays valid.
- The execution context is injected under a per-spec `context_parameter`.
  Registration fails closed when a tool declares the same name, MCP tools
  reserve `__tool_context__`, and `agent.request_user_input` declares
  `accepts_context=False` because its `context` field is model-supplied text.
- Schema failures are described from schema-side facts plus the type and size of
  the rejected value. jsonschema's own message is reused only for `required` and
  `additionalProperties`, whose text is built from schema names.

## Verification

- `.venv/bin/python -m pytest -q` — passed: 468 tests and 52 subtests.
- `.venv/bin/python -m compileall ai_agent_platform tests evals` — passed.
- `.venv/bin/python INTERVIEW_NOTES/validate.py` — passed; evidence-review
  advisories only.
- `git diff --check` — passed.
- New focused tests: abandoned-worker reporting and the racing-write guard,
  argument and output redaction, reserved context parameter, Anthropic
  finalization with declared tools, and finalization without tool definitions.

## Result

Fixed the four defects and recorded the new contracts in README, README.en, and
`INTERVIEW_NOTES/05-工具调用、MCP与安全.md`. Remaining review findings are not in
this task's scope: unbounded idempotency and per-Run pool caches, `repo.*` and
`sandbox.*` schemas without `additionalProperties: false`, orphan tool calls on
the permission-denied branch, mid-Run MCP registry mutation failing an in-flight
pool, output truncation dropping structured fields, constant-returning planner
tools, tool-result metadata replayed into the transcript, the extra token-count
round trip per tool turn, and the uncoupled graph recursion limit.
