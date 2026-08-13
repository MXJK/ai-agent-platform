# UNIFIED-PERMISSION-CONTEXT: 统一权限解析与工具使用上下文

## Goal

在现有 ToolSpec、requires_approval、Workspace RBAC、Sandbox 和
AGENT_APPROVAL_POLICY 之上建立唯一的 PermissionResolver 与 ToolUseContext，统一工具展示、
规划、人工审批和执行前授权，同时保留 Workspace、Sandbox 与 ChangeSet 执行点的纵深校验。

## In scope

- PermissionResolver 返回 allow、ask 或 deny，并记录匹配规则、原因与风险摘要。
- 进程工具上限、Workspace 根边界和身份 RBAC 为不可覆盖硬边界；显式 deny 优先于 allow，
  项目工具选择和审批策略只能收紧进程能力。
- `approval_policy=never` 将需要 ask 的调用转换为 deny；`always` 对所有工具请求审批。
- 用户批准绑定 run_id、call_id、工具名和规范化参数哈希；跨 Run/调用重放及参数变化不能
  沿用原批准。
- ToolRegistry/Run view 在展示阶段过滤 deny 工具，但每次执行仍重新解析权限以防 TOCTOU。
- MCP/Skill 权限注解标为不可信 advisory 输入，最终效果由 PermissionResolver 决定。
- AgentRunService、CodingAgentRuntime、ToolRegistry 和 ChangeSetService 接入统一上下文，
  Sandbox、Workspace catalog、RBAC 和 ChangeSet patch digest 校验继续保留。

## Out of scope

- 新增远程策略服务、OPA/Rego、数据库权限表或跨进程审批令牌签名。
- 修改 Workspace 成员模型、Sandbox 隔离实现或 MCP/Skill 生命周期。
- 执行数据库迁移、部署、提交、推送、创建 PR 或合并。

## Acceptance criteria

- [x] 权限矩阵覆盖 read/write、viewer/editor、on_request/always/never 与进程/项目拒绝。
- [x] 进程 deny、Workspace 边界和 RBAC 不能被项目选择或用户批准放宽。
- [x] 显式 deny 优先于 allow，项目配置只会收紧权限。
- [x] `never` 对 ask 调用返回 deny，而不是执行授权。
- [x] 审批载荷和执行授权绑定 run_id、call_id、工具名与参数哈希；参数篡改重新 ask。
- [x] 审批不能跨 Run、call_id 或工具重放；同一已完成调用仍由既有幂等机制安全复用。
- [x] 展示阶段可过滤 deny 工具，执行阶段无条件重新授权并覆盖 TOCTOU 回归。
- [x] MCP/Skill 自报注解不能单独产生 allow。
- [x] AgentRunService、CodingAgentRuntime、ToolRegistry、ChangeSetService 和 Sandbox 执行点
      使用统一 ToolUseContext，并保留既有执行点校验。
- [x] README、访谈手册 Part 与 facts 同步，过时的分散授权描述被修正。
- [x] `.venv/bin/python -m pytest -q`、`.venv/bin/python -m compileall ai_agent_platform tests
      evals`、`.venv/bin/python INTERVIEW_NOTES/validate.py`、`git diff --check` 全部通过。

## Decisions

- PermissionResolver 使用确定性、fail-closed 的规则顺序；硬边界先于项目选择和批准。
- ToolUseContext 是每次调用的不可变安全上下文，审批 grant 也是不可变、精确绑定的值对象。
- 外部 provider 注解只能提高风险/触发 ask，不能降低中央策略要求。
- 工具执行前的权限 ask 以结构化失败返回，不缓存为已执行结果；补充精确审批后可安全重试。
- 生产 `RuntimeContainer` 创建一个共享 Resolver，并注入 ToolRegistry、AgentRunService 和
  ChangeSetService；没有 Resolver 的直接单元 Registry 保留旧 ToolSpec 行为以兼容纯测试。
- `ToolApproval` 同时绑定批准人和 `run_id/call_id/tool_name/arguments_hash`。AgentRunService
  在 API 入队和 Worker 恢复各复核一次，LangGraph 恢复时生成 grant，Registry 执行前再判定。
- `always` 对只读探索也 interrupt；`never` 过滤/拒绝所有需要 ask 的调用。项目审批策略
  使用偏序而非数值强弱：进程 `always` 或 `never` 只能保持原值，`on_request` 可收紧为两者。
- 验证失败后的修复会为下一次验证生成新 call ID，并与修复调用一起重新审批，避免沿用
  首次验证的 grant 或耐久结果。

## Verification

- `.venv/bin/python -m pytest -q`：317 passed、47 subtests passed；仅有既有 Starlette
  `httpx` 弃用警告。
- `.venv/bin/python -m pytest -q tests/test_permissions.py tests/test_task_queue.py
  tests/test_mcp_provider.py tests/test_agent_change_loop.py tests/test_native_tool_calling.py`：
  37 passed、8 subtests passed。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 12 个 Markdown 文件和 33 项
  capability；evidence review 仅提示预期的工作树变化。
- `git diff --check`：通过。
- 安全审阅：未发现凭据值、迁移、部署或生成物；`20260810_0020` 没有执行。

## Result

- 新增集中、确定性、fail-closed 的 `PermissionResolver`、`ToolUseContext`、
  `PermissionDecision` 和 `ToolApproval`。判定带 effect、matched rule、reason、risk summary，
  硬边界按进程 deny/能力上限、Workspace root、身份 RBAC、项目收紧、审批策略和精确 grant
  顺序执行。
- ToolRegistry 展示可过滤 deny，执行前仍重新解析；权限复判发生在内存/耐久幂等命中之前，
  参数篡改不会拿旧结果绕过。Run-scoped view 对越出项目选择的调用返回结构化 deny，启用
  Resolver 后禁用绕过上下文的直接 `call()`。
- CodingAgentRuntime 的仓库探索、原生/规则工具规划、变更、验证、修复和 artifact 路径都
  使用统一上下文。审批载荷公开 run/call/tool/参数哈希，恢复生成的 grant 精确绑定批准人；
  `AgentRunService` 在 API 与 Worker 两边复核参数和最新 Workspace/RBAC。
- ChangeSet 查看、拒绝和应用进入相同 Resolver，并保留 editor 回调、live-write/process
  开关、patch digest、登记 root/revision、路径、symlink 和文件基线等原纵深校验。
- MCP 权限来源标为 `mcp_annotation`，自报只读也不会自行授权；中央策略当前要求先 ask。
  Skill 注解使用同一 advisory source 语义，未来接入 Skill Registry 时无需新增授权旁路。
- README、忽略 Git 的本地访谈手册 Part 04/05、主索引和 facts 已同步。没有执行迁移、
  部署、提交、推送、创建 PR 或合并。
