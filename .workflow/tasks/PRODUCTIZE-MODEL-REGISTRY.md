# PRODUCTIZE-MODEL-REGISTRY: 本机全局模型注册与智能会话路由

## Goal

把启动时环境变量模型目录产品化为本机全局模型注册中心：API Key 每个 Provider
只配置一次并供全部 Workspace 使用，会话可手动选择首选模型或按现有策略/任务难度
自动选择，并公开连接健康、fallback 和实测延迟。

## In scope

- 全局注册 OpenAI、DeepSeek、Anthropic、Google 连接及多个模型。
- API Key 只写不回显，数据库只保存 Secret 引用；保留环境变量兼容路径。
- 持久化模型目录、会话选模偏好和运行观测；所有 Workspace 共享。
- 会话支持手动首选模型、允许 fallback，以及 quality/cost/latency/smart 自动路由。
- Chat、完整代码 Agent Run 和 RAG Ask 使用会话偏好；Embedding、摘要和记忆提取
  保持后台独立策略。
- 区分连接级与模型级故障，展示熔断、成功率、样本量、TTFT 和总延迟。
- 增加模型管理与会话选模前端，并同步 README、Interview Notes 和事实索引。

## Out of scope

- 多用户、Workspace 模型白名单、用户私有 API Key 或组织级权限。
- 在线价格同步、定时付费探测、学习型路由或跨副本共享熔断。
- 自定义任意 Provider Base URL、部署、发布、推送或真实付费模型调用。

## Acceptance criteria

- [x] 四种真实 Provider 可在前端配置一次凭据并注册多个模型。
- [x] API Key 永不通过读取 API 返回，模型和会话偏好可持久化。
- [x] 动态模型目录无需重启即可参与 allowlist、能力过滤和路由。
- [x] 手动模型不可用时可 fallback；自动模式保留三种策略并新增可解释 smart 策略。
- [x] Chat、Agent、RAG Ask 使用同一会话偏好且 Agent 异步/恢复期间保持一致。
- [x] 状态列表区分静态预估和实测 TTFT/总延迟，公开健康与最近错误。
- [x] 手动模型选择按延迟排序并显示具体 ms，以绿/黄/红区分快、中、慢。
- [x] 手动模型弹层在桌面和窄屏下不越过 composer，且不与右侧 fallback 冲突。
- [x] 本机未认证模式下的密钥写接口保持 loopback 边界。
- [x] PostgreSQL/内存实现、迁移、前端和专项测试完整。
- [x] pytest、compileall、前端语法、迁移 SQL、Interview Notes 校验通过。

## Decisions

- 连接和模型均为本机全局资源，所有 Workspace 自动共享。
- 一个 Provider 默认一份连接凭据，连接下允许多个模型；后续可扩展多连接。
- 手动选择是“首选”而非硬锁定，`fallback_enabled=true` 时失败后继续路由。
- smart 第一版使用无额外模型调用的可解释任务画像，不引入路由元模型成本。
- 主动测试只由用户触发；持续状态和延迟来自真实请求的被动观测。
- 环境变量配置继续作为启动兼容与无注册数据时的安全回退。
- 手动选择使用总延迟实测 P50，尚无样本时回退到配置预估；`≤1000 ms` 为绿色、
  `1001–3000 ms` 为黄色、`>3000 ms` 为红色，无数据保持灰色。

## Verification

- `.venv/bin/python -m pytest -q`：208 passed，另有 4 个 subtests；仅保留既有
  Starlette/httpx 弃用警告。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/alembic heads`：单一 `20260804_0014 (head)`；离线 upgrade SQL
  生成成功，0014 中三个 Run 选模字段各只新增一次。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：12 个 Markdown、28 项能力通过；
  evidence review warnings 是未提交工作区变更提示。
- `git diff --check`：通过。
- 手动模型面板已完成浏览器视觉冒烟：默认桌面视口与 `900x720` 窄屏均通过；
  窄屏下弹层右边界 `703.39px`、fallback 左边界 `711.39px`，保持 `8px` 间距，
  且弹层完整位于 `900px` 视口和 composer 内。临时内存服务已关闭。

## Result

- 新增本机全局 Provider/模型注册中心，PostgreSQL 保存目录、偏好、Run 快照和最近
  100 个被动观测样本；API Key 仅以引用入库，实际值写入 OS keyring，读取接口不
  回显密钥。环境变量路径保持兼容。
- 前端可统一配置 OpenAI、DeepSeek、Anthropic、Google，维护多个模型并查看连接、
  熔断、成功率、TTFT/总延迟 P50/P95；会话可手动选择首选模型及 fallback，或开启
  smart/quality/cost/latency 自动路由。
- 手动模型控件升级为自定义选择面板：按延迟升序排列，直接显示具体毫秒和
  实测 P50/预估来源，并以绿、黄、红状态点表达速度等级；支持方向键和 Esc。
- 选择面板改为右侧锚定并设置视口最大宽度；手动模式隐藏右侧冗余说明，控制栏可
  自动换行，模型名和延迟列分别收缩，消除弹层、fallback 与 composer 右边界冲突。
- smart 第一版根据工具/结构化输出、上下文长度和任务信号形成可解释的
  low/medium/high 画像；Chat、Agent、RAG Ask 共用会话选择，Agent 在提交时持久化
  不可变快照供异步执行和审批恢复。
- DeepSeek 接入 Chat Completions SSE 与原生 Function Calling。Token 预算预检如实
  区分 count API 与 DeepSeek 保守估算，最终以 Provider usage 入账。
- README、Interview Notes 和 facts 索引已同步。未调用真实付费模型，未部署、提交
  或推送。
