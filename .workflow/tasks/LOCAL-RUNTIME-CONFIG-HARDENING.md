# LOCAL-RUNTIME-CONFIG-HARDENING: 本地运行时配置加固

## Goal

修复本地目录选择器、沙箱配置和持久化配置中已经确认的边界错配，使配置解析结果与实际运行时能力一致，并让无效组合在启动阶段明确失败。

## In scope

- 区分本机可信网关和普通 OIDC 网关，防止远端认证请求触发服务器桌面目录选择器。
- 将进程启动时构造的沙箱参数固定为进程安全配置，禁止项目配置产生无法实际生效的覆盖值。
- 保留 PostgreSQL 旧环境变量别名，同时忽略对不支持 SQLite 的存储后端的错误别名传播。
- 在配置阶段拒绝无法提供原子 Query 启动语义的 session/run 存储组合。
- 让本地 Compose 转发示例中公开的网关限流、大小和超时参数，并移除启动脚本中的无效导出。
- 补充聚焦回归测试并同步相关配置、安全边界和架构文档。

## Out of scope

- 不修改 Celery/Redis/PostgreSQL 分布式部署约束或 Project Memory outbox 并发协议。
- 不改变 Workspace 默认根目录、角色权限、direct/worktree 写入边界。
- 不提交、推送、合并、部署或迁移。

## Acceptance criteria

- [x] 只有经共享密钥验证且由本机模式网关签发的请求可在 `trusted_local_gateway` 模式打开原生目录选择器。
- [x] OIDC 网关和调用方无法通过伪造来源头获得本机桌面能力。
- [x] 项目配置不能覆盖由进程构造的全部沙箱执行参数；解析结果不再与实际工具执行器分离。
- [x] local profile 显式使用 SQLite session/run 存储时不再把 SQLite 传播给不支持它的 model/change-set 存储。
- [x] session/run 存储后端不一致时在配置解析阶段失败，并给出明确错误。
- [x] 本地 Compose 实际消费 `.env` 中公开的网关流量参数，启动检查不再导出被 Compose 覆盖的监听/上游变量。
- [x] 聚焦测试、全量 pytest、compileall、文档校验和 diff 检查通过。

## Decisions

- 使用网关剥离并重新签发的本机模式声明表达桌面能力来源；共享密钥继续证明请求经过受信网关。
- 沙箱模式、命令白名单、超时、输出上限、工作区生命周期和 Docker 构造参数统一归入进程安全配置。
- 旧环境变量回退只在目标存储支持该后端时生效；显式目标变量始终优先。
- Query 启动需要 session 与 run 共享同类存储事务，因此配置层要求二者后端一致。

## Verification

- `.venv/bin/python -m pytest -q tests/test_api.py tests/test_config.py tests/test_config_resolver.py tests/test_runtime_bootstrap.py`：`92 passed, 36 subtests passed`。
- `.venv/bin/python -m pytest -q`：`425 passed, 53 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- 临时 `golang:1.26.5-alpine` 容器执行 `gofmt -l` 检查和 `go test ./gateway/...`：通过。
- `node --check ai_agent_platform/static/app.js`、`bash -n scripts/start-local.sh scripts/start.sh`：通过。
- `docker compose --profile gateway config --quiet`、`./scripts/start-local.sh --check`：通过。
- 自定义网关 body/并发/速率/readiness 值经 `docker compose config` 均解析进容器环境。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 文件和 39 项能力；仅输出当前证据树的 review 提醒。
- `git diff --check`：通过。

## Documentation impact

已同步 `README.md`、`README.en.md`、local/production dotenv 示例、Part 06、Part 07
与 `INTERVIEW_NOTES/facts.json`。面试手册及事实索引是本机忽略文件，已在本机通过
validator，不会作为本次 Git diff 提交。

## Result

已完成。local 网关现在会剥离调用方 `X-Gateway-Mode` 并只在 local 模式重新签发
`X-Gateway-Mode: local`；OIDC 和未认证模式只剥离不签发。FastAPI 的原生目录选择器
同时验证共享密钥身份与该本机模式证明，正常远端 OIDC 身份不再能触发服务器 Finder。

全部沙箱执行器构造参数已归入 `process_security`，Workspace 项目配置不再生成无法由
既有执行器兑现的覆盖值。SQLite session/run 旧别名不会传播给不支持 SQLite 的模型注册表
和 ChangeSet Store，Session/Run 后端不一致会在配置解析阶段失败。Compose 现在实际转发
示例公开的网关流量参数，启动脚本也不再导出会被 Compose 覆盖的监听/上游值。

没有修改分布式 Outbox/Celery 协议、Workspace 根和写入权限，没有执行迁移、部署、提交
或外部写入。当前验证覆盖未提交工作树，因此未更新 `last_verified_commit`。
