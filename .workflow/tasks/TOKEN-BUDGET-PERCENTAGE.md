# TOKEN-BUDGET-PERCENTAGE: Composer Token 比例展示

## Goal

让 Chat composer 直接显示当前会话累计 Token、模型单次请求上下文上限，以及
“累计消耗 / 上下文上限”的百分比，移除容易误解的“历史”标签。

## In scope

- 调整 composer Token 状态文案、比例计算和进度条。
- 处理未知上下文上限、零用量和累计比例超过 100% 的状态。
- 更新静态资源 cachebuster、前端契约测试和用户文档。

## Out of scope

- 修改 Token 用量持久化、会话上下文估算或 API 数据结构。
- 将累计 Token 当作配额进行限制。
- 修改会话详情页现有的上下文估算展示。

## Acceptance criteria

- [x] Composer 显示“累计 N tokens”。
- [x] 有上下文上限时显示“上下文上限 M · P%”。
- [x] 百分比使用累计 Token 除以上下文上限，最多显示两位小数；极小的非零比例显示
  `<0.01%`，不误报为 0%。
- [x] 累计比例超过 100% 时保留真实百分比，进度条宽度封顶为 100%。
- [x] 未解析到上下文上限时不显示虚假百分比。
- [x] Tooltip 明确累计消耗跨请求增长，超过 100% 不表示当前请求超限。
- [x] 相关测试和仓库验证通过。

## Decisions

- 百分比是用户指定的“累计消耗 / 上下文上限”，不代表当前最终 Prompt 的上下文
  占用率；通过完整 tooltip 明确这一口径。
- 复用现有 scope chip 和进度条，不引入新的布局或视觉组件。
- 浏览器验收发现小样本会被一位小数舍入为 0%，因此使用最多两位小数并为极小非零值
  提供 `<0.01%` 下界文案。
- 390px 窄屏验收发现原三列比例会截断上限和百分比；移动端仅调整三列权重，优先保证
  Token 卡片完整显示，Workspace 与模型继续按既有规则省略长文本。

## Verification

- `tests/test_api.py`：36 passed。
- 完整 `pytest`：658 passed，91 subtests passed。
- `compileall ai_agent_platform tests evals`：通过；worktree 沙箱不允许源码目录写
  `__pycache__`，使用 `PYTHONPYCACHEPREFIX=/tmp/token-budget-percentage-pycache`。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- `INTERVIEW_NOTES/validate.py`：通过，24 份 Markdown、43 项能力；仅报告既有
  `AGENT-SSE-LIVE-EVENTS.md` 证据复核提示。
- Impeccable detector：本次改动无新增问题；仅报告 `index.html` 既有的全页破折号
  密度 advisory，HTML parser 依赖缺失时使用 regex 降级扫描。
- 本地浏览器桌面验收：`32 / 72,704` 显示为 `0.04%`，Token 卡片无横向溢出，
  tooltip 完整说明跨请求累计口径。
- 本地浏览器 390×844 验收：上限与百分比完整显示，DOM 测量
  `labelScrollWidth == labelClientWidth == 122`，未触发省略。

## Result

Composer 不再显示容易误解的“历史 ≈ N / M”，改为分别显示会话累计实际消耗和
模型单次请求上下文上限，并给出“累计消耗 / 上下文上限”百分比。未知上限不会显示
虚假比例；累计跨请求超过 100% 时保留真实百分比，进度条视觉宽度封顶且不使用错误色
暗示配额超限。Tooltip 和 README 明确该比例不等于当前最终 Prompt 占用率；本地面试
手册与事实索引同步更新并验证通过。未修改用量账本、上下文估算或 API 契约。
