# REMOVE-OBSOLETE-COMPATIBILITY-CODE: 删除退役兼容实现

## Goal

删除用户确认的 1–8 组非当前产品代码，使唯一受支持的运行面保持为单用户、单实例
Docker Compose：FastAPI/Web UI、PostgreSQL、Qdrant、进程内任务队列、Cogent 文件记忆
和加密文件密钥存储。

## In scope

- 删除无生产调用的 SSE、Cogent command/Skill loader/executor、stream collector。
- 删除旧规则型 GameAgent 及 `run_agent` 调试入口。
- 删除退役 ProjectMemory/UserMemory 业务实现、未注册路由、Schema、历史评测与配置。
- 将仍在使用的 Workspace 成员权限和 ConversationMemoryHit 从退役记忆模块中拆出。
- 删除 Go gateway、Go module、OIDC/local-gateway 专用配置和验证面。
- 删除 Celery/Redis Worker、发布队列、重试配置、依赖和旧 production profile。
- 删除 `AgentRunService` 兼容别名和 `start-local.sh` 包装入口。
- 删除 Chroma 与 OS keyring 可选后端，保留 Qdrant、memory 测试后端和 encrypted-file secrets。
- 同步 README、技术参考、Interview Notes、事实映射、测试和锁文件。

## Out of scope

- 不删除 SQLite local profile、SQLite Run/Session/Workspace Adapter 或其耐久性测试。
- 不删除 Cogent 文件记忆、会话全文搜索、Workspace 权限、独立 RAG、当前 MCP/Skill 实现。
- 不删除或改写已执行的 Alembic 历史迁移；不对现有数据库执行迁移。
- 实现阶段不提交、推送、部署或发布；2026-09-07 用户后续明确授权仅提交并推送本任务变更。

## Acceptance criteria

- [x] 当前生产代码不再包含 Go gateway、Celery/Redis Worker、Chroma、OS keyring 或退役数据库记忆业务实现。
- [x] Workspace 成员权限、会话搜索、Cogent 文件记忆和 SQLite local profile 保持可用。
- [x] 前端与消息 API 不再暴露旧规则型 Agent。
- [x] 正式运行时直接使用 QueryService，不再保留 AgentRunService 空别名。
- [x] Compose 服务仍仅为 postgres、qdrant、migrate、app，且启动检查通过。
- [x] 依赖、配置、README、技术参考、Interview Notes 和事实映射与删减后的产品边界一致。
- [x] 聚焦测试、完整 pytest、compileall、前端语法与测试、文档校验和 diff 检查通过，或精确记录非本任务 blocker。

## Decisions

- 保留现有数据库迁移链，避免破坏已有安装和从零升级；退役表的物理删除另行新增迁移。
- 保留 `AUTH_MODE=trusted_header` 作为通用受信反向代理身份边界，但删除 Go/OIDC 和
  `trusted_local_gateway` 专用证明；当前官方产品仍固定 `single_user`。
- 保留 `memory` 后端作为测试/评测依赖，不把测试替身与退役产品后端混同。

## Verification

- `.venv/bin/python -m pytest -q tests/test_self_hosted_compose.py tests/test_mcp_lifecycle.py tests/test_config.py tests/test_config_resolver.py tests/test_local_memory.py tests/test_postgres_repositories.py tests/test_runtime_bootstrap.py tests/test_task_queue.py tests/test_auth.py tests/test_model_registry.py tests/test_api.py tests/test_evals.py`
  - `190 passed, 1 warning, 49 subtests passed`
- `.venv/bin/python -m pytest -q tests/test_local_memory.py tests/test_self_hosted_compose.py tests/test_mcp_lifecycle.py`
  - `27 passed, 1 warning`
- `.venv/bin/python -m pytest -q`
  - 独立导出的暂存快照：`817 passed, 12 skipped, 1 failed, 108 subtests passed`
  - 唯一失败为未改动的 `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`：测试期待 `OSError`，当前基线 `ManagedFiles.read()` 对 `ELOOP/ENOTDIR` 明确转换为 `ValueError`；单独复现结果相同，且这两个文件 `git diff` 为空。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 24 个 Markdown 和 44 个 capability；仅输出工作区证据已变化的 review warnings。
- `node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs`：26/26 通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `docker compose --env-file .env.example config --quiet`：通过。
- `bash -n scripts/start.sh`：通过。
- `uv lock --check`：通过，解析 135 个包。
- `git diff --check`：通过。
- RAG 架构图使用 Archify 重新交付：showcase 9/9、0 error、0 warning；四个桌面尺寸无溢出，浅色/深色导出均已目视检查。

## Result

已删除确认的 1–8 组退役实现，共净删约 1.6 万行：

- 删除旧规则型 Agent、孤立 SSE/loader/executor/stream helper、AgentRunService 别名。
- 删除 Go module/gateway/OIDC-local-gateway、Celery/Redis Worker 和 production profile。
- 删除数据库 ProjectMemory/UserMemory 运行实现、旧路由/Schema/评测；旧 API 现在为 404。
- 将 Workspace 成员权限与 ConversationMemoryHit 移到独立的 domain/repository 边界。
- 删除 Chroma 和 OS keyring 后端及依赖；保留 Qdrant、测试用 memory 与 encrypted-file。
- 保留 SQLite local profile、Workspace 权限、会话搜索、Cogent Markdown 文件记忆、独立 RAG 和历史 Alembic revision。
- 同步 README、双语技术参考、Interview Notes、事实索引、RAG 架构图、测试和 `uv.lock`。

实现与验证阶段未提交、未推送、未部署、未执行数据库迁移；2026-09-07 用户随后明确
授权仅提交并推送本任务变更。共享工作区中的
`.workflow/tasks/ACTUAL-CONTEXT-TOKEN-USAGE.md`、`ai_agent_platform/static/styles.css`、
`tests/test_chat_message_ui.mjs` 并发修改保持不动。

已删除 4 个过期数据库记忆图源；对应 8 个未被文档引用的旧 PNG 导出原计划一并删除，
但精确 `rm` 被执行审批系统因额度限制拒绝，因此仍留在工作树中，未绕过审批。
