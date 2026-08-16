# Local Skill and MCP Test Setup Design

Date: 2026-08-16

## Goal

Prepare a local-only test setup that demonstrates this platform's project Skill
discovery and MCP lifecycle without committing generated Skill content, MCP
server configuration, or credentials. The MCP page must support creating,
editing, testing/refreshing, enabling/disabling, and deleting individual
servers.

## Existing constraints

- Project Skills are discovered only below the selected Workspace root at
  `.agents/skills/**/SKILL.md` and must use this project's strict declarative
  frontmatter schema.
- MCP server writes are accepted only when `AUTH_MODE=disabled`; unauthenticated
  HTTP startup remains bound to loopback by the existing CLI/start script.
- The MCP runtime requires both `MCP_ENABLED=true` and a writable
  `MCP_CONFIG_PATH`.
- Skill and MCP metadata do not grant tool permission. Tool selection,
  `PermissionResolver`, approval policy, and Sandbox checks remain unchanged.

## Chosen design

### Local configuration

Update the ignored `.env` with:

```dotenv
AUTH_MODE=disabled
SKILLS_ALLOWED=true
SKILLS_ENABLED=true
MCP_ALLOWED=true
MCP_ENABLED=true
MCP_CONFIG_PATH=mcp.json
```

Keep the service on the existing loopback default. Do not weaken the tool
approval policy or Sandbox configuration.

### Project Skill fixtures

Create three project-compatible fixtures:

1. `code-review`: command `/review`, requiring `repo.search_code` and
   `repo.read_file`.
2. `bug-triage`: command `/bug-triage`, requiring `repo.search_code`,
   `repo.read_file`, and `bug_investigator`.
3. `test-design`: command `/test-design`, requiring `repo.search_code`,
   `repo.read_file`, and `test_designer`.

Each fixture is pure Markdown and contains no executable hooks. The files live
under `.agents/skills/<name>/SKILL.md`, so selecting this repository as the
Workspace exercises the project source and `project:<name>` qualification.

Add `.agents/skills/` to `.gitignore`, as requested. Also ignore `mcp.json`
because it is a machine-local runtime registry and may contain absolute paths
or SecretStore references.

### MCP seed configuration

Create ignored `mcp.json` with two official reference servers:

- `everything`: enabled stdio server using the already-cached package via
  `npx --offline -y @modelcontextprotocol/server-everything`; bootstrap the npm
  cache once with online `npx -y` if needed. This is the primary protocol and
  tool-discovery smoke test, without a registry dependency on every startup.
- `filesystem`: disabled by default, using
  `npx -y @modelcontextprotocol/server-filesystem <repository-root>`; when the
  user enables it, its own access is restricted to this repository.

No credentials are stored. Context7, Playwright, and GitHub remain documented
recommendations for later front-end registration because they introduce remote
secrets or higher-impact browser/external-write behavior.

### Runtime and UI flow

```text
.env enables local MCP administration
  -> runtime opens mcp.json and starts enabled servers
  -> MCPConnectionManager discovers tools
  -> ToolRegistry receives mcp.<server>.<tool>
  -> /#mcp reads GET /api/v1/mcp/servers
  -> create/edit/test/toggle/delete calls the existing per-server APIs
  -> registry persists atomically and synchronizes only the target server
```

No UI redesign is planned. Existing controls must be exercised and fixed only
if verification shows that one of the five required operations is broken.

## Error handling and safety

- A failed optional MCP server must not prevent application startup.
- The page must expose sanitized connection state and error code without
  returning Secret values.
- Everything is the only server enabled initially. Filesystem remains disabled
  until explicitly activated in the front end.
- Server removal must close its connection and remove only its provider's tools.
- The local unauthenticated setup must not be started on a non-loopback host.

## Verification

1. Parse resolved configuration without printing secrets and confirm all five
   capability/runtime fields above.
2. Discover the repository Skill catalog and confirm the three qualified names,
   slash commands, and zero schema errors.
3. Validate `mcp.json` through `load_mcp_server_configs`, then start the runtime
   and confirm the Everything server reaches a visible state; report a network
   or package-download failure without hiding it.
4. Exercise MCP API tests covering list, upsert/edit, test/refresh,
   enable/disable, and delete.
5. Run the repository-required pytest and compileall verification plus the
   interview handbook validator if tracked documentation or mapped evidence
   changes.

## Out of scope

- Installing Codex curated Skills into `~/.codex/skills`; that is a different
  discovery system and schema.
- MCP resources, prompts, OAuth, or elicitation support.
- Disabling central permission checks, approvals, or Sandbox boundaries.
- Deploying this unauthenticated local configuration to a shared host.
