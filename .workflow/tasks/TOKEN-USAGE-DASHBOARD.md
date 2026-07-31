# TOKEN-USAGE-DASHBOARD: 会话与工作区 Token 用量面板

## Goal

让用户在前端看到每个会话和每个 Workspace 的累计 Token 用量，并看到当前
会话下一次请求会携带的上下文 Token 估算值。

## In scope

- 将 Chat 与 Code Agent 的输入、输出、思考 Token 统一写入用量记录。
- 为 Token 用量记录增加可空 Workspace 归属和思考 Token。
- 保持现有会话 Token API 向后兼容并增加思考、上下文与 Workspace 明细。
- 增加 Workspace Token 用量聚合 API。
- 上下文占用按当前滚动摘要和最近消息的实际注入结果进行确定性估算。
- 在会话页面展示全部会话累计用量、当前上下文占用与归属 Workspace。
- 在运行中心展示全部 Workspace 的累计用量。
- 更新 README、Interview Notes、事实映射、迁移和自动化测试。

## Out of scope

- 用户或 Workspace Token 配额、超限拒绝、成本计价与账单。
- 调用供应商远程 token counting API。
- 为历史上没有 Workspace 归属的 Chat 用量猜测或回填 Workspace。
- 执行生产数据库迁移、部署、提交、推送或发布。

## Acceptance criteria

- [x] Chat 用量持久化输入、输出、思考、总 Token 和请求 Workspace。
- [x] Agent 用量按 run ID 幂等持久化，并在审批恢复后更新累计值。
- [x] 每个会话 API 返回累计输入、输出、思考、总 Token。
- [x] 每个会话 API 返回当前上下文估算 Token、消息数和估算口径。
- [x] 每个 Workspace API 返回累计输入、输出、思考、总 Token 和会话数。
- [x] 会话列表逐项展示累计用量，详情展示上下文占用及 Workspace 分布。
- [x] 运行中心逐项展示所有可见 Workspace 的累计用量。
- [x] 未归属 Workspace 的旧记录继续计入会话总量但不错误归属。
- [x] PostgreSQL 迁移可向上和向下应用，内存实现保持行为一致。
- [x] pytest、compileall、JavaScript 语法和 Interview Notes 校验通过。

## Decisions

- 供应商返回的 usage 是累计用量的权威值；上下文占用是本地确定性估算，
  API 和 UI 都必须明确标记为估算。
- 上下文估算基于 `build_agent_context` 的实际摘要/最近消息结果，并计入消息
  结构开销；不把尚未输入的下一条用户消息算入当前占用。
- Agent 使用稳定记录 ID 对同一 run 执行 upsert，避免任务重投或审批恢复造成
  重复累计。
- Workspace 聚合只统计明确写入 `workspace_id` 的记录；旧记录保留为未归属。

## Verification

- `.venv/bin/python -m pytest -q`：158 passed；仅有既存的
  Starlette/httpx TestClient 弃用警告。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，验证 11 份 Markdown
  和 23 项能力；证据变更提示已人工复核。
- `git diff --check`：通过。
- `.venv/bin/alembic heads`：`20260730_0011 (head)`。
- `.venv/bin/alembic upgrade head --sql`：成功生成包含 0011 upgrade 的离线 SQL，
  未连接或修改目标数据库。
- 浏览器验收（内存后端）：Chat 后会话列表显示累计 27、上下文约 42；会话详情
  显示 input 11/output 16/total 27 和 Workspace 分布；运行中心显示相同
  Workspace 聚合及 1 个会话；控制台无 warning/error。

## Result

已完成会话、Workspace 和上下文三层 Token 可见性。Chat 与 Agent 的 provider
usage 会持久化并聚合，Agent 同一 run 使用稳定记录覆盖累计值；上下文以明确标注
的本地估算返回。前端会话列表、会话详情和运行中心均已接入，并补充 PostgreSQL
0011 迁移、README、Interview Notes、事实映射和自动化测试。

未执行目标数据库迁移、提交、推送或部署。交付后需要在目标环境执行 Alembic
upgrade，并使用真实 provider 再观察聚合值。
