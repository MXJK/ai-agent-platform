# QUERY-KERNEL: 抽出入口无关的 Query Kernel

## Goal

将 AgentRunService 的命令生命周期抽成稳定、可恢复、入口无关的 QueryService，同时保持现有 HTTP API 兼容。

## In scope

- 定义稳定的 `QueryParams`、`QueryCommand`、`AgentEvent` 和 `QueryResult` 契约。
- 将解析请求、建立冻结上下文、原子创建用户消息与 Run/快照、派发、事件记录、恢复和完成
  从 `AgentRunService` 抽到入口无关的 `QueryService`。
- 为 start、resume、continue、steer、pause 和 cancel 建立统一生命周期状态机。
- 让 HTTP SSE、轮询、CLI、SDK/领域调用共用同一个事件编码器和 EventStore。
- 保持 FastAPI 立即返回 202，并保留既有 API URL 与响应 Schema。
- 增加兼容性、事件 cursor 恢复、Worker 重复投递、最终消息幂等和控制命令状态机测试。
- 同步 README、访谈手册、facts 和必要的持久化迁移。

## Out of scope

- 改变 Agent Loop 的模型推理、工具语义或审批业务规则。
- 删除或重命名现有 HTTP API，或要求 HTTP 请求等待 Agent Loop 完成。
- 执行数据库迁移、部署、发布、提交、推送、PR 或合并。

## Acceptance criteria

- [x] `QueryParams` 覆盖 conversation、message、workspace、focus files、模型/模式覆盖和入口元数据。
- [x] `QueryCommand` 覆盖 start、resume、continue、steer、pause、cancel，并由单一状态机验证转换。
- [x] `AgentEvent` 稳定包含 sequence、run id、status、type、summary、output；`QueryResult` 表示终态结果与可恢复 cursor。
- [x] start 在派发前原子持久化用户消息、Run、模型/配置/上下文/工具快照。
- [x] `query(params)` 提供 `AsyncIterator[AgentEvent]`，FastAPI 仍立即返回 202 且不等待 Agent Loop。
- [x] HTTP SSE、轮询、CLI 和 SDK/领域调用复用相同事件编码器与 EventStore。
- [x] 助手最终消息仅记录一次；Worker 重投、恢复和客户端断线不会重复写入。
- [x] waiting_approval、waiting_input、paused 和所有终态使用同一生命周期状态机。
- [x] 路由只承担 HTTP 映射、认证和错误映射，旧 API URL 与响应 Schema 保持兼容。
- [x] 旧 API/QueryService 等价、cursor 恢复、重复投递与控制命令状态机测试通过。
- [ ] README、访谈手册与 facts 同步；全量 pytest、compileall、手册校验和 diff check 通过。

## Decisions

- `QueryService` 是唯一命令内核；`AgentRunService` 缩成继承自它的兼容名称，FastAPI 与
  Celery Worker 都调用 Query 契约，旧 app state 与导入名继续可用。
- start 通过内存或 PostgreSQL Query UoW 一次提交用户消息、queued Run/事件和完整
  `RunContextSnapshot`，再派发任务；内建运行时对不同后端组合 fail fast。
- `RuntimeEventStore` 与 `AgentEventEncoder` 是轮询、SSE 和异步迭代器的共同边界；HTTP
  编码时省略新增 run ID，保持原响应 Schema，领域事件仍携带 run ID。
- 最终助手消息使用确定性 message ID 和 `source_run_id + role` 部分唯一索引；正常完成、
  resume 和 Worker redelivery 共用同一个幂等写入路径。
- Run context 升级到 schema v2 以保存工具定义与入口元数据，加载器继续接受 schema v1。
- 新增 `20260813_0021` 迁移但不执行；迁移、部署、提交、推送和合并仍需人工授权。

## Verification

- PASS: `.venv/bin/python -m pytest -q` — `307 passed, 38 subtests passed in 8.33s`。
- PASS: `.venv/bin/python -m compileall ai_agent_platform tests evals`。
- PASS: 148 个 Python 文件使用 Python 3.10 grammar 解析。
- PASS: `.venv/bin/alembic heads` — `20260813_0021 (head)`，单一迁移头。
- PASS: `git diff --check`。
- PASS: 新增测试覆盖 HTTP 202/Schema 映射、原子回滚、异步事件迭代、cursor 恢复、共享编码器、
  最终消息/Worker 重投幂等、控制状态机、Worker canonical QueryService 和非原子存储组合拒绝。
- FAIL（既有手册基线）：`.venv/bin/python INTERVIEW_NOTES/validate.py` 报 18 个缺失证据路径，
  全部属于 `approval_change_loop`、`tool_registry`、`mcp_lifecycle`、`skill_discovery` 四个后续
  阶段 capability；没有 `query_kernel`、本阶段 Markdown 结构或链接错误。当前分支落后
  `origin/main` 10 个提交，而 `origin/main` 也不包含其中全部 MCP/ToolPool 证据，不能在本任务
  内安全地通过拉取解决。

## Result

Query Kernel 实现、测试、README 与本地忽略的访谈手册/facts 更新均已完成；没有发现密钥、
生成物或越界外部写入。由于仓库规定手册校验是必需检查，而共享手册包含当前代码分支尚未
具备的后续阶段“已实现”证据，本任务不能标记 done。需要人工决定使用包含这些阶段的正确
基线，或回滚/更正共享手册中的未来声明后，再重跑手册校验。迁移未执行，工作树未提交。
