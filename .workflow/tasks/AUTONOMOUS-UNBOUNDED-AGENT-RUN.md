# AUTONOMOUS-UNBOUNDED-AGENT-RUN: 自主决策与无累计步数上限的 Agent Run

## Goal

移除模型可见的 Plan 模式分支，由模型结合对话与实时仓库证据自主判断回答、诊断或修改；取消单个 Run 的累计模型请求、工具轮次、工具调用与 Agent step 终止上限，同时将每个模型 step 的安全只读工具并发上限固定为 10，并保留权限、验证、停滞、取消与审批门禁。

## In scope

- 将请求分类扩展为模型输出 `answer / diagnose / change` 行动决策，并为连续短指令注入受控对话历史、focus files 与 live repository evidence 候选。
- 取消新 Run 的 `plan` 终态/授权分支；模型选择 `change` 时仍须经过服务端 workspace、角色、审批、路径、验证与 Completion Contract 门禁。
- 为兼容旧 checkpoint 保留旧 `request_mode=plan` 的读取与恢复能力，但新自主模式不再生成它。
- 引入 unbounded Run 预算模式：累计模型请求、工具轮次、工具调用、扩展轮次、压缩次数与图节点切片不再作为任务失败或 partial 的终止条件。
- LangGraph 使用有限节点切片执行并从 checkpoint 自动续跑，有限 `recursion_limit` 只作为执行切片大小，不作为单任务最大 Agent steps。
- 每个模型 step 最多执行 10 个相互独立、幂等、无需审批的只读工具；写入、验证、审批和用户输入工具继续串行。
- 保留成功条件、Completion Contract、修改后验证、重复/无进展、连续失败、暂停、取消、审批、人工输入以及部署基础设施超时等安全边界。
- 同步 README、Interview Notes 入口/受影响 Part 与 facts.json。

## Out of scope

- 自动批准工具调用、扩大工作区或外部写入权限、绕过 sandbox/角色/审批配置。
- 并行 workspace mutation、验证命令、MCP 外部动作或其他非幂等工具。
- 移除所有停滞与失败保护；“无上限”仅指累计模型/工具/Agent-step 配额不再决定终态。
- merge、release、migration、deployment、commit 或 push。

## Acceptance criteria

- [x] 新自主模式的分类器结合当前请求、受控历史与 focus files 输出 `answer / diagnose / change`；连续“修改”可从历史与 live repo 证据恢复稳定目标，不再因默认 `plan` 而拒绝写入。
- [x] 模型的 `change` 决策只授予已有服务端允许范围内的 mutation authority；Completion Contract 的目标必须由当前请求或 live repository evidence 确认，模型提示本身不能扩大范围。
- [x] 新 Run 不因累计 model requests、tool rounds、tool calls、extension rounds、compaction count 或 LangGraph recursion slice 耗尽而进入 `partial / failed`。
- [x] checkpoint-safe 图切片在达到有限 `recursion_limit` 后自动续跑，直到业务终态、停滞/失败保护、暂停、取消、审批或用户输入边界。
- [x] 单个模型 step 最多并行执行 10 个安全只读工具；第 11 个及之后的调用留到后续 step，写入/验证/审批类工具保持单个串行执行。
- [x] bounded 兼容模式与旧 checkpoint 仍可恢复；现有 Completion Contract、change validation gate、重复/无进展和连续失败保护不回退。
- [x] 配置、README、Interview Notes 与 facts.json 准确区分“累计配额取消”“单 step 并发 10”和仍存在的安全/基础设施边界。
- [x] focused tests、全量 pytest、compileall、Interview Notes validate 与 git diff --check 全部通过。

## Decisions

- 默认生产配置为 `AGENT_AUTONOMOUS_MUTATION_ENABLED=true` 和 `AGENT_RUN_BUDGET_MODE=unbounded`；直接构造 Runtime 与缺少新字段的旧 checkpoint 继续采用 bounded 语义以保持兼容。
- 分类模型输出行动与目标提示；目标提示必须能在当前请求、受控用户历史或 focus files 中逐字找到，且历史目标必须在当前 Run 读取同一路径后才能进入 Completion Contract。
- 无累计配额只取消模型请求、工具轮次、工具调用、扩展、成功压缩和 LangGraph 节点切片的数量终止；权限、审批、Workspace/Sandbox、Completion Contract、修改后验证、无进展、重复调用、连续失败、暂停、取消和基础设施超时保持不变。
- `AGENT_MAX_PARALLEL_TOOLS_PER_STEP` 是模型单 step 的安全只读并发上限，配置和执行层均硬封顶 10；写入、验证、审批和用户输入继续单调用串行执行。
- `AGENT_MAX_READ_TOOLS_PER_ROUND=6` 继续只约束模型接管前的确定性种子探索；它不是累计调用配额，也不替代单 step 的 10 路并发上限。
- LangGraph 仍要求有限 `recursion_limit`，因此 unbounded Run 把它解释为 checkpoint 执行切片大小，并仅在存在 durable pending nodes 时自动续跑。

## Verification

- `.venv/bin/python -m pytest -q tests/test_autonomous_unbounded_agent.py tests/test_config.py tests/test_self_hosted_compose.py tests/test_trajectory_evals.py tests/test_eval_service.py` → `108 passed, 12 subtests passed`。
- `.venv/bin/python -m pytest -q tests/test_native_tool_calling.py::NativeToolLoopTests::test_parallel_read_batch_respects_existing_per_round_limit tests/test_autonomous_unbounded_agent.py tests/test_config.py` → `41 passed, 8 subtests passed`。
- `.venv/bin/python -m pytest -q` → `830 passed, 139 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals` → 通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py` → `Validated 24 Markdown files and 46 capabilities`；仅有既有 evidence-review 提醒，无校验失败。
- `git diff --check` → 通过。

## Result

已完成默认自主行动分类、历史短指令目标恢复、live-evidence Completion Contract 门禁、无累计 Run 配额、checkpoint-safe 自动图切片，以及每模型 step 最多 10 个安全只读工具并发。新增端到端回归确认“修改”可从历史恢复 `app.py` 并在实时读取后冻结合同，不再返回 `completion_contract_unavailable`。README、Interview Notes、facts.json、环境示例和 Compose 默认值已同步；Interview Notes 属于本仓库忽略的本地手册，但已完成本地更新并通过其校验器。未执行 commit、push、merge、migration 或 deployment。
