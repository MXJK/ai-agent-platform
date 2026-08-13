# EFFECTIVE-TOOL-POOL: 每 Run 的 Effective Tool Pool

## Goal

把进程级工具注册与单次 Run 可见/可执行的有效工具集合分离，由确定性、不可变、可恢复的
工具目录快照保证模型、审批与执行使用同一集合。

## In scope

- 保留 `ToolRegistry` 的 Schema 校验、超时、重试和幂等执行能力。
- 新增 `ToolCatalog`、`ToolPoolBuilder` 和不可变 `EffectiveToolPool`。
- 综合 base/local、MCP、Skill 声明、Agent 类型/模式、模型能力、`ToolUseContext`、deny
  rules 与 Sandbox 能力建立每 Run 工具池。
- 实现工具命名空间、稳定排序、重复检测和显式冲突错误，禁止静默覆盖。
- 模型、规划、审批和执行仅使用本 Run 的有效工具池。
- Run 快照保存目录版本、脱敏规范化摘要和 catalog hash；恢复时复用原始快照，工具缺失
  或定义漂移时明确失败。
- 增加 Agent/模式/角色、MCP 冲突、Skill 缺失依赖、deny 过滤和目录快照测试。
- 同步 README 与访谈手册。

## Out of scope

- 执行数据库迁移、部署、提交、推送、PR 或合并。
- 让 Skill 或 MCP 元数据绕过中央权限、RBAC、Workspace 或 Sandbox 边界。
- 改变 `ToolRegistry` 的可靠执行语义。

## Acceptance criteria

- [x] `ToolRegistry` 继续负责注册校验和可靠执行，目录/本 Run 选择由独立类型负责。
- [x] 工具来源有显式命名空间和稳定排序；重复名或规范化冲突均 fail closed。
- [x] `ToolPoolBuilder` 同时应用 Agent/模式/角色、模型、Skill、deny 与 Sandbox 条件。
- [x] `EffectiveToolPool` 深度不可变；模型只能列出其有效规范，越池调用被拒绝。
- [x] Run 快照不含密钥或完整敏感参数，并记录可校验的目录版本、规范化摘要和 hash。
- [x] 新 Worker/恢复 Run 使用原快照；工具缺失、定义改变或快照被篡改时明确失败。
- [x] 专项测试和仓库要求的全量验证通过，文档与 facts 同步。

## Decisions

- `ToolRegistry` 不再承担 Run 选择职责：它继续持有 callable、Draft 2020-12 Schema
  注册校验、权限执行复判、超时、重试、幂等缓存和上下文生命周期回调。
- `ToolCatalog` 采用 `tool-catalog/v1` 契约。目录项区分 base/local/MCP 来源，MCP 保留
  `mcp.<server>.<tool>` 命名空间；名称按 namespace/name/source/provider 稳定排序，按
  casefold 后的完整名检测冲突，重复或保留命名空间冒用直接失败。
- 目录摘要不保存 description、Schema 正文、凭据或完整敏感参数，只保存名称、来源、
  provider、权限、可用性约束，以及输入/输出 Schema 和完整定义的 SHA-256。完整目录
  summary/hash 和有效子集 summary/hash 均随 Run 持久化并互相校验。
- `ToolPoolBuilder` 先应用项目请求子集，再求交 Agent 类型、运行模式、模型能力、
  `ToolUseContext`/中央 display deny、显式 deny pattern、Workspace role 与 Sandbox 能力。
  Skill 的 `required_tools` 只做依赖检查，不授予工具；缺失依赖默认形成确定性诊断并使
  Skill 不注入，严格调用方也可选择直接失败。
- `EffectiveToolPool` 深度复制 ToolSpec 并冻结自身；模型、规则规划、变更循环、审批和
  执行均从同一个池取规范。越池调用返回明确 deny，池内调用仍委托原 Registry 的可靠
  执行与 TOCTOU 权限复判。
- `RunContextSnapshot` 升级为 schema v3；v1/v2 仍可加载。v3 恢复按原有效摘要逐项匹配
  当前执行器：全局新增无关工具不改变旧 Run，原项缺失、Schema/权限/超时等定义漂移、
  摘要/hash/版本篡改或目录契约不支持均明确失败。
- 快照仍存放在既有 `run_context_snapshot` JSONB 中，不新增数据库列或迁移。MCP 远端在
  相同本地契约下改变内部语义仍需要未来的外部版本/签名能力，本任务不虚构该保证。

## Verification

- `.venv/bin/python -m pytest -q`：348 passed、47 subtests passed。
- `.venv/bin/python -m pytest -q tests/test_effective_tool_pool.py
  tests/test_execution_context.py tests/test_mcp_provider.py tests/test_task_queue.py`：39 passed。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- Python 3.10 grammar `ast.parse`：159 个 Python 文件通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：12 个 Markdown、34 项 capability 通过；
  evidence review 仅报告事实基线后的预期工作树变化。
- `git diff --check`：通过。

## Result

- 新增独立 `ToolCatalog`、`ToolPoolBuilder` 和 `EffectiveToolPool`，完成命名空间、稳定
  排序、冲突检测，以及 Agent/模式/模型/Skill/角色/deny/Sandbox 的每 Run 求交。
- Agent 模型可见规范、规则规划、审批和执行均绑定同一有效池；`ToolRegistry` 的 Schema、
  超时、重试和幂等能力保持不变。
- 新 Run 保存脱敏、可校验的目录/有效池快照；Worker 重启和 resume 使用原始项，无关
  新工具不会渗入，MCP 工具消失或同名定义变化会 fail closed。
- 新增专项覆盖 Agent/模式/角色、模型/Sandbox、MCP 冲突、Skill 缺依赖、deny、越池调用、
  脱敏和快照恢复；同步 README、访谈手册 Part 04/05/08、入口与 facts。
- 无数据库 schema 变化；实现验证阶段未执行迁移或部署；提交和推送由后续用户指令单独
  授权，未执行 PR 或 merge。
