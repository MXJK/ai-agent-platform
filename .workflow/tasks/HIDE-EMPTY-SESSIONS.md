# HIDE-EMPTY-SESSIONS: 空会话不进入历史记录

## Goal

让尚未持久化任何消息的新会话只作为当前草稿存在，不出现在最近会话、会话记录或
启动恢复候选中；第一条消息成功持久化后再成为历史会话和最后活跃会话。

## In scope

- 内存与 PostgreSQL 会话列表统一排除零消息会话。
- 新建空会话时不覆盖最后活跃会话，首条消息落库后再激活。
- 前端加载空会话时不写入最后活跃偏好，启动恢复跳过遗留空会话。
- 仓储、服务、API 与前端契约测试及用户文档同步。

## Out of scope

- 删除数据库中已存在的空会话。
- 会话草稿、自动过期或后台清理机制。
- 模型注册中心及其他并行功能。

## Acceptance criteria

- [x] 零消息会话不出现在 active/archive 历史列表、搜索结果和最近会话中。
- [x] 创建空会话不会覆盖已有 `last_active_session_id`。
- [x] 第一条消息成功持久化后，会话进入历史并成为最后活跃会话。
- [x] 启动恢复不会使用遗留的零消息最后活跃会话。
- [x] 内存/PostgreSQL/API/前端行为有回归测试。
- [x] 项目规定验证全部通过。

## Decisions

- 空会话仍保留可寻址 ID，当前页面和显式 URL 可以继续使用；本次只改变历史可见性。
- 列表在仓储层过滤，避免前端过滤造成分页条数和游标不准确。
- 不做破坏性数据清理，已有空记录自然从历史查询中隐藏。

## Verification

- `.venv/bin/python -m pytest -q`：208 passed，1 个既有 Starlette/httpx 弃用
  警告，另有 4 个 subtests 通过。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/alembic heads`：单一 `20260804_0014 (head)`。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：12 个 Markdown、28 项能力通过；
  evidence review warnings 仅提示当前未提交工作区变化。
- `git diff --check`：通过。

## Result

- 内存仓储按 `message_count`、PostgreSQL 按消息 `EXISTS` 在分页前排除空会话，
  active/archive、搜索和最近会话共享同一服务端契约。
- 新建会话不再立即写最后活跃偏好；首条用户消息成功持久化后才激活。偏好 API
  拒绝显式激活空会话，避免新客户端重新引入空恢复候选。
- 前端不会在加载空会话时写最后活跃偏好，自动启动恢复也跳过旧数据中遗留的
  零消息会话；显式 URL 仍可继续使用当前空草稿。
- README、持久化/UI Interview Notes 与事实索引已同步。未删除已有空会话、
  未执行数据库迁移、未修改模型注册中心行为。
