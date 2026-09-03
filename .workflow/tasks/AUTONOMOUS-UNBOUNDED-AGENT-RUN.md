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
- [x] 分类器将自然语言目标词 `target_terms` 与显式路径提示分离；仓库发现只使用当前请求、受控 user 历史、focus files、显式路径和符号，不把 system/profile/assistant 内容混入搜索。
- [x] `resolve_change_targets` 在证据合并与 Completion Contract 之间以 checkpoint-safe 节点运行；服务端只接受显式请求目标、实时已读候选或实时入口暴露的缺失本地引用，歧义时请求用户选择，无候选时 fail closed。
- [x] 实际前端 Run 能展示目标解析节点、候选数与已解析数，并在变更工具执行前停在审批边界。
- [x] unbounded Run 的无进展判定基于 coverage 增长、Completion Contract 收缩、Artifact 分页游标向前、mutation 或 validation；普通成功读取不能重置停滞轮次。
- [x] Change Run 连续两轮没有语义进展且只剩写入/验证要求时，模型可见工具收窄到行动面；第三轮仍停滞时按既有门禁终止。
- [x] 结构化分类降级时，“目标没完成，继续做”式明确续做命令仍进入 `change / bounded_change` 并获得变更工具；原因、建议和选择问句保持只读。
- [x] 目标发现优先由模型基于服务端提供的实时已读候选与证据选择，只有仓库检查后仍有实质歧义才进入人工追问；模型选择仍不能越过候选集和 live-read 复核。
- [x] 新追问统一为 `structured-v1`：一至三题使用稳定 ID、候选项、单选/多选和自定义答案；前后端都拒绝空答案，Skip 必须显式提交，旧 checkpoint 文本回答仅走隔离兼容路径。
- [x] continue 在恢复前持久化 `user_question_answered`，原生 `agent.request_user_input` 的结构化答案作为同一 call ID 的普通成功 ToolResult 回灌模型循环。
- [x] bounded change 写入同时要求目标属于冻结 Completion Contract，并具有当前 Run 的可信 read-before-edit 观察；文件版本过期、未读目标、合同外路径和伪造外部工具哈希均 fail closed。
- [x] focused tests、全量 pytest、compileall、Interview Notes validate 与 git diff --check 全部通过。

## Decisions

- 默认生产配置为 `AGENT_AUTONOMOUS_MUTATION_ENABLED=true` 和 `AGENT_RUN_BUDGET_MODE=unbounded`；直接构造 Runtime 与缺少新字段的旧 checkpoint 继续采用 bounded 语义以保持兼容。
- 分类模型输出行动与目标提示；目标提示必须能在当前请求、受控用户历史或 focus files 中逐字找到，且历史目标必须在当前 Run 读取同一路径后才能进入 Completion Contract。
- 无累计配额只取消模型请求、工具轮次、工具调用、扩展、成功压缩和 LangGraph 节点切片的数量终止；权限、审批、Workspace/Sandbox、Completion Contract、修改后验证、无进展、重复调用、连续失败、暂停、取消和基础设施超时保持不变。
- `AGENT_MAX_PARALLEL_TOOLS_PER_STEP` 是模型单 step 的安全只读并发上限，配置和执行层均硬封顶 10；写入、验证、审批和用户输入继续单调用串行执行。
- `AGENT_MAX_READ_TOOLS_PER_ROUND=6` 继续只约束模型接管前的确定性种子探索；它不是累计调用配额，也不替代单 step 的 10 路并发上限。
- LangGraph 仍要求有限 `recursion_limit`，因此 unbounded Run 把它解释为 checkpoint 执行切片大小，并仅在存在 durable pending nodes 时自动续跑。
- 模型输出的 `target_terms` 只用于仓库发现和候选排序，不授予写权限；模型选择结果必须重新通过服务端候选集合、实时读取证据和工作区路径边界校验。
- 目标解析结果一经 `resolved` 即单调冻结并可由 checkpoint 恢复；Completion Contract 只消费冻结后的目标，不再直接把发现词或未验证的模型路径升级为修改目标。
- HTML `href/src` 与 JS/TS 本地 import/require 引用用于识别入口关系；仅实时读取入口中确认的缺失本地引用可以成为 `create` 目标，同分候选必须暂停请求用户选择。
- 成功工具调用与任务实质进展分开计数：重叠仓库证据和不推进游标的 Artifact 回读不会清零 `native_no_progress_rounds`，Completion Contract 未收缩的每个完整工具轮次都会累计 `completion_unresolved_rounds`。
- 当连续停滞达到阈值前一轮、变更已授权且 evidence contract 只剩 `applied_change / validation_result` 时，临时隐藏探索型只读工具，同时保留 mutation、validation、`agent.request_user_input` 与测试设计能力。
- 结构化分类器失败时不把自然语言续做命令默认为仓库问答：服务端高精度规则只接受“未完成/没完成”事实后紧接“继续/接着做、完成或实现”的命令，并继续用 advisory 问句门禁拒绝“为什么/是否继续做”等只读请求。
- 参照本地 DeepSeek Harness `49a606bc5b5934603f22a26957a07dc799ab0291` 的 `user-questions`、`tool-ask-user` 与 Web QuestionComposer 契约：问题/答案保持结构化，UI 禁止隐式空提交，模型等待结束后继续普通工具循环；本项目的无状态 HTTP continue 额外携带 `skipped=true` 以区分显式 Skip 与伪造空答案。
- DeepSeek Harness 的 filesystem observation policy 与本项目既有 Completion Contract 组合而非互相替代：只有平台可信文件读取/成功写入能产生版本观察，任意 MCP/外部工具自报 `path + sha256` 不构成写权限；执行层仍保留 Workspace/RBAC、审批、sandbox 基线、journal、原子替换和验证门禁。

## Verification

- `.venv/bin/python -m pytest -q tests/test_autonomous_unbounded_agent.py tests/test_config.py tests/test_self_hosted_compose.py tests/test_trajectory_evals.py tests/test_eval_service.py` → `108 passed, 12 subtests passed`。
- `.venv/bin/python -m pytest -q tests/test_native_tool_calling.py::NativeToolLoopTests::test_parallel_read_batch_respects_existing_per_round_limit tests/test_autonomous_unbounded_agent.py tests/test_config.py` → `41 passed, 8 subtests passed`。
- `.venv/bin/python -m pytest -q` → `830 passed, 139 subtests passed`。
- 目标解析与兼容 focused 回归 → `7 passed`；MCP 外部动作、扫雷、歧义确认和父目录本地引用联合回归 → `5 passed`。
- `.venv/bin/python -m pytest -q`（目标解析修复后全量）→ `837 passed, 139 subtests passed`。
- `node --check ai_agent_platform/static/app.js && node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs` → `20 passed`。
- `.venv/bin/python -m compileall -q ai_agent_platform tests evals` → 通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py` → `Validated 24 Markdown files and 46 capabilities`；仅有既有 evidence-review 提醒，无校验失败。
- `git diff --check` → 通过。
- `.venv/bin/python -m pytest -q tests/test_native_tool_calling.py tests/test_task_shaped_budgets.py tests/test_change_completion_contract.py tests/test_autonomous_unbounded_agent.py tests/test_agent_loop_boundaries.py tests/test_agent_change_loop.py`（语义停滞修复）→ `94 passed, 17 subtests passed`。
- `.venv/bin/python -m pytest -q tests/test_task_shaped_budgets.py tests/test_run_artifact_read.py::RunArtifactCheckpointAndStoreTests::test_native_model_loop_reads_and_reassembles_checkpoint_artifact_pages`（Artifact 游标边界）→ `20 passed, 2 subtests passed`。
- `.venv/bin/python -m pytest -q`（语义停滞与 Artifact 游标修复后全量）→ `841 passed, 139 subtests passed`。
- `.venv/bin/python -m compileall -q ai_agent_platform tests evals`、`.venv/bin/python INTERVIEW_NOTES/validate.py` 与 `git diff --check` → 通过；Interview Notes 校验为 `24 Markdown files / 46 capabilities`，仅输出既有 evidence-review 提醒。
- 最新问题 Run `run_bab9a480fda5` 持久化轨迹复核 → 新语义停滞门禁已生效，`native_no_progress_rounds` 按 `1 → 2 → 3` 累计并在 89 秒内以 `partial / no_progress` 收口；真正缺口是同一句“项目里的扫雷游戏没完成，继续做”被规则降级成 `repository_question / answer`，因此 `mutation_authorized=false` 且只暴露读取工具。
- `.venv/bin/python -m pytest -q tests/test_task_shaped_budgets.py tests/test_autonomous_unbounded_agent.py tests/test_agent_context_routing.py tests/test_agent_conversation_context.py tests/test_change_completion_contract.py tests/test_agent_loop_boundaries.py`（规则降级分类回归）→ `64 passed, 2 subtests passed`。
- 只执行 `_classify_request` 的安全回放 → `intent=change_planning`、`request_mode=change`、`mutation_authorized=true`、`task_shape=bounded_change`，冻结工具 Profile 包含 `sandbox.apply_patch`；未进入工具或工作区写入节点。
- `.venv/bin/python -m pytest -q` 首轮仅有异步画像重启用例 `test_local_memory_api_and_state_survive_restart` 时序失败，单独重跑通过；完整复跑 → `843 passed, 139 subtests passed`。
- `.venv/bin/python -m compileall -q ai_agent_platform tests evals`、`.venv/bin/python INTERVIEW_NOTES/validate.py` 与 `git diff --check` → 通过；未提交可能写入 direct workspace 的真实 Run。
- ego-browser 本地真实前端验收（fake Provider、内存存储、bounded、关闭 live workspace writes）→ 页面与新 `app.js?v=20260902-target-resolution-r1` 均 200；创建会话、注册/选择工作区、切换代码 Agent、提交 Run 和 SSE 正常；活动流显示 `resolve_change_targets` 为 `resolved · 4 个候选 · 1 个已解析`，随后在变更工具审批前暂停；取消 Run 后页面显示“Agent 已取消”，未批准任何写操作。
- `.venv/bin/python -m pytest -q tests/test_user_questions.py tests/test_query_service.py tests/test_agent_runtime_framework.py tests/test_agent_change_loop.py tests/test_autonomous_unbounded_agent.py tests/test_task_shaped_budgets.py tests/test_change_completion_contract.py tests/test_change_validation_gate.py tests/test_agent_loop_boundaries.py tests/test_agent_conversation_context.py tests/test_native_tool_calling.py tests/test_api.py tests/test_sandbox_tools.py tests/test_task_queue.py tests/test_celery_worker.py`（结构化追问、目标恢复与组合写入安全）→ `225 passed, 32 subtests passed`。
- `node --check ai_agent_platform/static/app.js && node --test tests/test_chat_message_ui.mjs` → `18 passed`，覆盖稳定问题 ID/候选提交、空答案阻断与显式 Skip 请求体。
- `.venv/bin/python -m pytest -q`（结构化追问与组合写入安全后全量）→ `852 passed, 142 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`、`.venv/bin/python INTERVIEW_NOTES/validate.py` 与 `git diff --check` → 通过；Interview Notes 校验为 `24 Markdown files / 46 capabilities`，仅输出共享工作树既有 evidence-review 提醒。

## Result

已完成默认自主行动分类、历史短指令目标恢复、live-evidence Completion Contract 门禁、无累计 Run 配额、checkpoint-safe 自动图切片，以及每模型 step 最多 10 个安全只读工具并发。新增端到端回归确认“修改”可从历史恢复 `app.py` 并在实时读取后冻结合同，不再返回 `completion_contract_unavailable`。

本次扩展修复进一步把目标词发现、实时候选排序、入口引用解析、用户歧义选择、服务端目标冻结和 Completion Contract 消费拆成独立可信边界；模型不能通过搜索词、system/profile/assistant 文本或候选集外路径扩大修改范围。扫雷式 HTML/CSS/JS 回归确认只提自然语言“扫雷”时会读取入口、识别缺失脚本并冻结 `create` 目标；同分入口会请求用户精确选择。README、Interview Notes、facts.json 与前端活动文案已同步，并完成真实浏览器 Run 验收。未执行 commit、push、merge、migration、deployment 或任何真实工作区写入。

本次语义停滞修复把“工具成功”与“任务进展”分离：不同查询、分页包装和重叠结果不再无限延长 unbounded Run；Artifact 仅在分页游标向前时计为进展。冻结完成合同未收缩的工具轮次现在会持续累计，连续两轮停滞后模型工具面收窄到写入、验证和人工输入，第三轮仍无进展即返回明确 partial，而不是继续读取直至 Provider 超时。未调整 Provider/模型超时配置，未执行 commit 或 push。

最新 Run 证明上述停滞修复已生效，但暴露了结构化分类失败后的规则降级缺口：带目标的“没完成，继续做”被当成只读问答，所以界面持续显示 `requested tools: repo.list_files`。现在规则 intent 与 mutation authority 共用同一高精度续做判断，明确续做进入 `change / bounded_change`，原因或选择问句仍保持只读。README、Interview Notes、facts.json 与正反回归已同步；未执行真实 direct workspace Run、commit 或 push。

本次继续按 DeepSeek Harness 源码把人工输入改为结构化协议：目标解析先让模型在实时已读候选中判断，只有同等候选才暂停；前端展示候选与自定义答案，空答案不提交，Skip 必须显式点击；后端逐题校验稳定 ID 并追加审计事件，恢复后的答案作为普通工具结果继续原模型循环。写入安全采用组合方案：冻结 Completion Contract 约束“允许改哪些文件”，可信 read-before-edit 与内容哈希约束“当前版本是否还能改”，底层审批、sandbox 基线与验证继续生效。全量 Python/Node/文档校验通过；未执行真实 direct workspace Run、commit 或 push。
