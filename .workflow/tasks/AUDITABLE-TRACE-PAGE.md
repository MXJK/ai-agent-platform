# AUDITABLE-TRACE-PAGE: 独立可审计 Trace 页面

## Goal

在主导航中新增独立的 Agent Trace 审计页，让用户从最近 Run 中选择一次执行，按时间线检查工具选择、精确参数、工具结果、错误、审批决定和状态转移，而不必依赖对话 Inspector 的摘要视图。

## In scope

- 新增只读的最近 Agent Run 列表 API，并保持现有 actor 授权边界。
- 将工具选择/执行结果与审批决定记录为可复盘的 Run 事件。
- 新增 Trace 审计导航与页面，提供 Run 选择、状态筛选、关键计数和结构化详情。
- 支持正在运行、等待审批/输入、终态、失败、空数据和加载失败状态。
- 保留桌面和移动端可读性、键盘操作与 reduced-motion 行为。
- 更新 README 与模块化面试手册中的 Trace 能力说明。

## Out of scope

- 改变 Agent Graph、工具权限策略、审批决策语义或执行结果。
- 新增数据库迁移、第三方可观测平台或跨项目 Trace 聚合。
- 在审计页直接执行批准、拒绝、暂停、恢复或重试操作。

## Acceptance criteria

- [x] 主导航包含独立“Trace 审计”入口，页面不依赖右侧 Inspector。
- [x] 页面可列出并选择最近 Run，选择后展示 Run、会话、工作区和当前状态。
- [x] 时间线可区分状态转移、节点、工具、审批和错误，并可按类别筛选。
- [x] 工具项展示工具名、稳定 call ID、参数、结果及成功/失败状态。
- [x] 审批项展示请求内容和批准/拒绝决定；历史事实不因 Run 继续执行而丢失。
- [x] 空、加载、无匹配、运行中、挂起和失败状态均有明确反馈。
- [x] actor 授权、现有 Run/SSE/审批接口和 Agent 执行语义无回归。
- [x] 聚焦测试、JavaScript 语法、手册校验、完整 pytest、compileall 与 diff check 通过。

## Test design

- API：最近 Run 列表按新到旧返回，限制条数并执行 actor 隔离。
- Event：终态 Run 生成工具选择/结果事件，审批恢复生成不丢失的批准/拒绝事件。
- Frontend contract：静态壳包含 Trace 页面、筛选控件和详情区域，脚本调用 Run 列表/详情/events API 并渲染所有审计类别。
- Regression：现有 Agent Run 状态、SSE、Inspector Trace、审批恢复和移动导航保持可用。

## Decisions

- 复用 Agent Run 的追加式事件与最终结果，不创建第二套 Trace 存储。
- 审计页只读；执行控制继续留在拥有该 Run 的助手消息中。
- 保留完整 JSON 参数/结果并默认折叠，摘要层优先服务快速扫描。
- `run.read_artifact` 复用既有无正文 observability 投影；审计事件保留 artifact ID、
  范围、哈希与 Token 元数据，但不复制受保护的分页正文。

## Verification

- `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m pytest -q`：624 passed，79 subtests passed。
- `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python INTERVIEW_NOTES/validate.py`：24 个 Markdown、43 个 capability 校验通过；已有 evidence-review 提示不阻塞。
- 浏览器验证：1440×960 与 390×844 均无横向溢出；`#trace-audit` 直达保持正确视图；桌面时间线内部滚动；移动底栏单行且“更多”具有当前页语义。
- Impeccable detector 使用降级正则模式；命中均位于既有 UI 区域，新 Trace 审计区无新增命中。独立终审第二轮结论：`SHIP`。
- 合并到含 Artifact Readback 的最新 `main` 后，首轮全量测试捕获到审计事件复制
  `run.read_artifact` 分页正文的回归；复用 `artifact_read_trace` 无正文投影修复后，
  聚焦回归测试 2 passed，最终主分支全量验证为 645 passed、87 subtests passed。

## Result

新增按身份授权的最近 Run 查询、完整工具/审批审计事件，以及独立只读 Trace 审计页。页面支持 Run 搜索与状态筛选、事件分类筛选、完整 JSON 折叠详情、活跃/暂停 Run 自动刷新，以及桌面/移动响应式布局；现有对话内 Trace 与审批控制保持不变。

合并验证额外确认：普通工具结果保持完整审计；`run.read_artifact` 的受保护分页正文不进入
持久化事件或 SSE，只保留 artifact 与完整性元数据。

文档影响：已同步 `README.md`、`README.en.md`、模块化面试手册根入口、Part 04 和 `facts.json`，说明新页面、API、事件投影与授权边界。

任务分支已提交并合入本地 `main`；合并后的安全修复与最终验证覆盖当前工作树内容。
