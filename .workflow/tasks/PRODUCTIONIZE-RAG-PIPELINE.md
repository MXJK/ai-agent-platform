# PRODUCTIONIZE-RAG-PIPELINE: 混合召回、可信评测与索引状态机

## Goal

将现有向量召回后的词法加权升级为真正的 Dense + Lexical 双路召回，
建立独立且可设置质量门槛的 RAG 评测，并用可查询、可恢复的索引任务状态机
记录文档摄取生命周期。

## In scope

- 增加独立词法候选召回，并使用 RRF 融合 Dense 与 Lexical 排名。
- 保留可选 CrossEncoder 重排和现有检索分数溯源。
- 将 RAG search 评测与 Agent/仓库检索评测分开统计。
- 增加 Precision@K、NDCG@K、Hit Rate、质量阈值和更完整的 RAG 语料。
- 为索引任务记录 pending、parsing、embedding、vector_written、active、failed。
- 提供内存和 PostgreSQL 索引任务存储、状态查询 API 与数据库迁移。
- 保证索引失败可见，且失败更新不会静默删除上一版有效向量。
- 更新配置、README 和覆盖关键状态转换/融合语义的测试。

## Out of scope

- 将文档摄取改成 Celery 异步接口。
- OCR、图片或表格语义解析。
- 用户、租户和 ACL 权限模型。
- 部署、执行数据库迁移、提交或推送。

## Acceptance criteria

- [x] 词法候选不依赖 Dense Top-N，Dense 与 Lexical 候选使用 RRF 融合。
- [x] 搜索响应能区分 Dense、Lexical、融合和重排分数/排名。
- [x] 内存与 PostgreSQL 运行时都能提供词法检索。
- [x] RAG 指标只统计 RAG search case，不混入 Agent 仓库检索。
- [x] 评测报告包含 Recall、Precision、MRR、NDCG、Hit Rate 并支持质量门槛。
- [x] 评测语料包含多命中、词法救回、无答案和 hard-negative 场景。
- [x] 每次摄取都生成索引任务并按合法状态迁移，失败保存错误。
- [x] API 可以按知识库查询索引任务状态。
- [x] PostgreSQL 迁移保存任务状态以及恢复词法/引用所需的 chunk 元数据。
- [x] 更新文档不会在新索引失败时先删除上一版有效向量。
- [x] 项目规定的 pytest 与 compileall 验证通过。

## Decisions

- Dense 候选继续来自配置的 VectorStore；词法候选在本地/测试运行时使用
  BM25，在 PostgreSQL 运行时使用带 GIN 索引的全文检索。
- 两路结果不混合原始分数，而以可配置的 weighted RRF 融合；
  `RAG_LEXICAL_WEIGHT` 控制通道权重，`RAG_RRF_K` 控制排名平滑。
- 保留原有 `recall_score`、`lexical_score`、`hybrid_score` 和
  `rerank_score`，并新增 `dense_rank`、`lexical_rank`、
  `fusion_score`，方便解释最终排序来源。
- 摄取接口暂时保持同步，避免本任务扩大到 Celery 协议；每次调用仍创建
  可持久化、可查询的完整索引任务状态记录。
- 向量替换先写新 points，再分页清理同文档 stale points；embedding
  失败发生在向量替换前，因此不会先删除上一版有效向量。
- RAG 聚合指标只接受 `type=search` 且带相关文档标注的案例；
  Agent 的 live-repo 检索仍保留逐案例断言，但不再污染 RAG 指标。
- no-evidence 场景使用空知识库验证；hard negative 使用 Top-1 断言，
  同时聚合指标始终基于 Top-5 完整排名。

## Verification

- `.venv/bin/python -m pytest -q`：106 passed；保留 1 个第三方 Starlette
  弃用 warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python evals/run_evals.py`：8/8 cases 通过；RAG-only
  Recall@5=1.000、Precision@5=0.250、MRR@5=1.000、NDCG@5=1.000、
  Hit Rate@5=1.000，4 个有相关性标注的 RAG case 均通过质量门槛。
- `.venv/bin/alembic upgrade head --sql`：完整迁移链 SQL 生成通过；
  未连接数据库、未实际执行迁移。
- Chroma 临时持久化 smoke test：同文档从 2 个 chunks 替换为 1 个后仅
  返回保留 chunk。
- Qdrant 内存测试覆盖知识库隔离和 stale point 清理。
- `git diff --check`：通过。
- 差异范围、凭据、生成文件和迁移风险审查通过；新增 PostgreSQL URL
  仅为测试占位值 `postgresql://test`。

## Result

RAG 检索现在拥有真正独立的 Dense 与 Lexical 候选集合，并通过 weighted
RRF 统一排名后再进入可选 CrossEncoder。搜索响应能够解释每个候选来自哪条
召回通道及其融合结果。

离线评测将知识库 RAG 与 Agent 仓库探索彻底分离，新增五类检索指标、可失败
的质量门槛、多文档、精确词法救回、hard negative 和无证据案例。

文档摄取新增 `pending → parsing → embedding → vector_written →
active/failed` 状态机、内存/PostgreSQL journal、查询 API 和迁移。失败任务
保留有限错误信息；更新向量不再先删除旧文档 points。
