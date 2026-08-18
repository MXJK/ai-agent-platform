# RUNTIME-CONFIG-PROFILES：本地与产品运行配置分层

## Goal

将当前容易混用的 SQLite 与 PostgreSQL/Celery 配置收敛为明确的运行 profile：
本地个人开发默认使用单进程 SQLite；未来产品部署使用 PostgreSQL、Qdrant、Redis/Celery，
且所有尚未执行的上线动作以 TODO 明示。

## In scope

- 增加可校验的 `local` / `production` / `custom` 运行 profile。
- 提供分类清晰的本地与产品配置示例，保留旧配置入口兼容性。
- 增加本地启动入口，并让现有持久化启动脚本明确只服务 production profile。
- 将当前 gitignored `.env` 收敛为无需 PostgreSQL/Qdrant/Redis 的本地 profile。
- 补充配置、启动边界测试和中英文文档/面试手册事实同步。

## Out of scope

- 执行 PostgreSQL Alembic 迁移、部署、发布或启动未来生产基础设施。
- 在本任务中补齐 ChangeSet、文档、模型注册表或 LangGraph checkpoint 的 SQLite repository。
- 实现 SQLite 到 PostgreSQL/Qdrant 的数据迁移工具。
- 提交、推送、合并或覆盖当前工作树中其他任务的未提交改动。

## Acceptance criteria

- [x] `RUNTIME_PROFILE=local` 自动选择 SQLite 核心状态、SQLite L1/L3 与 in-process queue，并拒绝混入生产后端。
- [x] `RUNTIME_PROFILE=production` 自动选择 PostgreSQL/Qdrant/Celery 共享后端，并拒绝 SQLite。
- [x] 当前 `.env` 不再要求 PostgreSQL/Qdrant/Redis 即可解析和启动本地 API。
- [x] 本地与产品配置示例按职责分组；未来上线的凭据、迁移、OIDC、共享目录、备份和扩容事项均标注 TODO。
- [x] 本地启动入口不会执行生产迁移；生产启动入口继续要求显式迁移授权。
- [x] 相关测试、全量 pytest、compileall、文档校验和 diff 检查通过。

## Decisions

- `custom` 保留现有低层开关兼容性；`local` 和 `production` 是锁定后端组合，避免默认使用混合模式。
- 本地尚无 SQLite adapter 的 ChangeSet、文档、模型注册表、checkpoint 和文档 RAG 暂用进程内实现，并在配置与文档中明确重启边界。
- Celery 只属于 production profile；产品早期若暂不需要独立 Worker，应使用 `custom` 的 PostgreSQL/Qdrant + in-process 组合。

## Verification

- `.venv/bin/python -m pytest -q`：`420 passed, 49 subtests passed`。
- `.venv/bin/python -m pytest -q tests/test_config.py tests/test_config_resolver.py`：`47 passed, 32 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python evals/run_memory_evals.py`：PASS；candidate precision `1.000`、Recall@6 `1.000`、cross-workspace leaks `0`。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 文件和 39 项能力；仅输出当前脏工作树的 evidence review 提醒。
- `node --check ai_agent_platform/static/app.js`、`bash -n scripts/start-local.sh scripts/start.sh`、`git diff --check`：通过。
- `./scripts/start-local.sh --check` 与 `docker compose --profile gateway config --quiet`：通过；本地 Compose 只解析 `gateway` 服务。
- production profile 使用完整共享后端环境覆盖执行 `./scripts/start.sh --check`：通过，未启动服务或执行迁移。
- 真实本地冒烟：临时 SQLite 路径启动 FastAPI，`GET /api/v1/health` 返回 HTTP 200、`session_storage=sqlite`、`persistent_sessions=true`，随后正常关闭；未连接 PostgreSQL/Qdrant/Redis。

## Documentation impact

- 已同步 `README.md`、`README.en.md`、本地/production dotenv 示例、模块化面试手册和 `facts.json`。
- production 示例仅记录未来上线 TODO；本任务没有执行 Alembic、部署、备份、数据迁移或外部发布。

## Result

已完成。默认 `.env.example` 与当前本机 `.env` 使用锁定的 local profile；新增
`scripts/start-local.sh`，本地可信写入只额外启动 Go 网关容器。production profile、
Compose 数据服务和原 `scripts/start.sh` 被明确隔离，且继续要求显式迁移授权。

当前本地重启持久化边界仍是 Session/Run/Workspace/L1/L3 和记忆向量；ChangeSet、
文档、模型注册表、LangGraph checkpoint 和文档 RAG 的 SQLite adapter 尚未实现，已在
配置和文档中保留 TODO。当前改造经用户授权与本地记忆/工作台作为同一 feature branch
提交并推送；未合并、迁移、部署，也未纳入工作树中无关的任务记录修改。
