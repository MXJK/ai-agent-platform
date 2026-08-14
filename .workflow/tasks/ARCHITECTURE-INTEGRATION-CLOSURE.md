# ARCHITECTURE-INTEGRATION-CLOSURE: 最终架构整合收口

## Goal

将旁支 `codex/mcp-lifecycle-v2` 已验证的 Effective Tool Pool 语义移植到当前同时包含
Query Kernel、Agent Loop Decomposition 和 Shell Adapters 的最新主线，使阶段 0–10 位于
同一条可运行、可恢复、可验证的代码线上。

## In scope

- 语义移植 `ToolCatalog`、`ToolPoolBuilder`、`EffectiveToolPool` 及其安全快照/恢复契约。
- 将新 RunContext 升级到 schema v3，同时保留 v1/v2 的 legacy Registry view 读取路径。
- 调整 `ExecutionContextFactory`、`RuntimeContainer`、`QueryService` 和拆分后的 Agent Loop，
  让创建、Worker 恢复、resume 与所有工具节点共享同一个冻结工具池。
- 为 Query/REPL 增加兼容的 Skill slash invocation，冻结选中 Skill 指令且不扩大工具或权限。
- 让 `/skills`、`/tools`、`/mcp`、`/permissions` 通过共享服务展示 Workspace/Run 有效状态。
- 移植并扩展工具池、Query 恢复、Skill command、Shell 与跨 Workspace 隔离测试。
- 同步 README 中英文版、模块化访谈手册及 `facts.json`，删除过时架构描述。

## Out of scope

- 直接 cherry-pick `d4a9be3d`，或把拆分后的 Agent Loop 重新合并进 facade。
- 恢复旧 `AgentRunService` 业务实现；`QueryService` 仍是唯一 Query Kernel。
- 让 Effective Tool Pool 复制 ToolRegistry 的 Schema、权限、重试、超时或幂等执行逻辑。
- 让 Skill/MCP 元数据产生授权，或修改现有 HTTP URL、202 行为和响应 Schema。
- 新增数据库列或执行数据库迁移、部署、提交、推送、PR、合并及其他外部写入。

## Acceptance criteria

- [x] Tool catalog/pool 具备显式来源与 namespace、稳定排序、深度不可变和 fail-closed 冲突检测。
- [x] Pool 同时求交进程/项目选择、Agent、mode、模型能力、Workspace role、中央 display deny、
      显式 deny、Sandbox 和 Skill 依赖，且 Skill 依赖不能增加工具。
- [x] 越池调用明确 `permission_denied`；池内执行仍委托 ToolRegistry 并由 PermissionResolver 复判。
- [x] 新 Run 一律写 schema v3；v1/v2 继续经明确 legacy `ToolRegistryView` 路径加载。
- [x] v3 快照保存脱敏 catalog/pool summary、hash、provenance 和安全诊断，并能检测缺失、漂移和篡改。
- [x] `ExecutionContextFactory` 在同一 run_id 下完成 Workspace 鉴权、项目配置、模型、Skill、权限、
      工具池和指令冻结，不修改全局 ToolRegistry。
- [x] RuntimeContainer 共享 ToolPoolBuilder，保持进程 allowlist 硬上限、逆序关闭与失败回滚。
- [x] Query start 原子持久化 v3 快照，队列只携带 run_id；Worker/resume/continue 恢复原冻结池，
      恢复失败时不调用模型或工具并记录稳定安全事件。
- [x] 拆分后的 exploration/native/change/validation/repair/artifact 路径和模型 Schema 都只访问同一池，
      LangGraph 节点、边、checkpoint、预算和终态语义不变。
- [x] Skill slash command 遵守来源覆盖、enabled_skills、Agent/mode 和 required_tools；成功后提交普通 Query，
      未知/禁用/缺依赖返回稳定诊断且不执行 Skill 代码或扩大权限。
- [x] `/skills`、`/tools`、`/mcp`、`/permissions` 通过共享服务显示当前 Workspace/Run 有效上下文。
- [x] v1/v2 兼容、Query/Worker 恢复、工具漂移、权限复判、Skill command、Shell、golden trajectory、
      入口事件 Schema 和双 Workspace 隔离测试通过。
- [x] README、README.en、访谈手册 Part 04/05/06/08 与 facts 同步且无过时架构声明。
- [x] 所有任务指定验证和仓库必需验证通过；未执行迁移、部署、提交、推送、PR 或合并。

## Decisions

- `ToolRegistry` 继续唯一拥有 callable、Schema 校验、超时、重试、幂等和执行点权限复判；
  `EffectiveToolPool` 只拥有每 Run 的目录选择、冻结、展示、越池拒绝和快照恢复。
- 旁支实现只作为语义来源，按当前 Query Kernel、拆分 Agent Loop 和 Shell adapter 接口重新适配，
  不直接 cherry-pick。
- `run_context_snapshot` JSONB 继续承载 schema v3，不新增迁移；`20260810_0020` 与
  `20260813_0021` 在 PostgreSQL runtime 启动前仍需操作者审阅并人工授权应用。
- 实现验证先覆盖未提交工作树；用户后续明确授权提交后，由工作流控制器将
  `last_verified_commit` 更新为该已验证实现提交。

## Verification

- `.venv/bin/python -m pytest -q` → `381 passed, 49 subtests passed in 12.84s`。
- 任务指定九组整合测试 → `83 passed, 10 subtests passed in 4.66s`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals` 通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py` → 验证 12 个 Markdown 和 37 个 capability；
  只报告本任务已审阅的 changed-evidence 提示。
- `.venv/bin/alembic heads` → `20260813_0021 (head)`，未应用任何迁移。
- `.venv/bin/python -m pip check` → `No broken requirements found.`
- `node --check ai_agent_platform/static/app.js`、`bash -n scripts/start.sh`、两个 Python CLI `--help`、
  `git diff --check` 全部通过。
- Agent Loop 绕过审计确认只有 `agents/coding/tool_access.py` 读取/选择全局 Registry；
  `change_loop.py` 中的 `list_specs()` 对象为已恢复的 pool。
- 基线 `main`/`4f99fbac` 同时含 Query Kernel、拆分 Agent Loop 和 Shell Adapters；
  已验证实现提交 `ca74c25f` 在该代码线上整合 Effective Tool Pool，不依赖旁支运行。

## Result

已完成语义整合。`ToolRegistry` 仍是 callable 与可靠执行真相，新的
`ToolCatalog`/`ToolPoolBuilder`/`EffectiveToolPool` 则成为 Run 级冻结与恢复边界。
Query start、Worker、resume/continue、拆分 Agent Loop 和 Shell/Skill command 均使用同一
RunContext v3 契约，v1/v2 保留明确 legacy view。

文档影响已处理：README 中英文版、本地模块化访谈手册 Part 04/05/06/08、总入口和
`facts.json` 均已同步，并移除 ToolRegistryView 作为最终工具池、Skill command 只注册不使用
等过时表述。

实现与验证回合没有执行迁移、部署、push、PR 或 merge，也没有 cherry-pick
`d4a9be3d`。用户后续明确授权提交与推送后，已将完整验证覆盖的工作树提交为
`ca74c25f`，并将 `last_verified_commit` 更新为该实现提交。Alembic `20260810_0020` 和
`20260813_0021` 仍需在 PostgreSQL runtime 启动前由操作者审阅并明确授权应用。
