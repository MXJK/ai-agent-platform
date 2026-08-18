# LOCAL-LAYERED-MEMORY：本地 L0/L1/L3 分层记忆

## Goal

在保留现有项目记忆治理规则的基础上，实现零外部记忆基础设施的本地持久化闭环：
SQLite 承载 L0 会话/Run 证据、L1 项目原子记忆和 L3 用户画像；L2 场景层明确不实现。

## In scope

- 单文件 SQLite 状态层、版本化 schema、WAL/外键/权限和运行时装配。
- Session、Workspace、Agent Run、Project Memory 的 SQLite repository 和原子 Query UoW。
- L0 跨会话 FTS5/LIKE 降级检索 API 与只读 Agent 工具。
- L1 独立 repository/vector 配置、SQLite FTS5 与本地向量索引。
- L3 用户原子事实、审核生命周期、确定性画像快照、API、提炼与 Chat/Agent 注入。
- 前端记忆工作台、架构图、README/面试手册/事实索引和回归评测。

## Out of scope

- L2 Scene Block、场景任务、接口或占位表。
- PostgreSQL/Qdrant 与 SQLite 之间的数据迁移、生产部署或多进程/分布式 SQLite。
- 将历史记忆提升为系统指令、权限或实时源码的替代事实。
- 自动 commit、push、merge、发布或执行生产数据库迁移。

## Acceptance criteria

- [x] SQLite 本地 profile 可持久化 Session、Workspace、Agent Run、L1 和 L3，并跨 Runtime 重启恢复。
- [x] Session 与 Agent Run 起始写入共享事务；SQLite schema 初始化幂等且启用 WAL、外键和 busy timeout。
- [x] L0 搜索按当前用户隔离并可选 workspace/session，FTS5 不可用时安全降级。
- [x] L1 保持现有生命周期、证据、revision、排序和 API 兼容，并支持 SQLite 词法/向量混合召回。
- [x] L3 仅由显式/手工事实自动生效，其余候选需审核；画像确定性、有界、可重建、可遗忘。
- [x] Chat 与 Agent 注入 L3，并把所有记忆标记为不可信历史上下文；敏感/越权内容被拒绝。
- [x] 前端提供项目记忆、我的画像、对话搜索治理入口。
- [x] 不存在 L2 表、后台任务、API 或隐藏功能路径。
- [x] README、面试手册、facts、架构图和配置示例与实现一致。
- [x] 项目要求的测试、编译、记忆评测、文档校验和 diff 检查通过。

## Decisions

- 复用现有 `ProjectMemoryService`，新增 SQLite repository/vector adapter，不复制项目记忆业务规则。
- L0 以现有 Session/Message/Run 为事实源；跨会话历史只按需搜索，不自动注入。
- L3 使用独立 user scope；快照由 active 原子事实确定性渲染，不让模型自由改写 persona 文档。
- 有 Workspace 且无全局提示的“记住”进入 L1；无 Workspace 或含全局偏好提示时进入 L3。
- 本地模式使用单 API 进程和 in-process queue；生产后端保持兼容但本任务不扩展。
- `PROJECT_MEMORY_STORE` 与 `PROJECT_MEMORY_VECTOR_STORE` 分别选择 L1 事实源和稠密索引，避免继续借用 Workspace/RAG 配置。
- SQLite FTS5 缺失时降级到有界 `LIKE`，向量索引异常时回退词法召回；索引 Outbox 在 Runtime 启动时恢复。
- 架构图拆成总览、上下文组装、写入治理和持久化四个可编辑页面文件，并同时导出普通 PNG 和内嵌 XML 的 `.drawio.png`。

## Verification

- `.venv/bin/python -m pytest -q`：通过，`410 passed, 49 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python evals/run_memory_evals.py`：通过；candidate precision `1.000`、Recall@6 `1.000`、跨 Workspace 泄漏 `0`。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 12 个 Markdown 文件和 38 项能力；输出仅包含既有 evidence review 提醒。
- `node --check ai_agent_platform/static/app.js`：通过。
- `python3 .../drawio-skill/scripts/validate.py docs/architecture/*.drawio --score`：四张图均为 `0 error(s)`；普通 PNG 已人工检查，最终 `.drawio.png` 已修复 IEND。
- `git diff --check`：通过。
- 额外覆盖：临时 SQLite 文件两次 Runtime/API 启动恢复、WAL 并发读取、事务回滚、FTS/向量降级、Outbox、user/workspace/revision 隔离、L1/L3 路由、画像预算/重建/遗忘及不可信上下文注入。

## Result

已完成本地 L0 + L1 + L3 分层记忆闭环，保留全局 memory/off 默认值，并新增显式启用的 `.env.local-memory.example`。本任务没有创建 L2 表、接口、任务或占位逻辑，也没有引入新的记忆基础设施服务。

README、英文 README、模块化面试手册、facts 索引、前端记忆工作台和四张本地架构图已经同步。此前 `DIRECT-EXECUTION-WORKSPACES` 已由独立提交交付；本地记忆与后续 runtime profile 改造经用户授权随 local-memory feature branch 提交并推送，未执行 merge、迁移或部署。
