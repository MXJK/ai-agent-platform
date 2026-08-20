# CODE-AGENT-DIRECT-SOURCE-DEFAULT: 代码 Agent 默认直接修改源码

## Goal

把代码 Agent 收敛为单一的直接源码执行体验：用户选择代码 Agent 后无需再选择执行位置，
Run 默认在已登记 Workspace 的源码根中读取、修改和验证，行为与本地 Codex 一致。

## In scope

- 删除统一输入框中的“执行位置”选择器及其前端状态、能力解析和请求字段。
- 从公开 Agent Run 请求与 composer capabilities 响应中移除 workspace mode 选择契约。
- 让所有入口使用服务端配置的唯一默认执行模式，不接受调用方逐 Run 覆盖。
- 将官方单用户 Compose 与示例配置锁定为 `direct`，开启受审批保护的实时源码写入。
- 保留 RunContext、Run 结果和 ChangeSet 中的 mode/execution root 历史审计字段。
- 更新聚焦测试、中英文 README、面试手册和事实证据映射。

## Out of scope

- 删除 `patch_only` / `worktree` 的底层历史兼容实现或迁移已有 Run/ChangeSet。
- 取消精确工具审批、路径边界、基线哈希、mutation journal、单写者锁或安全回滚。
- 自动 commit、push、创建 PR、merge、部署或重启当前服务。

## Acceptance criteria

- [x] 页面不再渲染或操作“当前源码 / Git Worktree / 仅生成补丁”选择器。
- [x] `POST /api/v1/agent/runs` 不再接受 `workspace_mode`，前端也不发送该字段。
- [x] composer capabilities 不再返回 workspace mode 选择元数据。
- [x] 官方 Compose 的 Agent Run 默认 `workspace_mode=direct`，且 execution root 等于登记源码根。
- [x] 写入、命令审批以及 ChangeSet 审计/安全回滚继续生效。
- [x] 聚焦测试、项目规定全量验证、前端语法检查、手册校验和 diff 检查通过。

## Decisions

- “不选择执行位置”同时约束 UI 和公开 API；调用方不能逐 Run 覆盖服务端默认值。
- 官方产品 allowlist 只包含 `direct`，所以默认模式不可用时直接失败，不静默降级到临时副本。
- 内部执行工作区抽象与历史响应字段保留，避免破坏已有持久化数据和回滚语义。
- `CHANGE_SET_APPLY_MODE=patch_only` 只保留为旧 ready ChangeSet 的兼容配置；新 direct
  ChangeSet 在工具执行时已落盘并捕获为 `applied`。

## Verification

- 聚焦 API、执行上下文与 Compose 契约测试：`57 passed in 4.92s`。
- `.venv/bin/python -m pytest -q`：通过，`435 passed, 53 subtests passed in 43.14s`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，验证 24 个 Markdown 文件和
  39 项能力；相对既有 facts 基线的 evidence review 警告为信息性提示。
- `docker compose --env-file .env.example config --quiet`：通过。
- `git diff --check`：通过。
- 用户另行明确授权后执行 `docker compose up -d --no-deps --build app`：仅重建并重启
  App 容器，未执行 migration；容器健康。实时 `/api/v1/health` 返回
  `ready=true`，页面无执行位置选择器，浏览器脚本不再发送 `workspace_mode`。
- Diff 审计：改动限于 UI/API 模式选择移除、服务端默认模式、Compose/env、测试、
  中英文 README 和工作流记录；未包含凭据、生成文件或数据库 migration。

## Result

已完成。代码 Agent 不再让调用方选择执行位置，官方单用户 Compose 将登记源码根冻结为
唯一的 `direct` 执行根；受审批的写入会直接修改源码，并继续生成已应用 ChangeSet、
保留冲突安全回滚。RunContext/Run 响应中的 mode 与 execution root 继续作为历史审计字段，
已有 `patch_only` / `worktree` 记录和旧 ready ChangeSet 无需迁移。

README 中英文说明与本地个人面试手册已同步。面试手册按仓库 `.gitignore` 约定不进入
Git 提交，但已通过校验。用户明确授权的 App 重建和实时验证也已完成；未执行数据库
migration。
