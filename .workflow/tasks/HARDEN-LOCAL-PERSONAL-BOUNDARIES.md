# HARDEN-LOCAL-PERSONAL-BOUNDARIES: 本机个人运行安全边界加固

## Goal

让默认本机个人运行配置只暴露在 loopback，并让 Agent Sandbox 在复制、命令执行、
资源限制和生命周期上形成可验证的最小安全边界。

## In scope

- 将 Gateway、PostgreSQL、Qdrant、Redis 的 Compose 端口绑定到 loopback。
- 将 PostgreSQL 本地凭据移到环境变量配置。
- 在认证关闭时拒绝 FastAPI 监听非 loopback 地址。
- Sandbox 复制时排除敏感文件并拒绝 symlink/特殊文件。
- Run 结束后清理 Sandbox，并在启动时清理超期残留目录。
- Local Sandbox 使用命令允许列表、最小环境变量、固定最大超时、限量输出和
  进程组终止。
- 强化 Docker Sandbox 的用户、只读根文件系统、capability、PID 和临时目录边界。
- 更新 README、Interview Notes、事实映射和自动化测试。

## Out of scope

- 引入 OIDC、完整多租户授权或知识库租户模型。
- 启用或重新设计 MCP 权限系统。
- 部署、发布、提交、推送或修改宿主机防火墙。
- 自动删除本任务开始前的现有 Sandbox 目录或重建当前 Compose 容器。

## Acceptance criteria

- [x] Compose 对外端口全部显式绑定到 `127.0.0.1`，PostgreSQL 密码不再硬编码。
- [x] `AUTH_MODE=disabled` 时启动脚本拒绝非 loopback `APP_HOST`。
- [x] Sandbox 不复制真实 `.env`、凭据、私钥、symlink 或特殊文件，并返回警告。
- [x] Sandbox 支持按 Run 清理和超期清理，运行时终态会触发清理。
- [x] Local 命令只允许配置的可执行文件，拒绝 Shell 包装并使用最小环境。
- [x] Local 命令超时有硬上限，会终止进程组，并在读取阶段限制输出大小。
- [x] Docker 命令使用无网络、非 root、只读根、cap-drop、no-new-privileges、
      PID/CPU/内存和 tmpfs 限制。
- [x] 默认数据库凭据由 `.env` 注入且文档明确本机边界。
- [x] 新增回归测试覆盖上述边界，仓库要求的全量验证通过。

## Decisions

- 保留 `SANDBOX_MODE=local` 作为可信自有仓库的默认开发模式；陌生仓库仍要求
  Docker 或远程执行器。
- 命令策略按可执行文件 basename 允许，不允许 `sh -c`/`bash -c` 等解释器包装。
- Sandbox 输出在采集阶段截断，API 仍保留退出码和截断标记。
- 超期清理只匹配 Sandbox 自己的固定目录前缀，避免扩大删除范围。
- Alembic 通过 `Settings.from_env()` 读取 `DATABASE_URL`；代码和
  `alembic.ini` 只保留无凭据的 loopback fallback。

## Verification

- `.venv/bin/python -m pytest -q`：通过，`166 passed`；仅有现存
  Starlette/httpx 弃用警告。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 11 个 Markdown
  文件和 24 项能力；相对上次已验证提交的 evidence review 提醒不属于校验失败。
- `node --check ai_agent_platform/static/app.js`：通过。
- `bash -n scripts/start.sh` 与 `./scripts/start.sh --check`：通过。
- `.venv/bin/alembic heads` 与指定临时 `DATABASE_URL` 的
  `.venv/bin/alembic upgrade head --sql`：通过，完整生成到 revision 0011。
- `docker compose --profile gateway config --format json`：通过；Gateway、
  PostgreSQL、Qdrant、Redis、Adminer 的全部 published port 均为
  `127.0.0.1`。
- `git diff --check`：通过；仓库内不再包含旧默认密码。
- Go 工具链在本机不可用，因此未运行 `go test ./gateway/...` 和
  `go vet ./gateway/...`；本任务未修改 Go 源码。
- 2026-08-02 经用户确认后完成运行环境应用：原地轮换 `ai_agent` 角色密码，
  PostgreSQL 新密码连接成功且 22 张 public 表完整，旧默认密码认证失败。
- 2026-08-02 使用 `docker compose up -d --force-recreate postgres qdrant redis`
  保留原 volume 重建服务；PostgreSQL/Redis 健康，Qdrant collections API 返回
  200，三个服务的 published port 均为 `127.0.0.1`。
- 本机 `.env` 已补齐 Compose 数据库变量并收紧为 `0600`；
  `./scripts/start.sh --check` 再次通过。

## Result

已完成 8 项本机个人运行边界加固，并同步 README、模块化 Interview Notes
及事实映射。首次交付未越权修改运行环境；用户随后明确确认后，已在保留现有
PostgreSQL volume 的前提下完成随机密码轮换、`.env` 同步和当前基础设施容器重建，
loopback 绑定及依赖连通性均已验证。未提交或推送代码。
