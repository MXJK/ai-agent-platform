# POLISH-CHAT-MESSAGE-UI: 修复 Agent 提交可见性并精修消息界面

## Goal

让 Agent 问题在提交后立即、稳定地出现在当前对话中，并参考 Codex 的聊天信息层级，
将用户问题与 AI 回答改造成更克制、易读、可操作的消息界面。

## In scope

- 修复 Agent 模式等待 Run 创建成功后才显示用户问题的时序缺陷。
- 为提交中、提交成功和提交失败提供连续、不会丢失问题文本的界面状态。
- 精简用户与 AI 消息的头像、标签、容器和操作区视觉层级。
- 保持 Markdown、执行过程、审批、ChangeSet、错误恢复和消息操作可用。
- 覆盖窄屏、长内容、键盘焦点与 reduced-motion 状态。
- 更新前端契约测试及相关项目文档。

## Out of scope

- 修改 Agent Run、消息持久化或 SSE API 契约。
- 重做 composer、侧栏或其他工作台页面。
- 复制 Codex 的品牌资产或逐像素复刻其界面。

## Acceptance criteria

- [x] Agent 问题在创建 Run 的网络请求开始前就显示在当前对话中。
- [x] 创建 Run 失败时问题文本仍保留，输入框恢复，并显示可理解的失败状态。
- [x] 创建成功后同一条用户消息不重复，AI 占位状态与 Run 状态连续衔接。
- [x] 用户问题使用紧凑、右对齐的弱强调气泡；AI 回答使用开放式阅读布局。
- [x] 复制、编辑、重试、Trace、审批和执行信息不受视觉调整影响。
- [x] 390px、桌面宽度、长文本和 reduced-motion 契约通过。
- [x] JavaScript、聚焦测试、完整测试和项目规定验证通过。

## Decisions

- 借鉴 Codex 的信息层级与消息节奏，但沿用本项目现有色彩、字体和语义 token。
- 用户问题采用乐观呈现；Run 创建失败不撤回问题，避免造成“没有提交”的误解。
- AI 回答不再使用整块卡片边框，让正文、执行过程和恢复动作成为主要视觉内容。

## Verification

- 合并提交 `ba756d1961da2c7d14c6d3bc0c2620d1cde91b7a` 在本地 `main` 上完成以下最终验证。
- `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m pytest -q`：
  663 passed，107 subtests passed（42.42s）。
- `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs`：7 passed；
  覆盖 Run 创建前乐观呈现、创建失败回调、delivery state 和既有模型配置键盘交互。
- `node --check ai_agent_platform/static/app.js` 与 `git diff --check`：通过。
- `tests/test_api.py::ApiTests::test_serves_unified_chat_and_workspace_agent_frontend`：通过；
  静态契约锁定 onReady 在 `/agent/runs` 前执行、无重复用户消息、失败恢复、68ch 长内容、
  430px 窄屏 padding、overflow wrap 与既有 reduced-motion 声明。
- Impeccable detector 按要求只运行一次；缺少 HTML/CSS parser，降级正则扫描。处理了本次
  聊天路径内错误卡和 inline checkpoint 的 3px 侧边色条；其余发现是既有其他页面样式，
  或 2px Token meter 的有界 width 过渡。
- `INTERVIEW_NOTES/validate.py`：24 个 Markdown、43 项 capability 通过；仅有相对旧提交的
  既有 evidence-review warnings。
- 真实浏览器使用 `RUNTIME_PROFILE=custom`、fake 模型和全内存存储验证任务分支；服务日志
  确认 `POST /api/v1/agent/runs` 返回 202。Run 创建等待期间已显示用户问题、“正在提交…”
  和助手等待态；接受后 DOM 只有 1 条 user / 1 条 assistant，delivery state 清空并原位绑定
  `run_id`。1440×960 下用户问题右对齐、助手正文与审批为开放布局；390×844 下 body
  scrollWidth=390、对话 client/scrollWidth=364，无横向溢出，用户/助手气泡宽 334/330px，
  底部导航与 composer 可见；控制台无 warning/error。Fake Agent 提出变更计划后已拒绝执行，
  未产生源码写入。

## Result

- 根因是 Agent 模式把 `appendChatMessage("user", ...)` 放在 `/agent/runs` 成功后的
  `onSubmitted` 回调中；普通 Chat 则在请求前呈现，所以 Run 创建慢或失败时 Agent 问题
  会暂时或永久不出现在消息区。
- `runAgent` 新增 Run 请求前的 `onReady` 与失败 `onSubmissionError` 生命周期；composer
  在 `onReady` 中只创建一次用户问题和助手等待态，成功后原位绑定 `run_id`，失败后保留
  问题、恢复输入并显示内联恢复卡。
- 消息 UI 参考 Codex 的信息层级但沿用现有 token：用户问题改为右对齐弱强调气泡，助手
  回答改为无整块卡片边框的开放正文，身份、时间和操作进一步降噪；移动端、长 Markdown、
  执行轨迹、审批、错误和 ChangeSet 保持现有能力。
- README 中英文与本地模块化面试手册已同步。没有 API/schema、迁移、依赖、Provider
  调用或部署影响。功能提交 `78192f3cfb6f0b6a12e9edf06ff7fcb0cf8b4317` 已在人工确认后
  通过合并提交 `ba756d1961da2c7d14c6d3bc0c2620d1cde91b7a` 合入本地 `main`；未推送、
  部署或重启 Docker 服务。
