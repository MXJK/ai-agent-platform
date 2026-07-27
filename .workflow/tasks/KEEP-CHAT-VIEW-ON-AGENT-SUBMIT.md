# KEEP-CHAT-VIEW-ON-AGENT-SUBMIT: Agent 提交后停留在对话工作台

## Goal

用户在对话工作台选择代码 Agent 并发送消息后，页面保持在对话工作台；
代码 Agent 页面继续承载运行详情、事件和审批。

## In scope

- 移除统一输入框提交 Agent 时的自动页面跳转。
- Agent 请求被接受后，在当前对话中立即显示用户消息和运行提示。
- 在对话工作台显示 Agent 完成、失败或等待审批状态。
- 保持代码 Agent 页面现有运行详情、轮询、审批、指标和产物行为。
- 补充前端契约与真实浏览器回归。

## Out of scope

- 合并 Chat SSE 与 Agent 运行接口。
- 将审批操作复制到对话工作台。
- 修改 Agent 后端执行、持久化或工具权限语义。
- 部署、迁移、发布或提交本任务改动。

## Acceptance criteria

- [x] 在对话工作台发送 Agent 任务后，当前视图和 URL hash 保持为 Chat。
- [x] Agent 请求成功入队后，对话区立即显示用户消息和已接收提示。
- [x] Agent 到达终态后，共享会话消息刷新并替换临时提示。
- [x] 等待审批、失败和后台超时在对话工作台提供明确状态。
- [x] 代码 Agent 页面仍可查看同一运行的详情和执行审批。
- [x] 普通 Chat 模式与代码 Agent 页面直接运行行为保持不变。
- [x] 项目规定测试、Python 编译和 JavaScript 语法检查通过。

## Decisions

- 统一输入框不再调用 `switchView("agent")`；Agent 详情页继续作为用户主动
  导航的二级界面。
- `runAgent` 接受可选的 `onSubmitted` 回调，只在 `/agent/runs` 成功返回后
  添加即时对话反馈，避免工作区或会话校验失败时显示虚假的已提交状态。
- 即时提示是临时 UI；Agent 到达终态后沿用现有 `refreshMessages`，以服务端
  共享 Session 中的用户和 Assistant 消息替换临时内容。
- 提交失败时恢复输入内容；等待审批和运行失败时在对话区追加明确指引，但不
  复制审批控件或错误详情。
- 代码 Agent 页面直接运行仍调用同一个 `runAgent`，不传回调，因此原有行为
  保持不变。

## Verification

- `.venv/bin/python -m pytest -q`：99 passed，保留 1 个第三方 Starlette
  弃用 warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- 静态前端契约确认统一输入框不再包含 `switchView("agent")`，并包含提交成功
  回调与留在对话工作台的提示。
- 真实浏览器即时状态：发送 Agent 任务后 `location.hash=#chat`、
  Chat panel active、Agent panel inactive；对话区立即显示用户消息和
  “代码 Agent 已接收任务”，状态为“Agent 运行中”。
- 真实浏览器终态：仍保持 `#chat`，共享会话刷新为正式 Agent 回答，状态为
  “Agent 已完成”，发送按钮恢复可用。
- 主动进入代码 Agent 页面后，同一 `run_id` 显示已完成、完整回答和 20 条
  运行事件；浏览器控制台 error/warn 为 0。
- 使用内存 Repository、内存向量库、进程内队列和 fake provider 启动临时
  验收服务；验收后已停止，未执行数据库迁移、部署或外部写入。

## Result

用户从对话工作台选择代码 Agent 并发送消息后，页面现在保持在原对话视图。
请求入队后会立即看到自己的消息和 Agent 已接收提示，完成后自动刷新为正式
回答。运行轨迹、指标、产物、错误详情和审批仍集中在代码 Agent 页面，用户可
按需进入查看。
