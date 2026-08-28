# AGENT-LIVE-ACTIVITY-ONLY: 精简 Agent 运行态为实时活动

## Goal

让 Code Agent 的消息内运行卡片只展示实时活动，移除重复的步骤流程与工具汇总，并用
克制的动态反馈标识仍在进行的活动。

## In scope

- Code Agent 运行卡片隐藏步骤时间线和相关工具汇总，只保留实时活动列表。
- 基于节点与工具生命周期判断活动是否仍在进行，而不是简单动画化最后一条记录。
- 为进行中的活动添加轻量动态标识，并适配 `prefers-reduced-motion`。
- 保留普通快速对话的既有执行过程、Agent 终态折叠和单一 polite live region。
- 增加前端行为/静态契约回归，同步 README 与 Interview Notes。

## Out of scope

- 修改 Agent SSE 事件协议、EventStore、LangGraph 拓扑或 Trace 审计页。
- 隐藏审批、等待输入、暂停、失败或最终回答状态。
- 重做消息气泡、composer 或右侧检查器。

## Acceptance criteria

- [x] Code Agent 卡片只展示实时活动，不再展示上方步骤流程或相关工具汇总。
- [x] 只有尚未收到对应完成事件的节点/工具活动具有动态效果。
- [x] 已完成、失败和终态活动保持静止，减少动态偏好下不执行循环动画。
- [x] 快速对话仍保留既有执行步骤展示。
- [x] 聚焦测试、完整测试、compileall、前端语法、文档校验和桌面/窄屏验收通过。

## Decisions

- 复用现有实时事件事实，不新增前端假进度；活动状态由完整事件序列归约。
- Code Agent 通过 `activityOnly` 呈现模式与快速对话隔离，避免扩大改动范围。
- 动效只作用于活动标记，不改变文字位置或可读性；事件列表未变化时不重建 DOM，
  避免流式回答增量反复重启动画。

## Verification

- `node --test tests/test_chat_message_ui.mjs`：12 项通过；新增活动生命周期归约测试覆盖
  工具完成/失败、节点完成和终态静止。
- `.venv/bin/python -m pytest -q tests/test_api.py::ApiTests::test_serves_unified_chat_and_workspace_agent_frontend`：
  1 项通过，静态资源版本和 activity-only 契约生效。
- `.venv/bin/python -m pytest -q`：`689 passed, 121 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`、
  `node --check ai_agent_platform/static/app.js`、`git diff --check`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 文件与 43 项能力；
  仅输出共享工作树相对既有 verified commit 的 evidence review warnings。
- 真实 Chromium + 本地 fake 服务：1200px 和 390×844 均无横向溢出；Code Agent
  卡片的步骤/工具子节点均为 0，活动工具获得 `execution-live-pulse`；减少动态偏好下
  动画缩短为单次 `0.01ms`。缓存版本更新后浏览器加载
  `20260828-agent-live-activity`。测试 Run 停在审批边界，未批准或执行写操作。
- Impeccable detector 按要求运行一次；本机缺少 HTML parser 而降级为正则模式，只报告
  本次范围外的既有侧边强调线和 width transition，新增实时活动样式未命中告警。

## Result

- Code Agent 的消息内运行卡片启用 `activityOnly`：步骤时间线和相关工具汇总不再生成
  子节点，只显示最多 16 条 EventStore 实时活动；快速对话继续使用原有步骤流程。
- 前端按 call ID 和 node completion 归约真正尚未结束的活动，只有该活动显示轻量脉冲；
  终态、工具结果/错误和已完成节点静止，单一 polite live announcer 保持不变。
- 活动签名未变化时复用列表 DOM，避免 `answer_delta` 高频更新反复重启动画；CSS/JS
  缓存版本已同步更新，避免浏览器继续使用上一任务资源。
- 中英文 README、Interview Notes Part 04/07 与 facts evidence 已同步。未修改 SSE/API、
  EventStore、Agent 图拓扑、审批策略或 Trace 审计页。
