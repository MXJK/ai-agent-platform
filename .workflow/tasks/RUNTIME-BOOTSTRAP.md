# RUNTIME-BOOTSTRAP: 统一应用运行时装配与生命周期

## Goal

以可测试的 `RuntimeContainer` / `ApplicationFactory` 取代 API 与 Celery Worker
重复的运行时装配代码，使两种进程角色共享同一依赖图、启动检查点和资源关闭语义，同时保持
现有 HTTP API、持久化记录、SSE、审批/恢复、测试注入与 Agent Graph 行为兼容。

## In scope

- 新增 `build_runtime(settings, role=api|worker|cli)`，统一创建 Repository、LLM、
  Model Registry、Workspace、RAG、MCP、Tool Registry、Checkpointer、
  `CodingAgentRuntime` 和各 Service。
- 新增显式持有服务与资源的 `RuntimeContainer`，提供线程安全、幂等、逆序关闭的
  `close()`，并在部分启动失败时回滚已创建资源。
- 新增可注入测试替身的 `ApplicationFactory`，由 API `create_app()` 和 Worker 单例共同使用。
- 记录 `config_loaded`、`stores_ready`、`mcp_ready`、`tools_ready`、`agent_ready`
  启动时间线检查点。
- 保留 `create_app()` 现有 `settings`、`llm_client`、`rag_service`、
  `coding_agent_runtime`、`directory_picker` 注入能力。
- 保留 `LazyASGIApp`、FastAPI lifespan、Celery Worker 进程内单例语义。
- 增加 API/Worker 装配一致性、资源只关闭一次、部分启动失败回滚与测试替身注入测试。
- 同步中英文 README、访谈手册相关 Part 与 facts 证据。

## Out of scope

- 新增 CLI 命令或 CLI 用户界面。
- 改变环境变量、配置文件或默认值的优先级。
- 改变 Agent Graph 节点、边、审批策略、恢复协议或执行语义。
- 改变 HTTP 路由、请求/响应模型、SSE 事件或数据库记录格式。
- 数据库迁移、部署、外部写入、提交、推送、PR 或合并。

## Acceptance criteria

- [x] `build_runtime(settings, role=api|worker|cli)` 是 API 与 Worker 的唯一核心装配入口；
      Worker 不再从 `main.py` 导入私有 `_create*` 函数。
- [x] API 与 Worker 从同一工厂得到一致的 Repository、LLM、Model Registry、
      Workspace、RAG、MCP、Tool Registry、Checkpointer、Agent Runtime 与 Service 依赖图。
- [x] `RuntimeContainer` 显式暴露运行时服务/资源；`close()` 幂等且严格按登记逆序关闭，
      每个资源最多关闭一次。
- [x] 任一装配阶段抛错时，已创建资源自动按逆序回滚，原始启动异常继续向上传播。
- [x] 启动时间线按序包含 `config_loaded`、`stores_ready`、`mcp_ready`、
      `tools_ready`、`agent_ready`。
- [x] `create_app()` 现有测试替身注入、`LazyASGIApp` 和 FastAPI lifespan 行为保持兼容。
- [x] Celery Worker 保持并发安全的进程内单例，并通过同一容器幂等关闭。
- [x] 现有 HTTP API、数据库记录、SSE 事件、审批/恢复、配置优先级和 Agent Graph 无变化。
- [x] 新增装配一致性、关闭一次、失败回滚和替身注入自动化测试。
- [x] README 中英文版、访谈手册相关 Part 与 facts 证据同步并通过校验。
- [x] 全量 pytest、compileall、前端语法、Go gateway、文档校验和 `git diff --check` 通过。

## Decisions

- 将 `ai_agent_platform/runtime.py` 作为唯一 composition root；`main.py` 只负责
  FastAPI 路由/静态资源接线，`workers/runtime.py` 只负责进程单例边界。
- `ApplicationFactory` 用可覆写的组件构造方法提供测试缝；顶层 `build_runtime()`
  保持生产默认值，并继续接收现有 LLM、RAG、Coding runtime、目录选择器注入。
- `RuntimeContainer` 在组件成功创建后立即登记 cleanup；正常关闭和异常回滚复用同一
  cleanup stack。回调错误会被记录且不阻断其余资源清理，容器本身只消费栈一次。
- 关闭登记顺序按资源创建顺序：任务队列、MCP provider、Tool Registry、checkpointer、
  AgentRunService；消费时逆序，避免先关闭仍被上层服务依赖的底层资源。
- 启动时间线保存在容器并同时暴露为 `app.state.startup_timeline`；只记录装配阶段，
  不新增 HTTP 端点或持久化表。
- `worker` 角色继续要求 Celery 配置；`cli` 仅允许工厂构建，不增加 CLI 命令。
- PostgreSQL checkpointer 在自身 `setup()` 失败时立即关闭刚创建的连接池，再由容器
  回滚更早资源，保证原始异常不被资源泄漏掩盖。

## Verification

- `.venv/bin/python -m pytest -q`：277 passed，11 subtests passed；仅有既有的
  Starlette `httpx` 弃用警告。
- `.venv/bin/python -m pytest -q tests/test_runtime_bootstrap.py`：6 passed，覆盖
  API/Worker 同工厂与同构依赖图、五阶段时间线、CLI 预留角色、逆序关闭一次、
  部分失败回滚、现有替身注入和 Worker 单例。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `go test ./gateway/...`：通过；系统 PATH 无 Go，使用 SHA-256
  `efb87ff28af9a188d0536ef5d42e63dd52ba8263cd7344a993cc48dd11dedb6a`
  校验通过的 Go 1.26.5 darwin/arm64 官方临时工具链执行，未安装到系统或修改仓库。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 12 个 Markdown 文件和
  31 项 capability；evidence review 仅提示相对最近验证提交存在预期工作树变更。
- `git diff --check`：通过。

## Result

- 新增统一 `ApplicationFactory`、`build_runtime()` 与显式 `RuntimeContainer`；API、
  Worker 和未来 CLI 角色共享 Repository、LLM、模型注册中心、Workspace、RAG、MCP、
  Tool Registry、checkpointer、Agent runtime 和业务 Service 依赖图。
- FastAPI lifespan 与 Worker shutdown 统一使用线程安全幂等的逆序 `close()`；部分启动
  失败自动回滚已登记资源，checkpointer 自身初始化失败也会释放连接池。
- `create_app()` 原有四类测试注入、`LazyASGIApp`、app.state 服务入口与 Worker 进程
  单例保留；新增容器和启动时间线状态，不改变 HTTP API、数据库记录、SSE、审批/
  恢复、配置优先级或 Agent Graph。
- README 中英文版、访谈手册入口、Part 06 与 facts 已同步。没有数据库迁移、迁移执行、
  部署或外部写入；未 commit/push/PR/merge，`last_verified_commit` 保持不变。
