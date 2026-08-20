# CODING-AGENT-OUTPUT-BUDGETS: Codex 风格的编程 Agent 输出预算与工具调用恢复

## Goal

让编程 Agent 使用分阶段、按模型能力裁剪的输出预算，并在工具参数被截断或生成非法
JSON 时自动进行收敛重试，避免一次多文件大参数生成直接导致 Run 失败。

## In scope

- 在模型注册中心保存每个模型的最大输出 token 能力，并参与请求额度计算。
- 为 Agent 的规划、变更和最终回答分别配置输出预算；普通聊天继续使用现有默认预算。
- 原生工具循环每轮只接受一个工具调用，并提示模型将变更拆成单文件、小 patch 的迭代。
- 将工具参数截断和非法 JSON 标记为可恢复错误，携带 finish reason、usage、参数长度和
  JSON 解析位置，并在同一模型内追加纠错提示重试。
- 对 OpenAI 原生工具调用关闭并行调用；其他 Provider 在运行时边界统一限制为单调用。
- 更新模型管理 UI、配置样例、README、面试手册和证据映射。
- 增加阶段预算、模型上限裁剪、非法参数重试、失败 usage 记账和单工具边界测试。

## Out of scope

- 将所有 Provider 迁移到 OpenAI `apply_patch` freeform custom tool；DeepSeek、Anthropic
  和 Google 的兼容工具协议仍使用各自的结构化参数格式。
- 自动执行数据库 migration、部署、发布或重启当前服务。
- 为供应商未公开或 discovery API 未返回的模型上限做远程猜测；这类模型使用后端保守
  profile，并允许在模型管理页显式调整。

## Acceptance criteria

- [x] 有效工具输出额度为阶段预算、模型最大输出能力和上下文剩余容量的最小值，并继续
  接受 Usage Ledger 的额度裁剪。
- [x] 默认规划/变更/最终回答额度分别为 4096/16384/4096 token，且可通过配置覆盖。
- [x] Agent 每轮最多执行一个模型工具调用；变更提示要求单文件或小 patch 迭代。
- [x] malformed/truncated tool arguments 在重试额度内不会立即终止 Run，重试请求包含明确
  的缩小范围纠错提示。
- [x] 失败请求的 usage 会计入 Usage Ledger 和 Run 错误诊断，错误诊断包含可安全
  持久化的 finish reason、参数字符数和 JSON 解析位置。
- [x] 模型管理 API/UI 可查看和修改模型最大输出 token；PostgreSQL migration 可升级和回滚。
- [x] 聚焦测试、项目全量测试、compileall、前端语法检查、手册校验和 diff 检查通过。

## Decisions

- `LLM_MAX_OUTPUT_TOKENS` 保持普通文本生成的默认额度，不再承担所有编程阶段的硬上限。
- 模型能力上限属于模型注册数据；Agent 阶段额度属于运行时策略。两者在授权前取最小值，
  避免把模型能力、成本策略和具体任务阶段混成一个配置项。
- Provider 原始截断参数不写入错误或日志，只记录长度和解析位置，避免源代码内容泄露。
- malformed JSON 的第一次恢复仍使用同一模型和同一阶段预算，但提示只生成一个更小的
  工具调用；预算不足由模型上限、上下文和 Usage Ledger 分别负责裁剪。

## Verification

- `.venv/bin/python -m pytest -q`：通过，`448 passed, 52 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，验证 24 个 Markdown 文件和 39 项
  capability；输出的 evidence review warnings 为证据文件变化提醒。
- `.venv/bin/alembic heads`：通过，唯一 head 为 `20260820_0022`。
- `.venv/bin/alembic upgrade 20260813_0021:20260820_0022 --sql`：通过，生成升级 SQL。
- `.venv/bin/alembic downgrade 20260820_0022:20260813_0021 --sql`：通过，生成回滚 SQL。
- `git diff --check`：通过。

## Result

已完成 Codex 风格的分阶段输出预算、按模型能力和上下文裁剪、单工具迭代，以及
malformed/truncated 工具参数的安全重试与失败 usage 记账。模型注册 API、管理 UI、
PostgreSQL schema migration、配置样例、双语 README、面试手册和回归测试已同步。

迁移 `20260820_0022` 仅完成静态升级/回滚 SQL 验证，未连接或修改当前数据库；部署前需由
操作者审阅并显式授权应用。文档影响为用户可见配置、模型注册契约、Agent 执行策略和错误
诊断边界变化，已同步对应文档而非记录为“无影响”。未创建 commit，因此
`last_verified_commit` 保持为此前已验证提交。
