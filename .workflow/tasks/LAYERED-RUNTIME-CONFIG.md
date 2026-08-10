# LAYERED-RUNTIME-CONFIG: 分层运行时配置解析与安全来源追踪

## Goal

在保留 Settings.from_env() 兼容入口的前提下，实现 ConfigResolver 与不可变 ResolvedConfig，按默认值、用户配置、项目配置、环境变量和显式覆盖合并并执行安全约束、来源追踪、Schema 校验与脱敏。

## In scope

- 新增无第三方大型依赖的 JSON 分层配置读取器，优先级固定为默认值、用户配置、
  项目配置、环境变量、显式入口覆盖。
- 新增不可变 `ResolvedConfig`，保留最终 `Settings` 兼容视图，并按
  Process/Security、Runtime、Project/Session 分区暴露值与逐字段来源。
- 为用户文件、项目文件、环境变量、显式覆盖提供独立边界校验、类型转换和未知字段错误。
- 项目文件禁止改写进程/安全字段或关闭强制 Sandbox；项目权限字段只能相对进程策略收紧。
- 配置诊断、日志安全视图与 Run 快照复用统一的密钥和连接串脱敏策略。
- 保留全部既有环境变量、`.env` 读取及兼容回退（如 `GEMINI_API_KEY`、
  `SESSION_REPOSITORY`/`AGENT_RUN_STORE` 的旧级联语义）。
- 同步 README、访谈手册相关 Part 和 facts 证据。

## Out of scope

- 改变现有 HTTP API、数据库 Schema、Agent Graph、审批/恢复协议或工具执行行为。
- 新增 YAML/TOML 依赖、远程配置中心、热重载、密钥轮换或管理端配置写入 API。
- 部署、迁移、外部写入、提交、推送、PR 或合并。

## Acceptance criteria

- [x] `ConfigResolver.resolve()` 以固定五层优先级生成冻结的 `ResolvedConfig`；
      每个 `Settings` 字段都有最终来源记录。
- [x] `ResolvedConfig` 清晰暴露 Process/Security、Runtime、Project/Session 三个冻结分区，
      且 `Settings.from_env()` 继续返回兼容的 `Settings`。
- [x] 用户配置、项目配置、环境变量和显式覆盖分别执行 Schema/类型校验；配置文件与
      显式覆盖的未知字段报错，相关错误不回显密钥完整值。
- [x] 项目配置无法修改数据库、认证、密钥后端、允许根目录、真实写入开关等
      Process/Security 字段，也无法关闭进程强制 Sandbox。
- [x] 项目工具、Skill、MCP 等权限集合只能做交集收紧，进程级禁用不能被项目配置放宽。
- [x] `ResolvedConfig` 的诊断/快照输出、异常和日志安全视图不包含 API key、信任密钥、
      Qdrant 密钥、含口令连接串等秘密的完整值。
- [x] 新增优先级、逐字段来源、非法安全覆盖、权限收紧、脱敏、未知字段、类型错误和
      旧环境变量兼容测试。
- [x] Python 3.10 语法/运行兼容，不为简单配置读取新增大型依赖。
- [x] README、访谈手册相关 Part 与 facts 同步；全量 pytest、compileall、手册校验和
      `git diff --check` 通过。

## Decisions

- 配置文件首版使用标准库 JSON；根对象以 `process_security`、`runtime`、
  `project_session` 分区，避免平铺字段造成权限边界含糊。
- `Settings` 继续作为现有运行时代码的兼容值对象；`ResolvedConfig` 负责分区、来源和
  安全诊断，不要求本任务迁移所有现有调用点。
- 字段 Schema 直接从冻结 `Settings` dataclass 及显式分区集合派生；JSON 原生值按字段
  默认类型转换，tuple 权限字段只接受字符串数组，避免引入 Pydantic/YAML/TOML 读取层。
- 环境层支持既有无前缀变量与新的 `AI_AGENT_PLATFORM_<FIELD>` 命名空间；后者未知名
  fail fast。`GOOGLE_API_KEY` 优先于 `GEMINI_API_KEY`，模型注册与 ChangeSet store
  保留原有存储变量回退。
- Process/Security 持有数据库/存储、认证、密钥、根目录、真实写入、MCP 路径、Docker
  镜像和进程 allowlist；项目层只能把 local sandbox 收紧为 Docker、增加审批、缩小
  命令/工具/Skill 选择，并受 `mcp_allowed`/`skills_allowed` deny 约束。
- `ResolvedConfig.safe_snapshot()` 是日志、Run 快照和诊断的唯一支持序列化视图；
  Settings repr 隐藏凭据/连接字段，JSON 日志对嵌套 mapping/list 再递归脱敏。
- `build_runtime()` 接受 `Settings` 或 `ResolvedConfig`，API/Worker 容器保留不可变解析
  结果和安全快照；有效工具选择在装配时不可逆裁剪 Tool Registry，默认 `None` 保持
  原工具行为不变。

## Verification

- `.venv/bin/python -m pytest -q`：291 passed、38 subtests passed；仅有既有的
  Starlette `httpx` 弃用警告。
- `.venv/bin/python -m pytest -q tests/test_config.py tests/test_config_resolver.py
  tests/test_observability.py tests/test_runtime_bootstrap.py tests/test_tool_execution.py`：
  52 passed、31 subtests passed，覆盖五层优先级、全部 131 个字段来源、冻结边界、
  JSON/.env/显式 Schema、项目安全拒绝、权限收紧、工具裁剪、递归脱敏和旧环境变量。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- 使用当前 Python 的 `ast.parse(..., feature_version=(3, 10))` 解析
  `ai_agent_platform`、`tests`、`evals` 共 140 个 Python 文件：通过；系统未安装独立
  `python3.10` 可执行文件。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 12 个 Markdown 文件和
  32 项 capability；evidence review 仅提示相对最近验证提交存在预期工作树变更。
- `git diff --check`：通过。

## Result

- 新增标准库 JSON `ConfigResolver`、五层确定性合并、不可变 `ResolvedConfig`、三个冻结
  分区、`ConfigSource`/逐字段 provenance 和显式入口覆盖；`Settings.from_env()` 继续
  返回兼容 `Settings`。
- 项目配置对 Process/Security 字段 fail closed；Sandbox、审批、命令、工具、Skill、
  MCP 只允许收紧，进程 deny 不可放宽，工具选择已接入实际 Tool Registry。
- API/Worker 真实启动路径保存安全配置快照；API key、Qdrant key、信任密钥和连接串
  凭据不会进入支持的诊断/Run 快照，结构化日志可递归遮蔽嵌套秘密字段。
- 中英文 README、`.env.example`、访谈手册入口、Part 05/06/08 与 facts 已同步。
  没有新增第三方依赖、数据库迁移、部署、PR 或 merge；提交与推送由用户在完成验证后
  单独明确授权。
- 已验证功能提交为 `2784da26031bd43040e4c090ae95c90cec7aae71`；workflow 的
  `last_verified_commit` 已指向该提交，后续收尾提交只记录任务元数据。
