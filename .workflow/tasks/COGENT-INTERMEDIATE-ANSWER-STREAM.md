# COGENT-INTERMEDIATE-ANSWER-STREAM：区分中间模型文本与最终回答

## Goal

让 Cogent 的公开文本保持实时流式能力，同时确保带工具调用的中间模型轮次不会被主回答
气泡误认为最终回答；结构化追问恢复后继续同一个 Run，只有最终无工具轮次保留为回答正文。

## In scope

- 带工具调用的模型轮次在完整响应确定后发送持久 `answer_reset` 展示边界。
- `AskUserQuestion` 进入 `waiting_input` 时只保留结构化问题 UI；回答后恢复原 Run。
- Worker 从未完成模型流恢复时清除已丢弃的临时正文。
- Web 与 Textual 客户端统一消费 `answer_reset`。
- 补充运行时、Web 和 Textual 回归，并同步中英文 README 与 Interview Notes。

## Out of scope

- 不删除 Provider transcript 中与工具调用配对的 assistant content。
- 不改变 Provider 协议、权限、工具执行顺序或结构化问题格式。
- 不提交、推送、迁移数据库或调用真实付费模型。
- 不处理 `INSTALL-RECOMMENDED-MCP-SERVERS` 已记录的 ManagedFiles 测试阻塞。

## Acceptance criteria

- [x] `content + tool_calls` 仍写入内部 transcript，但 SSE 在工具执行前持久化 `answer_reset`。
- [x] `content + AskUserQuestion` 不留下主回答正文，用户回答后同 Run 继续并只显示最终文本。
- [x] 未完成模型流的恢复先清除临时正文，重复恢复不重复同一 reset 语义事件。
- [x] Web 与 Textual 都按 reset 清空累计回答，等待输入状态和问题控件仍可用。
- [x] 聚焦测试、compileall、文档校验、Impeccable detector 和真实浏览器验收通过。
- [x] 完整 pytest 通过，或精确记录与本任务无关的既有 blocker。

## Decisions

- 保留低延迟乐观流式；在 Provider 完整响应确认含工具调用后提交 reset，而不是缓冲全部正文。
- reset 只影响展示层；canonical transcript 保留原 assistant content 和完整工具配对。
- 使用按模型请求编号生成的稳定事件键，使崩溃恢复和 Worker 重投保持幂等。

## Verification

- `.venv/bin/python -m pytest -q tests/test_cogent_runtime.py tests/test_cogent_acceptance.py tests/test_cogent_tool_transcript.py tests/test_cogent_tui.py tests/test_cogent_http_client.py tests/test_cogent_api.py tests/test_query_service.py`：`119 passed, 12 skipped`。
- `node --test tests/test_chat_message_ui.mjs`：`22 passed`。
- 新增关键场景聚焦复跑：Python `4 passed`；Node `1 passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 `24` 个 Markdown、`44` 项能力，通过；共享工作区其他未提交改动触发证据复核 warning。
- `node --check ai_agent_platform/static/app.js`、`jq empty INTERVIEW_NOTES/facts.json`、`git diff --check`：通过。
- Impeccable detector：`[]`。
- ego-browser 真实页面：临时正文显示后被 reset 清空；`waiting_input` 仅显示等待提示和结构化问题；恢复完成后问题卡移除、临时正文不再出现且只显示最终回答。页面与相关 API 均返回 `200`。截图辅助调用超时，但 DOM、可访问性树与状态断言均已完成。
- `.venv/bin/python -m pytest -q`：`829 passed, 12 skipped, 108 subtests passed, 1 failed`。唯一失败为既有且与本任务无关的 `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`：测试期望 `OSError`，`ManagedFiles.read` 对 `ELOOP/ENOTDIR` 的既定实现抛出 `ValueError`。

## Result

实现完成并通过本任务的运行时、HTTP/恢复语义、Web、Textual、静态与真实浏览器验收。
由于仓库要求的完整 pytest 仍有上述任务外既有失败，工作流按约定保持 `blocked`，不把当前未提交修改标记为已验证 commit。
