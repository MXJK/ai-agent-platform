# TRACE-RUN-LIST-ORPHANS: Trace Run 列表孤儿会话容错

## Goal

让 Trace 审计页在历史 Agent Run 对应会话已删除时跳过不可授权记录，而不是返回 500。

## In scope

- Trace 最近 Run 列表的 actor 过滤。
- 已删除会话仍残留 Agent Run 时的容错与回归测试。
- Docker 开发栈 API 实测。

## Out of scope

- 删除或迁移历史孤儿 Run。
- 修改单 Run 查询、会话删除或一般权限判定语义。

## Acceptance criteria

- [x] 已认证用户请求最近 Run 列表时，孤儿 Run 被视为不可见并跳过。
- [x] 其他用户的 Run 仍被过滤，当前用户的有效 Run 仍按新到旧返回。
- [x] `GET /api/v1/agent/runs?limit=50` 不再因孤儿 Run 返回 500。
- [x] 聚焦测试、完整 pytest、compileall 与 diff check 通过。

## Decisions

- 只在集合查询中吸收 `SessionNotFoundError`；单 Run 查询继续保留既有错误语义。
- 不自动删除孤儿 Run，避免读取操作产生数据写入或破坏审计历史。

## Verification

- Docker 日志复现：`QueryService.list_runs_for_actor` 对 `sess_780f65a5cd8a`
  调用 `_assert_actor`，`SessionNotFoundError` 冒泡导致 HTTP 500。
- 聚焦 Service + HTTP 回归：`2 passed, 12 deselected`。
- 完整验证：`646 passed, 87 subtests passed`。
- `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `git diff --check`：通过。
- 合并后在 `main` 重跑完整验证：`646 passed, 87 subtests passed`，compileall 通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过（24 个 Markdown 文件、43 项能力）。

## Result

最近 Run 集合查询现在把 `SessionNotFoundError` 与权限拒绝一样视为该记录不可见，跳过
孤儿 Run 后继续返回有效 Run。单 Run 查询、会话删除和权限判定的既有语义未修改，也不
在读取路径自动删除历史 Run。

文档影响：已更新中英文 README，并在合并后同步根 checkout 的 gitignored 模块化面试
手册，记录孤儿 Run 的集合查询容错边界。

实现与自动化验证已完成，用户已批准提交并合并到 `main`。按用户要求本次不重启 Docker
app、不做真实 PostgreSQL 数据回归，也不推送远端；这些操作留给后续明确授权。
