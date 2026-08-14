# SHELL-ADAPTERS: CLI、REPL、SDK 与最终入口收敛

## Goal

在稳定的 `RuntimeContainer`、`RunContext`、Tool Pool 和 `QueryService` 之上增加薄入口，
使 Web、Celery worker、CLI print、交互式 REPL 和 Python SDK 共享同一依赖装配、
`AgentEvent` 事件流、`QueryResult` 结果与 Run 生命周期语义。

## In scope

- 提供只负责 uvicorn 启动、HTTP 适配和 FastAPI lifespan 的 API entrypoint。
- 通过 Celery worker 进程生命周期钩子创建和关闭 `RuntimeContainer`。
- 提供正式 CLI `main()`：启动性能检查点、安全环境检查、warning/exit/SIGINT、参数解析。
- print mode 提交一个 Query，并逐条输出 Query Kernel 的同一 `AgentEvent` 流。
- REPL 支持多轮会话、Ctrl+C 取消当前 Run、显式退出。
- 支持 `/skills`、`/tools`、`/mcp`、`/permissions`、`/resume`、`/exit`。
- 提供 `query`、`resume`、`control` 的 Python SDK facade，返回相同 `AgentEvent` / `QueryResult`。
- 在 `pyproject.toml` 注册正式 console script。
- 用同一 fake provider 场景验证 Web、CLI print、REPL 和 SDK 的 Run 终态和事件 Schema 一致。
- 同步 README 与模块化面试手册及事实证据。

## Out of scope

- 复制或重写 Agent、Query、权限、Tool Pool、MCP、Skill 或持久化业务逻辑。
- 改变现有 HTTP API、Query 生命周期、事件字段或结果契约。
- 在可导入 SDK、Service 或领域模型中安装进程信号处理器。
- 部署、迁移、发布或外部写入。

## Acceptance criteria

- [x] API entrypoint 仅组装 FastAPI/uvicorn 和 lifespan，并复用 `RuntimeContainer`。
- [x] Celery worker 由生命周期钩子创建/释放同一个进程本地容器，不自行重组依赖。
- [x] CLI `main()` 覆盖启动检查点、安全环境、warning/exit/SIGINT 和参数解析。
- [x] print mode 输出 Query Kernel 产生的 `AgentEvent` 流并返回稳定退出码。
- [x] REPL 支持同一会话多轮 Query、Ctrl+C 取消当前 Run 和显式退出。
- [x] slash commands 至少包含 `/skills`、`/tools`、`/mcp`、`/permissions`、`/resume`、`/exit`。
- [x] SDK `query`、`resume`、`control` 返回统一 `AgentEvent` / `QueryResult` 契约。
- [x] `pyproject.toml` 注册可安装的正式 console script。
- [x] E2E 证明 Web、CLI print、REPL 和 SDK 的 fake-provider Run 终态与事件 Schema 一致。
- [x] 信号处理仅存在于拥有进程生命周期的入口，库代码可安全导入。
- [x] README、访谈手册与事实证据同步，完整验证通过。

## Decisions

- 保留 `ai_agent_platform.main:create_app/app` 兼容入口，新增
  `ai_agent_platform.api.entrypoint` 作为正式 uvicorn 进程入口；HTTP adapter 继续只做
  router/state/lifespan，不向内复制 Query 或 Agent 逻辑。
- `AgentSDK` 直接返回领域层 `AgentEvent`、`QueryResult`；注入容器时不拥有其生命周期，
  仅 `from_settings()` 创建的 facade 在 `close()` 时关闭容器。
- CLI stdout 对 Run 只输出 `AgentEventEncoder` JSON Lines；warning、提示和启动时间进入
  stderr，便于脚本稳定消费事件 Schema。
- CLI 在构建容器前规范化 Workspace root 并执行进程 allowlist 硬校验；Run 创建仍由
  WorkspaceService、ExecutionContextFactory 与 QueryService 完成完整鉴权和快照冻结。
- SIGINT handler 只安装在 `cli.main()` 启动的进程范围；REPL adapter 使用可注入中断控制器，
  SDK/Service/domain 不依赖 `signal`。
- Celery 在 `worker_process_init`（fork 后）取得 RuntimeContainer 单例，并在
  `worker_process_shutdown` 幂等关闭；保留 lazy getter 作为 task/测试兼容防线。

## Verification

- `.venv/bin/python -m pytest -q`
- `.venv/bin/python -m compileall ai_agent_platform tests evals`
- `.venv/bin/python INTERVIEW_NOTES/validate.py`
- `git diff --check`

## Result

已完成：

- 新增 FastAPI/uvicorn 正式入口，启动脚本改用该 ASGI target；未认证绑定继续 fail closed。
- 新增 CLI print/REPL、启动时间线、安全 Workspace 校验、稳定退出码和活动 Run SIGINT 取消。
- REPL 保持单一 conversation，并提供六个必需 slash commands。
- 新增 import-safe `AgentSDK`，query/resume/events 返回 `AgentEvent` iterator，control/result
  返回 `QueryResult`。
- Celery worker 生命周期钩子在每个 worker 进程创建/关闭统一容器，task handler 保持薄委派。
- `pyproject.toml` 注册 `ai-agent`、`ai-agent-platform` 和 `ai-agent-api`，并提供
  `python -m ai_agent_platform` 兼容入口。
- E2E 使用同一 fake provider 和工作区用例，证明 Web、CLI print、REPL、SDK 均到达
  `completed`，事件类型序列与七字段 canonical Schema 一致；另有测试覆盖 Ctrl+C、
  slash command、SDK resume/control、API 安全绑定和入口清理。
- 文档影响：README/README.en、Part 06、手册总入口与 `facts.json` 已同步；新增
  `shell_adapters` capability，不保留“CLI 仍是 future role”的旧说法。

验证结果：

- `.venv/bin/python -m pytest -q`：367 passed，47 subtests passed。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：12 个 Markdown、36 项 capability 通过。
- `git diff --check`：通过。
- `python -m ai_agent_platform --help` 与
  `python -m ai_agent_platform.api.entrypoint --help`：通过。

未执行迁移、部署、外部写入或 Git 提交。当前工作树还包含上一阶段已验证但未提交的
`AGENT-LOOP-DECOMPOSITION` 改动；本阶段在其上验证通过，未覆盖或暂存该范围。
