# CHAT-AGENT-CHANGE-REVIEW: 对话工作台内联代码变更审阅

## Goal

移除独立“代码 Agent”页面，把 Agent 运行、审批、结果、文件变更、Diff 与 ChangeSet
应用闭环统一放入“对话工作台”的 Agent 模式，形成类似 Codex / Claude Code 的会话内
代码变更体验。

## In scope

- 从主导航和页面路由中移除独立代码 Agent 入口与页面。
- 对话工作台 Agent 模式继续复用现有 Agent Run、SSE、审批、控制和恢复 API。
- 在对应 assistant 消息内展示运行状态、事件进度、工具审批、修改文件清单、增删行摘要、
  可展开 Diff、ChangeSet 状态与真实工作区应用/拒绝操作。
- 对 `patch_only` 给出显眼、可操作的解释，不能让用户误以为 Sandbox 变更已经落盘。
- 保留 Workspace、身份、权限、Sandbox 和 ChangeSet 校验边界。
- 更新前端回归测试、README 和访谈手册。
- 用真实前端完成 Agent 对话、审批、变更审阅和应用测试。

## Out of scope

- 修改 Agent/ChangeSet HTTP API Schema。
- 绕过审批或默认打开真实工作区写入。
- 修改模型路由、Sandbox 权限、数据库迁移、部署、提交或推送。
- 重新设计知识库、项目记忆、模型管理或 MCP 页面。

## Acceptance criteria

- [x] 主导航中没有“代码 Agent”，旧 `#agent` 路由安全回退到对话工作台。
- [x] 对话工作台选择 Agent 模式后可发起、恢复和观察 Agent Run。
- [x] 审批、暂停、取消、转向和需要用户输入均在对应对话消息内完成。
- [x] 变更结果明确展示修改文件、Diff 和 ChangeSet 状态。
- [x] `direct` 模式提供二次确认后应用到真实工作区；`patch_only` 明确显示不会落盘且禁用应用。
- [x] 用户切换会话时不会串用旧 Run、审批或 ChangeSet 状态。
- [x] 桌面和移动布局、键盘焦点、reduced-motion 均可用。
- [x] 专项测试、全量 pytest、compileall、前端语法和访谈手册校验通过。
- [x] 真实前端完成 Agent 对话到 ChangeSet 应用的端到端验收。

## Design direction

- 主题：面向开发者的“会话即工作树”，沿用现有深色工程控制台视觉语言。
- 布局：保留单一对话主轴；运行状态成为 assistant 消息的内联工作记录，不再建立第二套页面。
- 标志性元素：`Change review` 消息块像一张可折叠的提交单，顶部用文件账本概括变更，底部
  固定显示安全模式和唯一主操作。
- 视觉约束：沿用现有颜色与字体 token，减少页面级装饰；用 monospace 只表达路径、Diff 和
  运行 ID，避免做成另一套泛化 Dashboard。

## Decisions

- 保留现有 Agent Run、cursor SSE、审批/控制和 ChangeSet API，不引入第二套后端协议；
  独立页面只是重复前端状态，因此直接删除导航、路由面板和对应 DOM 绑定。
- 以 `data-agent-run-id` 绑定 assistant 消息与 Run。会话切换会递增观察/ChangeSet 请求代次，
  并同时校验 conversation ID 和 DOM 连接状态，避免迟到请求污染新会话。
- 终态按 Run 加载 ChangeSet，并把 A/M/D 文件状态、逐文件和总增删行、补丁摘要、校验状态、
  完整 Diff 与操作区呈现在同一消息。Diff 统计同时兼容带/不带 `diff --git` 的统一补丁。
- `patch_only` 显示“尚未写入磁盘”并禁用应用；`direct` 的主操作明确写入真实工作区且先弹出
  补丁摘要二次确认；`worktree` 使用单独文案，避免误称写入源目录。
- 旧 `#agent` 和历史本地偏好都规范化为 `#chat`，避免升级后出现空页面。

## Verification

- `.venv/bin/python -m pytest -q`
  - `386 passed, 49 subtests passed`
- `.venv/bin/python -m compileall ai_agent_platform tests evals`
  - 通过。
- `node --check ai_agent_platform/static/app.js`
  - 通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`
  - `Validated 12 Markdown files and 37 capabilities`；仅输出既有 changed-evidence 复核提醒。
- `git diff --check`
  - 通过。
- 真实浏览器（`http://127.0.0.1:8000`）：
  - `sess_b2e453338f43` / `run_fc29aae15027` 恢复 `patch_only` ChangeSet，显示 `index.html`
    `+46`、`style.css` `+186`、总计 `+232/-0`，完整 Diff 可展开，应用按钮禁用；
  - `sess_a658f445b852` / `run_b706aa1a371e` 恢复真实 `direct` 已应用记录，显示“已应用到真实
    工作区”；
  - `#agent` 自动改写为 `#chat`，侧栏独立入口计数为 0；
  - 390×844 viewport 下文档宽度与 viewport 同为 390px、操作区纵向排列、无横向溢出；
  - 浏览器控制台无 warning/error。

## Result

已完成。代码 Agent 不再是独立页面；对话工作台的 Agent 模式现在覆盖发起、恢复、实时
进度、审批/输入/暂停、转向/取消、回答、变更审阅和 ChangeSet 推广闭环。用户可以直接看见
Agent 修改的文件、A/M/D 状态、增删行和完整 Diff，并在安全模式允许时二次确认后应用。

文档影响：已同步 `README.md`、`README.en.md`、`INTERVIEW_NOTES.md`、
`INTERVIEW_NOTES/07-Go网关与产品工作台.md` 与 `INTERVIEW_NOTES/facts.json`，移除独立
Agent 页的过时描述并记录会话内 ChangeSet 体验。

未执行 commit、merge、release、migration 或 deployment。工作树还包含前一项
`AGENT-RUN-OUTPUT-RECOVERY` 已完成任务的未提交实现；本次没有覆盖或回退这些改动。
