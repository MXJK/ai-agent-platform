# AGENT-SSE-LIVE-EVENTS: Agent 实时 SSE 事件与前端呈现

## Goal

让 Agent Run 在执行过程中实时产生、持久化并通过 SSE 推送节点进度、工具生命周期、
安全的推理摘要和最终回答增量，前端按独立事件类型即时呈现。

## In scope

- Agent 节点开始/完成和安全推理摘要的实时事件。
- 工具选择、开始、结果和错误的实时事件及幂等落库。
- 最终回答 `answer_delta` / `answer_completed` 事件。
- SSE cursor 续传与前端多事件 reducer、执行时间线和增量回答。
- 聚焦回归、完整测试与架构/产品文档同步。

## Out of scope

- 暴露 Provider 原始隐藏思维链或 `reasoning_content`。
- 改变 Agent 图拓扑、工具审批策略或 Chat 快速对话协议。
- 部署、合并、重启根 checkout Docker 栈或真实生产数据迁移。

## Acceptance criteria

- [x] Run 终态前可观察到 `node_started` 和 `node_completed` SSE 事件。
- [x] 工具执行实时产生 `tool_selected`、`tool_started`、`tool_result|tool_error`，重试不重复投影同一语义事件。
- [x] 最终回答生成时产生有序 `answer_delta`，结束时产生 `answer_completed`。
- [x] `reasoning_summary` 只包含面向用户的结构化摘要，不暴露原始隐藏思维链。
- [x] 前端在每种实时事件到达时更新执行过程、工具状态或回答正文，而非只响应 `node_completed`。
- [x] cursor 重连保持顺序且不重复消费已确认事件。
- [x] 聚焦测试、完整 pytest、compileall、文档校验与 diff check 通过。

## Decisions

- EventStore 继续作为轮询、SSE 和审计的统一事实源；事件必须在执行源头追加。
- 使用稳定 event key 对节点和工具语义事件去重，保留 worker 重投安全性。
- “中间推理”定义为安全的 `reasoning_summary`，不传输 Provider 隐藏思维链。
- 回答 delta 采用小批次持久化，兼顾实时性和数据库写入放大。
- 前端最多保留 16 条实时活动和 280 字工具结果预览，并用单一 polite live region
  播报最新活动，避免流式回答期间重复读屏。

## Verification

- 回归测试先观察到预期失败：运行中 EventStore 只有 `run_queued/run_started`，且
  `LLMClient` 没有增量完成接口。
- 聚焦实时节点、慢工具、回答 delta 与前端静态契约：`4 passed`。
- 相关 Agent/LLM/API/Repository 套件：`107 passed, 8 subtests passed`。
- 完整测试：`648 passed, 87 subtests passed`。
- `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- 模块化面试手册基线校验：24 个 Markdown、43 项能力通过；facts evidence review
  仍提示自上次记录提交以来存在多项待同步证据。
- Impeccable detector：仅报告既存 CSS 侧边色条和 width transition，本次新增实时活动
  样式没有新增检测告警。

## Result

Agent Run 现在在执行源头持久化节点开始/完成、安全推理摘要、工具选择/开始/结果及回答
增量；稳定 event key 使 Worker 重投和终态反投影不会重复同一工具事实。前端对每种事件
实时归约，显示当前阶段、工具结果和回答正文，并在终态保留实时活动。

文档影响：中英文 README 已更新。模块化 `INTERVIEW_NOTES` 为 gitignored，本 task
worktree 不包含这些文件；在用户授权合并前不修改根 checkout 中仍描述 main 行为的本地
手册。合并后需同步 Part 04、Part 07 与 `facts.json`，运行手册校验，并在根 Docker 栈做
一次桌面/移动端浏览器实测。

实现与自动化验证已完成，但未提交、未合并、未重启 Docker，也未改写根 checkout。
