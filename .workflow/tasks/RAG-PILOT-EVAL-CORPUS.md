# RAG-PILOT-EVAL-CORPUS: 30 条分级相关性试标集

## Goal

建立一套可版本化、可重复运行的 30 条 RAG 试标集，用真实用户问法风格覆盖精确
关键词、同义改写、多文档、长文档、多 chunk、hard negative、无答案与冲突资料，
先验证标注契约和指标区分度，再扩展到 100–300 条正式数据集。

## In scope

- 新增独立 `evals/rag_cases.json`，固定语料快照并保存 30 条人工分级标注。
- 相关性使用 0–3 级；Recall/Hit Rate 以 `>=2` 为相关，MRR 以首个 3 级核心
  资料为准，NDCG 使用完整分级。
- 新增独立离线 runner，按文件去重后报告 K=1/3/5/10 的 Recall、Precision、
  MRR、NDCG、Hit Rate。
- 单独报告 hard-negative 违规、无答案仍返回候选、冲突资料首选正确率和检索
  p50/p95 时延。
- 提供固定确定性基线，以及读取当前检索参数但使用隔离内存索引的运行模式。
- 增加数据契约、指标和 runner 回归测试，并同步评测文档与 Interview Notes。

## Out of scope

- 把 30 条试标题描述为真实生产流量或正式留出集。
- 本阶段扩展到 100–300 条，或冻结 20% 最终 holdout。
- 调整 RAG 参数、加入相关性阈值、实现 search abstain、启用外部 embedding/reranker。
- 调用真实 LLM、计算 Prompt Token/USD、部署或迁移。

## Acceptance criteria

- [x] 数据集恰好 30 条，类别配额为 5/5/5/5/4/3/3，fixture、case 和引用唯一且合法。
- [x] 每个可回答 case 至少有一个 3 级核心资料；无答案 case 不伪造相关资料；
      hard negative 和冲突资料具有对应边界标注。
- [x] 文件级排名先去重，避免同一文件多个 chunk 重复抬高或压低指标。
- [x] runner 输出 K=1/3/5/10 分级指标、负例/冲突诊断、case 失败详情和 p50/p95。
- [x] 默认诊断模式不执行草案质量门槛；`--enforce-gates` 可显式启用。
- [x] 聚焦测试、完整 pytest、compileall、文档事实校验和 diff 检查通过。

## Decisions

- 试标集使用完全虚构但接近企业知识库的 AuroraDesk 快照，避免把合成数据冒充
  真实用户数据；后续正式集必须由脱敏真实查询补充。
- 数据集独立于混合 Agent L0 的 `agent_cases.json`，防止参数扫描污染 Agent 行为回归。
- 无答案题当前只报告 non-empty retrieval 诊断，不设假门槛；search 层尚无可靠的
  score threshold/abstain 契约。
- `current` profile 强制使用临时内存 VectorStore，避免评测写入 Chroma/Qdrant；其余
  chunk、embedding、融合和 reranker 参数来自当前环境。

## Verification

- 数据契约/指标/RAG 聚焦回归：`.venv/bin/python -m pytest -q
  tests/test_rag_pilot_evals.py tests/test_rag_retrieval.py tests/test_evals.py`：
  `27 passed`。
- 固定 baseline：`.venv/bin/python evals/run_rag_evals.py`：30 cases；K=5
  Recall `0.259`、Precision `0.059`、Core MRR `0.222`、NDCG `0.242`、
  Hit Rate `0.296`；hard-negative violation `0.250`、无答案 non-empty
  `1.000`、conflict preferred `0.000`；本机本次运行 p50 `1.885ms`、p95
  `1.969ms`。时延仅是本机隔离内存基线，不代表网络或生产时延。
- 当前 profile：`.venv/bin/python evals/run_rag_evals.py --profile current`：
  当前环境参数与固定 baseline 相同，质量指标一致；隔离内存索引未写外部存储。
- 完整回归：`.venv/bin/python -m pytest -q`：`781 passed, 133 subtests passed`。
- 编译检查：`.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- 文档事实：`.venv/bin/python INTERVIEW_NOTES/validate.py`：校验 24 个 Markdown、
  46 个 capability，通过；evidence review warnings 来自共享脏工作树的既有证据变更，
  不是校验失败。
- `git diff --check`：通过；范围审查未发现凭据、数据库 migration、部署或外部写入；
  独立 HTML/JSON 架构说明产物纳入本次用户明确授权的仓库收尾提交。

## Result

完成。新增 `evals/rag_cases.json`，用 26 份固定 AuroraDesk 合成文档保存 30 条
用户风格问题和 0–3 分级文件相关性；7 类配额严格固定为 5/5/5/5/4/3/3。新增独立
runner 与分级指标实现，按文件折叠重复 chunk，区分正向质量、hard negative、无答案
和冲突资料，并提供固定/current 两种隔离运行方式。

试标集成功暴露当前 baseline 的真实弱点：local-hashing 面对中文问法和英文资料时
K=5 Recall/Hit Rate 很低；现有 search 又没有 abstain 阈值，所以无答案题仍返回候选。
因此草案门槛默认只诊断，没有把低分伪装成通过，也没有为了让样例变绿而调参数或泄漏
文档原句。README、`evals/README.md`、评测设计和 Interview Notes 已同步，明确该集合
不是脱敏真实查询、正式 holdout、Prompt Token/USD 或最终答案质量证据。下一步应人工
复核这 30 条的相关性和难度，再用脱敏真实查询扩展到 100–300 条并冻结 20% holdout。
