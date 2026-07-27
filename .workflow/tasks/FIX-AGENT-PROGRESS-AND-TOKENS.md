# FIX-AGENT-PROGRESS-AND-TOKENS: 修复 Agent 轨迹跳步与 Token 展示

## Goal

保证 Agent 对话中的 LangGraph 执行阶段在运行中可见，即使任务很快完成也会
按顺序展示；同时在每条 Agent 回答下显示真实 Token 用量，并移除会话顶部
Token 展示。

## In scope

- 运行中从 LangGraph checkpoint 读取最新 trace，而非只读取开始/结束记录。
- 缩短前端轮询间隔，并顺序播放一次性返回的多个新增阶段。
- 所有阶段播放完成后再显示最终回答并折叠执行过程。
- 聚合 Agent 结构化规划和回答生成期间的真实输入、输出、思考 Token。
- 将 Agent Token 写入运行结果指标和 API 响应。
- 在 Agent 回答卡片底部展示耗时、阶段、工具和 Token。
- 移除对话页顶部的会话 Token 胶囊。

## Out of scope

- 暴露模型私有 chain-of-thought。
- 将 Agent 轮询协议改成 SSE 或 WebSocket。
- 为历史消息与 Token 记录新增数据库关联字段或迁移。
- 部署、提交、推送或回退当前工作区中的 RAG 改动。

## Acceptance criteria

- [x] Agent 运行中可从 checkpoint API 读取已经完成的 LangGraph 阶段。
- [x] 快速任务一次返回多个阶段时，前端仍按顺序展示后再出现答案。
- [x] Agent 回答底部展示真实输入、输出、思考和总 Token。
- [x] 对话页顶部不再展示会话 Token 用量。
- [x] Chat 回答卡片原有 Token 展示保持不变。
- [x] pytest、compileall、JavaScript 语法和 diff 检查通过。

## Decisions

- 后端运行记录仍只持久化关键状态；运行中 GET 动态合并 checkpoint 快照，
  避免每个 LangGraph 节点都产生额外数据库写入。
- 前端对后端一次返回的 trace 增量做短时顺序播放，最终答案必须等待播放队列。
- Agent Token 由 LLM 完成调用返回的 provider usage 聚合，不使用字符估算。
- Agent Token 保存在现有 result JSON 的 metrics 中，不新增数据库列或迁移。

## Verification

- `.venv/bin/python -m pytest -q`：108 passed；保留 1 个第三方 Starlette
  弃用 warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- 定向 34 项测试覆盖运行中 checkpoint trace、LLM usage 聚合、Agent API
  Token 指标、顶部 Token 移除和逐步播放前端契约，全部通过。
- 差异审查确认未新增凭据、生成文件、数据库迁移、部署或外部写入；现有 RAG
  迁移与相关未提交改动来自前序任务，本任务未回退或扩大其范围。

## Result

Agent 运行查询现在会在状态为 `running` 时动态读取 LangGraph checkpoint，
将已经完成的节点、当前节点和下一节点合并到 API 响应中。前端轮询间隔缩短
为 300ms；如果极快任务仍一次返回多个新节点，会以 240ms 的可见间隔依次
播放，并在播放队列结束后才显示最终答案和折叠执行过程。

LLM 完成调用现在返回 provider usage，并通过请求上下文安全聚合同一次 Agent
运行的输入、输出和思考 Token。指标随 Agent result JSON 持久化并显示在对话
回答底部和 Agent 详情指标中。会话顶部 Token 胶囊已移除；普通 Chat 每条回答
下方的 Token 展示保持不变。
