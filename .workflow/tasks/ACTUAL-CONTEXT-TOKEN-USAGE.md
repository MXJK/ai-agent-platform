# ACTUAL-CONTEXT-TOKEN-USAGE: 展示真实模型上下文输入

## Goal

让对话输入区展示最近一次前台模型请求实际记录的 Prompt 输入 Token，避免把仅包含
会话消息/摘要的本地估算误称为完整上下文占用。

## In scope

- 从会话 Token 账本中选择最近一次用户可见的 Agent/Chat/RAG 请求记录。
- 以该请求的实际 `input_tokens` 作为上下文主指标；后台记忆提取、压缩、Embedding
  等记录不得覆盖它。
- 仅当最近请求的 Provider/Model 与当前输入预算来源一致时展示占比。
- 保留会话历史/摘要估算，但降级为明确标注的辅助信息。
- 修正上下文详情的键盘、触摸和窄屏可访问性。
- 同步 API 契约、README、Interview Notes、事实映射与回归测试。

## Out of scope

- 远程 Provider 调用、计费估算、生产数据库迁移或历史记录回填。
- 修改 Provider 原始 usage 数值或把多个模型请求累计值伪装成单次上下文占用。
- 提交、推送、部署或发布。

## Acceptance criteria

- [x] 主指标使用最近一次前台模型请求的 `input_tokens`，不再使用历史估算作为分子。
- [x] 后台操作晚于 Agent 请求完成时，主指标仍指向最近 Agent/Chat/RAG 请求。
- [x] 百分比只在最近请求与预算 Provider/Model 匹配时出现；否则只展示实际绝对值。
- [x] 历史消息估算、累计会话消耗和实际 Prompt 输入具有不同且准确的标签。
- [x] 上下文详情可由鼠标、键盘和触摸打开，窄屏不丢失信息。
- [x] 聚焦测试、完整 pytest、compileall、前端语法、文档校验和浏览器验收通过，
      或将非本任务失败精确记录为 blocker。

## Decisions

- “真实上下文占用”定义为最近一次已经提交给前台 Agent、Chat 或 RAG Ask 模型请求的
  `input_tokens`。发送下一次请求前，系统不能把尚未发生的 Prompt 称为实际值。
- 会话累计 Token 继续包含所有账本记录；记忆提取、压缩等后台操作不参与“最近前台
  Prompt”选择。
- 输入预算来自当前模型解析结果。只有预算和最近记录的 Provider/Model 同源时才显示
  占比，否则仅显示实际绝对值，避免跨模型比较。
- `budget_provider`/`budget_model` 只扩展 Token Usage API 响应，不进入通用领域上下文
  对象，避免污染上下文装配与 golden 快照。
- 原 `estimated_tokens` 保留为“历史消息/摘要估算”详情，不再作为完整 Prompt 分子；旧的
  `context_shares` 图例不再作为当前 Cogent 主指标展示。

## Verification

- `node --check ai_agent_platform/static/app.js`：通过。
- `node --test tests/test_chat_message_ui.mjs`：22/22 通过。
- Token Usage/API/context focused pytest：通过；characterization golden 在响应层隔离修正后
  通过。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：24 个 Markdown、45 个 capability 通过；
  evidence review warnings 为共享工作区的变更提醒。
- `git diff --check`：通过。
- 浏览器桌面实跑：Agent 记录输入 `2,135`，历史消息估算 `50`，UI 主指标显示
  `2,135 / 68,608 · 3.11%`。
- 浏览器记忆实跑：Agent 记录输入 `2,142`，随后 `cogent_memory_extract` 输入 `256`；UI
  仍显示最近前台 Prompt `2,142`，会话累计正确显示 `2,547`。
- 浏览器可访问性：键盘聚焦可打开、Escape 可关闭、移动端点击可打开；390px 下
  `scrollWidth == innerWidth == 390`。
- `.venv/bin/python -m pytest -q`：`881 passed, 12 skipped, 111 subtests passed`，仅
  `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent` 失败；
  该共享工作区既有问题期望 `OSError`，当前实现抛出 `ValueError`，不属于本任务修改。

## Result

- 功能、API、文档、聚焦验证和真实浏览器验收已完成。
- 仓库级关闭仍被上述既有 symlink 异常类型测试阻塞；本任务未扩大范围修改该文件安全
  契约。
