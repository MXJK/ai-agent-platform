# CLI-PERMISSION-MODEL-CONTROLS: 完成终端权限与模型控制

## Goal

让公共 `cogent` CLI/TUI 可以直接切换四种权限模式，并通过共享 HTTP API 注册模型、
切换当前会话模型或恢复自动路由，不再要求用户返回 Web 页面完成这些操作。

## In scope

- `/permissions [default|acceptEdits|plan|bypassPermissions]` 在 CLI/TUI 本地即时生效。
- `/models` 显示当前选择、已注册模型及可执行子命令。
- `/models register <provider> <model>` 通过现有模型注册 API 注册启用模型。
- `/models use <model-id|provider/model>` 将当前会话切换为指定模型。
- `/models auto [smart|quality|cost|latency]` 恢复自动路由。
- CLI 状态栏、REPL 输出、slash completion、README 和技术事实文档同步。
- 使用 MockTransport、FastAPI/fake runtime 和真实终端进程验证管理命令与后续 Run。

## Out of scope

- 不在终端接收或保存 Provider API Key；Provider 连接仍使用现有安全配置入口。
- 不删除、禁用或测速模型。
- 不迁移、部署、提交或推送。
- 不修改权限硬拒绝、Workspace/RBAC、工具范围或模型路由算法。

## Acceptance criteria

- [x] `/permissions` 查看模式，带参数时无需创建 Run 即时切换；下一次 Run 使用新模式。
- [x] `/models register` 可注册已有 Provider 连接下的模型并刷新列表。
- [x] `/models use` 支持稳定模型 ID、`provider/model` 和唯一模型名，持久化到当前会话。
- [x] `/models auto` 可选择路由策略并恢复自动路由。
- [x] TUI 状态栏与标题在模型切换后即时刷新，命令错误不启动 Agent Run。
- [x] README、技术参考、Interview Notes 与事实映射同步。
- [x] 聚焦测试、CLI 实际运行、compileall 和完整 pytest 通过，或精确记录任务外 blocker。

## Decisions

- 权限模式是下一次 Run 的客户端输入，因此 CLI 本地切换即可；服务器仍会在 Run 创建时
  冻结和执行相同权限边界。
- 模型注册只接收 Provider 和模型 ID，模型能力与 token 配额继续由后端 profile 管理。
- 手动切换关闭 fallback，保证用户选择的模型就是下一次 Run 的首选且不静默换模；
  自动路由恢复时启用 fallback。
- Provider 密钥不进入命令行参数、REPL 历史或 CLI 输出。
- TUI 的模式循环、颜色和显示名称直接采用用户提供的 mewcode `app.py` 实现：
  `default -> acceptEdits -> plan -> bypassPermissions`，界面显示为 `default`、
  `accept-edits`、`plan`、`YOLO`；只把 mewcode 的本地 Agent setter 适配为 Cogent
  客户端下一次 Run 的 setter。

## Verification

- `.venv/bin/python -m pytest -q tests/test_cogent_http_client.py tests/test_cogent_tui.py
  tests/test_shell_adapters.py tests/test_model_registry.py`
  - `52 passed, 13 subtests passed`。
- `.venv/bin/python -m pytest -q`
  - `822 passed, 12 skipped, 1 failed, 108 subtests passed`。
  - 唯一失败仍为任务开始前已记录的
    `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`：
    测试期望 `OSError`，当前 `ManagedFiles.read()` 对 ELOOP/ENOTDIR 统一抛出 `ValueError`；
    本任务未修改这些文件。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：校验 24 个 Markdown 与 44 个 capability
  通过；仅有既有 evidence review warnings。
- `git diff --check`：通过。
- 状态栏可见性回归验证：
  - `.venv/bin/python -m pytest -q tests/test_cogent_tui.py`：`3 passed`。
  - Textual 80x25 布局中，状态栏为 `Region(x=1, y=23, width=78, height=2)`，
    模式标签为 `Region(x=32, y=24, width=9, height=1)`，完整位于屏幕内并显示 `default`。
- 真实 CLI/HTTP/SSE 验收使用 `127.0.0.1:8765` 隔离内存服务和 fake Provider：
  - REPL 中 `/permissions acceptEdits` 即时显示新模式，未产生管理 Run。
  - `/models use fake/demo-stream-model` 持久化当前会话；Run
    `run_e297589dcea2` 以该精确模型、explicit selection、`fallback_enabled=false`
    完成并输出 `run_completed`。
  - 仅在隔离内存注册中心建立 dummy Provider 连接后，CLI 成功注册并选择
    `openai/cli-smoke-model`，随后 `/models auto latency` 恢复自动延迟路由。
  - Textual TUI 实际启动并显示服务端 Workspace、`auto/smart · 2 models`、凭据和模式状态；
    `run_test` 覆盖管理命令、mewcode 同款四模式标签、Shift+Tab 完整循环和子命令补全。
  - 本次补充真实 PTY 复验时，本地 fake 服务启动并通过 `/api/v1/health`，但隔离内存
    Workspace 注册所需的后续本机命令被 Codex 用量限制拒绝，未将这次中断误记为功能通过；
    可复现的 Textual 虚拟终端验收已覆盖同一按键绑定与渲染路径。
  - 未调用真实 Provider；隔离服务已关闭，数据未持久化。

## Result

已完成公共 CLI/TUI 权限与模型控制闭环。`/permissions` 现在是客户端即时配置，不再依赖
服务器先完成一次 slash-command Run；后续 Run 仍由服务器冻结并执行相同权限硬边界。
`/models` 会显示当前路由和选中标记，并提供模型注册、精确会话选模、自动路由恢复；注册后
会刷新目录，切换后会同步标题、状态栏和当前会话。错误命令留在 REPL/TUI 中显示，不导致
进程退出或误启动 Agent Run。

TUI 权限状态已改为直接适配 mewcode 的 `_MODE_CYCLE`、`_MODE_COLORS`、
`action_cycle_mode()` 与 `_update_mode_label()`：启动即显示当前模式，Shift+Tab 依次切换
`default -> accept-edits -> plan -> YOLO -> default`，非默认模式同时显示切换提示。
同时补齐了 mewcode 状态栏的 `height: auto; min-height: 1` 布局；原 `height: 1` 被顶部
边框占满，会让标签落到终端可视区下一行，因此此前逻辑测试通过但肉眼不可见。

README、双语技术参考、忽略提交的本地 Interview Notes 与 `facts.json` 已同步。没有接收或
输出 Provider 明文密钥，没有数据库迁移、部署、提交或推送。功能范围和验收均完成，但仓库
必跑 pytest 仍被任务外既有 symlink 异常类型契约阻塞，因此工作流状态保持 blocked。
