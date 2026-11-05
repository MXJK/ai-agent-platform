# React workbench migration verification — 2026-11-04

## Delivered boundary

React + TypeScript + Vite now build the nine workbench page shells and navigation.
Provider/model, Skill/MCP, knowledge/document, session and recent-session lists
are keyed React components. The existing script was extracted into 26 domain
modules and the stylesheet split without changing its cascade order. FastAPI
serves hashed static assets; Docker builds them in a Node stage and Python wheels
include them. There is no extra production Node service.

This is an incremental migration. Chat/approval/ChangeSet/checkpoint, memory,
evaluation and audit controllers still contain imperative JavaScript rendering.
The shared controller state is also retained. They must not be described as a
complete conversion to React hooks or fully typed application state. The remaining
migration boundary and DOM ownership rules are in [frontend/README.md](../../frontend/README.md).

## Automated checks

| Check | Result |
| --- | --- |
| `npm --prefix frontend ci --offline --no-audit --no-fund` | Passed from lockfile and local cache |
| `npm --prefix frontend run build` | TypeScript and Vite build passed |
| `npm --prefix frontend test` | 10 tests passed across 4 files |
| `node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs tests/test_trace_audit_ui.mjs` | 31 controller behavior tests passed |
| `.venv/bin/python -m pytest -q` (default sandbox) | 881 passed, 12 skipped, 2 failed; same failures as the pre-migration baseline |
| `.venv/bin/python -m pytest -q` (local-port access allowed) | 882 passed, 12 skipped, 1 failed, 108 subtests passed |
| `.venv/bin/python -m compileall ai_agent_platform tests evals` | Passed |
| `.venv/bin/python INTERVIEW_NOTES/validate.py` | Validated 24 Markdown files and 46 capabilities; existing evidence-review warnings remain |
| `git diff --check` | Passed |
| Python wheel build and ZIP inspection | Passed; HTML and all hashed JS/CSS assets included |

The remaining Python failure is
`tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`.
It also failed before any frontend edit: the managed-file reader raises
`ValueError` for the symlinked parent. This migration does not change that backend
code or its test. The other baseline failure was sandbox denial of local socket
binding in the HTTP hook test; that test passes when local-port access is allowed.

## Browser verification

Used ego-browser against a temporary FastAPI instance with `llm_provider=fake`,
in-memory stores and isolated workspace/knowledge files. No real provider keys
were saved or real model calls made.

- Registered and selected the temporary workspace through the workbench.
- Sent a prompt and observed the user message, fake model answer, completed Run,
  metrics and recent-session entry.
- Navigated through all nine views and retained an unsent composer draft.
- Refreshed model telemetry while a test credential draft was present; the draft
  remained intact and was not submitted.
- Created a knowledge base, uploaded a Markdown file, observed an indexed document,
  and selected its React table row; the bulk-action count updated to one.
- Inspected desktop and 390px mobile layouts, including the recent-session drawer.
  The measured mobile document width was 390px, without horizontal overflow.

Approval/resume, answer-reset, stale audit-response and SSE-boundary behavior have
automated coverage; they were not all exercised with a real provider in this
browser pass. Docker build configuration was updated, but no image deployment,
merge, release or data migration was performed. Existing unrelated working-tree
changes were preserved.
