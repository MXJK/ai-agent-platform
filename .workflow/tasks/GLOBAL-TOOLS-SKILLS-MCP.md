# GLOBAL-TOOLS-SKILLS-MCP

## Goal

Make declarative Skills user-global across every Workspace, repair self-hosted MCP registration/runtime, and manage both capabilities from one Tools page with explicit `/` and conservative implicit Skill invocation.

## Acceptance criteria

- Every Workspace receives the same bundled/user Skill catalog from `~/.ai-agent-platform/skills`.
- The composer `/` menu exposes global Skill commands and MCP tools.
- Explicit Skill selection loads the selected instructions; ordinary Runs expose only metadata and can use `agent.load_skill` for a strong implicit match.
- Local admins can create, edit, enable, disable, and delete user Skills from the Tools page.
- Local admins can manage and test MCP servers from the same Tools page.
- Docker persists `~/.ai-agent-platform`, enables Skills/MCP, and contains the `npx` runtime used by common stdio servers.
- Focused tests, the full test suite, compile checks, and documentation validation pass.

## Scope

- Skill discovery, management API, execution context, and tool registration.
- MCP self-hosted configuration/runtime packaging.
- Tools page and composer capability UI.
- README and interview handbook synchronization.

## Decisions

- Runtime Skill discovery is user-global: every Workspace uses bundled Skills plus `SKILLS_DIRECTORY_PATH` (default `~/.ai-agent-platform/skills`); Workspace `.agents/skills` is no longer a runtime registration source.
- Any valid Skill receives a default slash command from its name/description when `command` is omitted.
- Skill bodies use progressive disclosure: explicit `/` invocation freezes one body; implicit selection sees metadata first and loads one body through the read-only `agent.load_skill` tool.
- Skill and MCP writes share the local-admin boundary and one Tools page. User Skills are atomically persisted with a `.disabled` marker; bundled Skills remain read-only.
- Self-hosted Compose bind-mounts host `~/.ai-agent-platform`, enables Skills/MCP, and installs Node/npm/npx in the app image.

## Verification

- `.venv/bin/python -m pytest -q` — passed: 461 tests and 52 subtests.
- `.venv/bin/python -m compileall ai_agent_platform tests evals` — passed.
- `.venv/bin/python INTERVIEW_NOTES/validate.py` — passed: 24 Markdown files and 39 capabilities; evidence-review warnings only.
- `node --check ai_agent_platform/static/app.js` — passed.
- `docker compose config --quiet` — passed.
- `git diff --check` — passed.
- Focused Skill registry, cross-Workspace, slash invocation, MCP lifecycle, Tools UI, and self-hosted Compose tests are included in the full suite.
- `docker compose build app` could not start because desktop approval for Docker buildx cache access was rejected after the account hit its usage limit. This is an external verification gap, not a source/build failure observed inside Docker.
- Creating and seeding the host `~/.ai-agent-platform/skills` directory was likewise rejected by the same external approval limit; the Tools page creates it atomically on first user Skill save.

## Result

Implemented the user-global Skill registry, CRUD/enable APIs, unified Tools page, default `/` exposure, explicit and conservative implicit Skill loading, and persistent self-hosted MCP runtime configuration. All repository-required verification passes. The only remaining operator checks are rebuilding the Docker image and optionally migrating the three legacy `.agents/skills` directories into the new global directory once desktop approval is available.
