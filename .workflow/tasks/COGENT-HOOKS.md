# COGENT-HOOKS

## Goal and scope

参考本地 mewcode-python 的 Hook 实现，复用条件语法和数据模型，为平台 CogentRuntime 接入可配置生命周期 Hook。仅修改 Hook、配置、运行时接入、测试与文档。用户已于 2026-09-27 明确授权提交并推送；不部署或迁移。此前 TRACE-AUDIT-PERFORMANCE 的完整测试阻塞记录保留在原任务。

## Acceptance criteria

- 服务端显式配置并校验 Hook；默认关闭，Workspace 配置不能启用宿主命令。
- 支持条件、once、prompt/command/http、pre_tool_use 拒绝及实际运行时生命周期事件；未实现的动作/事件明确拒绝。
- Run 独立状态、持久化执行账本，审批/恢复不重复执行已记录 Hook；不确定外部副作用不自动重放。
- Hook 不破坏工具结果配对或放宽现有工具权限；输出进入 Run Trace。
- 覆盖配置、动作、拒绝、恢复、隔离和提示词注入，运行全量 pytest、compileall 和手册校验。

## Decisions

- 复用 mewcode 条件匹配器；保留来源说明。引擎适配同步持久化 Run，全部动作同步、有超时；拒绝 async=true 与 agent 占位实现，不引入游离后台任务。
- session_start/session_end 对应一次 Run，不是数据库 Conversation 的生灭。服务端配置变更后旧 Run 不继续执行 Hook，防止恢复期间策略漂移。
- Hook 命令是管理员显式配置的宿主扩展；使用环境变量/JSON stdin 传入上下文，不把模型参数直接拼接进 shell 源码。

## Verification

- Final `.venv/bin/python -m pytest -q`: **865 passed, 12 skipped, 108 subtests passed, 1 failed** (44.25s). The only failure is the pre-existing `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`: expected OSError, actual ManagedFiles ValueError. Both the implementation and that test are unchanged by this task; the same blocker was already recorded in TRACE-AUDIT-PERFORMANCE at task start.
- All **36 new Hook tests passed** in the final full run. Coverage includes strict configuration and process-only fields, safe command argument transport, command timeout/nonzero, real loopback HTTP/redirect rejection, API /compact dispatch, lifecycle ordering, prompt consumption, SQLite approval restart, concurrent Run isolation, sibling rejection/approval pairing, inherited permission checks, uncertain execution, config drift, notification recovery, user input, and slash command recovery.
- `.venv/bin/python -m compileall ai_agent_platform tests evals`: passed.
- `.venv/bin/python INTERVIEW_NOTES/validate.py`: passed, 24 Markdown files / 45 capabilities; existing evidence-review warnings remain and do not establish verification of an uncommitted HEAD.
- `git diff --check`: passed. No source changes outside the Hook integration/configuration scope. The implementation verification did not deploy, migrate, deliver an external webhook, or enable production Hook configuration. Commit and push were subsequently authorized by the user.
- The HTTP integration test used a temporary loopback server with approved sandbox escalation. Provider calls used local scripted/fake fixtures; no live model was required.
- Logs were captured at `/tmp/cogent-hooks-full-pytest-final.log`, `/tmp/cogent-hooks-compile-final.log`, and `/tmp/cogent-hooks-handbook-final.log`.

## Authorized delivery verification (2026-09-27)

- Repeated full pytest before commit: 865 passed, 12 skipped, 108 subtests passed, the same one pre-existing symlink exception mismatch (46.37s). All 36 Hook tests still pass.
- compileall, handbook validation (24 Markdown / 45 capabilities), and diff checks passed again.
- Local HEAD and remote main both resolved to f3b1c4ca101f844cc0b64e5459944bdd44ae3299 before staging; no unrelated staged or pending changes were found.
- Verification logs: /tmp/cogent-hooks-precommit-pytest.log, /tmp/cogent-hooks-precommit-compile.log, /tmp/cogent-hooks-precommit-handbook.log.

## Result

Hook implementation and feature acceptance are complete. Required full-suite closure remains blocked only by the previously recorded symlink exception-contract regression; that unrelated boundary was preserved. Default remains disabled; enable with the server-owned COGENT_HOOKS_PATH described in docs/hooks.md.

README.md, .env.example, docs/hooks.md, INTERVIEW_NOTES.md, Part 04 and facts.json were synchronized. The interview handbook remains ignored by the repository's pre-existing .gitignore and is present locally. Reused source attribution and the supplied snapshot's missing license are recorded in hooks/SOURCE.md. Async background Hooks and the reference's stub agent executor are explicitly rejected rather than reported as successful.

Applied codex-close-task. User-authorized delivery targets origin/main and includes only this Hook task. Ignored interview handbook files remain local. The required full-suite blocker remains recorded, so last_verified_commit is not advanced. Commit identity and remote verification are reported in the delivery response.
