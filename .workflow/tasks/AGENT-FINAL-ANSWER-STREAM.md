# AGENT-FINAL-ANSWER-STREAM：Agent 最终回答实时流式显示

## Goal

让原生工具循环和预算收尾中的回答在 Provider 生成时即通过 SSE 显示，不再等完整回答后拆块。

## Scope

- 接入 OpenAI、Anthropic、DeepSeek 和 Google 的原生工具流式响应。
- 保留工具参数完整性校验、Provider 原始块、用量和最终运行结果。
- 增加回答重置事件，清除继续工具调用或未通过完成门的临时文本。
- 不更改快速对话、不进行真实付费模型请求、部署、迁移或提交。
- 保留并行进行的 SESSION-HISTORY-IN-PAGE 改动及其工作流归属。

## Acceptance criteria

- [x] 模型响应尚未结束时，Agent EventStore 已收到正文增量，前端即时渲染。
- [x] 工具参数和私有思考不作为回答显示；中间轮次不污染最终答案。
- [x] 流中断不静默拼接重试文本；完成事件不重复发送已流式显示的全文。
- [x] 普通完成与强制收尾均支持流式；旧测试/扩展的非流式接口保持兼容。
- [x] 聚焦回归、完整 pytest、compileall、前端检查和手册校验通过。
- [x] README、Interview Notes 和 facts 同步。

## Decisions

- 使用可选 on_delta 回调启用 Provider 流式；工具只在完整响应解析和校验后交给执行器。
- 临时正文使用 answer_reset/answer_delta；终态快照仍是权威答案。
- 已向调用者发送文本后禁止 Provider 内静默重试或切模型。

## Verification

- 聚焦 Python 回归：`72 passed, 36 subtests passed`。
- 完整 `.venv/bin/python -m pytest -q`：`689 passed, 121 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --test tests/test_chat_message_ui.mjs`：7 项通过；`node --check` 与
  `git diff --check`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 文件与 43 项能力；
  仅输出相对既有 verified commit 的 evidence review warnings。
- 本地 fake 服务 + ego-browser：真实页面依次呈现“第一段”→“第一段，第二段”→
  `answer_reset` 后空正文→“最终回答”；终态快照仍为“最终回答”。临时服务与浏览器空间
  已关闭，没有发起真实 Provider 请求。

## Result

- `LLMClient.decide_tools/finalize_tools` 增加可选正文 delta 回调，OpenAI、Anthropic、
  DeepSeek 与 Google 原生工具适配器在流式聚合完整 Provider 转录的同时即时转发公开正文。
- 新增 `NativeStreamAccumulator`，在终止事件前持续保存工具参数、usage、reasoning/signature
  等 Provider 块；工具仍只在完整解析、JSON 校验和权限检查后执行。
- Agent 使用稳定事件键发送 `answer_reset`/`answer_delta`。工具轮次、完成门拒绝与失败流不会
  污染终答；已公开正文后禁用静默重试/跨模型 fallback；终态不重复重放已显示全文。
- 前端按 reset 边界归约回答并继续以终态 Run 快照为权威；README、英文 README、Interview
  Notes 与 facts 已同步。
- 未执行提交、部署、迁移或真实付费模型调用。
