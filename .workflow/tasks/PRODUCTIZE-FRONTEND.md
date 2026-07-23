# PRODUCTIZE-FRONTEND: 产品化前端体验

## Goal

在保留 FastAPI 静态托管和现有 API 契约的前提下，将开发调试台升级为适合演示、日常使用和后续扩展的产品界面。

## In scope

- 重构前端信息架构，默认突出对话、Agent、知识库和仓库索引核心流程。
- 将低频的用户、模型和仓库参数集中到设置界面。
- 改善 Session、SSE Chat、Agent 运行/审批、RAG 引用和仓库索引的状态反馈。
- 增加安全的富文本呈现、取消流式响应、Toast、键盘快捷操作和设备本地偏好。
- 完善 1024px 及以上桌面端布局与基础可访问性。
- 修复 Google Chat provider 的前后端契约不一致。
- 增加静态产品契约和 API 契约测试，更新 README 使用说明。

## Out of scope

- 新增登录、RBAC、Session 删除/重命名等当前后端尚未提供的能力。
- 修改 Agent、RAG、数据库或任务队列业务语义。
- 引入独立前端构建链、Node 运行时依赖或前端框架。
- 部署、发布、迁移、提交或修改外部环境。
- 手机端布局与交互适配。

## Acceptance criteria

- [x] 首页默认呈现可直接使用的 Chat 工作区，主要产品能力可在一级导航中到达。
- [x] 用户、模型、会话和仓库配置集中管理，并显示当前上下文摘要。
- [x] SSE Chat 支持流式状态、取消、错误反馈和安全的 Markdown/代码呈现。
- [ ] Agent 页面展示运行状态、事件、指标及详细的审批风险/参数信息。
- [ ] RAG 搜索/问答以结构化引用卡片呈现，仓库索引显示清晰的进度与结果。
- [x] 页面在 1024px 及以上桌面宽度下布局稳定，导航、设置和 Inspector 可正常使用。
- [x] 基础键盘操作、焦点样式、ARIA 状态和 reduced-motion 偏好得到支持。
- [x] Google provider 可通过 Chat 请求模型校验。
- [x] Python 测试、compileall、JavaScript 语法检查和 git diff 检查通过。

## Decisions

- 保留原生 HTML/CSS/JavaScript 和 FastAPI 静态托管，避免为当前规模引入构建链。
- 采用产品工作区 + 可折叠 Inspector + 模态设置的信息架构。
- 仅在 localStorage 保存非敏感界面偏好，不保存 API key、文档内容或仓库路径。
- 富文本渲染先转义用户/模型输出，再应用有限 Markdown 转换，避免不可信 HTML 注入。
- 将 Chat 作为默认产品入口；Agent、知识库、仓库索引、会话记录和运行中心作为一级导航。
- 本轮按用户要求仅支持桌面端，目标宽度为 1024px 及以上；不再实现移动端底部导航或手机端 Inspector 交互。
- Agent 审批直接消费后端已有的 `approval_required_tools`、`risk_summary` 和脱敏参数，不扩展后端执行权限。
- 修复前端已有 Google provider 选项与 `ChatStreamRequest` Literal 不一致的问题。
- 2026-07-22 浏览器验收确认当前 `HEAD` 仍提供旧版 Debug Console，上述产品化实现与契约测试不在当前工作树中；任务保持 active，不能依据先前文字记录标记完成。
- 阶段一只修复正确性，不改信息架构：对齐 Google provider 契约；审批界面只消费 `approval_required_tools`；批准/拒绝后的异步状态继续轮询；会话 404 和空 Chat 提供明确反馈。
- 阶段二完成桌面端信息架构重构：Chat 作为默认入口，六项核心能力进入左侧一级导航；Session、Model 和 Repository 低频配置集中到原生模态设置；Trace 与 Raw response 收纳到可折叠 Inspector；1024–1280px 使用右侧浮层 Inspector，1281px 及以上使用三栏布局。
- 阶段三采用浏览器原生 `AbortController` 中止 SSE 请求；停止后保留已接收的部分内容，并将取消记录为中性 `ABORTED` 请求而非失败。
- 安全富文本只支持受控的标题、段落、列表、引用、加粗、行内代码和围栏代码块；所有模型文本先经过 HTML 转义，渲染器不接受原始 HTML、脚本或事件属性。
- 统一反馈使用页面级 Toast 区域；Chat 支持 `Ctrl/Cmd + Enter` 发送和 `Esc` 停止，并补充 `aria-busy`、焦点轮廓及 `prefers-reduced-motion`。

## Verification

- `.venv/bin/python -m pytest -q`：83 passed；保留 1 个第三方 LangGraph pending deprecation warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- 真实浏览器主流程：会话创建/加载、消息写入、SSE Chat、Agent 只读运行、RAG 导入/搜索/问答、仓库索引均可完成；测试 Agent 变更请求已通过拒绝审批收尾，未修改源码。
- 产品契约验收：仍未完成。首页仍为 Overview Debug Console；尚无设置界面、停止生成、Toast、Markdown 渲染、结构化引用卡、Inspector 浮层或本地偏好。
- 阶段一 Agent 审批验收：通过。变更请求只显示 `change_planner` 一张审批卡，包含 `write_safe`、provider、风险说明和参数摘要；点击拒绝后按钮立即禁用，并自动轮询至 `completed`，无需手动刷新。
- 阶段一错误体验验收：通过。不存在的会话只请求一次主资源，页面显示 `Session not found` 并清空旧摘要；空 Chat 显示 `Enter a message` 和 `Message cannot be empty.`。
- Google Chat 契约验收：通过。`ChatStreamRequest` 接受 `google`，新增模型契约测试；浏览器回归未调用真实 Google 服务。
- 浏览器控制台回归：阶段一流程无 error/warn；测试 Agent 变更请求已拒绝，未修改 README 或其他源码。
- 阶段二 1440×900 浏览器验收：默认进入 Chat；六个一级导航均能切换唯一工作面板并同步标题；Inspector 可折叠/恢复；设置弹窗完整显示；Session、provider/model、repository 输入会即时同步到顶部上下文；页面横向溢出为 0。
- 阶段二 1024×800 浏览器验收：默认折叠 Inspector，展开后以 360px 右侧浮层显示；设置弹窗尺寸为 976×744 且完整落在视口内；创建 Session 后返回 Chat，通过 fake provider 完成 SSE 请求并得到响应；页面横向溢出为 0。
- 阶段二静态契约测试覆盖默认 Chat、设置、上下文摘要、Inspector 和 1280px 桌面断点；完整测试仍为 83 passed、1 个第三方 LangGraph pending deprecation warning。
- 阶段三真实浏览器安全渲染验收：fake provider 返回标题、加粗、行内代码、代码块和 `<img onerror>` 混合内容；允许的 Markdown 正常渲染，输出中 `img=0`、`script=0`、事件标记未执行。
- 阶段三真实浏览器取消验收：使用仅存在于测试进程中的延迟 fake provider，Stop 按钮和 `Esc` 均在部分 delta 到达后将状态置为 `Stopped`、`aria-busy=false`、隐藏 Stop 并恢复 Send；Toast 显示 `Response stopped.`。
- 阶段三键盘与反馈验收：`Ctrl+Enter` 完成 Session 自动创建和 Chat 发送；空消息显示行内错误与 `role=alert` Toast；完成响应显示成功 Toast。
- 阶段三 1024×800 浏览器验收：Chat 工具栏宽度与滚动宽度均为 750px，快捷键提示、Send 和状态无溢出；页面横向溢出为 0。
- 延迟取消测试第一次启动包装进程时未显式覆盖本地默认 provider，页面在收到 Google `meta` 后立即通过 Stop 中止，未收到模型 delta、usage 或 done；随后关闭该进程并以 `LLM_PROVIDER=fake` 重新完成全部取消验收。
- Go gateway 校验未运行：当前主机没有 `go` 命令；Docker 中 PostgreSQL、Qdrant、Redis 均在运行且 PostgreSQL/Redis 健康。

## Result

阶段一正确性修复已提交为 `39324a3`，阶段二桌面信息架构已提交为 `c4f1de8`。阶段三 SSE 取消、安全 Markdown/代码呈现、Toast、键盘操作、ARIA 状态、焦点样式与 reduced-motion 已实现并通过自动化及真实浏览器验收。整体任务保持 active；下一阶段增强 Agent 运行指标与 RAG 结构化引用卡，并继续改善仓库索引进度。未执行部署、发布或外部环境修改。
