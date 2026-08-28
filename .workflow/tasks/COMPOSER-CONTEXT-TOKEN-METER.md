# COMPOSER-CONTEXT-TOKEN-METER: 修正对话工作台上下文 Token 指标

## Goal

将 composer 的累计 Token 统计与当前上下文占用分离，避免把跨请求累计消耗误显示为上下文占用率。

## In scope

- 保留会话累计实际 Token，但不再把它作为上下文占用进度的分子。
- composer 使用当前保留会话历史的估算 Token 与当前模型输入预算计算上下文比例。
- 明确估算边界、未知预算和高占用状态，并保持桌面与窄屏布局可读。
- 增加执行真实前端计算逻辑的回归测试，同步中英文 README、Interview Notes 与事实证据。

## Out of scope

- 修改 Usage Ledger、Provider 用量持久化或 `/sessions/{id}/token-usage` API。
- 把本地历史估算宣称为包含系统提示、工具 Schema、检索结果和下一条用户输入的完整 Prompt。
- 重做 composer 的三列布局或会话详情页 Token 面板。

## Acceptance criteria

- [x] composer 分开展示累计实际 Token 与当前历史上下文估算，不再显示“累计消耗 / 上下文上限”。
- [x] 上下文百分比与进度条只使用 `context.estimated_tokens / context.budget_tokens`。
- [x] 未知预算不显示虚假百分比；达到 72%/90% 时恢复 warning/error 语义状态。
- [x] Tooltip 与可访问状态明确估算不包含完整最终 Prompt 的其他组成部分。
- [x] 行为测试覆盖累计值大于预算但当前上下文较低、未知预算和高上下文占用。
- [x] 聚焦测试、完整测试、compileall、前端语法、文档校验和桌面/窄屏验收通过。

## Decisions

- 使用后端已有的 `context.estimated_tokens` 和 `context.budget_tokens`，不扩展 API。
- 累计 Token 保留在次要行；上下文估算、预算和比例作为主要行与进度条的唯一语义。
- 继续使用 `≈` 和完整说明，避免把 `unicode_heuristic_v1` 本地估算表述为 Provider 精确计数。
- 桌面显示完整的“估算 / 预算 · 比例”，390px 窄屏切换为“比例 · 紧凑估算”；完整口径始终保留在 tooltip 与 `aria-label`。
- 进度条保留四位百分比精度，避免小于 0.5% 的非零上下文被四舍五入为 0；72%/90% 分别使用既有 warning/error 色阶。

## Verification

- `node --test tests/test_chat_message_ui.mjs`：11 项通过；新增 2 项覆盖累计 180,000 与当前上下文 18,000 / 72,704 的分离、未知预算、90% 高占用和窄屏紧凑文案。
- 聚焦 API/模型预算测试通过：静态资源契约、Chat Usage 持久化和会话选模预算共 3 项。
- `.venv/bin/python -m pytest -q`：`689 passed, 121 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`、`node --check ai_agent_platform/static/app.js` 与 `git diff --check`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 文件与 43 项能力；仅输出相对既有 verified commit 的 evidence review warnings。
- `impeccable detect` 按要求运行一次；本机缺少 HTML parser 模块而降级为正则模式。本次涉及区域被提示原有 width transition，已移除；其余告警位于本次范围外的既有样式。
- 本地 fake 服务 + 真实 Chromium：1200px 桌面显示完整 `上下文 ≈ 42 / 72,704 · 0.06%`，390px 显示 `上下文 0.06% · ≈ 42`；两端 label、scope strip 和页面均无横向溢出，tooltip 与 `aria-label` 一致。临时浏览空间与服务已关闭。

## Result

- composer 不再用会话累计 `total_tokens` 除以单次输入预算；累计值只作为独立账本统计，上下文比例改为当前保留历史估算除以当前模型输入预算。
- 恢复可解释的上下文压力状态和非零小比例进度，未知预算不生成虚假百分比。
- 桌面与窄屏分别使用完整和紧凑文案，完整估算边界同时提供给鼠标提示与辅助技术。
- 中英文 README、Interview Notes 和 facts 证据已同步；未修改后端 Usage Ledger、Token API、上下文装配或模型路由。
