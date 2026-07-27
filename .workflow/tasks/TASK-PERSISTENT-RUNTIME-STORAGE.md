# TASK-PERSISTENT-RUNTIME-STORAGE: 持久化运行存储

## Goal

将当前运行配置从内存后端切换到 PostgreSQL、Qdrant 与 Redis/Celery，并明确 Chroma 和 Qdrant 的职责边界

## In scope

- 将当前 `.env` 的 Session、Agent Run、Document、Workspace 和 LangGraph
  checkpoint 后端切换为 PostgreSQL。
- 将当前 RAG 向量后端切换为 Qdrant。
- 将当前任务队列切换为 Redis broker/result backend 上的 Celery。
- 更新 `.env.example` 和 README，明确 PostgreSQL、Qdrant、Redis、
  Chroma 的职责和启动前置条件。
- 新增一键运行脚本，负责启动依赖、等待连通、执行迁移并托管 API/Worker
  生命周期。
- 保留内存实现作为显式测试替身，但当前运行配置不得选择内存后端。

## Out of scope

- 启动或部署数据库服务。
- 执行 Alembic 数据库迁移。
- 迁移现有内存数据。
- 删除测试使用的内存 Repository 或 Vector Store。
- 提交、合并或发布。

## Acceptance criteria

- [x] 当前 `Settings.from_env()` 解析出的结构化存储均为 PostgreSQL。
- [x] 当前 RAG 向量存储为 Qdrant。
- [x] 当前任务队列为 Redis/Celery，不再使用进程内队列。
- [x] `.env.example` 默认展示同一套持久化运行拓扑。
- [x] README 明确 Chroma 与 Qdrant 是同一向量存储边界的可替代实现。
- [x] 一键运行脚本可启动依赖、迁移数据库并同时运行 API 与 Celery Worker。
- [x] 脚本提供无副作用检查模式，并在退出时清理本次启动的应用子进程。
- [x] 项目规定验证与配置检查通过。

## Decisions

- PostgreSQL 保存关系型业务数据、文档正文/Chunk 元数据和 LangGraph
  checkpoint；向量及检索 payload 保存到 Qdrant。
- Redis 只承担 Celery broker/result backend，不作为业务数据源。
- 当前环境选择 Qdrant；Chroma 仅保留为单机嵌入式向量库选项，不和
  Qdrant 双写。
- 内存实现继续服务于隔离单元测试，避免测试依赖外部数据库。
- 启动脚本默认应用待处理迁移；用户实际运行脚本即代表同意执行该次启动所需
  的数据库迁移。

## Verification

- `Settings.from_env()` 配置断言通过：Session、Agent Run、Document、
  Workspace、LangGraph checkpoint 均为 `postgres`，RAG Vector Store 为
  `qdrant`，任务队列为 `celery`。
- `.env.example` 持久化后端断言通过，不包含上述存储的 `memory` 或
  `in_process` 选择。
- `docker compose config --quiet`：通过。
- 容器状态只读检查：PostgreSQL 和 Redis 为 healthy，Qdrant 正常运行。
- 只读连通性检查：PostgreSQL `SELECT 1`、Qdrant `/collections` 和
  Redis `PING` 均通过。
- `.venv/bin/alembic current`：数据库当前为 `20260720_0005`，低于代码
  最新迁移 `20260724_0007`；尚未执行迁移。
- `.venv/bin/python -m pytest -q`：通过，98 passed（1 个第三方弃用
  warning）。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `bash -n scripts/start.sh`：通过。
- `./scripts/start.sh --check`：通过，验证虚拟环境、Compose 和全部持久化
  后端选择，未启动服务或执行迁移。
- `scripts/start.sh` 权限：`-rwxr-xr-x`。
- ShellCheck 未安装，未执行该项可选静态检查。
- `git diff --check`：通过。

## Result

持久化运行配置和文档已完成。当前环境不再选择内存 Repository、内存向量库
或进程内任务队列：关系型数据使用 PostgreSQL，RAG 向量使用 Qdrant，任务
队列使用 Redis/Celery。内存实现仅保留为显式测试替身。

实际启动 API/worker 前仍需执行 `.venv/bin/alembic upgrade head`，将
PostgreSQL 从 `20260720_0005` 升级到 `20260724_0007`。启动脚本会在用户
运行时自动完成该迁移；本次交付没有代为迁移既有数据库，也没有启动或重启
应用进程。

新增可执行的 `scripts/start.sh` 后，用户只需运行该脚本。它会检查配置、启动
PostgreSQL/Qdrant/Redis、等待服务就绪、应用待处理 Alembic 迁移，并同时
运行 Celery Worker 和 FastAPI。`Ctrl+C` 会清理 Worker，持久化容器继续
运行。此次交付仅执行了 `--check`，没有触发迁移或启动应用。
