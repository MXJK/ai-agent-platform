# REFINE-FRONTEND-VISUAL: AI 工作台视觉与交互升级

## Goal

在不改变 API、路由、数据流和核心业务行为的前提下，将现有原生前端升级为简洁、克制、专业且具有未来科技感的 AI Agent 工作台，并补齐移动端与状态可访问性体验。

## In scope

- 统一深色中性设计变量、排版、圆角、间距、边框、阴影和交互状态。
- 优化顶部栏、侧边栏、主工作区、Inspector 与移动端底部导航的层级。
- 强化对话区、输入区、流式反馈、Agent 状态、审批工具与运行事件。
- 使用统一线性图标替换功能性 Unicode 符号。
- 将模型入口与文件入口放入对话输入区，同时复用现有设置和知识库流程。
- 清理装饰性元素、重复说明和过度卡片化样式。
- 验证桌面、平板和移动端布局、键盘焦点及 reduced-motion。

## Out of scope

- 修改后端接口、业务模型、路由或持久化语义。
- 新增聊天附件上传 API 或改变知识库上传行为。
- 引入前端框架、构建链或大型 UI 依赖。
- 部署、发布、迁移、提交或外部写入。

## Acceptance criteria

- [x] 默认 Chat 首屏突出对话与输入区，低频配置保持二级入口。
- [x] 使用统一 design tokens 与单一低饱和蓝色主强调色。
- [x] 用户、Agent、工具、运行状态在视觉与语义上清晰区分。
- [x] 工具审批与运行事件紧凑、可扫描，并提供明确状态文本。
- [x] 输入区包含模式、模型、文件入口、发送与停止状态。
- [x] 桌面、平板和移动端无横向溢出，移动端保留核心对话与任务能力。
- [x] 所有交互元素具有 hover、focus、disabled 与 loading 反馈。
- [x] 遵循 reduced-motion，功能图标不使用 emoji。
- [x] 项目规定测试、Python 编译和 JavaScript 语法检查通过。
- [x] 真实浏览器完成桌面端与移动端视觉和核心交互验收。

## Decisions

- 保留原生 HTML/CSS/JavaScript 与 FastAPI 静态托管，采用增量重构。
- 视觉基底采用石墨黑与冷灰，主强调色采用低饱和蓝色；红、黄、绿仅用于语义状态。
- 聊天“附件”入口复用现有知识库文件上传，不伪造后端尚未支持的消息附件 API。
- Inspector 在大屏为固定第三栏，中屏为浮层，移动端默认隐藏。
- 不添加营销插图或背景动画，科技感来自层级、边框、排版和克制的状态动效。
- 隐藏的 Inspector 同步使用 `hidden`、`aria-hidden` 与 `inert`，避免移动端辅助技术仍遍历不可见内容。
- Agent 审批工具使用原生 `details/summary`，在不增加依赖的前提下提供键盘可用的折叠交互。
- 项目没有 `package.json`、TypeScript、前端 lint 或构建脚本；保持既有零构建静态托管方式，以 JavaScript 语法检查、Python 契约测试和真实浏览器验收替代。

## Verification

- `.venv/bin/python -m pytest -q`：98 passed，保留 1 个第三方 Starlette 弃用 warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- `tests/test_api.py` 前端与 API 契约回归：16 passed，保留同一第三方 warning。
- 1440×900 浏览器验收：Chat、左侧导航、右侧 Inspector 和置底输入区布局稳定；页面横向溢出为 0。
- 真实流式交互：选择 fake provider 后自动创建会话，SSE 返回用户/Agent 两条消息；最终状态为“已完成”，`aria-busy` 清除，发送按钮恢复可用。
- 390×844 浏览器验收：顶部操作、五项底部导航、Chat 输入区、Agent 任务表单与 Inspector 浮层可用；Chat 与 Agent 页面横向溢出均为 0。
- 可访问性验收：移动端模式和模型选择保留明确 accessible name；隐藏 Inspector 不再出现在可操作树中；控制台 error/warn 为 0。
- 使用内存 Repository、内存向量库、进程内队列和 fake provider 启动临时验收服务，未执行数据库迁移、外部写入或部署；验收后已停止服务。

## Result

前端已升级为石墨深色 AI 工作台：统一低饱和蓝色设计变量、无衬线排版、克制圆角和细边框；减少装饰性渐变与阴影，强化对话、输入和 Agent 状态的视觉优先级。功能性 Unicode 图标已替换为统一线性图标，输入区可直接选择响应模式和 provider，并通过文件入口复用知识库上传流程。

流式 Chat 与 Agent 增加运行、完成、失败、等待审批、禁用和忙碌状态；工具审批改为可折叠组件，运行事件同时显示文本状态而非只依赖颜色。响应式导航、移动端表单、Inspector 浮层、焦点轮廓和 reduced-motion 均已完成实际浏览器验证。

本轮未修改 API、路由、后端数据流或核心业务语义，也未引入前端依赖、构建链、部署或迁移。后续可在后端提供消息附件契约后，把当前“文件到知识库”入口升级为真正的会话级附件。
