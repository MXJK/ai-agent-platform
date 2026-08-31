# AGENT-EVIDENCE-EXECUTOR: 批量只读采证与原始结果外置

## Goal

让 Agent 用一个严格、可验证的 `EvidencePlan` 批量完成确定性的仓库列举、搜索、读取、过滤、去重与聚合；内部原始结果保留在 Run Artifact 与审计事件中，只向模型转录返回一个有硬上限的 `EvidenceBundle`，降低原生工具循环的历史重放放大。

## In scope

- 定义严格、带安全默认值的 `EvidencePlan` 与 `EvidenceBundle` 契约。
- 增加内部 `repo.collect_evidence` 只读编排层，只能调用现有 `repo.list_files`、`repo.find_files`、`repo.search_code`、`repo.read_file`。
- 对独立读取受控并发；规范化并去重路径、查询、参数与重复文件内容。
- 对递归深度、文件数、单文件字符数、单查询结果数和模型可见 Token 设置硬上限，并跳过仓库忽略目录与构建产物。
- 将每个内部原始结果写入现有 Run Artifact，保留参数、状态、截断与 `artifact_id`，但不逐条进入 native model transcript。
- 保留真实内部工具调用的事件流、运行详情、计量和 durable replay 语义；单个子调用失败只形成结构化错误。
- 补充聚焦、回放、权限与普通工具/Artifact readback 回归测试，并同步受影响文档事实。

## Out of scope

- `task_shape`、任务分级预算或全局工具轮数调整。
- 里程碑压缩、Prompt Cache 或 UI Token 展示调整。
- 任意模型生成代码的通用执行器，或允许 write、shell、网络及其他副作用工具进入 Evidence Executor。
- 历史 Run 数据回填、部署、迁移、提交或推送。

## Acceptance criteria

- [x] 一个 `EvidencePlan` 能执行多个只读子调用，并对缺失或非法字段使用可验证的安全默认值/边界。
- [x] 重复路径、重复查询与相同内容被去重；忽略目录、深度、文件数和输出预算均受硬限制。
- [x] 每个内部原始结果进入 Run Artifact，记录 `artifact_id`、工具、参数、成功状态与截断信息，且可由 `run.read_artifact` 恢复。
- [x] 内部原始子调用结果不作为逐条 `role=tool` 消息进入 native transcript；模型只收到一个严格有界且显式标记截断的 `EvidenceBundle`。
- [x] 运行详情和事件流仍展示实际内部调用；工具指标统计实际内部执行次数，而不是只统计外层编排调用。
- [x] 子调用部分失败时成功证据仍保留，错误以结构化、无原始大正文的形式返回。
- [x] Executor 对副作用工具采用闭集拒绝，不能执行 write、shell 或任意未授权工具。
- [x] checkpoint/replay 按稳定子调用身份复用成功结果，不重复执行；调用身份冲突可审计地失败。
- [x] 现有普通工具调用、并发只读批次与 Run Artifact readback 不回归。
- [x] 聚焦测试、完整 pytest、compileall 与文档事实校验通过。

## Decisions

- Evidence Executor 是仓库只读能力的闭集编排器，不接受任意工具名或可执行代码。
- 外层 `repo.collect_evidence` 是模型协议边界；指标中的实际工具数以内部仓库调用结果为准，外层编排不冒充一次仓库读取。
- 每个内部调用使用由外层 call id、规范化工具名与参数派生的稳定身份，复用现有 durable tool execution store。
- 原始 Artifact 始终创建；模型可见 Bundle 只保留定位、短证据、原因与 Artifact 引用，并在预算收缩时显式设置 `truncated`/`unresolved`。
- 第一阶段不改变普通工具的 transcript、全局轮数、任务分类、缓存或 UI Token 语义。

## Verification

- `.venv/bin/python -m pytest -q tests/test_evidence_executor.py`
  - `6 passed`。
- `.venv/bin/python -m pytest -q tests/test_evidence_executor.py tests/test_native_tool_calling.py tests/test_run_artifact_read.py tests/test_agent_runtime_framework.py tests/test_effective_tool_pool.py`
  - `93 passed, 28 subtests passed`。
- `.venv/bin/python -m pytest -q`
  - `707 passed, 121 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`
  - 通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`
  - `Validated 24 Markdown files and 44 capabilities`；其余 evidence review warning 来自共享工作区已有变更，不是校验失败。
- `git diff --check`
  - 通过。
- 当前工作树代表性采证测量：2 个查询、6 个候选路径、`max_files=8` 时实际执行并外置 13 个子结果；原始 canonical ToolResult 合计估算 `16,132` Token，模型可见单 Bundle 估算 `3,119` Token，减少 `13,013` Token（`80.67%`），模型 `role=tool` 消息从 13 条原始观察收敛为 1 条 Bundle；指标仍记录 13 个实际工具结果。

## Result

- 新增严格 `EvidencePlan`/`EvidenceBundle` 模型和 `repo.collect_evidence` runtime ToolSpec；标准 native 跨文件探索不再向模型广告四个底层 repo 工具，旧 checkpoint 与普通直接调用仍兼容。
- Evidence Executor 以最多 6 个并发子调用的受控批次执行 list/find/search/read，查询、参数、规范化路径与内容哈希去重；`max_depth` 已下沉到 list/find、ripgrep 与 Python fallback 的真实遍历点，忽略目录在两种搜索引擎中一致生效。
- 每个内部成功/失败结果都立即建立可校验 `tool_result` Artifact，携带工具、参数、状态与截断元数据；native transcript 只追加一个有 Token 硬上限的 Bundle，部分失败、unresolved 与截断均显式可见，原始正文可凭 `artifact_id` 分页恢复。
- 内部调用继续复用现有 Permission/ToolRegistry、事件、运行详情和 durable execution store；稳定子调用 ID 防止 checkpoint 重放重复执行。终态 metrics 从内部 ToolResult 计算真实执行数，外层编排不虚增。
- README、Interview Notes 的 Agent 编排/工具安全章节与 `facts.json` 已同步；这是模型协议、数据流、审计和能力边界变更，具有文档影响。
- 未实现 task shape、任务分级预算、里程碑压缩、Prompt Cache、全局工具轮数调整或 UI Token 调整。
- 共享工作区原有 Provider/模型注册/UI 变更和 `.impeccable/` 保持原样，未纳入本任务；未提交、未推送、未部署或重放历史 Run。
