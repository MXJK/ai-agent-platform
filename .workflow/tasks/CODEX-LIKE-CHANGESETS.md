# CODEX-LIKE-CHANGESETS: Agent ChangeSet 审阅与安全落盘框架

## Goal

在现有每次运行独立 Sandbox 的代码修改循环之后，增加持久化 ChangeSet、二次审批、
并发冲突检测和安全应用框架，使 Agent 生成并验证的补丁能够在明确授权后落到登记的
真实工作区，同时保留 patch-only 与 Git worktree 扩展边界。

## In scope

- 定义 ChangeSet 领域模型、状态机、内存/PostgreSQL 存储和数据库迁移。
- 在 Sandbox 清理前持久化完整补丁、补丁摘要、受影响文件基线哈希和验证状态。
- 新增 ChangeSet 查询、应用和拒绝 API，并接入现有 Agent Run 授权、任务队列与审计事件。
- 实现 `patch_only`、`direct` 和 `worktree` 应用模式契约；`direct` 提供安全原子落盘，
  `worktree` 提供受控 Git worktree/分支框架。
- 对真实工作区写入执行显式启用、editor 权限、路径/符号链接/敏感文件校验、补丁摘要、
  文件基线冲突、幂等和失败回滚保护。
- 前端提供 ChangeSet 状态、Diff、应用和拒绝的最小交互入口，同时保留现有未提交的
  Agent trace 展示改动。
- 更新 README、面试手册、facts.json 和覆盖核心安全路径的测试。

## Out of scope

- 自动 commit、push、PR、merge、部署或实际执行数据库迁移。
- 绕过 Git 冲突自动重写用户当前修改。
- 允许任意 Shell、网络访问或未经批准的外部写入。
- 生产级分布式文件锁；本任务提供进程内锁和持久化幂等状态契约。

## Acceptance criteria

- [x] 终态 Agent Run 在 Sandbox 清理前持久化不可截断的完整 ChangeSet。
- [x] ChangeSet 包含 patch SHA-256、变更文件、基线文件哈希、Workspace 快照和验证结果。
- [x] viewer 可以查看 ChangeSet，但只有 editor 能批准真实工作区写入或拒绝变更。
- [x] 真实写入默认关闭；`AUTH_MODE=disabled` 时不能启用。
- [x] apply API 校验用户确认的 patch SHA-256，并具有稳定、幂等的状态转换。
- [x] 路径逃逸、符号链接、敏感文件、二进制补丁和超出限制的 ChangeSet 被拒绝。
- [x] 文件自 Sandbox 基线后发生变化时返回 conflict，且不覆盖用户修改。
- [x] direct 模式按全有或全无语义写入，失败时恢复原文件。
- [x] patch-only 模式保留可审阅产物而不写真实工作区。
- [x] worktree 模式具有受配置约束的分支/目录生命周期框架和测试。
- [x] API、服务、内存/PostgreSQL 存储、权限、冲突、回滚和幂等行为有测试覆盖。
- [x] README、INTERVIEW_NOTES、facts.json 与实现一致且校验通过。
- [x] 项目规定的 pytest、compileall 和面试手册校验通过。

## Decisions

- 模型产生补丁不等于获得真实工作区写权限；真实落盘使用独立的二次审批入口。
- 展示用 Diff 可以截断，但落盘必须引用服务端持久化的完整补丁及其摘要。
- Workspace `revision` 只标识登记根路径版本；内容并发保护使用逐文件 SHA-256 基线。
- 默认应用模式为 `patch_only`，真实写入必须同时满足配置启用、认证启用和 editor 授权。
- ChangeSet 是 Run 的独立持久化事实和二次审批边界；事件流只记录 ID、摘要、文件数、
  模式、操作者和错误，不复制完整 patch。
- `changes_ready`（未配置验证命令）可保留为待审阅变更；明确的 execution/validation/
  repair failure 会持久化为 `failed`，不可应用。
- direct 模式使用目标文件备份加 `git apply --check/apply`，失败恢复；worktree 模式
  要求捕获时 Git 源目录干净，并从捕获 HEAD 建 sibling 工作树。
- 没有末尾换行的 UTF-8 文件使用 Git 兼容的 `No newline at end of file` patch 标记。

## Verification

- `.venv/bin/python -m pytest -q`：通过，263 tests + 11 subtests。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，12 Markdown、30 capabilities；
  仅报告事实基线提交之后已有/本轮证据文件发生变化的 review warnings。
- In-app browser ChangeSet 回归：通过。真实 Agent 运行经工具计划审批、Sandbox 修改和
  校验后，前端显示 `待审阅`、`patch_only`、`validated`、变更文件数、SHA-256 和完整
  Diff；拒绝操作后状态更新为 `已拒绝`，浏览器 console 无 error/warn，源工作区未生成
  演示文件。
- 未执行 `alembic upgrade`：迁移执行需要人工确认；本任务只新增 revision 文件并
  通过 compileall、迁移链检查与 PostgreSQL repository 映射测试。

## Result

已完成 ChangeSet 领域模型、内存/PostgreSQL CAS 存储、Alembic revision、Sandbox
清理前完整导出、Agent/Worker 装配、query/apply/reject API、Run 审计事件、
patch-only/direct/worktree 应用器和浏览器审阅卡。真实工作区写入默认关闭，且未执行
数据库迁移。浏览器已实际验证 ChangeSet 待审阅、完整补丁展示和拒绝后的状态刷新。
工作树还包含本任务开始前已有的 Agent trace 前端改动；本任务在其上做了小范围兼容
扩展，没有回退或覆盖这些改动。
