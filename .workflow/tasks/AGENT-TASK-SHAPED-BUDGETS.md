# AGENT-TASK-SHAPED-BUDGETS: 任务整形、证据契约与分级执行预算

## Goal

在第一阶段 `repo.collect_evidence` 批量 Evidence Executor 之上，按任务类型冻结工具 Profile 和独立预算，并依据可持久化的证据覆盖、无进展、重复调用与有限扩展状态确定性停止，降低概览任务的模型请求和工具调用成本，同时保留复杂修改、调查、审批和恢复语义。

## In scope

- 识别 `overview`、`targeted_read`、`bounded_change`、`investigation`、`broad_review` 五种 `task_shape`。
- 为每种任务生成含证据要求、工具族、模型请求、工具轮次/调用、证据 Token、扩展和停止条件的 `evidence_contract`。
- 在 Run 开始分类后冻结任务级工具 Profile，并在普通执行轮次及 checkpoint resume 中保持集合、顺序、预算与覆盖状态一致。
- 每轮维护 evidence coverage、新证据、覆盖增量、未解决要求、重复调用数和扩展轮次；契约满足、无进展或预算边界触发确定性最终回答。
- 为任务识别、分级预算、工具权限、停止/扩展、最终回答禁用工具、checkpoint 恢复及原有运行语义补充聚焦测试和脚本化概览回归。
- 同步受影响的 README、Interview Notes 与事实映射。

## Out of scope

- 新的 Compaction 机制或改变现有 transcript reduction ladder。
- Prompt Cache。
- UI Token 语义调整。
- 全局粗暴降低现有 24/72 上限。
- 重做、绕过或扩展第一阶段 Evidence Executor 的能力闭集。
- 第三、第四阶段、部署、迁移、提交或推送。

## Acceptance criteria

- [x] 规范化和组合判断稳定识别指定 overview 同义表达，且具体故障、修改、验证或目标请求不会误判。
- [x] 五种 task shape 生成独立 evidence contract，overview 使用 5 次模型请求、2/3 轮、8/12 调用、12000 Evidence Token 和最多一次扩展的建议预算。
- [x] overview 只暴露仓库 list/find/search/read 与 `repo.collect_evidence`，不能调用 write 或 shell；其他 shape 使用各自冻结 Profile。
- [x] 每轮持久化 coverage、新证据、coverage delta、unresolved、重复调用和扩展轮次；checkpoint resume 保持一致。
- [x] 契约满足立即最终回答；一轮无有效新证据后停止；等价重复调用不执行。
- [x] 软预算优先完成答案；只有明确 unresolved requirement 才允许一次有限扩展；硬预算保留 partial/blocked 语义。
- [x] 最终回答模型请求的工具集合为空。
- [x] bounded change 与 investigation 不受 overview 小预算限制，原有 pause/resume/approval/failure 语义不回归。
- [x] 固定“小/大仓库：分析下当前项目”回归分别满足模型请求不超过 4/5、实际工具调用不超过 10/12，答案覆盖用途、模块、入口和技术栈。
- [x] 聚焦测试、完整 pytest、compileall 与文档事实校验通过。

## Decisions

- 第一阶段 Evidence Executor 是本阶段的只读采证原语；本阶段只约束何时、以何种预算和工具集合使用它。
- `task_shape`、`evidence_contract`、工具 Profile 和证据进度进入 LangGraph state，从第一次分类后冻结并由 checkpoint 原样恢复。
- 任务级硬预算不能超过进程级安全上限；复杂修改保留原有 24/72 上限，避免把 overview 限制传播到高复杂度任务。
- context seed 的内部 inventory fallback 可使用目标任务需要的底层 repository 子工具，但 overview 的模型/执行视图仍是严格仓库只读闭集；非原生规划器提出的越界辅助调用记录为 `task_tool_profile` suppressed，不升级为权限错误。
- 失败诊断结果本身是有效的新证据，可进入恢复计划；durable replay 不算新增证据。最终回答始终使用空工具集合，不依赖 Provider 的 tool choice 禁止调用。

## Verification

- `.venv/bin/python -m pytest -q tests/test_task_shaped_budgets.py`
  - `11 passed, 2 subtests passed`。
- `.venv/bin/python -m pytest -q tests/test_task_shaped_budgets.py tests/test_evidence_executor.py tests/test_native_tool_calling.py tests/test_agent_runtime_framework.py tests/test_effective_tool_pool.py`
  - `84 passed, 22 subtests passed`。
- 固定 overview 脚本测量：小仓库 `1` 次模型请求、`6` 个实际工具结果；大仓库 `1` 次模型请求、`7` 个实际工具结果，均为 `completed`，答案包含用途、模块、入口和技术栈。
- `.venv/bin/python -m pytest -q`
  - `719 passed, 123 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`
  - 通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`
  - `Validated 24 Markdown files and 45 capabilities`；其余 evidence review warning 来自共享工作区已有未提交变更，不是校验失败。
- `git diff --check`
  - 通过。

## Result

- 新增 `task_shaping` 规范化分类与五类契约；指定中英文 overview 同义表达稳定命中，路径/符号、修改、故障、验证、显式工具和多目标/继续读取信号通过组合判断覆盖，避免字符串特判误伤具体任务。
- 分类后冻结任务工具 Profile 与分级预算；overview 只允许 repository list/find/search/read 和第一阶段 `repo.collect_evidence`，bounded change 保留 24/72，显式更小的进程级安全上限继续优先。
- LangGraph state/checkpoint 记录 coverage、新证据、增量、unresolved、重复调用、扩展轮次和任务模型请求；resume 保留契约/Profile/既有覆盖，覆盖只随新证据单调增加。
- 原生循环在契约满足、一轮无进展、重复等价调用、soft/extension/hard 边界确定性收口；Evidence Executor 的 Token 与真实子调用量按剩余任务预算裁剪，外层调用继续不冒充一个实际 ToolResult。
- 最终回答使用空工具集合；原有 pause/resume、waiting input、审批、MCP 写操作、失败恢复、hard partial/blocked、Eval 和 trajectory 语义通过全量回归。
- README 与被 `.gitignore` 忽略但由项目工作流要求维护的 Interview Notes/facts 已同步；这是 Agent 行为、工具能力边界、checkpoint 数据流和运行预算变更，具有文档影响。
- 未实现新 Compaction、Prompt Cache、UI Token 语义、全局 24/72 粗降或第三/第四阶段；未提交、未推送、未部署或迁移。
