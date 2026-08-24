# CODEX-LIKE-THINKING-UI: Codex 风格思考过程体验

## Goal

让统一对话工作台中的思考过程更接近 Codex：运行时聚焦当前动作，完成后收敛为
低干扰摘要，展开后仍能快速复盘真实执行阶段、工具与状态。

## In scope

- 重构聊天消息内执行过程的摘要文案、状态语义与可访问标记。
- 将步骤列表改为有当前/完成/暂停/失败状态的连续工作轨。
- 让工具信息融入过程详情，并优化完成态折叠、窄屏和 reduced-motion 体验。
- 补充静态前端契约测试并完成真实浏览器验收。

## Out of scope

- 暴露或推断模型私有 chain-of-thought。
- 修改后端事件、Agent 生命周期、工具权限、审批或 API schema。
- 引入前端框架、构建链、外部字体或第三方 UI 依赖。
- 部署、发布、迁移、提交或推送。

## Acceptance criteria

- [x] 运行态以一行“正在工作 + 当前动作 + 已用时间”为主，详情默认展开并标记当前步骤。
- [x] 完成态自动折叠为低干扰工作摘要，仍可通过原生键盘交互展开完整过程。
- [x] 展开内容能区分已完成、当前、等待、失败和取消状态，且仅展示后端真实轨迹。
- [x] 工具、阶段数和时间信息层级清晰，不与最终回答争夺注意力。
- [x] 过程更新通过克制的 live region 暴露，状态不只依赖颜色，并尊重 reduced-motion。
- [x] 320px 以上宽度无过程组件横向溢出。
- [x] 项目验证、静态契约与浏览器回归通过。

## Decisions

- 保留原生 `details/summary`，以获得稳定的键盘和屏幕阅读器折叠语义。
- “思考过程”指可解释的执行轨迹、阶段摘要和工具名，不等于模型私有思维链。
- 延续现有 Orbit Navy 设计系统；本次只以细线工作轨作为视觉签名，不新增色板。
- 运行态强调最后一个步骤，终态按成功、暂停/受阻、失败/取消分别呈现语义状态。
- 只在首次进入终态时自动折叠，之后尊重用户手动展开；恢复运行时重新展开。
- 终态耗时取前端已展示时长与后端时长的较大值，避免事件到达后时间倒退。

## Verification

- `.venv/bin/python -m pytest -q tests/test_api.py`：35 passed。
- `.venv/bin/python -m pytest -q`：562 passed，60 subtests passed。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 文件和 42 项能力；仅有既有 evidence-review 提示，无验证错误。
- Safari 真实页面回归：验证运行态当前动作/计时/当前步骤、取消态摘要、成功态自动折叠、原生展开及完整阶段可访问树。
- In-app Browser 因本机地址被客户端策略阻止，Chrome 控制不可用，因此按浏览器技能回退到 Safari 完成视觉与交互验收。

## Result

- 重构执行过程为 Codex 风格的低干扰摘要与连续工作轨，覆盖运行、完成、暂停、受阻、失败和取消语义。
- 运行时不再重复渲染占位正文；终态保留最终回答为视觉主角，并允许随时展开真实执行轨迹与相关工具。
- 加入可访问状态文本、当前步骤标记、焦点样式、非纯颜色状态图形、窄屏规则与 reduced-motion 兼容。
- 更新缓存版本、静态契约测试、README 和面试手册能力证据。
- 无后端/API/schema/数据迁移影响；经用户后续明确授权，本次收尾直接提交并推送 `main`，不执行合并、发布或部署。
