# CHANGE-COMPLETION-CONTRACT-PHASE-2: 多交付项完成契约

## Goal

在第一阶段“有修改必须验证”的门禁之上，引入独立、checkpoint-safe、可观测且
单调推进的 `ChangeCompletionContract`。`bounded_change` 只有在用户请求或 live
repository evidence 支撑的全部变更项、逐项验证与最终工作区产物均满足后才允许
`completed`，避免多文件任务只完成一个文件就提前结束。

## In scope

- 新增独立 Completion Contract 模型、冻结/扩展/推进 helper 和兼容适配。
- 在证据合并后、第一次 workspace mutation 前定义并冻结合同；生成不可靠时 fail closed。
- 以真实 Diff/文件状态逐项匹配 create/update/delete，保持 resume 后 revision 与 satisfied
  集合单调，拒绝静默缩小合同。
- 将 required validations 与第一阶段通用验证门禁分离；`git diff --check` 只表示 Diff
  格式检查，不单独证明功能交付。
- unresolved checklist 回灌 Native Tool Loop；模型提前给最终文本时继续执行，hard budget
  后以 `partial / completion_requirements_unresolved` 保留部分结果。
- 在 ChangeSet、Run result、API/Event 与 trace 中公开有界的 Completion Contract 摘要。
- 为旧 checkpoint 提供清晰兼容路径，至少保留第一阶段验证门禁。
- 同步 README、Interview Notes 入口/受影响 Part 与 facts.json。

## Out of scope

- 扩大 workspace mutation authority、自动批准、部署、迁移、merge、commit 或 push。
- 将 Completion Contract 合并回 EvidenceContract，或回滚/绕过第一阶段验证门禁。
- 依赖 Provider 特定的多工具并行能力；单工具一轮轨迹必须可完成。
- 基于无当前请求或 live repository evidence 支撑的模型臆测扩展交付范围。

## Acceptance criteria

- [x] Completion Contract 在第一次 workspace write 前冻结，包含稳定 ID、路径/目标、
  operation、描述、来源、状态、required validations、revision、冻结时间和 trace 原因。
- [x] 合同只能收紧完成条件，不能扩大授权；路径合法、operation 合法、无重复或父子冲突。
- [x] Resume 后合同稳定，已满足项单调增加；扩展合同必须增加 revision 并留下原因/trace，
  模型不能静默删除或缩小条目。
- [x] 单次写入只满足实际 Diff/文件状态匹配的目标；CSS 写入不能满足 JS。
- [x] 所有 required changes、第一阶段验证门禁、required validations 和最终 status/Diff
  都满足后，`completion_contract_satisfied` 才为 true，Run 才可 completed。
- [x] unresolved checklist 会回灌模型继续执行；提前最终文本被门禁阻止，hard budget 后为
  `partial / completion_requirements_unresolved`。
- [x] 合同无法可靠生成时 fail closed；旧 checkpoint 无合同仍可恢复并保留 Phase 1 门禁。
- [x] API/Event/trace 可区分 evidence satisfied、mutation applied、validation passed 和
  completion contract satisfied，且输出保持有界。
- [x] 两文件 CSS/JS、validation failure、DeepSeek 单工具轮次、checkpoint、静默缩小、
  hard budget、旧 checkpoint 和 API/Event 回归全部通过。
- [x] 真实扫雷 fixture 从 HTML live evidence 推导缺失 CSS/JS，依次写入并执行 JS 语法与
  本地引用存在性检查，全部通过后才 completed。
- [x] focused tests、全量 pytest、compileall、Interview Notes validate 与 git diff --check
  全部通过。

## Decisions

- 新建 `completion_contract.py`，不向 `EvidenceContract` 继续添加泛化标签。合同包含稳定
  change/validation ID、来源、状态、冻结时间、revision、unresolved 列表、最终产物状态和
  有界 trace；`EvidenceContract` 只负责判断何时可停止探索并进入修改阶段。
- `bounded_change` 在 `merge_evidence` 后进入 `define_completion_contract`。仅使用当前请求
  中的显式路径/符号、focus path 和 live repository evidence；无法得到稳定目标时以
  `completion_contract_unavailable` fail closed。合同不改变工具权限或 mutation authority。
- 合同推进只接受实际成功 ToolResult 和最终文件/Diff 状态。写入只能满足同一路径与 operation
  的目标；`git diff --check` 只标记 Diff 格式检查，不能代替功能验证。
- 新 live evidence 只能通过 append-only revision 扩展合同并记录原因；既有 required item
  不会因模型输出或 resume 被删除，已满足集合单调增加。
- `BudgetPolicy` 对严格 bounded change 以 `completion_contract_satisfied` 为成功条件，并继续
  要求 Phase 1 验证门禁通过。提前最终文本被清除并附 unresolved checklist 继续规划；hard
  budget 返回 `partial / completion_requirements_unresolved`。
- 旧 checkpoint 缺少合同且已有 mutation 时进入显式 `legacy_phase1_contract` 兼容模式：不尝试
  重建历史多文件需求，但仍保留 Phase 1 的修改后验证门禁。
- 非 workspace 的显式 MCP 外部动作不强行套用文件完成合同；这只避免错误分类，不扩大原有权限。

## Verification

- Focused：`.venv/bin/python -m pytest -q tests/test_change_completion_contract.py
  tests/test_change_validation_gate.py tests/test_native_tool_calling.py
  tests/test_agent_runtime_framework.py tests/test_api.py tests/test_agent_change_loop.py`
  -> `123 passed, 27 subtests passed in 8.76s`。
- Full：`.venv/bin/python -m pytest -q` -> `821 passed, 139 subtests passed in 44.97s`。
- Compile：`.venv/bin/python -m compileall ai_agent_platform tests evals` -> 通过。
- Handbook：`.venv/bin/python INTERVIEW_NOTES/validate.py` ->
  `Validated 24 Markdown files and 46 capabilities`；仅输出共享工作树已有 evidence-review 提醒。
- Diff：`git diff --check` -> 通过。

## Result

完成。`ChangeCompletionContract` 已独立接入冻结、单调推进、resume、预算、终态门禁、
ChangeSet、Run/API/Event 和 trace。确定性扫雷回归从缺失引用的 HTML 推导 CSS 与 JS 两个
交付项，依次写入 CSS、阻止提前最终文本、写入 JS、执行 `node --check` 和本地引用存在性
检查、收集最终 status/Diff 后才进入 `completed`。README 与本地 Interview Notes/facts 已同步。

共享工作区中预先存在的 `.workflow/tasks/RAG-ANSWER-QUALITY-DEEPSEEK.md` 修改保持原样，未纳入
本阶段实现。本任务没有 commit 或 push；`last_verified_commit` 保持原值，因为验证覆盖的是
未提交的 Phase 1 + Phase 2 工作树。
