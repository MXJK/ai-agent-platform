# UNIFIED-LLM-GOVERNANCE: 模型白名单、统一用量账本与 Token 预算

## Goal

让所有模型调用在发送最终 Prompt 前完成供应商真实 Token 计数和预算决策，并将
Chat、Agent、对话压缩、RAG Ask 与 Embedding 统一写入可按会话和 Workspace
聚合的用量账本。

## In scope

- OpenAI Responses 流式请求传递 `max_output_tokens`。
- Provider 与 Provider/Model 组合使用精确 allowlist，拒绝请求级任意覆盖。
- OpenAI、Anthropic、Gemini 在发送最终 Prompt 前调用各自 token-count API。
- 用量记录支持无会话的后台/RAG/Embedding 调用、操作类型和预算决策元数据。
- Chat、Agent、对话压缩、RAG Ask 和 Embedding 写入同一账本。
- 会话和 Workspace 支持可配置 Token 预算；`reject` 为硬拒绝，
  `downgrade` 超预算后路由到 allowlist 内的廉价模型。
- API 和前端公开预算上限、已用、剩余、策略及操作类型分布。
- 补充 PostgreSQL 迁移、配置、README、Interview Notes 和自动化测试。

## Out of scope

- 金额计价、供应商账单对账和组织级成本中心。
- 跨进程强一致的并发预算预留。
- 为旧账本记录猜测操作类型、资源或 Workspace。
- 在目标数据库执行迁移、合并、发布或部署。

## Acceptance criteria

- [x] OpenAI 流式 Responses payload 包含配置的 `max_output_tokens`。
- [x] 未在 allowlist 的 Provider 或 Provider/Model 在调用前被明确拒绝。
- [x] OpenAI/Anthropic/Gemini 最终请求使用供应商 token-count API 进行输入计数。
- [x] Chat、Agent、对话压缩、RAG Ask 和 Embedding 都生成统一账本记录。
- [x] 无会话调用可持久化，旧会话/Workspace 聚合保持兼容。
- [x] 会话与 Workspace API 返回预算状态和按 operation 聚合。
- [x] `reject` 策略超预算返回明确错误，`downgrade` 策略使用配置的廉价模型。
- [x] 前端显示会话/Workspace 预算和统一 operation 用量。
- [x] PostgreSQL 迁移可生成 upgrade/downgrade SQL，内存实现一致。
- [x] pytest、compileall、JavaScript、迁移和 Interview Notes 校验通过。

## Decisions

- Provider 返回的完成 usage 是实际用量；调用前计数只用于预算决策和缺失 usage
  时的输入兜底，不伪装成供应商账单。
- `reject` 是硬预算；`downgrade` 是软预算，超阈值后切到 allowlist 内配置的
  fallback Provider/Model，并在账本和响应元数据中记录降级。
- 硬预算在最终 Prompt 精确计数后把 Provider 输出上限收紧到剩余额度；无法
  保留至少一个输出 Token 时在保存用户消息或调用模型前返回 429。
- allowlist 为空时只允许配置中的主 LLM、预算 fallback 和 Embedding 模型，
  保持默认配置可运行但不允许请求任意选模。
- 每次 Agent 模型 turn 单独入账，不再用一条 run 聚合覆盖；`done` 发出前完成
  记账，避免 RAG Ask 等消费方提前结束迭代而漏记。
- Embedding 用量进入相同账本并参与后续 LLM 预算预检；无 session/workspace 的
  独立知识库摄取保留为全局记录，不虚构归属。
- 并发请求按已提交账本做进程内预检；跨进程严格预留作为独立生产化演进。

## Verification

- `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m pytest -q`
  - 171 passed，1 个来自 FastAPI TestClient 的上游弃用 warning。
- `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m compileall -q ai_agent_platform tests evals migrations`
  - 通过。
- `node --check ai_agent_platform/static/app.js`
  - 通过。
- `git diff --check`
  - 通过。
- `python -m alembic heads`
  - `20260731_0012 (head)`。
- `python -m alembic upgrade head --sql`
  - PostgreSQL 离线 upgrade SQL 生成通过。
- `python -m alembic downgrade 20260731_0012:20260730_0011 --sql`
  - PostgreSQL 离线 downgrade SQL 生成通过。
- 本地 Interview Notes 在以功能分支作为证据根的隔离验证目录运行
  `INTERVIEW_NOTES/validate.py`
  - 11 份 Markdown、25 项能力验证通过。

## Result

已完成模型调用治理闭环：

- OpenAI 流式请求传递授权后的 `max_output_tokens`，三家真实 Provider 对最终
  Prompt/Tool Schema 调用官方 Token count 接口。
- Provider/Model 精确 allowlist 在外部调用前生效；会话与 Workspace 预算支持
  硬拒绝/输出收紧或审计式廉价模型降级。
- Chat、Agent、RAG Ask、语义会话压缩、Embedding 和后台无会话调用统一写入
  可迁移的 operation 账本，API/UI 展示累计、预算、operation 和最近最终 Prompt
  的精确输入 Token。
- API 与 Celery worker 使用同一账本装配；README、环境变量示例和本地面试手册
  已同步。

剩余生产化边界：预算预检读取已提交记录，没有跨进程 Token 预留；`downgrade`
是允许继续累计的软预算；独立知识库的无归属摄取只显示为全局账本记录。
