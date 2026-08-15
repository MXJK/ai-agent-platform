# SLASH-COMMAND-COMPOSER：对话框 Slash 能力与交互优化

## Goal

让统一对话页像 Codex / Claude Code 一样，在输入框键入 `/` 即可发现并使用当前
会话与 Workspace 真正可用的内置命令、Skill 和 MCP 工具；同时改善键盘操作、
草稿、输入框尺寸和长对话滚动等交互逻辑，不改造既有视觉设计语言。

## Acceptance criteria

- [x] 输入框在光标所在的 slash 查询处打开能力面板，并按内置命令、Skill、MCP
      工具分组过滤。
- [x] 面板支持方向键、Enter/Tab、Escape、鼠标选择和可访问性语义；中文输入法
      组合态 Enter 不会误发送。
- [x] Skill 选择通过 HTTP Agent 请求进入现有显式 Skill 调用链，参数与调用元数据
      被 RunContext 冻结；不可用 Skill 由后端 fail closed。
- [x] MCP 工具选择只展示当前有效工具池内的已注册工具，并以非授权的工具偏好进入
      Agent 上下文；权限、审批和沙箱边界保持不变。
- [x] 内置 slash 命令至少支持切换快速对话/代码 Agent、新建会话和打开 MCP 管理页。
- [x] 对话输入自动增高、按会话保存草稿；发送按钮反映空输入/忙碌/归档/工作区状态。
- [x] 长对话仅在用户位于底部附近时自动跟随，并提供显式“回到底部”入口。
- [x] API、静态前端契约和关键解析逻辑有自动化覆盖，README 与面试手册同步。

## Permitted scope

- 对话与 Agent API schema、只读能力目录、Query Kernel/RunContext 参数透传。
- 静态前端对话页的 HTML、JavaScript、交互所需最小 CSS。
- 相关测试、README、README.en 和模块化面试手册。
- 不新增数据库迁移，不部署，不提交或推送。

## Decisions

- 能力目录按当前用户、会话和 Workspace 通过 `ExecutionContextFactory.preview()`
  计算，避免展示进程注册了但项目配置、模型能力或权限已排除的工具。
- Skill 使用既有限定名与 `skill_arguments`，不复制 Skill 指令到浏览器。
- MCP 选择是“优先使用此工具”的显式用户意图，不直接执行任意 Schema，也不授予
  新权限；工具仍由原生 tool-calling、中央权限解析和审批链执行。
- slash token 保留在用户输入、会话历史和任务正文中以便编辑与审计，同时解析为
  结构化调用字段；Agent 通过结构字段获得 Skill 与 MCP 工具偏好语义。

## Verification

- `.venv/bin/python -m pytest -q`：通过，`388 passed, 49 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 12 个 Markdown 和
  37 个 capability；只报告相对旧提交事实基线的 evidence review 提醒。
- `git diff --check`：通过。
- 真实 Codex 内置浏览器冒烟：工作区选择后 `/` 菜单与空能力提示、命令名前缀排序、
  `/chat` 键盘执行、Escape、自动增高、刷新草稿恢复和 `/new` 均通过；控制台无错误。

## Result

已完成。新增按当前身份、会话、Workspace、模型和有效工具池生成的只读 composer
能力目录；浏览器统一输入框可发现并调用内置命令、Skill 与 MCP 工具。Skill 复用
既有显式 invocation 和 fail-closed 校验，MCP 只冻结已验证的非授权工具偏好。

交互侧补齐键盘/鼠标菜单、IME 保护、输入自动增高、每会话本机草稿、发送状态、
近底部跟随与显式回到底部。README、README.en、模块化面试手册和 facts.json 已同步。
未新增迁移、依赖或外部写入；未提交、未部署。
