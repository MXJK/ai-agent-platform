# UNIFY-CHAT-AGENT-WORKSPACE: 统一 Chat 与代码 Agent 入口

## Goal

在不合并 Chat 与代码 Agent 后端执行链路的前提下，让用户从同一个对话输入框选择快速对话或代码 Agent，并让代码 Agent 有限、真实地利用共享 Session 的最近历史。

## In scope

- 在现有 Chat composer 中增加“快速对话 / 代码 Agent”模式选择。
- 代码 Agent 模式复用已有 Agent 运行页、审批、轮询、指标和产物展示。
- Agent 运行完成后刷新共享 Session，使结果出现在对话历史中。
- 将裁剪后的最近会话上下文用于 Agent 仓库检索和结构化工具规划。
- 增加静态前端契约和会话上下文单元测试。

## Out of scope

- 合并 `/chat/stream` 与 `/agent/runs` 两条后端接口。
- 新增消息来源数据库字段或数据库迁移。
- 重构现有 Agent 页面、审批流、工具系统或任务队列。
- 部署、发布、合并、提交或推送。
- 修改当前分支上既有的 Gemini 流式修复语义。

## Acceptance criteria

- [ ] 默认仍为快速对话，现有 SSE Chat 行为保持不变。
- [ ] 用户可在同一输入框选择代码 Agent，并进入已有 Agent 运行/审批界面。
- [ ] Chat 与 Agent 继续复用同一个 `conversation_id`，Agent 终态会刷新共享消息历史。
- [ ] Agent 的检索查询和结构化工具规划包含有消息数和字符数上限的最近会话上下文。
- [ ] 不新增持久化字段或数据库迁移，不复制现有 Agent UI 组件。
- [ ] 项目规定的 pytest、compileall、JavaScript 语法检查和 diff 检查通过。

## Decisions

- 当前分支从带有未提交 `HARDEN-GOOGLE-STREAMING` 改动的工作树创建；本任务不回退或覆盖这些既有修改。
- Agent 模式从统一 composer 跳转到既有 Agent 运行页，以复用审批、事件、指标和 artifacts 展示。
- 暂不新增消息 `source` 字段；Agent 结果仍通过运行页和 `run_id` 区分，避免为展示标签引入数据库迁移。
- 最近会话上下文最多保留 6 条、1,800 字符，单条内容最多 280 字符；当前请求继续独立保留，不受历史裁剪影响。

## Verification

- `.venv/bin/python -m pytest -q`：通过，92 passed、1 个第三方 LangGraph pending deprecation warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过；沙箱首次因 macOS Python 缓存目录无写权限失败，授权写入系统缓存后通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- 真实浏览器主流程：默认快速对话；切换代码 Agent 后提示、placeholder 和按钮文案同步变化；统一输入可创建 Session、提交 Agent run 并自动进入既有运行页。
- 共享 Session 回归：首轮 Agent 完成后，用户消息和 Agent 回答均出现在 Chat 历史；第二轮 Agent trace/answer 显示读取 2 条历史消息。
- 1024×800 回归：viewport 与 body scroll width 均为 1024px，composer 750px、mode bar 726px，无横向溢出。
- 浏览器控制台：无 warning/error。

## Result

完成 Chat 与代码 Agent 的统一 composer 入口。默认 Chat SSE 行为保持不变；Agent 模式复用原运行、审批、事件、指标和 artifacts 页面，并在终态刷新共享 Session。Agent 最近会话上下文现实际进入仓库检索查询和结构化工具规划，同时受消息数、单条长度和总字符上限约束。未新增持久化字段、数据库迁移或重复 Agent UI；未提交、推送、合并、部署或发布。
