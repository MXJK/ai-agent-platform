# TASK-DRIVEN-CODE-AGENT: 按需上下文代码 Agent

## Goal

移除代码仓库 RAG 索引链路，建立工作区注册与按任务搜索、读取原始文件的多轮代码 Agent 上下文循环，同时保留审批、Sandbox 修改、验证和 Diff 产物。

## In scope

- 新增 memory/PostgreSQL 工作区注册、查询和允许根路径校验。
- 将 Agent 公开契约从 `repository_id` 切换为 `workspace_id`。
- 实现最多四轮的实时文件搜索、行段读取、上下文预算与 AGENTS 指令加载。
- 保留现有变更审批、Sandbox、验证和有限修复闭环。
- 完整移除仓库索引运行时、API、Celery 任务、配置和前端入口。
- 保留独立知识库 RAG。
- 增加数据库迁移、测试、最小前端适配和文档。

## Out of scope

- 合并智能对话与代码 Agent 的整体界面。
- 删除独立知识库能力。
- 部署、发布或自动合并分支。

## Acceptance criteria

- [x] 未索引的新文件可被 Agent 通过实时搜索和读取用于回答。
- [x] Agent 不再依赖 RAGService、Embedding 或 repository index。
- [x] 工作区根路径受允许范围、路径穿越和符号链接边界保护。
- [x] Agent 最多探索四轮、读取十二个文件，并遵守上下文字符预算。
- [x] AGENTS 指令链按目录作用域加载。
- [x] 写操作继续经过审批、Sandbox、验证和 Diff 产物闭环。
- [x] 仓库索引 API、任务、配置和页面被移除；知识库 API 保持兼容。
- [x] PostgreSQL 迁移保留工作区与 Agent 历史关联并删除索引元数据。
- [x] 项目规定验证命令、JavaScript 语法检查和 diff 检查通过。

## Decisions

- 新分支从 `main` 创建，分支名为 `codex/task-driven-code-agent`。
- 使用注册式 workspace 契约，不接受旧 `repository_id` 别名。
- 工作区 API 使用 `PUT/GET /api/v1/workspaces`，v1 不提供删除。
- 默认允许根目录为服务启动目录；Celery 模式要求 PostgreSQL workspace store。
- 上下文预算为四轮、每轮六个只读工具、十二个文件、32,000 字符证据和 16,000 字符项目指令。
- 本分支只做删除索引入口和接入 workspace 的最小前端改动。
- `repo.*` 工具名保持稳定，但根路径改由每个 run 的
  `ToolExecutionContext.workspace_root` 动态提供。
- Agent run 在排队时持久化规范化 `workspace_root`；Worker 忽略任务载荷中的
  路径并从 run 记录读取快照。
- PostgreSQL 仅在历史结果反序列化边界兼容旧
  `repository_id/rag_context`，公开 Schema 使用 `extra=forbid`。
- 解决与 `main@7f13665` 的合并冲突时，保留 Gemini 思考等级、SSE
  心跳/截断处理和统一 Chat/Agent composer；统一入口改为稳定的
  `workspace_id/context_sources` 契约，不恢复 repository index 或代码 RAG。
- 最近六条、最多 1,800 字符的会话历史同时进入结构化工具规划和规则兜底的
  工作区搜索查询。

## Verification

- `.venv/bin/python -m pytest -q`：通过，73 passed（1 个第三方
  LangGraph pending deprecation warning）。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- `.venv/bin/python evals/run_evals.py`：通过，4/4；Recall@5=1.000，
  MRR=1.000。
- Alembic：在隔离临时 PostgreSQL 数据库从空库升级到
  `20260723_0006`；随后降级到 `20260720_0005`，写入历史
  repository/run/index 数据并再次升级。确认 `workspaces` 根路径及
  `agent_runs.workspace_id/workspace_root` 回填保留，两个索引表被删除。
  临时数据库已删除。
- 合并 `main@7f13665` 并解决六个冲突文件后：
  - `.venv/bin/python -m pytest -q`：通过，89 passed（1 个第三方
    LangGraph pending deprecation warning）。
  - `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
  - Python 3.11 `compileall`：通过。
  - `node --check ai_agent_platform/static/app.js`：通过。
  - `git diff --check`：通过。
  - `.venv/bin/python evals/run_evals.py`：通过，4/4；Recall@5=1.000，
    MRR=1.000。

## Result

完成。代码 Agent 现在只针对具体任务搜索并读取实时工作区文件，不创建代码
Embedding 或向量索引；独立知识库 RAG、审批、Sandbox 验证和 Diff 闭环保持
可用。与 `main` 的 Gemini 流式和统一 Chat/Agent 界面改动已合并，统一入口
直接使用 `workspace_id` 与 `context_sources`，PR #5 可重新检查并合并。
