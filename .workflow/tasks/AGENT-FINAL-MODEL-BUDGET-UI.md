# AGENT-FINAL-MODEL-BUDGET-UI: Agent 最终回答模型上限预算与终态 UI

## Goal

最终回答使用实际选中模型的最大输出 Token 能力，并修复 completed 事件快照覆盖实时答案的 UI 竞态。

## In scope

- 让内置代码 Agent 的最终 text-only 请求按路由实际候选模型声明的
  `max_output_tokens` 请求输出额度。
- 保留上下文窗口、Usage Ledger 和 Provider 实际能力对最终额度的安全下调。
- 修复前端收到 completed 事件快照时，因尚无权威 `result.answer` 而覆盖已流式
  输出正文的问题。
- 增加 Python/Node 回归测试，并同步 README、Interview Notes、事实索引和静态资源
  cache-buster。

## Out of scope

- 改变规划、mutation、压缩等非最终回答阶段的 Token 预算。
- 绕过会话/工作区 Token 预算、模型上下文窗口或 Provider 限制。
- 调整模型路由排序、价格、健康检查、重试和跨 Provider fallback 策略。
- 部署、迁移或调用真实付费模型。

## Acceptance criteria

- [x] 最终回答使用实际候选模型的 `max_output_tokens`；模型未声明上限时回退
      `LLM_MAX_OUTPUT_TOKENS`。
- [x] Usage Ledger、上下文余量和 Provider 限制仍可下调实际请求额度。
- [x] 普通工具轮次及旧自定义 planner 的阶段预算语义不变。
- [x] completed 事件快照优先保留 `streamed_answer`，权威 `result.answer` 到达后
      正常替换；只有权威结果确实为空时显示空答案占位。
- [x] 聚焦测试、完整 pytest、compileall、Interview Notes 校验和 diff 检查通过。

## Decisions

- 由 `LLMClient` 在候选模型已确定后解析最终输出额度，避免在 Agent state 中复制
  可能过期的模型能力数据。
- `AGENT_FINAL_MAX_OUTPUT_TOKENS` 仅保留给不支持模型上限模式的旧自定义 planner；
  内置 `LLMAgentPlanner` 请求模型能力上限。
- UI 同时修复终态发布顺序和渲染 fallback，避免网络刷新失败时重新出现同类竞态。

## Verification

- 预算/UI/API 聚焦回归：`.venv/bin/python -m pytest -q
  tests/test_agent_runtime_framework.py tests/test_native_tool_calling.py tests/test_api.py`：
  `101 passed, 23 subtests passed`。
- 前端消息与终态事件回归：`node --test tests/test_chat_message_ui.mjs`：`16 passed`；
  `node --check ai_agent_platform/static/app.js`：通过。
- 完整回归：`.venv/bin/python -m pytest -q`：`781 passed, 133 subtests passed`。
- 编译检查：`.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- 文档事实：`.venv/bin/python INTERVIEW_NOTES/validate.py`：校验 24 个 Markdown、
  46 项 capability，通过；evidence review warnings 来自共享脏工作树中的既有并发改动，
  不是校验失败。
- `git diff --check`：通过。范围审查未发现凭据、部署、迁移或外部写入；静态资源
  cache-buster 已更新。Git 提交与推送仅在后续用户明确授权下作为收尾动作执行。

## Result

完成。内置 `LLMAgentPlanner` 在最终 text-only 阶段请求“使用实际候选模型的注册输出
上限”；`LLMClient` 等路由选出候选后再解析额度，因此手动选模、自动路由和 Provider
fallback 都引用实际模型，而不是 Agent 进程的固定 4096。模型未声明上限时回退
`LLM_MAX_OUTPUT_TOKENS`，实际 Provider 参数仍取模型能力、上下文剩余空间和 Usage Ledger
授权后的安全值。规划、mutation 和旧自定义 planner 继续使用既有阶段预算。

前端不再先发布只有 completed 状态、尚无 `result` 的事件快照；即使调用方直接渲染这种
快照，终态正文也优先使用已归约的 `streamed_answer`。权威 `result.answer` 到达后正常
替换，只有权威结果和流式正文都确实为空时才显示“没有返回文本内容”。README、Interview
Notes、`.env.example` 和静态 cache-buster 已同步。
