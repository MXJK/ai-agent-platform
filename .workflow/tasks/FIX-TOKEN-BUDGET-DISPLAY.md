# FIX-TOKEN-BUDGET-DISPLAY: 修复 Token 预算展示口径

## Goal

让会话输入框附近的 Token 信息准确区分累计实际消耗与当前会话历史估算，并让上下文
预算按会话当前选择的模型解析，避免用户把历史估算误认为已消耗 Token。

## In scope

- 输入框 Context 区域同时展示累计实际消耗和会话历史估算。
- 明确历史估算不等于完整最终 Prompt。
- 会话 Token API 使用当前会话模型偏好解析上下文预算。
- 增加后端与前端契约回归测试。
- 同步用户可见文档。

## Out of scope

- 改变 Usage Ledger 的累计、预算拒绝或降级语义。
- 修改历史 Token 记录或数据库 schema。
- 重启 Docker 服务、部署、迁移、提交、合并或推送。

## Acceptance criteria

- [x] 输入框不再把会话历史估算呈现为唯一的预算消耗值。
- [x] 用户可以直接看到当前会话累计实际 Token 消耗。
- [x] 历史估算明确标记其不包含完整最终 Prompt 的其他组成部分。
- [x] 手动选择不同上下文窗口模型时，Token API 返回对应模型预算。
- [x] 聚焦测试、完整 pytest、compileall、前端语法和 diff 检查通过。

## Decisions

- 累计消耗继续以 Provider usage 写入的 Usage Ledger 为权威值。
- Context 估算继续保持只读、无副作用，只修正文案与模型选择解析。
- 会话 Token API 在 `model_selection_scope` 中解析预算，以复用真实 Chat/Agent 的
  auto/manual、routing policy 和 fallback 选择语义。
- 切换模型偏好后立即重新加载当前会话用量，避免前端保留旧预算分母。

## Verification

- 聚焦 Token/预算/前端契约回归：`20 passed, 52 deselected, 4 subtests passed`。
- 完整 pytest：`647 passed, 87 subtests passed`。
- `python -m compileall -q ai_agent_platform tests evals`：通过；worktree 使用根 checkout
  解释器并把 pycache 定向到 `/tmp`。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- 合并提交 `2ded8f24` 后在根 checkout 的 `main` 重跑完整 pytest：
  `647 passed, 87 subtests passed`；compileall、JavaScript 语法和 diff 检查通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，验证 24 份 Markdown 和 43 项
  能力；evidence review warnings 为既有证据变化提醒。
- 差异审查未发现凭据、生成文件、数据库迁移、部署或任务外修改。

## Result

输入框固定 Token 区现在以两层文案展示：上层是 Usage Ledger 的会话累计实际消耗，
下层是只包含保留消息和摘要的会话历史估算/模型预算；tooltip 明确历史估算不等于完整
最终 Prompt。模型偏好保存后会立即刷新用量数据。

`GET /sessions/{id}/token-usage` 现在在当前会话模型选择作用域内解析上下文预算，手动
选择不同窗口模型时不再沿用默认模型预算。Usage Ledger 的累计、拒绝/降级语义、历史
记录和数据库 schema 均未改变。

文档影响已同步中英文 README、模块化面试手册相关 Part 与 facts 映射。实现提交为
`f5283afb`，工作流验证记录为 `1e46a3c0`，已按用户授权通过 `2ded8f24` 合并到 `main`。
本任务不推送远端、不重启 Docker 或部署；按仓库规则，真实前端浏览器验收需在根
checkout 重启服务后进行。
