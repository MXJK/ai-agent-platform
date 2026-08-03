# INTEGRATE-MODEL-ROUTER-USAGE: 整合模型路由、统一用量治理与本机安全加固

## Goal

在独立整合分支中组合已验证的本机安全加固、统一模型用量治理和真正模型路由，
解决重叠实现并保持三者的验收语义与测试证据。

## In scope

- 保留安全加固分支的 loopback、凭据、Sandbox 和生命周期边界。
- 合入 `codex/unified-llm-usage-budget` 的 allowlist、精确 Token 预检、统一账本和预算。
- 让 ModelRouter 与预算授权、Provider usage 记录和 Chat SSE 正确组合。
- 解决配置、运行时装配、LLM、Chat、前端、文档和测试冲突。
- 在新 `codex/` 分支提交本地整合结果，但不推送、不建 PR、不部署。

## Out of scope

- 修改既有两个来源分支、发布、迁移数据库或调用真实付费模型。
- 集群共享熔断、在线价格同步或新的预算产品需求。

## Acceptance criteria

- [x] 新分支同时包含三个来源能力，原来源分支不变。
- [x] 路由候选必须通过 allowlist，预算降级模型必须重新通过能力与健康校验。
- [x] 每个实际 Provider 尝试使用对应模型的精确输入计数和授权输出上限。
- [x] 成功、partial failure 和首 delta 前 fallback 的 usage 不重复、不漏记。
- [x] Chat meta/route/error 同时保留预算审计和路由 Trace。
- [x] 安全加固配置和运行时清理没有被旧分支覆盖。
- [x] 专项测试、全量 pytest、compileall、前端语法和文档校验通过。

## Decisions

- 使用新的整合分支，避免直接改写 `codex/harden-local-boundaries` 或
  `codex/unified-llm-usage-budget`。
- ModelRouter 决定候选顺序；每个候选在调用前进入 UsageLedger 授权。预算 downgrade
  不是无条件跳转，目标模型仍必须在目录中满足能力与健康要求。
- 首个文本 delta 仍是禁止自动重放的边界；usage 以实际 Provider 返回为准。
- 空 allowlist 仍只派生主 LLM、Embedding 与预算 fallback；目录里的其他模型会在
  Trace 标记 `model_not_allowlisted`，不会进入精确计数或生成调用。
- 预算 fallback 未显式出现在目录时，从 fallback 配置派生保守低质量目录项，保持
  既有 downgrade 配置兼容；显式目录可覆盖其能力、价格、质量和延迟。
- Chat 预检保存 UsageContext，流生成器退出作用域后，重试与跨 Provider fallback
  仍使用原 session/workspace 做预算授权和用量归属。

## Verification

- `.venv/bin/python -m pytest -q`
  - 190 passed；仅有 FastAPI TestClient 的上游弃用 warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`
  - 通过。
- `node --check ai_agent_platform/static/app.js`
  - 通过。
- `git diff --check`
  - 通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`
  - 11 份 Markdown、26 项能力验证通过；变更证据提示已逐项审阅并同步。
- `.venv/bin/python -m alembic heads`
  - `20260731_0012 (head)`。
- `.venv/bin/python -m alembic upgrade head --sql`
  - PostgreSQL 离线 upgrade SQL 生成通过。
- `.venv/bin/python -m alembic downgrade 20260731_0012:20260730_0011 --sql`
  - PostgreSQL 离线 downgrade SQL 生成通过。
- 专项 Fake Provider 测试覆盖 429、超时、熔断打开、half-open 恢复、首 delta
  后不重放、逐候选精确计数/授权、allowlist 过滤、预算目标能力复验和 usage 单次记录。

## Result

已在 `codex/model-router-usage-integration` 完成三条能力线的本地整合：

- `ModelRouter` 负责能力、上下文、质量/成本/延迟和 Provider 健康排序；UsageLedger
  在每个实际候选调用前负责 allowlist、Provider 精确计数和 Token 预算授权。
- Chat 在首个非空 delta 前可跨 Provider fallback，新候选会重新计数/授权；首个
  delta 后只返回 partial error，不自动重放。成功、partial 和 fallback usage 均按
  实际尝试单次入账。
- Route Trace 同时记录目录候选、过滤/失败原因、最终模型与预算请求/实际模型；
  Chat meta/route/error 和前端 `model_route` 节点公开同一审计信息。
- Chat、Agent turn、压缩、RAG Ask、Embedding 和后台模型调用共享统一账本；会话
  与 Workspace API/UI 提供预算和 operation 聚合。
- README、环境变量、迁移和本地面试手册已同步；本机 loopback、凭据、Sandbox
  与运行时清理边界保持不变。

剩余生产化边界：熔断仍为进程内状态；预算读取已提交账本，不做跨进程强一致
预留；价格/质量/延迟是运维配置而非在线事实；本任务未执行数据库迁移、推送、PR
或部署。
