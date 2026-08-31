# AGENT-PROMPT-CACHE-STABILITY: 稳定模型前缀、延迟工具暴露与 Prompt Cache 可观测性

## Goal

在不改写 Provider 原始累计 Token 的前提下，通过稳定模型可见前缀、按冻结的
`task_shape` 延迟暴露工具以及按 Provider 能力接入 Prompt/KV Cache，减少重复前缀的
实际重新计算、提高缓存命中，并准确公开缓存读写、未缓存输入、稳定前缀和工具 Schema
开销。

## In scope

- 规范化 system/developer instructions、工具顺序和 JSON Schema 序列化；动态
  workspace/runtime/config 信息只追加到稳定前缀之后。
- 在 Run 开始冻结 `task_shape` 对应的模型可见工具 Profile；overview、targeted read、
  Skill、插件和动态 MCP 工具按明确需求延迟暴露。
- 分别适配 OpenAI Responses、Anthropic Messages、Google GenAI、DeepSeek 与其他
  OpenAI-compatible Chat Completions 端点的真实缓存请求/usage 能力。
- 扩展 Agent Run/API/UI 指标，区分累计输入、缓存命中、未缓存输入、缓存写入、当前
  保留上下文估算、稳定前缀估算和工具 Schema 估算。
- 增加固定前缀、Provider 能力、安全降级、连续 Profile 请求和阶段四综合回归测试；
  同步 README、Interview Notes 与 facts 证据。

## Out of scope

- 扣除、重写或隐藏 Provider 返回的 `input_tokens`、`output_tokens`、`total_tokens`、
  `cached_tokens`。
- 为不报告缓存 usage 的 Provider 虚构命中率或缓存节省。
- 在一个冻结执行阶段内重排工具，或为降低 Schema Token 破坏 checkpoint/replay、审批、
  权限和 Provider 请求语义。
- 自动创建和管理 Google CachedContent 资源、历史 Run 回填、部署、迁移、提交或推送。

## Acceptance criteria

- [x] 相同输入产生字节稳定或规范化稳定的 system/developer 前缀、工具定义和 JSON Schema；
      动态 workspace/runtime/config 信息位于稳定前缀之后，配置变化通过追加消息表达。
- [x] `task_shape` 工具 Profile 在 Run 开始冻结且顺序稳定；overview 不暴露 write/shell/MCP，
      targeted_read 不暴露修改工具，无明确外部需求时不暴露动态 MCP/Skill/插件工具。
- [x] OpenAI 只在自身 Responses 路径使用稳定 `prompt_cache_key` 和受模型能力约束的缓存
      控制；Anthropic 使用自身 `cache_control`；Google/DeepSeek 采集其真实缓存 usage；
      其他兼容端点不接收 OpenAI 专属字段并安全退化。
- [x] 至少记录 `input_tokens`、可靠时的 `cached_input_tokens`、
      `uncached_input_tokens=input_tokens-cached_input_tokens`、Provider 提供时的
      `cache_write_tokens`、可靠时的 `prompt_cache_hit_ratio`、`stable_prefix_tokens`、
      `tool_schema_tokens`、`visible_tool_count` 与 provider/model/cache capability。
- [x] API/UI 明确区分累计输入 Token、缓存命中输入 Token、未缓存输入 Token和当前保留
      上下文估算；缓存命中不被描述为没有消耗 Token。
- [x] 固定测试覆盖同 workspace 连续 overview、相同 Profile 连续请求、不同 task_shape、
      中途追加配置变化和不支持缓存的 Provider 降级；工具顺序与 total_tokens 原始语义不变。
- [x] “分析下当前项目”小/大仓库回归分别满足模型请求不超过 4/5、实际工具调用不超过
      10/12、累计输入不超过 50,000/120,000，且 replay amplification、零重试和答案覆盖
      不回归；第二次相同 Profile 的未缓存输入或延迟有明确改善证据。
- [x] 聚焦测试、完整 pytest、compileall、前端语法/测试与文档事实校验通过。

## Decisions

- Provider capability 是显式闭集，不由 OpenAI-compatible 协议名称推断；没有可靠 usage
  字段时返回 `null`/不可用，不用本地猜测冒充 Provider 命中。
- Google 本阶段使用原生隐式缓存和 usage 可观测性；显式 CachedContent 需要资源创建、TTL、
  失效和清理生命周期，超出无状态 Agent Run 的安全范围。
- `input_tokens`、`output_tokens` 和 Provider 可用时的 `total_tokens` 保持响应原值。Anthropic
  的 cache read/cache creation 与 `input_tokens` 不是本地可安全合并的统一口径，因此分列
  采集且不计算 `uncached_input_tokens` 或命中率。

## Verification

- 聚焦回归：`.venv/bin/python -m pytest -q tests/test_prompt_cache_stability.py
  tests/test_task_shaped_budgets.py tests/test_api.py`：`63 passed, 4 subtests passed`。
- 完整回归：`.venv/bin/python -m pytest -q`：`739 passed, 131 subtests passed`。
- 编译检查：`.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- 前端语法/回归：`node --check ai_agent_platform/static/app.js` 通过；
  `node --test tests/test_chat_message_ui.mjs`：`15 passed`。
- 文档事实：`.venv/bin/python INTERVIEW_NOTES/validate.py`：校验 24 个 Markdown、46 个
  capability，通过；输出的 evidence review warnings 是共享脏工作树中既有相关证据提示，
  不是校验失败。
- 补充检查：`git diff --check`：通过；新增文件未包含凭据，也没有数据库 migration。
- 固定“分析下当前项目”合成 Provider 回归同时覆盖 2/40 模块仓库：每次 2 个模型请求、
  实际工具调用分别为 5/7、累计输入 10,000、Provider 原始合计 10,340、重试 0，
  且答案均覆盖项目用途、主要模块、运行入口和关键技术栈。第二次同 workspace/Profile 的
  缓存命中由 0 增至 7,500，未缓存输入由 10,000 降至 2,500，原始累计和 total 不变。
- replay amplification 通过既有 durable call identity、等价调用阻断和 replay 不计新增证据
  的回归约束；重复回放不会再次执行已完成的仓库子调用。
- 本地 Schema 词法估算：未整形 registry 为 18 个模型可见定义、约 2,166 Token；overview
  冻结视图为 1 个 `repo.collect_evidence` 定义、约 513 Token，减少 1,653（约 76.3%）。

## Result

已完成。新增 Provider-neutral 稳定前缀规范化、Run 级缓存键、按 task shape 冻结并延迟
暴露的工具 Profile，以及 OpenAI/Anthropic/Google/DeepSeek/兼容端点隔离的缓存请求与
usage 解析。Usage Ledger、Agent Run domain/API 和静态 UI 现在保留 Provider 原始累计
Token，并分列可靠的缓存读写、未缓存输入、命中率、当前保留上下文与前缀/Schema 估算。

README、Interview Notes、对应 Part 和 facts 已同步。UI 文案明确“缓存命中仍计入累计”并
标明本地估算字段；没有数据迁移。上面的 Token/缓存改善来自确定性合成 Provider 和本地
词法估算，用于验证计量语义与预算，不是真实厂商账单；本任务未发送真实 Provider 请求，
因此没有可报告的真实命中率、费用或延迟变化，也没有用本地耗时冒充网络延迟。
