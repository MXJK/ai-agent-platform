# CHANGE-VALIDATION-GATE-PHASE-1: Change Run 验证完成门禁

## Goal

修复 `bounded_change` 在工作区已修改但尚未执行并通过验证时被提前标记为
`completed` 的问题，使 Change Run 的 evidence contract、变更摘要、终态和最终回答
对验证事实保持一致。

## In scope

- 对产生工作区修改的 `bounded_change` 强制要求实际验证结果且全部成功后才能完成。
- 成功写入只覆盖 `applied_change`，不再覆盖修改前的 `current_behavior`。
- 无验证命令时回到 Native 工具规划，并给出明确验证缺口；重试由现有 task/hard
  budget 有界控制，耗尽后以 `partial / validation_missing` 收敛。
- 验证失败时保留验证证据，并沿用现有 repair loop；无法修复或预算耗尽时不得完成。
- 对齐 `AgentChangeSummary.validation_passed`、`change_status`、Run terminal status 与
  最终回答。
- 增加覆盖单工具轮次 Planner、验证成功/失败、预算耗尽、checkpoint resume、
  非修改型任务及既有 ChangeSet/approval/artifact 兼容性的回归测试。

## Out of scope

- 多文件 `required_changes` / checklist。
- 第二阶段 `ChangeCompletionContract`。
- 请求授权分类、“继续做”识别或 UI 改动。
- 通过增加模型轮数规避门禁，或把“执行过验证命令”视为“验证通过”。
- commit、push、merge、release、migration 或 deployment。

## Acceptance criteria

- [x] `bounded_change` 写入文件但没有验证时不得 `completed`，并继续规划验证。
- [x] 成功写入仅证明 `applied_change`；`current_behavior` 需要读取/搜索等独立证据。
- [x] 实际验证全部成功后 Change Run 才可 `completed`。
- [x] 验证失败进入 repair 或非完成终态，并保留失败验证证据。
- [x] 无验证直至预算耗尽时返回 `partial`，terminal reason 为 `validation_missing`。
- [x] checkpoint resume 后验证门禁仍有效。
- [x] `validation_passed`、`change_status`、Run 终态与最终回答语义一致。
- [x] answer/diagnose/plan、approval、失败恢复、ChangeSet 和 artifact 既有行为不回归。
- [x] focused tests、全量 pytest、compileall 与 `git diff --check` 全部通过。

## Decisions

- 第一阶段在现有 evidence/budget/change-loop 模型内增加最小门禁，不引入第二阶段契约。
- `bounded_change` 固定要求 `validation_result`，但 completed 另外由共享
  `change_validation_state()` 校验“实际有 Diff + 最新验证非空且全部 exit code 0”；因此
  “执行过命令”和“验证通过”不会混为一谈。
- Native Loop 在 missing/failed 时回灌验证或修复提示；有用工具调用继续使用原 task/hard
  budget，无工具完成尝试使用 checkpoint-safe `validation_missing_rounds` 有界收敛。
- 非 Native 兼容路径在 artifact 收集后最多补一次规划机会，随后以明确非完成终态收口；
  现有验证失败 repair approval loop 保持不变。
- `AgentRunResult` / API response / terminal event 增加 `terminal_reason`；最终回答在验证门禁
  未通过时使用确定性非完成说明，并重置先前可能流出的候选回答。
- 文档影响已同步到 `README.md`、`INTERVIEW_NOTES.md`、Part 00、Part 04 与
  `INTERVIEW_NOTES/facts.json`；Interview Notes 文件在当前仓库由 `.gitignore` 排除，但已在
  工作区更新并通过校验。
- 共享工作区原有 `.workflow/tasks/RAG-ANSWER-QUALITY-DEEPSEEK.md` 修改属于其他工作，
  本任务不读取、不覆盖、不纳入交付 diff。

## Verification

- Focused：`.venv/bin/python -m pytest -q tests/test_change_validation_gate.py
  tests/test_agent_change_loop.py tests/test_task_shaped_budgets.py
  tests/test_native_tool_calling.py tests/test_agent_runtime_framework.py
  tests/test_agent_loop_boundaries.py tests/test_agent_loop_characterization.py
  tests/test_change_sets.py tests/test_change_set_api.py`：`120 passed, 32 subtests passed`。
- Full：`.venv/bin/python -m pytest -q`：`811 passed, 139 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 24 个 Markdown 文件、
  46 个 capabilities；仅输出基于历史 revision 的 evidence-review warnings，无 validation
  error。
- `git diff --check`：通过。
- Diff 审计：无 migration、部署、凭据或生成产物；保留并排除共享工作区原有
  `.workflow/tasks/RAG-ANSWER-QUALITY-DEEPSEEK.md` 修改；未 commit、未 push。

## Result

完成。所有实际产生工作区修改的 `bounded_change` 只有在修改后验证真实执行且全部成功时
才能 `completed`。缺失验证会继续规划并在 hard/no-progress budget 耗尽后以
`partial / validation_missing` 收敛；失败验证进入既有 repair loop 或以
`partial / validation_failed` 收敛，拒绝修复为 `blocked / repair_rejected`。Change summary、
公开 terminal reason、Run status、终态 event 和最终回答已统一到同一判定。

本阶段未覆盖多文件 required-changes/checklist，也未引入 `ChangeCompletionContract`。
第二阶段可直接复用 `change_validation_state()` 作为当前最小完成判定适配点，再把多文件
要求、逐项验证和更丰富的阻塞原因移入新契约，而无需改写 Provider 或审批边界。真实
DeepSeek API 未在本地凭据环境执行；“每轮一个非只读工具”的行为由 Native Planner 回归
确定性覆盖。
