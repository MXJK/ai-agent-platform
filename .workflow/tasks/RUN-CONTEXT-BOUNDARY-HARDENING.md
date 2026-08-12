# RUN-CONTEXT-BOUNDARY-HARDENING: 加固每 Run 配置、指令与工具边界

## Goal

修正 `LAYERED-RUNTIME-CONFIG` 与 `UNIFIED-RUN-CONTEXT` 之间的作用域错位：进程启动配置
不得从服务 cwd 隐式继承某个项目的设置，项目配置、项目指令和项目工具选择必须根据本次
Run 选中的已登记 Workspace 解析、校验并冻结，且不能污染其他 Workspace 或全局工具目录。

本任务是阶段 3 与阶段 4 之间的边界加固，不实现完整权限引擎、Skill/MCP 生命周期或最终
Effective Tool Pool。

## In scope

- 将配置解析拆成明确的进程基线与 Workspace 项目覆盖边界：
  - 进程启动只解析默认值、用户配置、环境变量和显式进程覆盖。
  - 创建 Run 时，从已鉴权的主 Workspace root 发现并解析项目配置。
  - 项目配置继续只能收紧进程安全边界，不能改写数据库、认证、密钥、允许根目录、真实
    写入等 Process/Security 字段。
  - `AI_AGENT_PLATFORM_PROJECT_CONFIG` 若继续支持，必须定义为显式、进程级受控输入；
    不得与 Workspace 自动发现产生含糊优先级，也不得重新引入服务 cwd 泄漏。
- `ExecutionContextFactory` 按本次 Workspace 生成脱敏、可序列化、带 provenance/version
  的有效项目配置快照，并确保 API、Worker、CLI 共用同一语义。
- 停止使用项目 `enabled_tools` 不可逆裁剪进程全局 `ToolRegistry`：
  - 进程 `tool_allowlist` 可以作为全局能力上限。
  - 项目 `enabled_tools` 必须作为 Run-scoped 选择被快照和执行。
  - 可增加最小的只读 Registry view/selector 以维持当前行为，但不要在本任务扩展为完整的
    Skill/MCP/Agent 类型过滤、排序和去重框架。
- 将配置中的 `project_session.project_instructions` 转换为带明确 provenance 的
  `ContextSource`/`InstructionSourceSnapshot`，与 Workspace 中的
  `AGENTS.override.md`、`AGENTS.md`、`CLAUDE.md` 一起在提交时冻结。
- 明确并记录配置指令与文件指令的优先级、作用域和共享字符预算；不得让 Worker 在执行时
  重新读取这些可变来源。
- 加固指令文件读取：拒绝符号链接文件、解析后越出 Workspace 的路径、非普通文件以及
  读取过程中的路径替换；错误应 fail closed 或产生明确、安全的诊断，不得读取工作区外
  内容。
- 修正工作流和运维说明：Alembic `20260810_0020` 尚未执行，启动 PostgreSQL runtime
  前必须保留人工授权迁移这一前置条件。
- 同步 README、`INTERVIEW_NOTES.md`、受影响的 `INTERVIEW_NOTES/*.md` 和
  `INTERVIEW_NOTES/facts.json`，删除“项目配置在启动时裁剪全局 ToolRegistry”等过时描述。

## Out of scope

- 实现阶段 4 的统一 PermissionContext、用户确认回调或完整 Sandbox 策略重构。
- 实现阶段 5–7 的 Skill 发现、MCP 生命周期或完整 Effective Tool Pool。
- 修改 Agent Loop/Query Kernel、LangGraph checkpoint、ChangeSet 协议或前端交互。
- 支持任意未登记路径作为 Workspace 或 additional directory。
- 热重载项目配置；同一 Run 在提交后继续使用冻结快照。
- 执行 Alembic 迁移、部署、外部写入、提交、推送、创建 PR 或合并。

## Acceptance criteria

- [x] API/Worker/CLI 进程从任意 cwd 启动时，都不会自动把该 cwd 下的项目配置应用到所有
      Workspace；进程基线配置不包含隐式 Workspace 项目覆盖。
- [x] 每个新 Run 都从已鉴权的主 Workspace root 解析项目配置，生成脱敏、稳定、带来源和
      版本的快照；Worker 只凭持久化 `run_id` 恢复同一配置，不重新解析文件或环境变量。
- [x] 两个 Workspace 可同时使用不同的 `enabled_tools`、`project_instructions` 和项目配置，
      连续或并发创建 Run 时互不污染；API 与 Worker cwd 不同也不改变结果。
- [x] 全局 `ToolRegistry` 不被项目 `enabled_tools` 修改；进程 `tool_allowlist` 仍是不可突破
      的上限，Run 实际可见/可调用工具同时满足进程上限和该 Run 的项目选择。
- [x] 未知工具、项目尝试放宽进程 allowlist、以及恢复快照中非法工具选择均 fail closed，
      错误中不泄露秘密或工作区外路径内容。
- [x] `project_session.project_instructions` 实际进入 RunContext，具有明确 kind/path 或来源
      标识、优先级、hash、截断状态，并与文件指令共享确定性的 `max_instruction_chars` 预算。
- [x] `AGENTS.override.md`、`AGENTS.md`、`CLAUDE.md` 的既有兼容语义保持不变；指令文件若为
      symlink、解析后越界或读取时被替换，不会被读取或持久化。
- [x] 新增回归测试覆盖：服务 cwd 项目配置泄漏、双 Workspace 隔离、API/Worker cwd 差异、
      全局 Registry 不变、进程/项目工具交集、配置指令注入与优先级、symlink 文件逃逸、
      symlink 目录逃逸、TOCTOU 可合理模拟路径以及快照恢复。
- [x] PostgreSQL Repository 与既有 nullable 历史快照兼容；本任务不执行迁移，并在任务结果
      和 `.workflow/state.yaml` 中保留 `20260810_0020` 需人工授权应用的说明。
- [x] 用户可见配置、架构、数据流和安全边界文档同步，过时事实被修正；README、访谈手册
      Part 与 facts 证据一致。
- [x] `.venv/bin/python -m pytest -q`、`.venv/bin/python -m compileall ai_agent_platform tests
      evals`、`.venv/bin/python INTERVIEW_NOTES/validate.py`、`.venv/bin/alembic heads` 和
      `git diff --check` 全部通过。

## Decisions

- Workspace catalog 与授权结果是项目配置根目录的唯一可信来源；用户消息、请求 cwd、focus
  path 和服务进程 cwd 都不能选择另一份项目配置。
- 推荐让 `ConfigResolver` 暴露“解析进程基线”和“在可信 Workspace root 上应用项目覆盖”
  两个显式入口，复用现有 Schema、来源追踪、安全收紧和脱敏逻辑，不复制一套解析器。
- 项目配置必须在 Run 入队前冻结。RunContext 保存恢复执行所需的有效值与 provenance/version；
  Worker 不依赖当前文件系统配置。
- 全局 Registry 是进程能力目录，不是项目状态容器。若为当前 Agent 执行增加过滤能力，应返回
  不修改源 Registry 的 Run-scoped 视图，并为后续阶段 7 留出清晰替换边界。
- 指令的确切优先级由实现先依据现有 AGENTS 语义制定并写入测试/文档；配置指令不得静默覆盖
  `AGENTS.override.md`。所有来源共同遵守一个确定性字符预算。
- 进程基线使用 `resolve_process()`；`resolve_workspace()` 只接受 Workspace catalog 已解析并
  鉴权的 root。`AI_AGENT_PLATFORM_PROJECT_CONFIG` 仅作为显式进程级受控输入保留；默认
  location discovery 不再读取服务 cwd 的项目 JSON。
- 指令优先级记录为 `AGENTS.override.md` 400、`AGENTS.md` 300、`CLAUDE.md` 200、配置指令
  100。文件指令保持根到目标目录的既有加载语义，并先消费共享字符预算；配置指令即使预算
  已耗尽也保留空文本、完整 hash 和 `truncated=true` 的来源元数据。
- RunContext schema 升级为 v2，新增确切工具名、选择 provenance/version 和指令 priority；
  加载器继续接受 v1 和 nullable 历史列。v2 恢复会核对配置 hash、工具选择 hash、冻结的
  `enabled_tools` 与进程 `tool_allowlist` 上限。
- 全局 Registry 只用进程 `tool_allowlist` 执行一次性能力裁剪；项目 `enabled_tools` 返回不
  拥有资源、不可注册、不会关闭或修改源 Registry 的 `ToolRegistryView`。Agent 规划、探索、
  原生调用和 Change Loop 都从 Run state 重建该视图。
- 指令读取使用 Workspace root 目录描述符与 no-follow `openat` 语义；文件读取前后核对
  inode/type/size/mtime/ctime，并复核已打开目录链，symlink、非普通文件和可模拟的替换均
  fail closed。
- 路径安全检查以可信 Workspace root 的真实路径为边界；仅调用 `is_file()` 不足以证明安全。
  实现应避免先校验路径、后通过可被替换的路径重新打开文件。
- 此任务完成后才进入 `UNIFIED-PERMISSION-CONTEXT`；不要为赶下一阶段留下兼容双路径。

## Verification

- `.venv/bin/python -m pytest -q`：308 passed、38 subtests passed；仅有既有 Starlette
  `httpx` 弃用警告。
- `.venv/bin/python -m pytest -q tests/test_config_resolver.py tests/test_execution_context.py
  tests/test_runtime_bootstrap.py tests/test_tool_execution.py`：42 passed、27 subtests passed，
  覆盖 cwd 泄漏、双 Workspace、工具上限/视图/非法恢复、配置指令预算、symlink/FIFO/
  TOCTOU、Worker cwd 差异和 v1/PostgreSQL 兼容。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 12 个 Markdown 文件和 33 项
  capability；evidence review 仅提示相对既有事实基线存在预期工作树变化。
- `.venv/bin/alembic heads`：通过，唯一 head 为 `20260810_0020`；没有执行 `upgrade`。
- `git diff --check`：通过。
- `bash -n scripts/start.sh`：通过；只做 shell 语法检查，没有启动服务或应用迁移。

## Result

- 进程启动改用不隐式发现 cwd 项目文件的基线解析；Run 创建在 Workspace 鉴权之后读取并
  安全冻结该 root 的项目 JSON。配置快照增加 schema、逐字段 source/detail 和内容版本，
  两个 Workspace 的有效值、配置指令和工具集合连续创建时相互隔离。
- 全局 `ToolRegistry` 只受进程 allowlist 约束。项目选择保存到 RunContext，并通过只读
  Run-scoped view 进入 Agent 探索、规划、原生工具调用和 Change Loop；未知、越权和恢复
  冲突均 fail closed，源 Registry 保持不变。
- `project_session.project_instructions` 已作为带虚拟来源路径、priority、完整 SHA-256 和
  截断状态的 `config_instruction` 进入 RunContext；Worker 只凭持久化 `run_id` 恢复配置、
  指令、工具和既有身份/会话/Workspace 语义，不读取变化后的文件、环境或 cwd。
- `AGENTS.override.md`/`AGENTS.md`/`CLAUDE.md` 保持原选择语义，读取改为 descriptor-anchored
  no-follow 边界并拒绝 symlink、路径逃逸、FIFO 等特殊文件和读取过程替换。
- README、`.env.example`、`INTERVIEW_NOTES.md`、Part 04/05/06/08 与 facts 已同步；过时的
  “项目 enabled_tools 在启动时裁剪全局 Registry”和“五层均在进程启动解析”表述已删除。
- 没有实现阶段 4 的完整 `PermissionContext`，也没有实现 Skill、MCP 生命周期或完整
  Effective Tool Pool；当前只提供维持既有本地工具行为所需的最小 Run-scoped view。
- 迁移状态：`20260810_0020` 仍仅为待应用 Alembic head，本任务没有执行迁移。启动脚本
  现在要求操作者显式传入 `--apply-migrations`；启动 PostgreSQL runtime 前仍必须审阅并
  人工授权。没有部署、提交、推送、创建 PR 或合并。
