# FRONTEND-ONLY-MODEL-REGISTRATION: 移除 Gemini 启动配置并收紧模型注册来源

## Goal

移除当前实例中的 Google/Gemini 硬编码配置，并让 PostgreSQL 产品运行时只使用前端模型
管理页持久化的 Provider 连接和模型目录。

## In scope

- 删除本地 `.env` 中的 Google/Gemini LLM、API Key 和 embedding 配置。
- PostgreSQL 模型注册中心不再从 `LLM_PROVIDER`、`LLM_MODEL` 或静态 catalog 启动导入。
- 保留 memory 模式的离线 fake bootstrap，服务单元测试与临时开发。
- 删除当前 PostgreSQL 注册中心中的遗留 Google Provider 连接。
- 更新配置样例、双语 README、面试手册与证据映射。
- 重建 App 容器并验证 DeepSeek 注册、凭据和健康状态仍然存在。

## Out of scope

- 修改 DeepSeek 模型或凭据。
- 执行新的数据库 migration、发布或外部部署。
- 移除 Google Provider adapter；用户未来仍可从前端显式重新注册。

## Acceptance criteria

- [x] `.env` 不再包含 Google/Gemini LLM、API Key 或 embedding 配置。
- [x] PostgreSQL 空注册中心启动后保持为空，不导入配置中的候选。
- [x] 当前注册中心只保留前端注册的 DeepSeek 连接与模型。
- [x] 重启后 DeepSeek 注册和加密凭据仍可读，服务健康。
- [x] 聚焦测试、全量测试、compileall、前端语法、手册校验与 diff 检查通过。

## Decisions

- PostgreSQL 是产品模型目录，不再接受静态/default catalog bootstrap；空目录是合法的
  未配置状态，由前端模型管理页完成首次注册。
- memory Store 仍接收 fake/static bootstrap，仅服务离线测试和显式临时运行，避免扩大
  本次产品行为变更。
- 保留 Google Provider adapter，删除的是当前配置与持久化连接；未来只有用户从前端
  显式注册后才会重新出现。
- 当前 `.env` 的 embedding 改为 `local/local-hashing`，避免 LLM 配置删除后仍通过
  Gemini embedding 发出请求。
- 数据库已经处于 `20260820_0022 (head)`；重启只构建并 force-recreate `app`，没有运行
  migrate、重启 PostgreSQL 或重启 Qdrant。

## Verification

- `.venv/bin/python -m pytest -q tests/test_runtime_bootstrap.py tests/test_model_registry.py tests/test_self_hosted_compose.py`：通过，`35 passed, 4 subtests passed`。
- `.venv/bin/python -m pytest -q`：通过，`450 passed, 52 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，验证 24 个 Markdown 文件和 39 项 capability；evidence review warnings 为工作树既有证据变化提醒。
- `bash -n scripts/start.sh scripts/start-local.sh`、`docker compose config --quiet`、`git diff --check`：通过。
- `docker compose exec -T app alembic current`：`20260820_0022 (head)`。
- 重建后 `/api/v1/health`：`ready=true`、`session_storage=postgres`；模型注册中心只包含 credential 可读且状态为 available 的 `deepseek-v4-flash`。

## Result

已删除当前 `.env` 的 Google/Gemini LLM、API Key 与 embedding 配置，并删除 PostgreSQL
中的 Google Provider 连接。PostgreSQL 模型注册中心不再导入静态启动候选；配置样例、
双语 README、面试手册和证据映射已同步。App 容器已用新镜像单独重建并恢复健康，前端
注册的 DeepSeek 连接、加密凭据和模型在重启后完整保留。

未创建 commit；`last_verified_commit` 保持为此前已验证提交。没有执行 migration、发布
或其他外部部署。
