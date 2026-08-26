# MEMORY-EVIDENCE-IDEMPOTENCY：重复来源不再阻断项目记忆提炼

## Goal

修复项目记忆提炼时同一任务多次命中同一文件造成的 PostgreSQL 证据唯一键冲突，
使正常提炼和模型失败后的确定性兜底都能保存合法记忆。

## In scope

- 提炼来源按现有数据库来源键去重，保留首条完整证据，再应用最多五条来源预算。
- PostgreSQL 证据插入同时容忍主键与来源唯一键冲突，其他数据库错误继续抛出。
- 扩展现有测试，验证重复来源、不同来源、预算、兜底及 SQL 冲突处理。
- 同步 README、Interview Notes、facts 和验证结果。

## Out of scope

- 数据库迁移、历史任务重放、真实模型调用、部署。
- 修改模型 JSON 输出策略、前端或正在进行的 SESSION-HISTORY-IN-PAGE 改动。
- 更改现有证据身份定义，或合并不同片段的行号和哈希。

## Acceptance criteria

- [x] 同一提炼任务重复文件来源只保留首条完整证据，不消耗其他来源的预算。
- [x] 不同文件、来源类型或来源任务仍保留；最多五条附加来源。
- [x] PostgreSQL 写入 SQL 容忍重复来源/证据 ID，非唯一约束错误不被吞掉
  （由 SQL 契约及本地约束回归验证，真实 PostgreSQL 验证待授权）。
- [x] 模型失败的确定性兜底仍可保存，错误信息保留，完成任务不重复提炼。
- [x] 聚焦测试、全量 pytest、compileall、手册校验通过。

## Decisions

- 对齐现有唯一键 `(memory_id, source_kind, source_id, path)`，无需迁移。
- 同一来源的多个片段保留首条，避免拼接行号后与原有 content_hash 不匹配。
- PostgreSQL 使用无指定目标的 `ON CONFLICT DO NOTHING` 处理两个已有唯一约束；
  不捕获或忽略其他异常，记忆、证据、审计和 Outbox 继续共享事务。
- 文档有影响：说明来源去重规则、预算与失败恢复边界。
- 当前共享工作树已有 SESSION-HISTORY-IN-PAGE 活跃任务；本修复单独记录，
  不覆盖其代码、任务内容或活动状态，收尾仅刷新 workflow 时间戳。
- 用户随后明确授权直接在 `main` 提交并推送；仅纳入本修复的代码、测试、
  README 段落和任务记录，保留其他任务的未提交改动。Interview Notes 按仓库现有
  `.gitignore` 约定只在本地维护，不强制加入版本控制。

## Verification

- 修复前新增服务回归、扩展的 PostgreSQL 写入契约回归均按预期失败。
- `.venv/bin/python -m pytest -q tests/test_project_memory.py tests/test_postgres_repositories.py tests/test_local_memory.py`：
  `56 passed, 2 subtests passed`。
- `.venv/bin/python -m pytest -q`：`680 passed, 109 subtests passed in 41.08s`。
  共享工作树中的并行任务仍可能继续修改文件，该结果不是后续变更的验证承诺。
- 用户授权提交后，从暂存区导出不含其他任务改动的独立副本，确认导入路径来自该副本；
  完整 pytest 再次通过：`680 passed, 109 subtests passed in 40.71s`，compileall 通过。
  最终暂存区仅额外更新本任务验证记录，代码/测试/README 与该副本一致。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，24 个 Markdown 文件、43 项能力；
  输出相对于既有提交基线的 evidence review warnings。
- `git diff --check`：通过。
- PostgreSQL 事务临时表 QA 命令在执行前因缺少单独的人类写入授权被拒绝，未连接执行。
  新增本地 SQLite 内存约束回归仅转换参数占位符，实际执行仓库 INSERT，验证同批/跨批
  唯一键重复可跳过、不同来源保留、首条哈希不变、外键/非空错误继续抛出。
  不把此本地验证表述为真实 PostgreSQL 集成测试。

## Result

- 代码修复和本地验证完成：提炼先去重再应用五条来源预算，PostgreSQL 写入覆盖
  ID 与来源两个唯一冲突目标，保持记忆、证据、审计和 Outbox 事务边界不变。
- 没有改变模型 JSON 解析与兜底策略；本次保证兜底产物也不会被重复证据阻断。
- README 中英文、Interview Notes 首页、Parts 04/06 和 facts 已同步。
- 未重放既有 4 条失败任务，未调用真实模型，未执行迁移或部署。
- 如需验证现有运行栈并恢复历史记忆，下一步需用户明确授权真实数据库临时表验证，
  再另行确认历史任务重试（可能产生模型费用并写入业务记忆）。
- 保留共享工作树中会话历史、Agent 流式输出任务的改动和 workflow 活动任务；
  不更新 `last_verified_commit`，本修复结果以本任务文件为准。
