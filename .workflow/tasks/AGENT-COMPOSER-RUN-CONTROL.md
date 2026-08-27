# AGENT-COMPOSER-RUN-CONTROL: 精简 Agent 对话运行控制

## Goal

移除对话内容区的运行中转向与取消控制，并将暂停和继续收拢到输入框右侧的同一紧凑按钮。

## In scope

- 删除助手消息底部运行中转向、暂停和取消组成的独立控制卡。
- 将当前 Run 的暂停/继续合并为输入区右侧同一个紧凑按钮，并随状态切换。
- 暂停后允许直接继续，也允许把输入框中的可选补充要求随继续请求提交。
- 保留等待审批、Agent 主动追问、checkpoint 历史和快速对话停止功能。
- 增加前端回归测试并同步 README、Interview Notes 与 facts 证据。

## Out of scope

- 删除或变更后端 pause、continue、steer、cancel API 与领域能力。
- 改造审批、追问、ChangeSet 或 checkpoint 交互。
- 重做对话工作台的整体视觉体系。

## Acceptance criteria

- [x] 运行中的助手消息不再渲染占空间的转向/暂停/取消控制卡。
- [x] 当前 Run 为 running 时，输入区右侧显示可访问的紧凑暂停按钮，发送按钮让位。
- [x] Run 到达 paused 后，同一按钮原位切换为继续；空输入可直接继续，非空输入作为可选补充要求提交。
- [x] Run 离开 running/paused 后恢复普通发送按钮；快速对话停止、审批、追问与 checkpoint 行为不回归。
- [x] 桌面与窄屏布局均不溢出，聚焦测试、完整测试、compileall、前端语法和文档校验通过。

## Decisions

- 只精简运行中与手工暂停态的控制 UI，不删除后端能力，审计和其他入口仍可使用原协议。
- paused 不再生成大块内联 checkpoint 卡；等待审批和 waiting_input 仍保留必要的上下文表单。
- 复用 composer 的输入内容作为 continue 的可选 message，避免再造转向输入区。
- pause 请求尚未到达安全边界时保留当前输出正文，只更新状态与按钮忙碌态，避免控制操作清空流式内容。

## Verification

- `node --test tests/test_chat_message_ui.mjs`：9 项通过，覆盖 running→pause、paused→continue、可选 message 和既有流式回答。
- 聚焦 API 静态契约：`tests/test_api.py::ApiTests::test_serves_unified_chat_and_workspace_agent_frontend` 通过。
- `.venv/bin/python -m pytest -q`：`689 passed, 121 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`、`node --check ai_agent_platform/static/app.js` 与 `git diff --check`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 文件与 43 项能力；仅输出相对既有 verified commit 的 evidence review warnings。
- `impeccable detect` 完成一次机械检查；本机缺少 HTML parser 模块而降级为正则模式，告警均位于本次未改动的既有样式区域。
- 本地 fake 服务 + ego-browser 真实 Chromium：1200px 桌面和 390px 窄屏均无横向溢出；pause/continue 按钮均为 40×40 且完整位于 composer 内，状态、可访问名称和 paused 占位文案正确。临时浏览器空间与服务已关闭。

## Result

- 删除助手消息下方的运行中转向/暂停/取消控制卡及对应死 CSS；审批、waiting_input、ChangeSet 和 checkpoint 交互保持原位。
- composer 右侧新增同一紧凑控制：running 显示暂停并替代发送按钮，paused 原位切为继续；继续可直接点击，也可携带输入框中的补充要求。
- 状态更新与 SSE 增量会同步刷新控制；暂停请求 pending 时阻止重复点击且不清空当前输出，离开 running/paused 后恢复普通发送。
- README、英文 README、被仓库忽略但受项目契约管理的 Interview Notes 与 facts 已同步；新增前端回归和静态契约断言。
- 未执行提交、部署、迁移、真实 Provider 请求或后端 API 删除。
