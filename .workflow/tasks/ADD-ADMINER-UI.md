# ADD-ADMINER-UI: 添加 PostgreSQL 可视化界面

## Goal

为本地 Docker Compose 开发环境增加可通过浏览器访问的 PostgreSQL
数据管理界面。

## In scope

- 在 Docker Compose 中增加 Adminer 服务。
- 复用现有 PostgreSQL 服务和 Compose 内部网络。
- 仅向本机暴露 Adminer 端口。
- 在 README 中记录启动、访问和登录方式。

## Out of scope

- 修改数据库结构、数据或迁移。
- 将数据库管理界面暴露到公网。
- 部署生产环境、提交或发布。

## Acceptance criteria

- [x] `docker compose config` 能够正确解析 Adminer 服务。
- [x] Adminer 等待 PostgreSQL 健康后启动，并默认连接 `postgres` 服务。
- [x] Adminer 仅绑定本机端口。
- [x] README 包含访问地址、登录字段和安全说明。
- [x] Adminer 页面实际返回 HTTP 200。
- [x] 项目规定的测试和 Python 编译检查通过。

## Decisions

- 使用官方固定镜像 `adminer:5.5.0-standalone`，避免浮动标签导致不可控升级。
- 使用 `ADMINER_DEFAULT_SERVER=postgres` 预填 Compose 内部数据库地址。
- 将宿主机端口绑定为 `127.0.0.1:8081`，不向局域网公开管理界面。
- 使用 PostgreSQL 的健康检查作为 Adminer 启动依赖。

## Verification

- `docker compose config --quiet && docker compose config --services`：通过，服务列表包含 `adminer`。
- `.venv/bin/python -m pytest -q`：98 passed，保留 1 个第三方 Starlette 弃用 warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `git diff --check`：通过。
- `docker compose up -d postgres adminer`：通过；PostgreSQL healthy，Adminer running。
- `curl --noproxy '*' -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/`：返回 200。

## Result

本地 Compose 环境现已包含 Adminer。启动后可通过
`http://localhost:8081` 浏览 PostgreSQL 的表、数据和结构；管理端口仅在本机
可访问。README 已记录登录字段以及容器内服务器名必须使用 `postgres`。
本次未修改数据库结构或业务数据。
