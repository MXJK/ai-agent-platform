# RAG-ANSWER-QUALITY-DEEPSEEK: 注册模型答案质量试标

## Goal

在既有 30 条 AuroraDesk RAG pilot 上，把“检索是否找到证据”和“模型拿到证据后是否
正确回答”拆开评估，并用模型注册表中已启用的 `deepseek/deepseek-v4-flash` 运行首轮
真实答案质量基线。

## In scope

- 为 30 条 query 标注原子事实、事实来源与无答案拒答规则。
- 使用生产 RAG prompt，但直接注入 oracle evidence；冲突资料和 hard negative 保留在
  上下文中，避免生成评测被检索召回率混淆，同时覆盖抗干扰能力。
- 从产品模型注册表解析指定 Provider/Model 与凭据，关闭 fallback，并验证实际 route。
- 报告 case pass、fact coverage、fact attribution、citation validity、abstention、
  route mismatch、Token 和 p50/p95 生成耗时。
- 支持保存 JSON 与离线 replay，标注修正不重复发起付费调用。
- 新增固定 BGE-M3 + `BAAI/bge-reranker-base` profile 和 `--hybrid-only`，对比
  reranker on/off 的检索质量、延迟与风险集端到端答案。
- 同步 README、评测设计、Interview Notes 和事实索引。

## Out of scope

- 用同一个 LLM 对自己的回答打分，或声称正则评分等价于人工语义评审。
- 将合成 pilot 当作生产分布、正式 holdout 或不同模型的通用质量结论。
- 相关性/拒答阈值、reranker 模型替换和提示词调参。
- 部署、迁移、提交或推送。

## Acceptance criteria

- [x] 30 条 retrieval case 均有确定性答案质量标注；答案 case 和 retrieval case 必须
      一一对应，引用来源必须存在于该 case 上下文。
- [x] runner 只允许调用已注册且启用的显式 Provider/Model，关闭 fallback，并将实际
      route mismatch 计为失败。
- [x] answerable case 同时检查事实覆盖、事实附近的正确来源引用和引用编号合法性；
      unanswerable case 检查明确拒答。
- [x] hard negative/冲突资料保留在 oracle 上下文，检索指标和生成指标不混为一谈。
- [x] JSON 报告可离线 replay，评分标注修正不触发新的模型调用。
- [x] runner 可保存 Hybrid 排名并作为答案 evidence 重放；风险最大的 hard negative、
      conflict、unanswerable 共 10 条完成 reranker on/off + DeepSeek 端到端 A/B。
- [x] 聚焦测试、完整 pytest、compileall、Interview Notes 校验与 diff check 通过。

## Decisions

- 不使用 LLM-as-judge。pilot 的事实是稳定数字、代码、路径与动作，用多语言正则覆盖
  允许的表述，再检查事实附近引用是否指向声明来源；这比自评可复现，但仍需人工复核
  评分假阴性。
- 生成端使用 oracle-plus-adversarial evidence：相关资料直接注入，hard negative 和
  低等级旧资料继续保留；无答案 case 注入主题近似但不能回答的资料。该分数证明的是
  “拿到这组证据后的生成质量”，不是端到端 RAG 质量。
- 复用生产 `build_rag_prompt_messages`，抽为纯函数，避免评测 prompt 与产品 prompt
  漂移。
- 真实运行通过隔离的 Compose one-off container 读取现有注册表和 Secret Store；凭据
  不写入命令、日志或结果。

## Verification

- 注册表只读确认：存在且启用 `deepseek/deepseek-v4-flash`，registry ID
  `mdl_a6538c34fe83dc0b`；评测输出实际 route mismatch `0.000`，未输出凭据。
- 一条冲突 smoke：Compose one-off container 运行 `conflict_retention`，case pass、事实
  覆盖/归属、引用有效性均 `1.000`；真实路由正确，input/output/thoughts Token 为
  `721/65/27`，耗时 `1071.8 ms`。
- 完整 30 条真实调用：无 Provider 错误，input/output/thoughts Token 合计
  `10838/2327/1442`，生成 p50/p95 `1332.6/1861.3 ms`。初始规则报 case pass
  `0.867`；逐条人工复核发现 4 条均为规则假阴性（“未找到”拒答、欧盟同义词、恢复流程
  语序，以及冲突问题正确选择现行值但未复述旧值），没有修改 prompt 或重发模型调用。
- 使用保存的原始回答执行 `--replay` 重评分：30/30 case pass；fact coverage、fact
  attribution、citation validity、abstention accuracy 均 `1.000`，route mismatch
  `0.000`。
- BGE reranker A/B（固定 BGE-M3、Hybrid@5、CPU）：
  - 无 reranker：NDCG `0.979`、hard-negative violation `1.000`、conflict preferred
    `0.333`、p50/p95 `49.638/53.826 ms`。
  - `BAAI/bge-reranker-base`：NDCG `0.973`、hard-negative violation `0.500`、
    conflict preferred `0.667`、p50/p95 `590.763/813.056 ms`。
  - reranker 改善负例/冲突排序，但仍未过 hard-negative 草案门槛，NDCG 略降，p50
    约为无重排的 11.9 倍，故不修改产品默认 `rerank=false`。
- 风险集端到端 DeepSeek A/B（4 hard negative + 3 conflict + 3 unanswerable）：
  - BGE-M3 Hybrid Top-5：10/10 pass，input/output/thoughts Token
    `8354/824/520`，generation p50/p95 `1657.8/3207.1 ms`。
  - BGE-M3 + reranker Top-5：10/10 pass，input/output/thoughts Token
    `9056/733/462`，generation p50/p95 `1572.3/2509.2 ms`。
  - 两组事实覆盖、引用归属、引用合法性、拒答均 `1.000`，无 route mismatch；生成
    延迟是单次小样本波动，不能归因给 reranker。reranker 在本风险集没有带来答案通过率
    增益，反而增加约 8.4% input Token。
- 聚焦：`.venv/bin/python -m pytest -q tests/test_rag_answer_evals.py
  tests/test_rag_pilot_evals.py tests/test_rag_retrieval.py`：`34 passed`。
- 完整：`.venv/bin/python -m pytest -q`：`805 passed, 135 subtests passed`。
- `.venv/bin/python -m compileall -q ai_agent_platform tests evals`、`jq empty
  evals/rag_answer_cases.json INTERVIEW_NOTES/facts.json`、`git diff --check`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：校验 24 个 Markdown、46 个
  capability，通过；review warnings 来自共享工作树的证据变更，不是校验失败。
- 未执行完整 30 条两组端到端生成、正式人工语义评审、相关性阈值、部署、迁移、
  提交或推送。

## Result

完成。新增 30 条 oracle-plus-adversarial 答案标注和真实注册模型 runner，生产 RAG prompt
现在通过纯函数复用，避免评测 prompt 漂移。runner 关闭 fallback、校验实际 route，输出
事实覆盖、正确来源引用归属、引用编号、拒答、Token 与延迟；JSON 可离线 replay。

DeepSeek V4 Flash 在本轮保存的 30 条 oracle 回答和 10 条检索风险集两组 A/B 上经规则
复核后全部通过。BGE reranker 将 hard-negative 违规减半并改善冲突排序，但没有提升该
风险集答案通过率，同时显著增加检索时延并增加输入 Token，因此保留为显式实验 profile，
不修改产品默认。下一轮应固定本轮 scorer，优先设计相关性/拒答阈值和人工复核集，不能
在当前 pilot 上继续调规则后把它称为 holdout。

并发的 `AGENT-DEEPSEEK-TOOL-BOUNDARY` 完成并释放工作流状态后，已由工作流控制器登记
本任务为 `done`。工作树包含两轮 RAG 与该并发任务的未提交改动，因此没有填写
`verified-head`；本任务未提交或推送。
