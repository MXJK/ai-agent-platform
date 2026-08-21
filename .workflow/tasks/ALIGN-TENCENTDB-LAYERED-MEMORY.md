# ALIGN-TENCENTDB-LAYERED-MEMORY: 对齐 TencentDB 分层记忆流水线

## Goal

让本项目的本地与 Docker 运行路径具备可用的 TencentDB 风格分层记忆：L1 自动生效、
L0 可可靠搜索中文历史消息，并从已生效 L1 自动沉淀 L2 场景和 L3 个人画像。

## In scope

- 将项目记忆默认模式改为 `auto`，自动提炼且通过安全校验的 L1 直接生效。
- 修复 SQLite L0 中文索引/查询分词不一致，并迁移既有索引。
- 为 Docker 使用的 PostgreSQL Session Repository 实现按用户、Workspace、Session 隔离的 L0 搜索。
- 持久化 L2 场景快照，并在 L1 创建、确认、更新、删除或自动提炼后重建 L2/L3。
- 让 Docker 默认启用 L1→L2→L3 流水线，并更新记忆工作台展示。
- 同步 README、面试手册和事实证据映射。

## Out of scope

- 逐行移植 TencentDB 的 TypeScript MemoryCore、COS/VDB、Jieba WASM 或 Memory Hub。
- 引入新的外部模型、凭据或云服务。
- 删除已有会话、记忆或 Docker volume。
- 提交、发布或部署到外部环境。

## Acceptance criteria

- [x] `auto` 模式下所有通过阈值与安全校验的自动 L1 均为 `active`，既有 review 模式仍可显式选择。
- [x] SQLite 中中文关键词可以命中既有和新写入消息；英文、用户隔离、Workspace/Session 过滤保持正确。
- [x] PostgreSQL Session Repository 支持同一套 L0 搜索契约。
- [x] active L1 会按用户和 Workspace 生成/更新 L2 场景，并自动重建跨 Workspace 的 L3 画像。
- [x] L1 更新、确认、删除会刷新下游 L2/L3；没有 active L1 时移除对应场景内容。
- [x] Docker 默认启用项目自动记忆和用户画像，容器重建后健康检查与 L0/L1/L2/L3 冒烟通过。
- [x] 聚焦测试、全量测试、compileall、文档校验与 diff 检查通过。

## Decisions

- 保留 `review` 作为显式治理选项；Docker 和 local profile 默认 `auto`。auto 仍执行候选
  阈值、敏感信息和 Prompt Injection 校验，但不再要求人工确认或额外 authority 门槛。
- SQLite L0 使用依赖无关的 CJK 1–4 gram 写入索引与同源查询切分；schema v2 会原地重建
  既有 FTS，不复制消息事实。Docker 的 PostgreSQL L0 使用转义 ILIKE 并支持空查询列出
  最近消息。
- L2 使用 `user_memory_scenes` 按 user/workspace 持久化确定性场景，保留所有 source L1
  IDs；L3 在字符预算内纳入 L2，超长场景按预算截断而不是整段丢弃。
- L1 的创建、编辑、确认、拒绝、删除和自动抽取统一调度下游刷新；单用户启动时把既有
  Workspace 切到配置的 auto，并恢复已有 active/candidate 数据的 L2/L3。

## Verification

- `.venv/bin/python -m pytest -q`：`457 passed, 52 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：24 个 Markdown、39 个 capability 通过；
  仅报告工作树中既有证据变更复核提醒。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- `docker compose up -d --build` 后 App/PostgreSQL healthy、Qdrant running；健康 API ready。
- Docker 冒烟：空查询返回最近 L0 消息；中文“五子棋”返回真实命中；两个既有 Workspace
  均为 L1 auto；`test-workspace` 生成 L2 scene；L3 v7 非空且来源为对应 L2 ID。
- UI 精简后再次验证：全量 `457 passed, 52 subtests passed`；compileall、文档校验、
  `node --check`、`git diff --check` 通过；Docker 重建后 healthy，实际首页 HTML 不再包含
  L1 模式选择及其三个手动操作按钮，静态资源版本为 `20260821-layered-memory-4`。

## Result

- 已完成 TencentDB 风格的自动分层流水线。前端进入 L0 会先展示最近消息，中文检索在
  SQLite/PostgreSQL 均可用；画像页可检查 L2 场景、L1 来源数量和最终 L3 快照。
- Docker 已用最新代码重建并保持运行，可在 `http://127.0.0.1:8000` 直接测试。
- README、双语说明、面试手册与 facts 证据已同步；用户已授权提交并推送，验证覆盖的
  提交将由工作流状态记录。
- 后续 UI 精简：已移除 L1 的 CURRENT WORKSPACE 说明、模式选择、保存模式、手动重建索引
  和刷新控件；项目记忆页进入即自动加载，后端继续固定 auto 流水线。
