# Local Skill and MCP Test Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable a local-only project Skill and MCP test environment, seed safe test fixtures, and verify that the existing MCP page can create, edit, test, enable/disable, and delete Servers.

**Architecture:** Keep product code and permission boundaries unchanged. Project-compatible declarative Skill fixtures live under the selected Workspace's ignored `.agents/skills` tree; ignored `.env` and `mcp.json` activate the local MCP runtime and seed official reference Servers. The existing `MCPRegistryService`, API routes, and static front end are validated end to end and are changed only through a separately diagnosed bugfix if an acceptance check fails.

**Tech Stack:** Python 3.11+, FastAPI/TestClient, official MCP Python SDK 2.0.0, JSON, Markdown/YAML frontmatter, vanilla JavaScript, Node/npm (`npx`).

## Global Constraints

- Project Skill fixtures must use only `name`, `description`, `agents`, `modes`, `context_budget`, `tools`, and `command` frontmatter fields.
- `.agents/skills/`, `.env`, and `mcp.json` are local artifacts and must not be committed.
- `AUTH_MODE=disabled` is permitted only with the existing `127.0.0.1` loopback startup default.
- Do not weaken `PermissionResolver`, approval policy, ToolRegistry validation, or Sandbox boundaries.
- Enable only the official Everything reference Server initially; seed Filesystem as disabled and scope it to `/Users/mxjk/programming/vs code project/ai-agent-platform`.
- Existing MCP page operations—create, edit, test/refresh, enable/disable, and delete—are all acceptance requirements.
- If an existing acceptance check fails, stop that task and invoke `superpowers:systematic-debugging` before editing product code.

---

## File map

- Create: `.workflow/tasks/LOCAL-SKILL-MCP-TEST-SETUP.md` — authoritative repository task record.
- Modify: `.workflow/state.yaml` — mark the task active, then close it through `codex-close-task`.
- Modify: `.gitignore` — ignore project Skill fixtures and local MCP registry.
- Modify, ignored: `.env` — enable local Skill/MCP runtime and MCP administration.
- Create, ignored: `.agents/skills/code-review/SKILL.md` — read-only review command.
- Create, ignored: `.agents/skills/bug-triage/SKILL.md` — debugging command.
- Create, ignored: `.agents/skills/test-design/SKILL.md` — test-design command.
- Create, ignored: `mcp.json` — local Everything and Filesystem Server registry.
- Verify unchanged: `ai_agent_platform/static/index.html` — MCP form and action controls.
- Verify unchanged: `ai_agent_platform/static/app.js` — MCP CRUD/test/toggle handlers.
- Verify unchanged: `ai_agent_platform/api/routes/mcp_registry.py` — per-Server management API.
- Test: `tests/test_mcp_lifecycle.py` — API lifecycle and front-end contract coverage.

### Task 1: Establish the workflow task and ignored local boundary

**Files:**
- Create: `.workflow/tasks/LOCAL-SKILL-MCP-TEST-SETUP.md`
- Modify: `.workflow/state.yaml`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: project workflow contract in `AGENTS.md` and `.workflow/state.yaml`.
- Produces: active task ID `LOCAL-SKILL-MCP-TEST-SETUP` and ignore rules for later local artifacts.

- [ ] **Step 1: Record the active repository task**

Create `.workflow/tasks/LOCAL-SKILL-MCP-TEST-SETUP.md` with:

```markdown
# LOCAL-SKILL-MCP-TEST-SETUP：本地 Skill 与 MCP 前端测试配置

## Goal

为当前仓库准备不入库的 project Skill 和 MCP 测试配置，并验证 MCP 页面可以新增、
编辑、测试/刷新、启停和删除单个 Server。

## Acceptance criteria

- [ ] `.agents/skills` 下存在三个可被项目发现器加载的测试 Skill。
- [ ] Skill 目录和 `mcp.json` 被 Git 忽略。
- [ ] 本机 `.env` 允许并启用 Skill/MCP，MCP 管理写接口在 loopback 本机模式可用。
- [ ] Everything MCP 默认启用；Filesystem MCP 默认停用且只指向当前仓库。
- [ ] MCP 页面新增、编辑、测试/刷新、启停和删除均通过验证。
- [ ] 项目要求的测试与 compileall 通过。

## Permitted scope

- 本地忽略配置、测试 Skill、MCP Server 注册表、相关验证与最小文档记录。
- 若既有 MCP 页面验收失败，先系统调试，再只修复经证实的缺口。
- 不部署、不迁移、不推送、不放宽中央工具权限、审批或 Sandbox。

## Result

进行中。
```

Update `.workflow/state.yaml` to:

```yaml
project_id: "ai-agent-platform"
status: "in_progress"
active_task: "LOCAL-SKILL-MCP-TEST-SETUP"
next_action: "Create ignored project Skill fixtures and local MCP configuration, then verify the MCP management page."
blockers: []
last_verified_commit: "94ff8cccab15d4631e39effe3d97f7c8d3a230d5"
updated_at: "2026-08-16T00:00:00+08:00"
vault_note: "10 Projects/ai-agent-platform/Home.md"
```

Use the actual current Asia/Shanghai timestamp instead of the example midnight value.

- [ ] **Step 2: Add the local artifact ignore rules**

Append these exact entries to `.gitignore`:

```gitignore
.agents/skills/
mcp.json
```

- [ ] **Step 3: Verify the ignore rules before creating artifacts**

Run:

```bash
git check-ignore -v .agents/skills/code-review/SKILL.md mcp.json
```

Expected: both prospective paths are reported from `.gitignore`; Git can match
ignore rules before those files exist.

- [ ] **Step 4: Commit the workflow and ignore boundary**

```bash
git add .workflow/state.yaml .workflow/tasks/LOCAL-SKILL-MCP-TEST-SETUP.md .gitignore
git commit -m "chore: prepare local skill and MCP test setup"
```

### Task 2: Create and verify project Skill fixtures

**Files:**
- Create, ignored: `.agents/skills/code-review/SKILL.md`
- Create, ignored: `.agents/skills/bug-triage/SKILL.md`
- Create, ignored: `.agents/skills/test-design/SKILL.md`

**Interfaces:**
- Consumes: `SkillDiscovery.discover(project_root=...)`, local tool names `repo.search_code`, `repo.read_file`, `bug_investigator`, and `test_designer`.
- Produces: qualified Skills `project:code-review`, `project:bug-triage`, and `project:test-design`, with commands `/review`, `/bug-triage`, and `/test-design`.

- [ ] **Step 1: Run the discovery acceptance assertion before fixtures exist**

Run:

```bash
.venv/bin/python -c 'from pathlib import Path; from ai_agent_platform.skills import SkillDiscovery; c=SkillDiscovery().discover(project_root=Path.cwd()); expected={"project:code-review","project:bug-triage","project:test-design"}; assert expected <= {s.qualified_name for s in c.skills}'
```

Expected: FAIL because the three local fixtures do not yet exist.

- [ ] **Step 2: Create the code-review Skill**

Write `.agents/skills/code-review/SKILL.md`:

```markdown
---
name: code-review
description: Review live workspace changes with evidence and prioritized findings
agents: [coding]
modes: [default]
context_budget: 4000
tools: [repo.search_code, repo.read_file]
command:
  name: review
  description: Review code or changes in the current Workspace
  usage: "[path or review focus]"
  aliases: [cr]
---
Inspect the requested files and their callers before forming conclusions. Report
actionable correctness, security, reliability, and test gaps in priority order.
Use live repository evidence and avoid inventing behavior that is not present.
```

- [ ] **Step 3: Create the bug-triage Skill**

Write `.agents/skills/bug-triage/SKILL.md`:

```markdown
---
name: bug-triage
description: Trace a reported symptom to likely causes and focused verification
agents: [coding]
modes: [default]
context_budget: 4000
tools: [repo.search_code, repo.read_file, bug_investigator]
command:
  name: bug-triage
  description: Investigate a bug in the current Workspace
  usage: "<symptom> [candidate path]"
  aliases: [bug]
---
Reproduce or characterize the symptom before suggesting a cause. Follow the data
and control flow through live code, distinguish evidence from hypotheses, and
propose the smallest focused verification that can confirm the root cause.
```

- [ ] **Step 4: Create the test-design Skill**

Write `.agents/skills/test-design/SKILL.md`:

```markdown
---
name: test-design
description: Design focused tests for requested behavior and failure boundaries
agents: [coding]
modes: [default]
context_budget: 4000
tools: [repo.search_code, repo.read_file, test_designer]
command:
  name: test-design
  description: Design tests for behavior in the current Workspace
  usage: "<behavior or path>"
  aliases: [tests]
---
Read the implementation and existing tests before proposing coverage. Separate
happy paths, boundary cases, permission failures, and regression cases. Prefer
small deterministic tests with explicit observable outcomes.
```

- [ ] **Step 5: Verify discovery, commands, dependencies, and diagnostics**

Run:

```bash
.venv/bin/python -c 'from pathlib import Path; from ai_agent_platform.agents.coding.tools import create_coding_tool_registry; from ai_agent_platform.skills import SkillDiscovery, SkillService; registry=create_coding_tool_registry(); service=SkillService(SkillDiscovery(), enabled=True, available_tools=tuple(s.name for s in registry.list_specs())); c=service.effective_catalog(workspace_root=Path.cwd(), agent="coding", mode="default"); assert {s.qualified_name for s in c.skills}=={"project:bug-triage","project:code-review","project:test-design"}; assert {x.name for x in c.commands}=={"bug-triage","review","test-design"}; assert not [d for d in c.diagnostics if d.severity=="error"]; registry.close()'
```

Expected: PASS with no output.

- [ ] **Step 6: Confirm the Skill fixtures remain ignored**

```bash
git status --short --ignored .agents/skills
```

Expected: `!! .agents/skills/` and no tracked Skill fixture.

### Task 3: Enable and validate the local MCP registry

**Files:**
- Modify, ignored: `.env`
- Create, ignored: `mcp.json`

**Interfaces:**
- Consumes: `Settings.from_env()`, `load_mcp_server_configs(path)`, local executable `npx`.
- Produces: writable, runtime-enabled MCP registry containing enabled `everything` and disabled `filesystem`.

- [ ] **Step 1: Prove the requested configuration is not active yet**

Run:

```bash
.venv/bin/python -c 'from ai_agent_platform.core import Settings; s=Settings.from_env(); assert s.auth_mode=="disabled" and s.skills_allowed and s.skills_enabled and s.mcp_allowed and s.mcp_enabled and s.mcp_config_path=="mcp.json"'
```

Expected: FAIL because the local toggles have not been applied.

- [ ] **Step 2: Update only the safe local toggles in `.env`**

Preserve all unrelated and secret-bearing lines. Replace or append exactly:

```dotenv
AUTH_MODE=disabled
SKILLS_ALLOWED=true
SKILLS_ENABLED=true
MCP_ALLOWED=true
MCP_ENABLED=true
MCP_CONFIG_PATH=mcp.json
```

- [ ] **Step 3: Create the seed MCP registry**

Write `mcp.json` with the absolute repository boundary:

```json
{
  "mcp_servers": {
    "everything": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-everything"],
      "required": false,
      "enabled": true,
      "connect_timeout_seconds": 60,
      "request_timeout_seconds": 20,
      "max_retries": 0
    },
    "filesystem": {
      "transport": "stdio",
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/Users/mxjk/programming/vs code project/ai-agent-platform"
      ],
      "required": false,
      "enabled": false,
      "connect_timeout_seconds": 60,
      "request_timeout_seconds": 20,
      "max_retries": 0
    }
  }
}
```

- [ ] **Step 4: Validate resolved settings without displaying secrets**

Run the Step 1 assertion again.

Expected: PASS with no output.

- [ ] **Step 5: Validate the MCP file and disabled-state contract**

Run:

```bash
.venv/bin/python -c 'from ai_agent_platform.integrations.mcp import load_mcp_server_configs; active=load_mcp_server_configs("mcp.json"); all_servers=load_mcp_server_configs("mcp.json", include_disabled=True); assert [x.name for x in active]==["everything"]; assert [(x.name,x.enabled) for x in all_servers]==[("everything",True),("filesystem",False)]'
```

Expected: PASS with no output.

- [ ] **Step 6: Start Everything through the real connection manager**

Run:

```bash
.venv/bin/python -c 'from ai_agent_platform.integrations.mcp import MCPServerState, create_mcp_connection_manager_from_configs, load_mcp_server_configs; manager=create_mcp_connection_manager_from_configs(load_mcp_server_configs("mcp.json")); manager.start(); status=manager.status_for("everything"); assert status is not None and status.state is MCPServerState.READY, status; assert manager.server_tools("everything"); manager.close()'
```

Expected: PASS. The first invocation may download the official npm package; a download failure is reported as an external setup failure, not hidden.

- [ ] **Step 7: Confirm local MCP configuration remains ignored**

```bash
git status --short --ignored .env mcp.json
```

Expected: both paths are ignored and contain no tracked diff.

### Task 4: Verify all MCP page lifecycle operations

**Files:**
- Verify: `ai_agent_platform/static/index.html`
- Verify: `ai_agent_platform/static/app.js`
- Verify: `ai_agent_platform/api/routes/mcp_registry.py`
- Test: `tests/test_mcp_lifecycle.py`

**Interfaces:**
- Consumes: `GET/PUT/PATCH/POST/DELETE /api/v1/mcp/servers...`, ignored `mcp.json`, and the existing MCP form/action handlers.
- Produces: evidence that create, edit, test/refresh, enable/disable, and delete work from the browser page.

- [ ] **Step 1: Run the focused automated MCP lifecycle contract**

```bash
.venv/bin/python -m pytest -q tests/test_mcp_lifecycle.py::MCPLifecycleTests::test_frontend_registry_persists_secret_refs_and_hot_registers_tools tests/test_mcp_lifecycle.py::MCPLifecycleTests::test_frontend_registry_writes_are_local_only tests/test_mcp_lifecycle.py::MCPLifecycleTests::test_frontend_registry_registers_streamable_http_with_secret_header tests/test_mcp_lifecycle.py::MCPLifecycleTests::test_frontend_registry_reports_missing_writable_config_path
```

Expected: four tests pass. The first test covers create/list/test, disable/enable, delete, hot ToolRegistry synchronization, and front-end contract markers.

- [ ] **Step 2: Validate the browser JavaScript syntax**

```bash
node --check ai_agent_platform/static/app.js
```

Expected: exit code 0.

- [ ] **Step 3: Start a deterministic local application runtime**

Run on loopback:

```bash
LLM_PROVIDER=fake MODEL_SECRET_BACKEND=memory SESSION_REPOSITORY=memory AGENT_RUN_STORE=memory DOCUMENT_STORE=memory WORKSPACE_STORE=memory MODEL_REGISTRY_STORE=memory LANGGRAPH_CHECKPOINTER=memory RAG_VECTOR_STORE=memory RAG_RERANKER_PROVIDER=none TASK_QUEUE_BACKEND=in_process PROJECT_MEMORY_ENABLED=false AUTH_MODE=disabled SKILLS_ALLOWED=true SKILLS_ENABLED=true MCP_ALLOWED=true MCP_ENABLED=true MCP_CONFIG_PATH="/Users/mxjk/programming/vs code project/ai-agent-platform/mcp.json" .venv/bin/python -m uvicorn ai_agent_platform.main:app --host 127.0.0.1 --port 8765
```

Expected: health is ready or explicitly degraded only for an optional MCP Server; the process stays available.

- [ ] **Step 4: Exercise the MCP page in the in-app browser**

Open `http://127.0.0.1:8765/#mcp` and perform this exact reversible sequence:

1. Confirm `everything` and disabled `filesystem` render.
2. Add `ui-smoke` as enabled stdio: command `npx`, args `-y` and `@modelcontextprotocol/server-everything` on separate lines.
3. Edit `ui-smoke`, changing request timeout from `10` to `20`, save, and confirm it reconnects.
4. Click `测试 / 刷新` and confirm the Server reaches `ready` with discovered/registered tools.
5. Click `停用`, confirm state `disabled`, then click `启用` and confirm it returns to `ready`.
6. Click `删除`, accept confirmation, and confirm only `ui-smoke` is removed.
7. Refresh the page and confirm the original two seed entries remain.

Expected: all five lifecycle operations succeed with no browser console error and no Secret value rendered.

- [ ] **Step 5: Stop the local runtime and validate final registry contents**

Run the Task 3 Step 5 assertion again.

Expected: only enabled `everything` and disabled `filesystem` remain, proving the smoke sequence was reversible.

### Task 5: Run full verification and close the workflow task

**Files:**
- Modify: `.workflow/tasks/LOCAL-SKILL-MCP-TEST-SETUP.md`
- Modify: `.workflow/state.yaml`

**Interfaces:**
- Consumes: all configured local artifacts and repository verification commands.
- Produces: evidence-backed task Result and final workflow state.

- [ ] **Step 1: Run repository verification**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall ai_agent_platform tests evals
node --check ai_agent_platform/static/app.js
git diff --check
```

Expected: all commands pass. No handbook validation is required unless implementation unexpectedly changes `README.md`, `INTERVIEW_NOTES.md`, an `INTERVIEW_NOTES/*.md` Part, `INTERVIEW_NOTES/facts.json`, or a mapped evidence path.

- [ ] **Step 2: Verify tracked and ignored state**

```bash
git status --short
git status --short --ignored .env .agents/skills mcp.json
```

Expected: tracked changes are limited to workflow/result records and any evidence-backed fix; `.env`, `.agents/skills/`, and `mcp.json` remain ignored.

- [ ] **Step 3: Close through the repository workflow**

Invoke `codex-close-task` and record:

- the three discovered qualified Skill names and commands;
- resolved local Skill/MCP toggles without secret values;
- Everything connection result and Filesystem disabled state;
- automated and browser evidence for create/edit/test/toggle/delete;
- full pytest, compileall, JavaScript syntax, and diff-check results;
- documentation impact: no product documentation change because this task adds only ignored local fixtures/configuration and validates already-documented behavior.

- [ ] **Step 4: Commit the verified tracked result**

```bash
git add .workflow/state.yaml .workflow/tasks/LOCAL-SKILL-MCP-TEST-SETUP.md .gitignore
git commit -m "chore: enable local skill and MCP testing"
```

Record the new commit as `last_verified_commit` only if it is fully covered by the verification above.
