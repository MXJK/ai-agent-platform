# LOCAL-GATEWAY-ADMIN-CHAIN: 本地网关管理链路修复

## Goal

修复可信本地 Go 网关模式下模型注册表和 MCP 注册表管理请求被错误拒绝的问题，并统一所有本机专属能力的认证语义。

## Symptom and root cause

- `AUTH_MODE=trusted_header + GATEWAY_AUTH_MODE=local` 是本地 direct/worktree 的标准启动组合。
- 带有效共享密钥、固定本地用户和 `X-Gateway-Mode: local` 的模型注册表写入仍返回
  `403 model registry writes are available only in loopback local mode`。
- 模型注册表和 MCP 注册表只检查 `settings.auth_mode == "disabled"`，没有接入本地网关
  已签发的本机能力证明；目录选择器则有一套独立判断，三条本地链路语义分裂。

## In scope

- 在公共 HTTP 认证边界集中实现本机能力校验。
- 接入原生目录选择器、模型注册表全部管理端点和 MCP 注册表全部管理端点。
- 验证关闭认证的直连 loopback、可信 local 网关、OIDC 网关、远端直连和伪造 Header。
- 跑通真实 FastAPI + Go local gateway 的模型注册表/MCP/Workspace 核心 HTTP 链路。
- 同步中英文 README、配置注释、面试手册与事实映射。

## Out of scope

- 不使用或保存真实 Provider/MCP 凭据，不调用需要用户账号的外部服务。
- 不改变 OIDC 远端管理权限、Workspace RBAC、生产部署或数据迁移。
- 不提交、推送、合并或部署。

## Acceptance criteria

- [x] `AUTH_MODE=disabled` 仅允许直连 loopback 使用本机管理能力，远端来源返回 403。
- [x] `AUTH_MODE=trusted_header` 仅允许共享密钥身份有效且带 local 网关证明的请求使用本机管理能力。
- [x] OIDC/未认证网关剥离伪造 local 证明后，模型/MCP 管理请求继续返回 403。
- [x] 模型连接保存、测试/发现、模型增删改和 MCP server 增删改/测试统一使用公共校验。
- [x] 原生目录选择器复用同一公共边界且既有安全语义不回退。
- [x] 真实本地 FastAPI + Go gateway HTTP 冒烟链路通过。
- [x] 聚焦测试、全量 pytest、Go 测试、compileall、前端/Shell/Compose/文档/diff 检查通过。

## Decisions

- 共享密钥证明请求经过受信网关，`X-Gateway-Mode: local` 证明该网关运行在本机模式；二者缺一不可。
- 关闭认证模式不能只依赖启动脚本约定，路由层仍显式检查 HTTP client 为 loopback。
- 公共校验返回本地 actor ID，便于未来管理审计复用，但本任务不扩展审计模型。

## Verification

- 修复前以有效本地网关身份、共享密钥和 `X-Gateway-Mode: local` 复现模型连接保存仍返回
  `403 model registry writes are available only in loopback local mode`。
- `.venv/bin/python -m pytest -q tests/test_model_registry.py tests/test_mcp_lifecycle.py tests/test_api.py`：
  `62 passed, 4 subtests passed`。
- `.venv/bin/python -m pytest -q`：`425 passed, 53 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- 网关 Docker 镜像构建通过；Dockerfile 在产出二进制前强制执行 `go test ./gateway/...`。
  宿主机未安装 Go，因此没有重复执行宿主机 `go test`/`go vet`。
- 隔离端口启动真实 FastAPI 与 Go local gateway 后，从网关入口完成模型连接/模型 CRUD、
  MCP server CRUD、Workspace 登记/浏览、Session 创建与 Chat SSE `meta/delta/done`；调用方
  伪造的身份、共享密钥和 gateway mode 均被 local 网关覆盖。临时模型/MCP 配置已删除，
  隔离进程、Docker 测试镜像和 `/tmp` 数据目录已清理。
- `bash -n scripts/start-local.sh`、`./scripts/start-local.sh --check`、
  `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 文件和 39 项能力；
  仅输出当前证据树的 review 提醒。
- `git diff --check`：通过。

## Documentation impact

已同步 `README.md`、`README.en.md`、`.env.example`、Part 05、Part 07 和
`INTERVIEW_NOTES/facts.json` 中的模型管理、MCP 管理、本地网关及统一本机能力边界说明。
面试手册及事实索引是本机忽略文件，已通过 validator，不会作为本次 Git diff 提交。

## Result

已完成。问题不是 Go 网关没有发送本地标记，而是模型注册表与 MCP 注册表仍把
`AUTH_MODE=disabled` 当作唯一的本地模式，和标准的
`AUTH_MODE=trusted_header + GATEWAY_AUTH_MODE=local` 启动组合相互冲突。

公共认证层现在统一提供本机能力校验：关闭认证时只接受真实 loopback peer；可信 Header
模式先验证共享密钥身份，再要求由 local 网关签发的 `X-Gateway-Mode: local`。模型注册表
全部管理端点、MCP 注册表全部管理端点和原生目录选择器都复用该边界。普通 OIDC 身份、
远端直连、缺失模式声明和只有伪造声明但共享密钥无效的请求继续 fail closed。

没有调用真实 Provider/MCP 外部服务，没有保存真实凭据，没有执行迁移、部署、提交或
外部写入。当前验证覆盖未提交工作树，因此未更新 `last_verified_commit`。
