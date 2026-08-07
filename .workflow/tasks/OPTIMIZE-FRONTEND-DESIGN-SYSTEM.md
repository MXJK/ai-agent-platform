# OPTIMIZE-FRONTEND-DESIGN-SYSTEM: Agent 工作台设计系统升级

## Goal

在保留原生 HTML/CSS/JavaScript、FastAPI 静态托管和现有业务契约的前提下，
为 AI Agent Platform 建立更清晰、独特且可持续维护的前端设计系统，强化任务入口、
Agent 执行可观察性、响应式与无障碍体验。

## In scope

- 重构全局色彩、字体、间距、层级、控件和状态设计变量。
- 优化顶部栏、主导航、欢迎态、Composer、内容 Surface 与 Inspector 的视觉层级。
- 以真实 Agent 生命周期为依据，引入一致的 Signal Spine 视觉语言。
- 优化 1440px 桌面、1024px 平板和 375px 手机布局，避免横向溢出。
- 保留键盘焦点、reduced-motion、语义状态和现有线性 SVG 图标。
- 补充静态前端契约测试，并完成浏览器或等效渲染视觉验收。

## Out of scope

- 修改后端 API、路由、数据流、持久化或 Agent 执行语义。
- 引入前端框架、构建链、外部字体或第三方 UI/动效依赖。
- 新增后端尚未支持的附件、认证、删除或协作能力。
- 部署、发布、迁移、提交、推送或外部写入。

## Acceptance criteria

- [ ] 设计变量覆盖画布、表面、文本、边框、品牌和语义状态，组件不依赖零散主色。
- [ ] Chat 首页以真实任务入口为视觉主角，并呈现 ASK → PLAN → ACT → VERIFY 信号脊线。
- [ ] 导航、执行轨迹和消息状态共享一致但克制的信号语言。
- [ ] Composer、表单、按钮、卡片和 Inspector 的信息层级与交互状态清晰。
- [ ] 1440px、1024px、375px 宽度无横向溢出，移动端核心功能可达。
- [ ] 焦点可见、控件命中区域合理、状态不只依赖颜色，并尊重 reduced-motion。
- [ ] Python 测试、compileall、JavaScript 语法、静态契约与 diff 检查通过。

## Decisions

- 采用 Orbit Navy、Flight Deck、Panel、Signal Iris、Mineral Teal 和 Alert Gold
  组成的深色工程控制台色板，避免通用的近黑加酸绿方案。
- 标题使用本地 Avenir Next / 中文系统黑体，正文使用系统无衬线，数据与代码使用
  SF Mono；不增加字体网络请求。
- Signal Spine 只用于真实的任务阶段、活动导航与执行轨迹，不作为无意义装饰。
- 保留现有三栏信息架构与移动端底部导航，仅重排欢迎态和视觉层级。

## Verification

待实现后填写。

## Result

待实现后填写。
