# SINGLE-NODE-SELF-HOSTED-MVP: 单实例 Docker 自托管收敛

## Goal

把项目唯一公开支持的产品运行路径收敛为单用户、单实例 Docker Compose 自托管：
复用现有 FastAPI、PostgreSQL、Qdrant、进程内任务队列和业务能力，不依赖 Go 网关、
Redis 或 Celery。

## In scope

- 增加可构建并运行 FastAPI/Web UI 的应用镜像和 Compose 服务。
- 复用现有 PostgreSQL Repository、Qdrant Vector Store、Alembic 迁移和
  `in_process` TaskQueue。
- 增加无网关的固定单用户认证模式，并只通过 Compose loopback 端口发布。
- 通过 bind mount 和现有网页目录浏览器管理 `/workspaces` 下的项目。
- 保留项目记忆、知识库/RAG、ChangeSet、模型路由和可选 MCP 等现有能力。
- 更新配置示例、启动文档、面试手册和事实映射。
- 为配置、认证、Compose 和启动契约补充聚焦验证。

## Out of scope

- 不实现多用户、多租户、OIDC、公网暴露或多实例部署。
- 不启动 Go 网关、Redis、Celery Worker 或 Adminer。
- 不实现不可信代码的强隔离；MVP 只支持用户信任的本地仓库。
- 不新增 SQLite Adapter、分布式用户记忆或容器内持久化密钥系统。
- 不执行真实迁移、部署、提交、推送或外部写入。
- 暂不物理删除旧的本地/生产 Adapter 和兼容代码，避免扩大改动面。

## Acceptance criteria

- [x] `docker compose up -d --build` 的默认服务集合只有 App、迁移、PostgreSQL 和 Qdrant，
      不包含 Gateway、Redis 或 Celery。
- [x] App 使用 PostgreSQL/Qdrant 持久化后端和 `in_process` TaskQueue，配置启动时 fail fast。
- [x] `single_user` 模式始终返回固定 owner 身份，不信任调用方用户 Header，并允许本机管理能力。
- [x] App 只通过宿主机 `127.0.0.1` 发布，原生目录选择关闭，现有网页目录浏览器可访问
      `/workspaces` 挂载。
- [x] 项目记忆可用；用户记忆默认关闭；模型密钥通过环境变量引用，页面写入密钥不承诺持久化。
- [x] 默认 Workspace 写入保持 `patch_only`，Sandbox 明确限定为可信仓库的容器内执行。
- [x] 配置/认证测试、Compose 配置检查、全量 pytest、compileall 和文档验证通过。

## Decisions

- 使用 PostgreSQL + Qdrant 而不是补齐多个 SQLite Adapter，以最小代码改动换取重启持久化。
- 暂时复用 `RUNTIME_PROFILE=custom` 组合现有 Store，避免大规模删除或重命名 Profile。
- 单实例任务在 API 进程内执行；应用重启会中断正在运行的任务，这是 MVP 明确边界。
- `SANDBOX_MODE=local` 在 App 容器内运行，只接受用户自己信任的仓库，不挂载 Docker Socket。
- 单用户模式不是公网认证；安全边界由 Compose 固定的 loopback 端口发布和个人主机信任共同构成。

## Verification

- `.venv/bin/python -m pytest -q`：通过，`433 passed, 53 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `docker compose --env-file .env.example config --quiet`：通过。
- `docker compose --env-file .env.example config --services`：只输出
  `qdrant`、`postgres`、`migrate`、`app`。
- `./scripts/start.sh --check`、`bash -n scripts/start.sh scripts/start-local.sh`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `docker build --check .`：通过，无警告。
- `docker compose --env-file .env.example build app`：通过；精简依赖镜像实际构建完成，
  应用入口在最终非 root 用户下导入成功。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，验证 24 个 Markdown 文件和
  39 项能力；只输出相对旧 `last_verified_commit` 的非阻塞 evidence review 警告。
- `git diff --check`：通过。
- 未执行 `docker compose up`、Alembic 迁移、部署、提交或推送。

## Result

已完成。默认产品面收敛为单用户、单实例 Docker Compose：App 直接通过 loopback 提供
API 与 Web UI，复用 PostgreSQL、Qdrant、进程内任务队列、网页 Workspace 浏览器、
项目记忆、RAG、ChangeSet 和模型路由。新增固定 owner 的 `single_user` 模式，移除默认
Compose 中的 Go Gateway、Redis、Celery 和 Adminer，旧 Adapter/实现仍留作兼容代码。

App 镜像新增自托管专用依赖集合，避免把当前拓扑不会加载的 Celery/Redis、Chroma、
keyring 和 SentenceTransformer/Torch 带入产品镜像。README、中英文运行说明、模块化
面试手册和事实索引均已同步为当前架构；文档影响属于用户可见运行、配置、架构、安全
边界和验证命令的实质变化。

当前工作树未提交，因此没有更新 `last_verified_commit`，也没有声称现有 HEAD 覆盖本次验证。
