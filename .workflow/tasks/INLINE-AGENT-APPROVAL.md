# INLINE-AGENT-APPROVAL: 对话内 Agent 审批与恢复

## Goal

让 Chat 中发起的 Agent 任务像 Codex 一样在原对话内展示并处理审批、补充输入和继续执行，同时保证刷新或重新打开会话后仍能恢复未完成的 Run。

## In scope

- 在助手消息中渲染待审批工具、风险说明、可选反馈以及拒绝/继续操作。
- 审批后在原消息中继续展示 Agent 进度与最终回答，不要求切换到代码 Agent 页面。
- 为会话提供最新 Agent Run 查询，并在加载、刷新和启动恢复时找回挂起或最近的 Run。
- 在对话中呈现 `waiting_input`、`paused` 等可继续状态及对应操作。
- 保留代码 Agent 专页作为详细运行轨迹与备用控制面。
- 补齐 API、仓储、前端静态契约和恢复流程测试。
- 同步 README、访谈手册与事实证据。

## Out of scope

- 取消或弱化现有工具权限审批策略。
- 自动批准任何写入、命令或外部工具调用。
- 修改 Agent 图的工具规划或执行语义。
- 部署、迁移或清理已有挂起 Run。

## Acceptance criteria

- [x] Chat 中的 `waiting_approval` 助手消息直接显示待审批工具及“拒绝执行”“确认并继续”。
- [x] Chat 中批准或拒绝后，当前消息原地更新并继续跟踪 Run，最终回答仍显示在同一对话。
- [x] `waiting_input` 和 `paused` 可在 Chat 中输入补充内容并继续。
- [x] 刷新或重新打开会话后，能恢复该会话最新 Run 的状态、审批卡和事件进度。
- [x] 已完成会话不会错误显示旧的挂起操作，切换会话不会串 Run。
- [x] 代码 Agent 专页现有审批、控制、事件和结果功能无回归。
- [x] 桌面与移动布局、键盘焦点、忙碌态和错误恢复可用。
- [x] pytest、compileall、前端语法、文档校验和浏览器视觉 QA 通过。

## Decisions

- 后端增加按会话读取最新 Run 的受所有权约束接口；不把瞬时 Run ID 塞进 session 模型。
- `waiting_approval` 仍是安全暂停状态，前端停止无意义轮询，但保留明确可操作的内联检查点。
- 内联卡片复用现有 Agent Run 数据和 `/resume`、`/continue` 控制接口，不创建第二套审批协议。
- 会话消息仍只持久化用户与最终助手回答；挂起卡片由持久化 Run 重建，避免把审批协议文本伪装成普通助手消息。
- 代码 Agent 专页继续提供完整轨迹；Chat 优先承载当前任务必须完成的人机交互。
- 视觉沿用现有海军蓝工作台与 Avenir/Inter/SFMono 字体，使用琥珀色风险边框、青色继续状态和紧凑工具明细；不引入新的装饰性主题。
- SSE 读取返回后再次核对 Run、会话和观察器代次；事件列表响应也只允许更新仍匹配的当前 Run，封住切换会话期间的晚到更新窗口。
- 审批、补充输入和暂停继续使用各自的完成回执，不把所有继续操作误写成“已确认执行计划”。
- 390×844 移动视口为审批卡预留足够的末端滚动安全区，拒绝和继续按钮不会落入 sticky composer 下方。

## Verification

- `.venv/bin/python -m pytest -q`：271 passed，11 subtests passed；仅有既有的
  Starlette `httpx` 弃用警告。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `go test ./gateway/...`：通过；本机 PATH 无 Go，使用 Go 官方 SHA-256 校验通过的
  1.26.5 darwin/arm64 临时工具链执行，未修改仓库或系统安装。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 12 个 Markdown 文件和
  30 项 capability；evidence review 提示为工作区相对上次验证提交已有变更的提醒。
- `git diff --check`：通过。
- 浏览器端到端 QA：使用 memory stores 和仓库既有测试 Planner 创建真实
  `waiting_approval`、`waiting_input` Run；桌面与 390×844 移动视口均可看到完整
  内联操作，移动端批准/拒绝按钮位于 sticky composer 上方。刷新和重新打开会话均通过
  latest Run 恢复，切换到新会话不会串卡；批准、拒绝、补充输入后的原消息分别收拢为
  正确回执并完成运行，浏览器控制台无 warning/error。

## Result

- 新增受会话所有权约束的 latest Agent Run 查询，内存与 PostgreSQL 仓储保持一致。
- Chat 助手消息内直接承载审批、追问和暂停恢复；仅展示真正需要审批的工具，折叠精确
  参数，支持可选反馈、忙碌态、错误反馈和已确认/已拒绝回执。
- 会话加载会恢复最新 Run；SSE 与轮询观察器捕获 Run/会话 ID，避免切换会话后的晚到
  更新串页。代码 Agent 详情面板仍保留完整备用控制面。
- 修复审批卡被 sticky composer 遮挡、移动端操作拥挤、通用英文审批原因和权限标签、
  详情页混入只读工具等 UX 问题。
- 收紧异步观察器的 Run/会话/代次校验，SSE 阻塞读取返回、轮询响应和控制请求晚到时
  都不会覆盖后来打开的会话；补充输入与暂停继续使用各自语义准确的完成回执。
- README 中英文版及本地 interview handbook/facts 已同步。没有迁移、部署或外部写入；
  未提交，`last_verified_commit` 不变。
