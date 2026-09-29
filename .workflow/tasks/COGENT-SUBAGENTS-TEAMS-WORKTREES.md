# COGENT-SUBAGENTS-TEAMS-WORKTREES

## Goal and scope

参考用户提供的本地 `mewcode-python`，为 Cogent 补全 Subagent、Agent Teams
和 Worktree 能力。优先复用参考实现的数据模型、Agent 定义格式、工具语义、邮箱、
共享任务与 Git worktree 生命周期；边界处适配当前项目的持久化 Run、冻结工具池、
Workspace/RBAC、权限模式、ChangeSet 和服务端进程模型。

不引入 tmux/iTerm2 外部窗格进程，不绕过现有审批、危险命令、保护路径、工具池或
Workspace 授权，不提交、推送、合并、部署或迁移。

## Acceptance criteria

- 支持内置、项目级 `.cogent/agents/*.md` 与用户级 `~/.cogent/agents/*.md`
  Agent 定义；校验工具、模型、权限模式、轮次上限、后台与 worktree 隔离字段。
- Cogent 暴露 Subagent 工具，支持指定 Agent、对话 fork、前台结果、后台任务、
  状态查询/停止和完成通知；禁止无界嵌套，并继承父 Run 的工具、权限与 Workspace
  硬边界。
- Agent Teams 支持创建/删除、隔离 worktree 队友、点对点/广播邮箱、共享任务板、
  停止与完成通知；服务端使用进程内执行，不伪装为 tmux/iTerm2。
- Worktree 支持安全 slug、命名创建/恢复、状态/变更检查、干净自动清理和有改动保留；
  当前 Run 的冻结 execution root 不在中途切换，Worktree 用于隔离 Subagent/队友。
- 子 Run、团队和 Worktree 状态进入可审计事件/结果；内部子 Run 不污染普通最近 Run
  列表或重复生成父 Run ChangeSet。
- README、英文 README、Interview Notes、事实索引和来源说明与实际能力边界同步。
- 运行聚焦测试、全量 pytest、compileall、Interview Notes 校验和 diff 检查；若全量
  测试仍只有既存 symlink 异常契约失败，明确分离记录，不把它算成本任务回归。

## Decisions

- 沿用当前同步 `CogentRuntime` 和持久化 AgentRun 作为子 Agent 执行单元；团队队友由
  服务端进程内后台执行器承载，完成/失败通过父 Run 通知注入，而不是外部终端窗格。
- 普通前台 Subagent 可借用父 Run 已冻结的 execution workspace；后台、团队和显式
  isolation=worktree 使用独立 Git worktree，避免并发写入同一根目录。
- 参考源码快照未提供 LICENSE；保留来源/适配说明，不把来源品牌或注释复制进产品代码。
- 子 Run 保留内部合成 conversation ID 作为隔离身份，但模型用量和预算统一归属真实的
  父 Session，并用子 Run ID 作为 usage resource，避免 PostgreSQL 用量预检把内部身份
  当作真实 Session 查询。
- 前台 Subagent 的 `failed`、`blocked`、`cancelled` 状态必须转为失败 ToolResult，不能
  只把失败详情包进 `ok: true` 的工具结果；后台排队语义保持不变。
- AgentRun 终态核心字段继续不可变；协作完成通知的 `finalized` 标记通过专用
  runtime-state 元数据更新接口持久化，保证恢复时不会重复通知。
- 空邮箱轮询保持只读；记忆名称允许模型冗余返回匹配的作用域前缀或 `.md` 后缀，规范化
  后仍拒绝跨作用域路径和目录穿越。

## Verification

- `.venv/bin/python -m pytest -q tests/test_cogent_collaboration.py tests/test_cogent_memory.py tests/test_cogent_persistence.py tests/test_postgres_repositories.py tests/test_sandbox_tools.py tests/test_query_service.py tests/test_runtime_bootstrap.py tests/test_cogent_runtime.py`
  - 通过：`155 passed, 1 warning`。
- `.venv/bin/python -m pytest -q tests/test_cogent_collaboration.py tests/test_cogent_memory.py tests/test_cogent_persistence.py tests/test_postgres_repositories.py tests/test_api.py::ApiTests::test_agent_does_not_automatically_route_to_knowledge_bases tests/test_cogent_api.py::test_default_api_uses_cogent_and_fast_chat_is_removed`
  - 通过：`59 passed, 1 warning`；覆盖空邮箱轮询和 API 回归。
- `.venv/bin/python -m pytest -q tests/test_cogent_hooks.py::test_http_action_sends_json_and_rejects_redirect`
  - 在受限沙箱外复跑通过：`1 passed`；沙箱内本地 TCP bind 的 `PermissionError` 不是产品失败。
- `.venv/bin/python -m pytest -q`
  - 在受限沙箱外完整执行：`880 passed, 12 skipped, 1 warning, 108 subtests passed`。
  - 保留 1 个任务开始前已存在的失败：
    `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`
    期望 `OSError`，当前 `ManagedFiles.read` 对符号链接父目录抛出 `ValueError`；
    本任务未修改该合同或对应实现。
- `.venv/bin/python -m compileall -q ai_agent_platform tests evals`
  - 通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`
  - 通过：`Validated 24 Markdown files and 46 capabilities`；证据审查提示保留，
    其中 `cogent_collaboration` 已映射本任务源码与测试。
- `git diff --check`
  - 通过。

## Result

完成。按 mewcode 的主要数据模型与工具语义补入 Agent 定义加载、持久子 Run、fork、
前台/后台任务、任务查询/停止/通知、进程内 Agent Teams、结构化邮箱、共享任务板、
plan approval 恢复，以及命名 Git worktree 的创建、重启接管、变更检测与安全清理。

平台边界适配为：子任务继续使用冻结 EffectiveToolPool 和父 Run 的 Workspace/RBAC/
权限硬限制；前台子任务共享父 execution workspace，后台和团队任务使用独立 worktree；
内部子 Run 可恢复、可审计但不进入普通最近 Run 列表，也不重复生成 ChangeSet、会话消息
或文件记忆。普通和团队完成通知均落盘，团队/子任务重启后可从持久 Run 和 worktree
元数据恢复；干净 worktree 自动删除，有提交或未提交改动则保留并回报。

针对真实 PostgreSQL 运行记录复核时发现并修复了生产路径缺陷：子 Run 的用量预检不再
查询不存在的合成 Session；前台子任务失败现在会使 Agent 工具失败；终态 Run 可以安全
补写 `finalized` 元数据；空邮箱轮询不再无意义创建锁文件；模型返回
`project/<name>.md` 形式的记忆名会在保持路径安全的前提下规范化。新增回归覆盖真实
UsageLedger/Session 集成、失败传播、终态元数据、SQLite/PostgreSQL 持久化、记忆名规范化
和空邮箱只读行为。

README 中英文、Interview Notes、事实索引和三处来源说明已同步。没有引入 tmux/iTerm2、
没有复制 `.env` 或依赖目录、没有自动合并分支，也没有提交、推送、部署或迁移。

功能验收已完成；按仓库 closeout 合同，工作流状态仍标为 `blocked`，唯一 blocker 是上面
单独记录的既存全量测试失败，并非本任务实现缺口。
