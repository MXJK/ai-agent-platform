# RAG-BGE-M3-ABLATION: 多语言向量与三路检索评测

## Goal

在 30 条 RAG 分级试标集上，用 BAAI/bge-m3 替换 local-hashing 作为真实多语言
Dense embedding，并在同一份索引上分别报告 Dense-only、Lexical-only 与 Hybrid
（weighted RRF）三组质量指标和检索时延，从而量化语义召回与融合的增益。

## In scope

- 新增基于 `sentence-transformers` 的本地 embedding provider，默认模型为
  `BAAI/bge-m3`，向量归一化后供 cosine/dot-product 检索使用。
- 为 RAG 检索增加显式 `dense`、`lexical`、`hybrid` 模式；现有调用默认仍为
  `hybrid`，不改变产品默认行为。
- `evals/run_rag_evals.py` 新增 BGE-M3 profile，并在一次索引后输出三种检索模式
  的 K=1/3/5/10 指标、负例/冲突诊断与 p50/p95 时延。
- 增加 provider 延迟加载、配置、检索模式隔离和 runner 报告测试。
- 同步 README、评测文档、Interview Notes 与事实索引。

## Out of scope

- 启用 reranker、调 lexical weight、实现 abstain/相关性阈值或修改 30 条标注。
- 将 BGE-M3 设为所有部署 profile 的强制默认，或自动重建现有 Qdrant/Chroma 索引。
- 调用真实 LLM、计算 Prompt Token/USD、扩展到 100–300 条或冻结正式 holdout。
- 部署、迁移、提交或推送。

## Acceptance criteria

- [x] `EMBEDDING_PROVIDER=sentence_transformer` 与 `EMBEDDING_MODEL=BAAI/bge-m3`
      可创建真实本地多语言 embedding provider，模型只加载一次且输出归一化向量。
- [x] Dense-only 不执行 lexical recall；Lexical-only 不执行 query embedding/dense
      recall；Hybrid 保持现有 weighted RRF 行为。
- [x] runner 对同一批已生成向量输出三组独立 K 指标与时延，不重复 ingestion。
- [x] 默认 deterministic profile 继续使用 hashing，BGE-M3 通过显式 profile 运行，
      日常离线回归不触发模型下载。
- [x] 聚焦测试、真实 BGE-M3 运行、完整 pytest、compileall、Interview Notes 校验与
      `git diff --check` 通过，或如实记录外部模型下载阻塞。

## Decisions

- Provider 名使用 `sentence_transformer`，避免把实现硬编码成只能加载 BGE-M3；本轮
  BGE-M3 profile 固定模型名，防止环境变量悄悄改变 A/B 基线。
- 三路评测共享一次文档 embedding；每条 query 各跑三种检索模式。Lexical-only
  完全跳过 query embedding，保证其时延和质量口径不是“权重为 1 的伪 lexical”。
- 质量门槛仍只对 Hybrid 应用，Dense/Lexical 是诊断对照组。

## Verification

- 真实 BGE-M3：`HF_HOME=/tmp/ai-agent-platform-bge-m3-cache .venv/bin/python
  evals/run_rag_evals.py --profile bge-m3`：模型下载约 1.4 GiB，CPU 加载/推理成功。
  K=5 三组结果：
  - Dense-only：Recall `1.000`、Precision `0.237`、Core MRR `0.963`、NDCG
    `0.979`、Hit Rate `1.000`，p50/p95 `48.070/54.107 ms`。
  - Lexical-only：Recall `0.407`、Precision `0.096`、Core MRR `0.370`、NDCG
    `0.384`、Hit Rate `0.407`，p50/p95 `1.666/1.712 ms`。
  - Hybrid：Recall `1.000`、Precision `0.237`、Core MRR `0.963`、NDCG
    `0.979`、Hit Rate `1.000`，p50/p95 `48.546/53.617 ms`。
  - Dense/Hybrid hard-negative violation `1.000`、unanswerable non-empty `1.000`、
    conflict preferred `0.333`；因此草案门槛仍不应启用。
- 固定 hashing 对照：`.venv/bin/python evals/run_rag_evals.py`：三组报告成功；
  K=5 Dense `0.259/0.221`、Lexical `0.407/0.384`、Hybrid `0.259/0.242`
  （Recall/NDCG）。
- 聚焦：`.venv/bin/python -m pytest -q tests/test_rag_pilot_evals.py
  tests/test_rag_retrieval.py tests/test_config.py tests/test_project_memory.py
  tests/test_self_hosted_compose.py`：`85 passed, 8 subtests passed`。
- 完整回归：`.venv/bin/python -m pytest -q`：`787 passed, 133 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：校验 24 个 Markdown、46 个
  capability，通过；review warnings 是共享工作树证据变更提示，不是失败。
- `docker compose --env-file .env.example config --quiet`、`git diff --check`：通过。
- 未执行 Docker image 完整构建、已有 Qdrant 知识库重索引、部署、迁移、提交或推送。

## Result

完成。新增懒加载的 Sentence Transformer embedding provider，官方 Compose 默认配置
`BAAI/bge-m3`/CPU，并在自托管镜像中包含依赖；模型缓存用独立 named volume 持久化，
且镜像预建可写目录，避免非 root App 首次下载失败。开发态默认继续使用 hashing，防止
日常测试自动下载大模型。项目记忆复用同一 embedding provider 配置。

RAG Service 新增内部 `dense`、`lexical`、`hybrid` 模式；产品调用默认仍为 Hybrid。
Lexical-only 完全跳过 query embedding/vector search，Dense-only 完全跳过 lexical
search，Hybrid 保持 weighted RRF。runner 只 ingest 一次，再对 30 条 case 输出三组
K=1/3/5/10 质量、负例/冲突诊断和 per-mode p50/p95。

BGE-M3 显著修复了中文问法到英文资料的语义召回，但没有解决精度治理：Top-5 中四类
hard negative 均仍带出禁止文档，冲突资料首选正确率也只有 0.333。下一轮应固定 BGE-M3
和本轮语料，先做 reranker on/off，再设计 score/abstain 阈值；在此之前不应启用草案
质量门槛。切换现有部署到 BGE-M3 后必须重建已有向量，本任务未替用户执行数据重索引。
