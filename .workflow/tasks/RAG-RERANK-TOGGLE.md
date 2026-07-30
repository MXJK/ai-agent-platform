# RAG-RERANK-TOGGLE: 知识库按请求启用 CrossEncoder 精排

## Goal

让知识库页面使用可按压按钮选择本次检索或问答是否使用服务端配置的
CrossEncoder reranker，并由 API 明确报告精排能力和实际执行状态。

## In scope

- 增加全局 RAG reranker 能力查询 API。
- 为知识库 search/ask 请求增加向后兼容的按请求精排选项。
- 在 RAG 服务中根据请求选择 RRF Top-K 或 CrossEncoder 精排。
- 为 search/ask 响应增加检索执行元数据。
- 在知识库检索参数中增加可访问的切换按钮和能力状态。
- 展示 RRF 与 reranker 分数，并记住当前浏览器的按钮选择。
- 更新 README、Interview Notes、事实映射和自动化测试。

## Out of scope

- 修改 Agent 自动 RAG 路由的精排策略。
- 允许浏览器选择或下载任意 reranker 模型。
- 修改知识库数据库模型或持久化每个知识库的默认精排策略。
- 部署、提交、推送或模型下载。

## Acceptance criteria

- [x] 页面使用 `button` 和 `aria-pressed` 控制精排，不使用 checkbox。
- [x] 未配置真实 reranker 时按钮禁用并说明原因。
- [x] search/ask 都传递用户选择，省略字段的旧客户端保持原行为。
- [x] 请求启用不可用 reranker 时返回明确的 409 错误。
- [x] 响应区分 requested/applied，并报告 provider、model、候选数和结果数。
- [x] 开启时执行 CrossEncoder，关闭时保留 RRF 顺序并直接截取 Top-K。
- [x] 结果卡片在适用时展示 RRF 和 reranker 分数。
- [x] Agent 自动 RAG 路由行为不变。
- [x] CrossEncoder 默认使用 CPU，避免自动选择不稳定的 MPS 导致 API 进程崩溃。
- [x] 默认 reranker 同时支持中文和英文检索。
- [x] 检索/问答请求期间锁定相关控件，取消旧请求并阻止过期响应覆盖新结果。
- [x] 请求开始即清空旧结果，失败时在结果区显示错误。
- [x] 折叠参数外始终显示当前 RRF/CrossEncoder 策略。
- [ ] 测试、compileall 和 Interview Notes 校验通过。

## Decisions

- UI 选择是单次请求策略，使用现有浏览器状态持久化偏好，不写入知识库。
- RAG 前端同一时间只允许一个 search/ask 请求；使用 `AbortController` 和递增
  generation 双重保护，避免重复点击和过期响应竞态。
- `rerank_enabled` 使用可空布尔值；省略时沿用服务端原有配置行为。
- 服务端能力与用户请求分离，前端只控制是否使用已配置模型。
- 显式请求无法满足时失败，不静默降级。
- Sentence Transformers 作为默认可用 provider，但请求默认关闭；模型使用进程内
  线程安全懒加载，普通启动和 RRF-only 请求不初始化模型。
- 默认模型使用 `BAAI/bge-reranker-base`，官方模型卡明确标注支持 Chinese and
  English；保留环境变量覆盖，后续可按语种、延迟和资源评测替换。
- CrossEncoder 设备通过 `SENTENCE_TRANSFORMER_RERANKER_DEVICE` 配置，默认
  `cpu`；只有部署环境明确验证过加速器后才主动改为 `cuda`、`mps` 等设备。
- Agent 不传按请求字段，继续使用 `RAG_RERANK_DEFAULT_ENABLED` 服务端策略。

## Verification

- `.venv/bin/python -m pytest -q`：115 passed；保留 1 个第三方 Starlette
  弃用 warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- `.venv/bin/python evals/run_evals.py`：8/8；RAG Recall@5=1.000、
  Precision@5=0.250、MRR/NDCG/Hit Rate=1.000。
- CrossEncoder CPU 路径此前使用 MiniLM 真实模型完成加载、预测和持久化 API
  烟测；`rerank_enabled=true` 返回 200，随后健康检查仍为 200，没有再次发生
  MPS 段错误。
- 新默认模型 `BAAI/bge-reranker-base` 的官方模型卡标注 language 为 `en`、`zh`，
  默认配置、环境变量覆盖、能力 API 模型字段和 CrossEncoder 构造路径由自动化
  测试覆盖。按任务范围未预下载新模型，首次真实请求仍会触发懒加载下载。
- Qdrant 客户端已按约束安装为 1.17.1，连接 1.16.1 服务端成功且无版本兼容
  warning。
- 浏览器验收：能力探测后按钮可用；点击从 `aria-pressed=false` 切换为
  `true`，文案从“精排：关闭”变为“精排：开启”，刷新后偏好保留，布局正常。
- 隔离浏览器回归：折叠参数摘要显示“策略：RRF”；双击“仅检索”仅产生一个
  search POST；服务停止后再次检索会清空旧引用、在结果区显示错误并恢复按钮。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：未通过。当前 `main` 缺少
  `codex/native-tool-calling` 分支中的 `tests/test_tool_execution.py` 和
  `tests/test_native_tool_calling.py`，但本地忽略的 handbook 已引用这两个文件。

## Result

功能实现和代码验证完成。运行中发现的 PyTorch MPS 原生段错误已通过显式 CPU
设备配置修复；默认精排模型已由英文 MiniLM 改为支持中文和英文的
`BAAI/bge-reranker-base`。新模型继续按需懒加载，未在本任务中预下载。
Qdrant 客户端/服务端版本漂移也已消除。前端 search/ask 已增加请求互斥、取消
和 generation 防竞态保护，请求期间锁定参数，开始时清除旧结果，失败信息直接
显示在结果区；当前排序策略不再隐藏在折叠参数内部。

任务仍不能按仓库规则关闭：Interview Notes 当前描述尚未合入的
`codex/native-tool-calling` 分支。需要人工决定将本任务迁移到该分支，或让
handbook 改回只描述当前分支；未擅自 merge、提交或删除无关事实映射。

当前改动已按用户要求从 `main` 移到新建的
`codex/rag-rerank-toggle` 分支，且未合并其他分支。
