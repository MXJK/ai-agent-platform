# TASK-AUTO-CONTEXT-ROUTING: Agent 自动选择 Repo / RAG 上下文

## Goal

在保持现有代码 Agent、工作区实时搜索和独立知识库 RAG 能力的基础上，
让 Agent 根据用户问题自动选择 repo、RAG、混合或无外部上下文，并合并证据后
生成可追溯回答；同时为知识库增加 PostgreSQL 目录元数据和管理 CRUD。

## In scope

- 新增知识库目录模型、内存/PostgreSQL 存储、服务、迁移和管理 API。
- 文档录入前校验知识库存在，删除知识库时级联清理向量、文档和分块。
- 扩展 LangGraph 的上下文路由、知识检索和证据合并节点。
- 返回上下文路由、选中知识库和知识片段来源。
- 扩展现有知识库管理页面。
- 增加路由、CRUD、迁移兼容和降级行为测试。

## Out of scope

- 代码仓库向量索引。
- workspace 与知识库的固定绑定。
- 快速聊天与后台 Agent 执行模型合并。
- 多租户权限、单文档编辑/删除、部署、发布或自动合并。

## Acceptance criteria

- [x] 知识库目录支持创建、列表、读取、更新和删除，包含 ID、名称、描述、标签和文档数。
- [x] PostgreSQL 迁移回填已有知识库并为 documents 增加级联外键。
- [x] 文档只能录入已存在知识库，删除知识库同步清理向量和持久化文档。
- [x] Agent 自动输出 none/repo/rag/hybrid 路由并最多选择三个真实知识库。
- [x] RAG 证据复用现有搜索链路，受独立字符预算限制，并与 repo 证据合并。
- [x] RAG 无结果或失败时按计划降级，不使 Agent run 技术失败。
- [x] Agent API 返回 context_route、selected_knowledge_base_ids 和知识片段来源元数据。
- [x] 原有代码修改审批、Sandbox、验证和 artifact 流程保持可用。
- [x] 知识库管理页面支持目录 CRUD、删除确认和对已有库录入文档。
- [x] 项目规定验证、前端语法检查和 diff 检查通过。

## Decisions

- `/agent/runs` 请求和必填 `workspace_id` 保持不变。
- 知识库目录跟随 `DOCUMENT_STORE` 选择内存或 PostgreSQL 存储。
- 分类器最多看到 50 个目录、12,000 字符，并最多选择 3 个知识库。
- 每个知识库检索最多 5 个片段，RAG 总证据预算复用 `rag_max_prompt_chars`。
- 知识库 ID 不可修改；名称、描述和标签可更新且不触发重新 embedding。
- 新文档录入要求知识库已创建；历史数据由迁移回填。
- 删除顺序为幂等向量清理后删除目录记录，由数据库外键级联文档和分块。
- 历史 Agent result 在 PostgreSQL 反序列化边界默认补齐
  `context_route=repo` 和空知识库选择，公开请求契约不增加手动路由字段。
- 目录提示最多包含 50 项和 12,000 字符；超限优先保留与请求名称/标签匹配的项。

## Verification

- `.venv/bin/python -m pytest -q`：通过，96 passed（1 个 TestClient
  第三方弃用 warning）。
- `.venv/bin/python -m compileall ai_agent_platform tests evals migrations`：
  通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- `.venv/bin/python evals/run_evals.py`：通过，4/4；Recall@5=1.000，
  MRR=1.000。
- `.venv/bin/alembic heads`：通过，唯一 head 为 `20260724_0007`。
- `.venv/bin/alembic upgrade head --sql`：通过，确认从空库到新 head 的
  PostgreSQL DDL、历史目录回填和 documents 级联外键可生成。
- 未对用户的实际 PostgreSQL 实例执行迁移；部署迁移仍需人工确认。

## Result

完成。知识库现在具有可管理的目录元数据和级联删除能力；Agent 会在一次结构化
分类中选择 repo、RAG、混合或无外部上下文，校验最多三个真实知识库，复用现有
RAG 搜索并与实时源码证据合并。RAG 失败会在 Trace 和回答上下文中降级说明，
不会破坏现有审批、Sandbox、验证或 artifact 流程。管理页面已支持目录 CRUD
和从已有知识库录入文档。
