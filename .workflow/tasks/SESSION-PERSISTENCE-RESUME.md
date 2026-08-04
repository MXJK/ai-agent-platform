# SESSION-PERSISTENCE-RESUME: 配置持久化与 Codex 式会话续聊

## Goal

让本地 Agent 平台在服务和浏览器重新打开后恢复用户默认配置、最后活跃会话与
完整消息历史，并提供可搜索、重命名、归档和恢复的 Codex 式会话入口。

## In scope

- PostgreSQL 持久化用户默认配置、会话元数据、最后活跃会话和消息历史。
- 会话级模型、思考等级、工作区与 Composer 模式配置。
- 会话标题、最近活跃排序、搜索、游标分页、归档和恢复。
- Chat 与代码 Agent 统一解析会话配置并复用历史上下文。
- 最近会话侧栏、历史管理页、启动自动恢复和临时存储模式提示。
- 内存/PostgreSQL 仓储、API、前端、迁移、测试和文档同步。

## Out of scope

- API Key、数据库凭据或完整环境变量的前端/数据库管理。
- 会话删除、置顶、标签、文件夹和批量操作。
- 模型注册中心、在线价格同步、后台摘要/记忆任务的会话级选模。
- 部署、发布、迁移执行、推送或真实付费模型调用。

## Acceptance criteria

- [x] 新会话复制用户默认配置，设置可同步到当前会话和以后新会话。
- [x] 会话消息、标题、配置、归档状态和最后活跃会话在 PostgreSQL 重启后保留。
- [x] 会话按最近活跃排序，支持搜索、游标分页、重命名、归档和恢复。
- [x] 首条用户消息生成确定性标题，手工标题不被覆盖。
- [x] 启动按 URL、最后活跃、最近会话、欢迎页顺序恢复。
- [x] Chat 与代码 Agent 使用“请求覆盖 > 会话配置 > 服务端默认”的配置优先级。
- [x] 归档会话拒绝继续 Chat/Agent，内存模式明确提示重启会丢失。
- [x] 会话/偏好链路不接收或保存密钥、数据库地址和允许路径。
- [x] README、Interview Notes 与事实索引同步。
- [x] pytest、compileall、前端语法、迁移和 Interview Notes 校验通过。

## Decisions

- PostgreSQL 是持久化运行的事实来源；内存实现只用于测试和显式临时模式。
- 用户默认配置与会话配置分层；保存当前会话设置时同时更新以后新会话默认值。
- 模型名称和 Provider 可保存，API Key、数据库地址和允许路径仍只保留在 `.env`。
- 自动标题来自首条用户消息前 48 个 Unicode 字符，不增加模型调用。
- 归档是首版唯一隐藏方式；已归档会话只读，恢复后才能继续对话。
- 保留未完成的 `PRODUCTIZE-MODEL-REGISTRY` 任务文件，但本任务不实现其密钥管理和
  动态模型注册范围；最新用户请求作为当前任务来源。

## Verification

- `.venv/bin/python -m pytest -q`：203 passed；仅有已知 Starlette/httpx 弃用警告。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/alembic heads`：单一 head `20260804_0014`。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，12 个 Markdown、28 个能力。
- `git diff --check`：通过。
- 浏览器冒烟：1280×720 与 390×844；验证新建、最近列表、URL 刷新恢复、
  active/archive 筛选、归档只读和恢复；控制台无错误。

## Result

- 新增 PostgreSQL 迁移、用户偏好与会话元数据/配置模型；内存和 PostgreSQL 仓储
  共享标题、排序、搜索、游标、归档与配置复制契约。
- 会话 API 返回列表摘要与游标，支持 PATCH 会话和用户偏好；Chat/Agent 统一解析
  配置，归档前置拒绝，Agent run 持久化不可变模型选择快照。
- 前端增加最近会话、历史筛选/加载更多、重命名/归档/恢复、确定性启动恢复、
  URL/最后活跃同步与归档只读；移除 Composer 配置的 localStorage 副本。
- README、Interview Notes Part 02/06/07 与 facts.json 已同步。
- 工作区同时存在另一个未完成的模型注册中心改动，其前端 API Key → OS keyring
  能力未由本任务创建或移除；本任务保证密钥不会进入 sessions、user_preferences
  或 localStorage，但不把该并发功能表述成 `.env-only`。
