# AGENT-LOOP-DECOMPOSITION: 拆分 Agent Loop 大类

## Goal

在 Query Kernel 稳定后拆分 `CodingAgentRuntime`，降低 LangGraph 编排、节点实现、运行记录、
checkpoint 恢复和策略逻辑的耦合，同时保持现有可观察语义不变。

## In scope

- 先用 characterization/golden tests 固定只读问答、原生多轮工具调用、变更/审批/验证/
  一次修复、`waiting_input`、pause/steer/cancel、checkpoint resume、hard budget
  partial/blocked、ChangeSet 捕获和清理轨迹。
- 提取 graph builder。
- 提取 context/retrieval nodes。
- 提取 tool-loop nodes。
- 提取 completion/budget/control policies。
- 提取 run recorder/event sink。
- 提取 checkpoint/resume coordinator。
- 保留 `CodingAgentRuntime` 兼容 facade。
- 更新 README 与 Agent/LangGraph 访谈手册和证据映射。

## Out of scope

- 改变 LangGraph 节点名称、边、入口或终点。
- 改变预算阈值和 hard/soft budget 语义。
- 改变审批、工具调用、控制动作、checkpoint 或终态语义。
- 将 LangGraph `CodingAgentState` 暴露给 API、CLI、Service 或 Repository。
- 一次性重写整个运行时。

## Acceptance criteria

- [x] golden tests 覆盖约定的九类关键轨迹并通过。
- [x] graph builder 与节点、策略、记录、恢复职责位于独立模块。
- [x] `CodingAgentRuntime` 继续提供现有公开方法和结果模型。
- [x] API、Service、Worker、Repository 不依赖 `CodingAgentState`。
- [x] LangGraph 节点、边、预算、审批、工具调用和终态语义保持不变。
- [x] 每批提取后相关测试通过，最终完整 pytest 与 compileall 通过。
- [x] 文档和访谈手册同步，事实证据校验通过。

## Decisions

- 采用 facade + 内部协作者的渐进式提取；每个协作者只持有运行时依赖，不改变公共 DTO。
- golden 断言稳定的节点序列、状态、审批类型、工具调用/结果和清理副作用，过滤耗时、
  UUID、checkpoint ID 等非确定字段。
- 发现本地 `main` 落后且不含 Query Kernel 后，切换到以 `codex/query-kernel` 为基线的
  `codex/agent-loop-decomposition`；保留新基线的 per-run `ToolRegistryView`、
  `PermissionResolver`、审批人和执行点复判语义后再完成提取。
- `tool_access` 作为图内部的每 Run 工具选择/权限投影协作者，避免权限逻辑回流 facade。

## Verification

- `.venv/bin/python -m pytest -q`
- `.venv/bin/python -m compileall ai_agent_platform tests evals`
- `.venv/bin/python INTERVIEW_NOTES/validate.py`

## Result

已完成：

- `CodingAgentRuntime` 从约 3,200 行收敛为约 770 行兼容 facade；公共 `run`、`resume`、
  control、查询和 ChangeSet audit 回调保持不变。
- 新增 `graph_builder`、`context_nodes`、`tool_loop_nodes`、`policies`、`tool_access`、
  `run_recorder` 和 `checkpoint_coordinator`，并保留现有 `change_loop`。
- golden/characterization tests 固定只读、原生多轮、变更/审批/验证/一次修复、
  waiting_input、pause/steer/cancel、checkpoint resume、hard budget partial/blocked 和
  ChangeSet 捕获/清理；边界测试固定完整节点/边集合并禁止外层导入 `CodingAgentState`。
- Query Kernel、每 Run 工具快照、集中权限和审批复判语义均在新基线上通过回归。

验证结果：

- `.venv/bin/python -m pytest -q`：358 passed，47 subtests passed。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：12 个 Markdown、35 项 capability 通过。
- `git diff --check`：通过。
- 文档影响：README 和 Part 04 已同步职责边界，`facts.json` 已补充新源码与 golden 证据。
