# LITELLM-GATEWAY-RELIABILITY: 借鉴 LiteLLM 强化模型网关重试与回退

## Goal

参考 LiteLLM 官方仓库的 Router 可靠性设计，在不引入新的网关运行时依赖、也不改变
现有 Provider 原生协议适配的前提下，把统一的固定重试升级为可配置的错误级策略，
正确消费上游 `Retry-After`，并让退避与回退决策进入路由 Trace。

参考基线：`BerriAI/litellm@891b23d6804eff7daa71adcd6411017e74a3f9b4`。
仓库 enterprise 目录以外代码为 MIT；本任务只借鉴公开设计，不复制 enterprise 代码。

## In scope

- 为稳定的 Provider 错误代码配置独立最大重试次数，并保留 `LLM_MAX_RETRIES` 兼容默认值。
- 从普通 HTTP 与 SSE 错误响应解析 delta-seconds / HTTP-date 两种 `Retry-After`。
- 使用有上限的指数退避和抖动；合理的上游建议优先于本地退避。
- 在 Route Trace 中记录每次重试、错误代码、有效预算、等待时间与等待来源。
- 保持首个非空文本 delta 之后禁止自动重放的安全边界。
- 为 Chat 流、原生工具调用、HTTP 错误解析、配置解析与跨 Provider fallback 补测试。
- 同步 `.env.example`、中英文 README 和工作流结果。

## Out of scope

- 直接嵌入或部署 LiteLLM Proxy、替换现有 Provider SDK/HTTP 适配器。
- 分布式熔断/冷却状态、Redis 路由状态、在线定价或自适应质量学习。
- 首个 delta 后续传、自动重放或请求迁移。
- 调用真实付费 Provider、迁移数据库、合并、发布或部署。

## Acceptance criteria

- [x] `LLM_RETRY_POLICY_JSON` 可按错误代码覆盖重试次数，未知键和非法值启动即失败。
- [x] 未配置策略时，所有 retryable 错误继续使用 `LLM_MAX_RETRIES`。
- [x] 429/5xx 的 `Retry-After` 被安全解析并受本地最大等待上限约束。
- [x] 无有效 `Retry-After` 时使用有上限的指数退避与抖动。
- [x] Chat 与工具调用共享同一重试预算和等待算法。
- [x] Route Trace 公开重试次数、错误、等待秒数和 `retry_after` / `exponential_backoff` 来源。
- [x] 错误预算耗尽后仍按现有规则在首个 delta 前 fallback；首个 delta 后不重放。
- [x] 专项测试、全量 pytest、compileall、文档校验和 diff 检查通过。

## Decisions

- 保留项目自有轻量网关，不新增 `litellm` Python 依赖；当前模型注册、预算账本、
  Provider 原生工具消息和流边界已经深度集成，整体替换的回归面大于本次收益。
- 策略键使用项目已经对外进入 Trace 的稳定错误代码，而不是 Provider 特有异常类；
  `default` 覆盖其余 retryable 错误。
- `Retry-After` 只影响同一候选的下一次重试，不延长熔断恢复窗口；熔断仍由现有
  Provider 健康管理器负责。
- 等待值必须有本地上限。无效、负数或超过接受上限的上游值回落到指数退避，防止
  Provider Header 把工作线程无限挂起。
- 抖动注入源和 sleep 函数由 `LLMClient` 构造边界提供，生产使用标准库默认值，测试
  使用确定性替身，不产生真实等待。

## Verification

- 使用根 checkout 的解释器从任务 worktree 运行
  `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m pytest -q`：
  `655 passed, 91 subtests passed in 42.30s`。
- 专项运行 `tests/test_config.py tests/test_llm_streaming.py tests/test_model_router.py
  tests/test_native_tool_calling.py`：`87 passed, 8 subtests passed in 3.12s`。
- 合并到最新本地 `main` 后在根 checkout 再次运行全量测试：
  `656 passed, 91 subtests passed in 43.82s`；新增的 1 项来自此前已合入的 Token
  预算显示回归测试，组合树无失败。
- 使用同一解释器运行 `-m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- 差异审查：无数据库迁移、无生成物、无凭据值、无依赖变更；只包含配置、模型网关、
  路由 Trace、对应测试、中英文 README、环境示例和工作流记录。
- 当前提交不跟踪 `INTERVIEW_NOTES.md`、`INTERVIEW_NOTES/` 或验证脚本，因此
  Interview Notes 校验不适用；用户可见配置与架构说明已同步 `README.md` 和
  `README.en.md`。
- 验证阶段未启动 Docker、未调用真实 Provider，也未执行迁移、部署或发布；Git
  提交、推送与合并在验证完成后得到用户明确授权。

## Result

已完成基于 LiteLLM Router 设计思想的模型网关可靠性升级，但没有引入 `litellm`
运行时依赖或复制 enterprise 代码：

- `LLM_RETRY_POLICY_JSON` 以严格白名单错误代码覆盖每类最大重试次数；未覆盖项通过
  `default` 或原有 `LLM_MAX_RETRIES` 保持兼容。
- Chat 流与原生工具决策共享同一等待算法。普通 HTTP 和 SSE 错误都能解析秒数或
  HTTP-date `Retry-After`；不可信、非正数或超过本地接受上限的值回落到有界指数
  退避。抖动、退避上限、Retry-After 上限均可配置，NaN/无穷值启动即失败。
- 5xx 现在使用稳定的 `llm_server_error` 代码，可与限流、超时、传输错误分开治理。
  Route Trace 新增 `retries`，公开每次候选重试的错误、序号、有效预算、等待时间和
  来源；最终失败仍保留原有 `failures` 与 fallback 证据。
- Fake Provider 与 HTTP 替身覆盖默认兼容、按类跳过/增加重试、即时跨 Provider
  fallback、Retry-After、过大 Header、本地退避、工具调用和 SSE 路径。

剩余边界：熔断状态仍为单进程；同步 Provider 调用期间的等待仍占用当前执行线程；
合理的 `Retry-After` 最长可等待运维设置的上限。多副本共享冷却和异步调度不在本任务
范围内。变更位于 `codex/litellm-gateway-reliability` worktree；用户已明确授权
提交、推送和合并，但未授权部署、发布或数据库迁移。功能提交为 `0816fa5f`，任务分支
验证记录提交为 `30aee4a5`，两者已推送；最新本地 `main` 的合并提交为 `7bbc7a2c`，并已
通过上述组合验证。本工作流收尾提交将随 `main` 一并推送。
