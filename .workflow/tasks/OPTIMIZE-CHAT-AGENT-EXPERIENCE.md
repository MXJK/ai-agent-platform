# OPTIMIZE-CHAT-AGENT-EXPERIENCE: 优化对话与 Agent 执行反馈

## Goal

让统一对话工作台实时展示可解释的 Agent 执行阶段、工具调用、思考耗时与
Token 用量，并优化短消息气泡和输入区控件。

## In scope

- 在对话消息内实时展示 Agent 的 LangGraph 节点、阶段摘要和工具调用。
- Agent 完成后自动折叠执行过程，保留用户手动展开入口。
- 在回答消息下展示响应耗时与输入、输出、思考及总 Token。
- 提供会话级 Token 用量查询与汇总展示。
- 让短消息气泡按内容收缩，同时保持长消息和代码块可读。
- 从统一输入框移除文件和模型选择，只保留模式选择。
- 补充 API 与静态前端契约测试。

## Out of scope

- 暴露模型未提供的私有 chain-of-thought 文本。
- 改造 Agent 为 SSE/WebSocket 推送协议。
- 修改 Agent 工具权限、审批或 LangGraph 执行语义。
- 数据库迁移、部署、提交或推送。
- 回退当前工作区中既有的 RAG 改动。

## Acceptance criteria

- [x] Agent 运行中可在当前对话消息看到最新 LangGraph 阶段与已调用工具。
- [x] Agent 完成后执行过程默认折叠，仍可展开查看完整轨迹。
- [x] Chat 回答展示响应耗时以及输入、输出、思考、总 Token。
- [x] 当前会话可查看累计 Token 用量，并在切换/刷新会话时同步更新。
- [x] 短消息气泡不会无意义占满整行，长内容仍能响应式换行。
- [x] Composer 仅保留快速对话/代码 Agent 模式选择。
- [x] 项目 pytest、compileall、JavaScript 语法与 diff 检查通过。

## Decisions

- “思考过程”只展示后端真实提供的可解释执行轨迹、阶段摘要和工具名，不推断
  或伪造模型私有思维链。
- Agent 继续通过现有轮询接口获取状态；在统一对话消息内增量更新 trace。
- Chat Token 使用现有 SSE usage/done 事件；会话累计用量通过只读 API 返回
  已持久化的 TokenUsageRecord。
- Agent 当前执行链尚未持久化 LLM 调用 Token，因此 Agent 卡片展示可准确
  提供的执行耗时、阶段数和工具调用数，不伪造 Token 数字。

## Verification

- `.venv/bin/python -m pytest -q`：106 passed；保留 1 个第三方 Starlette
  弃用 warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- 定向 `tests/test_api.py tests/test_session_service.py`：21 passed；覆盖静态
  前端契约、会话 Token API 与已有会话用量持久化。
- 差异审查确认本任务没有新增凭据、生成文件、数据库迁移、部署或外部写入；
  当前工作树中的 RAG 迁移和相关改动来自前序任务，未被本任务回退。

## Result

统一对话工作台现在会在回答卡片内实时显示可解释的执行过程。快速对话展示
模型请求、流式输出、响应耗时及输入/输出/思考/总 Token；代码 Agent 展示
LangGraph 节点、阶段摘要和实际工具名称，运行完成后自动折叠，用户可随时
展开复盘。会话头部新增累计 Token 用量，并通过只读会话 API 在加载会话或
完成回答后刷新。

短消息气泡移除了继承自通用富文本容器的固定最小高度，并按内容宽度收缩；
长消息仍受对话列宽约束并正常换行。Composer 已移除文件和模型快捷选择，仅
保留快速对话/代码 Agent 模式；模型配置仍可在全局设置中调整。
