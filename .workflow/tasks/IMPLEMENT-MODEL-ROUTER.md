# IMPLEMENT-MODEL-ROUTER: 真正的多 Provider 模型路由

## Goal

把当前按请求显式选择 Provider/Model 的适配层升级为独立模型路由能力：先做能力与
健康过滤，再按质量、成本或延迟排序；模型调用在首个文本 delta 之前可以跨
Provider fallback，首个 delta 之后绝不自动重放。

## In scope

- 新增独立 `ModelRouter`、模型目录、路由需求和可序列化路由 Trace。
- 配置模型工具调用、结构化输出、上下文窗口、输入/输出价格、质量和延迟指标。
- 实现 `quality`、`cost`、`latency` 三种确定性策略。
- 实现 Provider 近期错误率、closed/open/half-open 状态与简单熔断恢复。
- 将路由与跨 Provider fallback 接入流式聊天、普通 completion 和原生工具调用。
- SSE/结构化日志暴露候选、过滤原因、选择理由、失败及最终模型。
- 使用可编程 Fake Provider 覆盖限流、超时、熔断、恢复和流边界。
- 同步 README、模块化面试手册和事实索引。

## Out of scope

- 分布式共享熔断状态、在线价格同步、线上质量反馈或自适应学习排序。
- 首个文本 delta 之后的自动续传、重放、请求迁移或生成任务持久化。
- 部署、迁移、提交、推送或调用真实付费模型。

## Acceptance criteria

- [x] 模型目录可配置能力、价格、上下文长度、质量分和预估延迟。
- [x] 三种策略在相同候选集上产生可测试、确定性的选择结果。
- [x] 能力不足或上下文不足的模型不会进入可执行候选。
- [x] Provider 达到错误阈值后熔断，冷却后 half-open 探测成功可恢复。
- [x] 首个 delta 前的 Provider 失败会跨 Provider fallback。
- [x] 首个 delta 后失败只返回 partial error，不重放到备用模型。
- [x] Trace 记录全部候选、过滤/选择理由、失败原因和最终模型。
- [x] Fake Provider 测试覆盖 429、超时、熔断、恢复与流开始边界。
- [x] 仓库要求的测试、编译与 Interview Notes 校验全部通过。

## Decisions

- 显式 `provider`/`model` 请求作为硬过滤条件保留兼容性；未显式指定时才在完整
  模型目录上按策略路由。
- 熔断状态按 Provider 聚合，路由排序按模型粒度执行。
- “流开始”定义为首个业务文本 `delta`，在此之前的 usage/done 暂存以便安全
  fallback；首个 delta 发出后任何失败都禁止切换。
- 熔断器是单进程内状态，适合当前服务边界；多副本共享健康状态留作后续演进。
- 只把 retryable Provider 错误计入熔断窗口；输出上限、参数等非瞬时业务错误仍写
  路由 Trace，但不把健康状态误判为基础设施故障。
- 模型目录为空时从原有 `LLM_PROVIDER`/`LLM_MODEL` 派生单候选；真实多模型竞争
  必须显式配置至少两个目录项，避免默认启动意外调用付费 Provider。

## Verification

- `.venv/bin/python -m pytest -q`：通过，`173 passed`；仅有现存
  Starlette/httpx 弃用警告。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 11 个 Markdown
  文件和 25 项能力；changed evidence review 为预期提醒，不是校验失败。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- 差异审查未发现凭据、生成文件或数据库迁移；所有新增文件均属于模型路由、测试
  或任务记录范围。

## Result

已完成独立 ModelRouter、模型能力/价格/上下文目录、三种路由策略、Provider
近期错误率与 closed/open/half-open 熔断、跨 Provider fallback 和可序列化路由
Trace。Chat SSE 与前端执行轨迹会公开候选、选择理由、失败及最终模型；普通
completion 和原生工具调用也返回路由 Trace。Fake Provider 测试验证 429、超时、
熔断恢复，以及首个文本 delta 后不自动重放。

README、模块化 Interview Notes 和事实索引已同步；同时纠正了当前分支中指向尚未
合入源码的统一用量账本/精确预算陈述。未提交、推送、合并、迁移、部署或调用真实
付费模型。当前熔断状态仍是进程内的，价格/质量/延迟仍由部署配置维护。
