# SINGLE-USER-WORKSPACE-OWNERSHIP-RECOVERY: 单用户工作区归属恢复

## Goal

修复持久化安装从旧的本地/可信网关身份切换到 `single_user` 固定身份后，已有工作区被
隐藏且无法通过网页目录浏览器重新关联的问题，同时保留既有成员与项目数据。

## In scope

- `single_user` 运行时启动时为固定用户补齐所有已有工作区的管理员成员关系。
- 恢复范围包含当前工作区和软移除工作区，确保列表、详情与重新登记路径一致。
- 保留旧成员记录，不删除工作区、会话、项目记忆或其他持久化数据。
- 增加旧身份数据切换到固定单用户身份的聚焦回归测试。
- 更新中英文运行说明、工作区说明、面试手册和事实证据映射。

## Out of scope

- 自动改写旧工作区的绝对根路径；用户仍通过现有重新关联流程选择容器内 `/workspaces` 路径。
- 删除旧用户成员关系或迁移会话 owner。
- 改变 `trusted_header`、`disabled` 等多身份模式的 RBAC 行为。
- 执行真实数据库迁移、部署、提交或推送。

## Acceptance criteria

- [x] `single_user` 启动后，固定用户可看到并管理由旧身份创建的当前工作区。
- [x] 固定用户可恢复并重新关联由旧身份创建的软移除工作区。
- [x] 旧成员关系保持不变，非 `single_user` 模式不会隐式接管工作区。
- [x] 新登记工作区仍沿现有流程为固定用户授予管理员权限。
- [x] 聚焦测试、全量 pytest、compileall、前端/文档相关校验通过。
- [x] README、英文 README、面试手册与事实证据同步说明兼容行为。

## Decisions

- 在 Runtime 组装出 Workspace 与 ProjectMemory 服务后执行幂等兼容接管，不增加数据库
  migration，也不把单用户语义散落到各个 HTTP route。
- Workspace Store 增加 `list_including_removed` 契约，使软移除记录也在启动时获得固定
  owner 的管理员成员关系；恢复相同 ID 时不再被旧身份 RBAC 阻断。
- `ensure_member` 采用“只升级、不降级”角色语义，保证已有 viewer/editor owner 能提升为
  admin，同时不会因较低权限的重复确保请求降低既有角色。
- 保留旧成员、会话、项目记忆和根路径。旧宿主机绝对路径不会被猜测改写，仍由用户通过
  现有受控目录浏览器明确重关联到 `/workspaces/...`。

## Verification

- 聚焦 API、角色提升、Memory/SQLite/PostgreSQL Store 与 Runtime 启动测试：
  `14 passed in 0.88s`。
- `.venv/bin/python -m pytest -q`：通过，`435 passed, 53 subtests passed in 42.52s`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，验证 24 个 Markdown 文件和
  39 项能力；相对旧 facts 基线的 evidence review 警告为信息性提示。
- `docker compose --env-file .env.example config --quiet`：通过；服务集合仍为
  `postgres`、`migrate`、`qdrant`、`app`。
- `git diff --check`：通过。
- 用户明确授权后执行 `docker compose up -d --no-deps --build app`：App 镜像重建并仅重启
  App 容器，未启动依赖或执行数据库 migration；容器健康检查通过。
- 实时 API 验证：`GET /api/v1/health` 返回 `200`/`ready=true`；旧身份创建的
  `ai-agent-platform` 与 `test-workspace` 均通过 Workspace 列表返回 `role=admin`、
  `can_update=true`。旧宿主机路径按设计仍为 `unavailable`，可从页面重新关联到
  `/workspaces/...`。
- Diff 审计：改动限于 Workspace 全量列举、单用户启动接管、成员角色只升不降、回归
  测试、产品文档和工作流记录；未包含凭据、生成文件、schema migration 或部署改动。

## Result

已完成。持久化安装切换到固定 `single_user` owner 后，Runtime 会为当前与软移除 Workspace
补齐 admin 成员关系，因此旧身份创建的目录不再从列表消失，也可沿现有 PUT 流程重新关联
容器路径。旧成员和项目数据保留，非单用户模式保持原 RBAC 行为。

README、中英文工作区说明、个人面试手册相关 Part 与 facts 证据均已同步；后者按仓库
`.gitignore` 约定保留为本地个人资料并已通过校验。用户随后明确授权重启，当前 Docker
App 已重建并运行修复后的镜像，实时健康和旧工作区 owner/admin 可见性均已验证。未执行
数据库 migration、提交或推送。
