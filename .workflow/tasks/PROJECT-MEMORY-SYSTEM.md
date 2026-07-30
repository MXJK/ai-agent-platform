# PROJECT-MEMORY-SYSTEM: 实现工作区共享的项目长期记忆

## Goal

在现有会话历史、实时源码探索、知识库 RAG 和 LangGraph checkpoint 之外，实现
独立的项目长期记忆闭环：按 workspace/revision 隔离，支持自动提炼、混合检索、
人工治理、可靠索引、Chat/Agent 注入、权限控制、可观测性和产品工作台。

## In scope

- 项目记忆领域模型、生命周期、证据、任务、审计和索引 Outbox。
- 内存与 PostgreSQL 存储、Alembic 迁移、独立 Qdrant/in-memory 向量索引。
- Dense + lexical + RRF 检索、敏感信息拦截、去重/冲突/替代和源码哈希失效。
- workspace revision、共享成员角色和本地/可信身份边界。
- Chat SSE、Coding Agent LangGraph、管理 API 和前端记忆治理页面。
- 可靠异步提炼、指标、离线评测、README 和面试手册同步。
- 持久化滚动会话摘要，以及 Chat/Agent 共用的有界上下文压缩。
- 按相关性、近期性、重要性进行可解释的项目记忆加权排序。

## Out of scope

- 个人画像、跨项目用户偏好或知识图谱。
- 部署、执行生产迁移、发布、合并、推送或接入具体商业身份供应商。
- 将项目源码预先建立向量索引，或让历史记忆覆盖实时源码与 AGENTS 指令。

## Acceptance criteria

- [x] 记忆按 workspace 和 workspace revision 隔离，旧 revision 不参与检索。
- [x] 固定六类记忆和 candidate/active/superseded/rejected/stale 生命周期可用。
- [x] 自动提炼按阈值、证据可信度、幂等、敏感信息和冲突规则写入。
- [x] PostgreSQL 为事实源，Qdrant 为可重建索引，失败可降级到 lexical。
- [x] Chat 与 Agent 能透明展示使用的记忆，未启用时保持旧行为兼容。
- [x] 管理 API/UI 支持查看、创建、编辑、确认、拒绝、遗忘、配置和重建索引。
- [x] 工作区共享权限至少覆盖 viewer/editor/admin，并不信任伪造的身份头。
- [x] 覆盖跨会话命中、跨工作区隔离、旧 revision、删除、过期、失效和降级测试。
- [x] README、面试手册、事实索引与实际能力同步。
- [x] 旧会话轮次可增量压缩为摘要且不删除来源消息；Chat 与 Agent 同时使用
      “滚动摘要 + 最近消息”。
- [x] 会话压缩可持久化、重复后台投递幂等、受来源/摘要字符预算约束，并通过
      summary API 透明展示。
- [x] 项目记忆对所有合格候选按可配置的相关性、近期性、重要性权重排序，并
      暴露三个分项分数。
- [x] 会话压缩和加权排序具备内存/PostgreSQL/API/Worker 回归测试及同步文档。
- [ ] 项目规定验证和相关附加验证通过。

## Decisions

- PostgreSQL 保存完整正文与生命周期；向量存储只保存最小检索 payload。
- 记忆检索与 repo/rag 路由正交；涉及当前代码的结论必须继续实时探索。
- 功能默认关闭，按 `off → shadow → review → auto` 分阶段启用。
- 生产身份边界采用 Gateway 验证后的可信用户头；本地开发允许关闭认证。
- 显式“记住”走确定性提炼，不依赖模型是否正确分类；模型声明的 user/verified
  权威还要由原始用户文本或已验证 Agent Run 约束。
- 记忆写事务仅提交 PostgreSQL 与 Outbox，独立 `memory_index_outbox` 任务更新
  Qdrant；检索只读索引并按 PostgreSQL version 二次校验。
- workspace root 变化后旧 revision 立即退出召回；仅 admin 可显式确认旧记录并
  复制到当前 revision，旧记录本身保留。
- 可信身份目前覆盖会话、工作区、Agent 和项目记忆边界；知识库全域多租户授权
  仍是后续生产化范围，不在本任务中虚构为完成态。
- 会话摘要是有损派生上下文，不是项目记忆；来源消息保留且仍为事实基线，
  Prompt 将摘要标记为不可信历史上下文。
- 项目记忆排序使用归一化混合检索相关性、指数近期性衰减和归一化重要性，
  三项配置权重之和必须为 1。

## Verification

- `.venv/bin/python -m pytest -q`：150 passed，1 个既有
  Starlette/httpx 弃用警告。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python evals/run_evals.py`：8/8；Recall@5、MRR、NDCG@5、
  HitRate@5 均为 1.000，Precision@5=0.250。
- `.venv/bin/python evals/run_memory_evals.py`：PASS；候选精确率 1.000、
  Recall@6=1.000、跨工作区泄漏 0。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，11 份 Markdown、
  22 项能力；仅报告工作树相对上次已提交基线的 evidence review warnings。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/alembic heads`：`20260730_0010 (head)`。
- `.venv/bin/alembic upgrade 20260730_0009:20260730_0010 --sql`：会话摘要
  迁移 SQL 静态生成通过；未连接或修改真实数据库。
- `git diff --check`：通过。
- `go test ./gateway/...`：未执行成功，当前环境没有 `go` 命令（exit 127）；
  曾检查常见安装路径，均无 Go 工具链。未获准使用容器替代执行。

## Result

项目记忆的领域模型、存储/迁移、提炼/检索、Outbox、Chat/Agent、API/UI、
OIDC 可信身份、观测、评测与文档已实现。源码和文档影响已同步到 README、
面试手册各相关 Part 与 `facts.json`。

实现已变基到包含供应商原生 Function Calling 的最新 `origin/main`，原生工具
选择、结果回灌和可靠执行测试与项目记忆/会话压缩回归同时通过。

本轮继续实现了持久化滚动会话摘要：成功回答后异步增量压缩旧消息，来源消息
保留，摘要使用边界 message ID 与乐观版本保证幂等，并由 Chat/Agent 共用。
项目记忆的最终排序改为可配置的相关性、指数近期性和重要性加权，三个分项及
最终分均向 Chat SSE 和 Agent provenance 透明暴露。

任务暂不标记 done：唯一剩余项是使用真实 Go 工具链执行网关认证测试。未执行
生产迁移、部署、提交、推送或外部写入。
