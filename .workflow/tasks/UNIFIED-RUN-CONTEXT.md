# UNIFIED-RUN-CONTEXT: 统一、可恢复的 Run 上下文快照

## Goal

实现 `ExecutionContextFactory`，在 Run 入队前建立可序列化、深度不可变、可持久化恢复的
`RunContextSnapshot`，作为 API、Worker、未来 CLI 与 Agent Loop 的统一执行输入。

## In scope

- 冻结 actor、认证模式和主/附加 Workspace 角色。
- 冻结 conversation、受控历史、摘要和模型选择。
- 冻结 Workspace ID/root/revision、cwd、Git 状态摘要和安全配置快照。
- 在提交时按既有优先级捕获 `AGENTS.override.md` / `AGENTS.md`，并以最低回退优先级
  兼容 `CLAUDE.md`。
- 额外目录只接受已登记 Workspace ID；所有真实路径、路径逃逸和符号链接都在入队前验证。
- 将快照写入 Agent Run store，Worker 仅凭持久化 Run ID 恢复执行语义。
- Git 缺失、非仓库、无提交或状态探测失败仅产生诊断，不阻断 Run。
- 同步数据库迁移、API Schema、README 和访谈手册。

## Out of scope

- 扩展 Agent 工具使其跨 Workspace 读写额外目录。
- 修改 AGENTS 既有覆盖优先级、LangGraph checkpoint 协议或 ChangeSet 应用语义。
- 执行数据库迁移、部署、提交、推送、PR 或合并。

## Acceptance criteria

- [x] 快照包含 Identity、Session、Project、Instruction、AdditionalDirectories 和 Run metadata。
- [x] 快照可 JSON 往返、深度不可变，且支持明确的 Schema/配置版本。
- [x] Agent Run 的内存与 PostgreSQL 存储均持久化快照；新 Worker 可按 `run_id` 恢复。
- [x] Agent Loop 使用持久化历史、actor、模型选择、Workspace 和指令内容，不在 Worker
      执行时重新解析可变来源。
- [x] 额外目录不能用任意路径注入，未登记、未授权、跨用户、`..` 和符号链接逃逸均拒绝。
- [x] Git 不可用或非仓库时保留稳定诊断，不导致 Run 无条件失败。
- [x] 快照中的配置密钥、认证秘密和连接凭据保持脱敏。
- [x] 新增跨用户、路径逃逸、符号链接、Worker 恢复、Git 降级、序列化/不可变和脱敏测试。
- [x] README、访谈手册与 facts 同步；全量 pytest、compileall、手册校验和 diff check 通过。

## Decisions

- `RunContextSnapshot` 放在 domain 层，由只含冻结 dataclass、tuple 和规范化 JSON 字符串
  的值对象组成；`to_dict()` 每次返回隔离副本，`from_dict()` 按显式 schema version
  恢复，不查询任何可变外部状态。
- `ExecutionContextFactory` 是 API、Worker、预留 CLI 与 Agent Loop 共用边界；真实运行时
  根据 `role` 装配同一工厂，并使用原始 `ResolvedConfig.safe_snapshot()` 生成配置版本。
- 主目录和额外目录都先通过 Workspace catalog 解析；额外目录 API 只接收 ID。可信认证
  模式逐个要求 viewer 及以上角色；本机 `AUTH_MODE=disabled` 保持既有本地 admin 语义。
- `cwd` 和显式 focus path 解析真实路径后必须位于主 Workspace。用户文本推断出的路径若
  越界只不参与指令作用域，不能把普通消息变成路径验证错误；工具执行仍重复校验边界。
- 项目指令在提交时冻结。每层优先级保持 `AGENTS.override.md` > `AGENTS.md`；只有两者都
  缺失才选择 `CLAUDE.md`，避免兼容读取改变现有 AGENTS 语义。
- Git 仅捕获 HEAD、branch 和有界 dirty 摘要；命令缺失、非仓库、无 HEAD、timeout 或
  status 失败转为稳定诊断。快照不把 workspace 文件内容当作源码副本。
- 新 Run 的 Agent 启动任务只发布 `run_id`。历史兼容路径仍接受旧队列载荷；有快照时
  Worker 无条件以持久化值覆盖队列中的历史、路径、actor 和模型。
- PostgreSQL 新列保持 nullable，以便历史 Run 继续由旧存储边界加载；新 Run 总是写入
  schema v1 快照。迁移文件已生成但未执行，遵守本任务不做外部迁移的范围。

## Verification

- `.venv/bin/python -m pytest -q`：299 passed、38 subtests passed；仅有既有 Starlette
  `httpx` 弃用警告。
- `.venv/bin/python -m pytest -q tests/test_execution_context.py
  tests/test_runtime_bootstrap.py`：15 passed，覆盖 JSON 往返/深度不可变、摘要与模型、
  配置脱敏、AGENTS/CLAUDE 优先级、跨用户与 Workspace role、任意路径/`..`/symlink、
  Git 正常/缺失/非仓库、PostgreSQL 往返、新 Worker 只凭 Run ID 恢复和 CLI 装配。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- 使用当前 Python 的 `ast.parse(..., feature_version=(3, 10))` 解析 143 个 Python 文件：
  通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 12 个 Markdown 文件和
  33 项 capability；evidence review 仅提示相对既有事实基线的预期工作树变化。
- `.venv/bin/alembic heads`：唯一 head 为 `20260810_0020`。
- `git diff --check`：通过。

## Result

- 新增统一快照领域契约和 `ExecutionContextFactory`，完整捕获用户身份/认证与角色、受控
  会话历史和摘要、模型选择、Workspace revision/root/cwd、Git 摘要、项目配置、指令、
  已授权额外目录以及 Run ID/时间/入口/版本。
- Agent Run 内存与 PostgreSQL store 保留同一快照；API/CLI 提交后 Celery 只需 Run ID，
  Worker 重启不会继承变化后的会话偏好、指令文件或进程内模型缓存，Agent Loop 直接消费
  已冻结内容。
- 新 API 字段 `cwd` 与 `additional_workspace_ids` 均受注册目录、角色和真实路径边界保护；
  Git 不可用或非仓库不会阻断 Run，敏感配置不会进入快照明文。
- 增加 `20260810_0020` 迁移和专项安全/恢复测试，并同步中英文 README、访谈手册 Part
  00/04/06 与 facts。未执行迁移、部署、提交、推送、PR 或 merge。
