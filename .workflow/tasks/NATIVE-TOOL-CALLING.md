# NATIVE-TOOL-CALLING: 原生工具协议与可靠执行闭环

## Goal

将现有依赖 Prompt JSON 的一次性工具规划升级为供应商原生 Function Calling，
并建立工具结果回灌、多轮 observe/replan、完整 Schema 校验、可靠执行和 MCP
语义归一化。

## In scope

- 为 OpenAI、Anthropic、Google 和 fake provider 建立统一的原生工具决策契约。
- 通过 provider API 的原生 tools/function declarations 发送 ToolSpec。
- 将模型 tool call 与 tool result 通过稳定 call ID 关联。
- 在 LangGraph 中增加有轮次、调用数和上下文预算上限的 observe/replan 循环。
- 使用完整 JSON Schema 校验工具输入和输出。
- 为工具执行增加超时、仅限只读/幂等工具的有限重试和调用幂等缓存。
- 归一化 MCP `content`、`structuredContent` 与 `isError` 语义。
- 保留规则规划器作为 fake/offline 降级和确定性测试路径。
- 更新 README、面试手册、事实索引和相关测试。

## Out of scope

- MCP Streamable HTTP transport。
- 用户/租户 RBAC、审批 grant 重构和生产级策略服务。
- 将 local workspace runner 升级为安全认证沙箱。
- 提交、推送、合并、部署或数据库迁移。

## Acceptance criteria

- [x] LLM adapter 使用原生 tools 参数，而不是要求模型输出工具 JSON。
- [x] OpenAI、Anthropic、Google 与 fake provider 都映射到统一 ToolDecision。
- [x] 工具结果按 call ID 回灌模型，模型可在上轮结果基础上继续选工具。
- [x] 多轮循环有最大轮次、最大调用数和无进展保护。
- [x] 输入和输出按完整 JSON Schema 校验，错误包含稳定 code 和 JSON path。
- [x] 每个 ToolCall 都有稳定 call ID。
- [x] 通用执行器支持超时；只读/幂等瞬时错误可有限重试。
- [x] 同一 run 内重复 call ID 不会重复执行工具。
- [x] MCP `isError=true` 映射为失败，结构化和文本内容得到统一结果。
- [x] 工具选择、回灌、Schema、超时、重试、幂等及 MCP 错误语义均有测试。
- [x] README、面试手册和 facts.json 与实现保持一致。
- [x] 项目规定的 pytest、compileall 和面试手册校验通过。

## Decisions

- 原生 provider 差异收敛在 LLM integration 层，Agent 只依赖统一 ToolDecision。
- 工具执行仍由后端 ToolRegistry 完成；原生 Function Calling 不被视为授权。
- 自动重试只允许 `read_only` 或显式 `idempotent` 工具，外部副作用不自动重放。
- 幂等缓存以 `run_id + call_id` 为边界，并验证相同 call ID 的工具名和参数摘要。
- 规则规划器继续用于离线模式；生产 LLM planner 不再解析 Prompt JSON 工具计划。
- 通用工具超时使用线程 future；它能按时返回 `tool_timeout`，但不能强制终止已经
  进入不可中断调用的第三方函数，生产强隔离仍需进程或远端执行器。

## Verification

- `.venv/bin/python -m pytest -q`：120 passed，1 个既有
  `StarletteDeprecationWarning`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，11 份 Markdown、19 项能力；
  输出的 evidence review 为旧事实基线后的变更提醒，不是校验错误。
- `git diff --check`：通过。

## Result

已完成。OpenAI、Anthropic、Google 使用原生工具协议，fake provider 保留确定性
离线路径；LangGraph 将工具结果按 call ID 回灌并进行有界 observe/replan。
ToolRegistry 现在执行 Draft 2020-12 输入/输出校验、稳定错误码、通用超时、受限
重试和 run 级幂等。MCP 工具可进入同一模型选择入口，并归一化结构化内容、文本
block 与 `isError`。README 与本地面试手册已同步。

本任务没有数据库迁移、部署或外部写入。实现提交
`395697197e8145806585f76cbf60b846873acdf1` 已通过上述完整验证，并记录为
`last_verified_commit`；下一步在 GitHub 审阅分支并按需创建 PR。
