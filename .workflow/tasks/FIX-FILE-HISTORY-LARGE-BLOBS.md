# FIX-FILE-HISTORY-LARGE-BLOBS: 修复大文件历史快照失败

## Goal

修复 Cogent 在命令执行后保存文件历史时，因工作区包含版本化虚拟环境目录和超过
8 MB 的 blob 而导致整个 Agent Run 失败的问题。

## In scope

- 工作区复制、基线和文件历史快照排除 `.venv-*` 目录。
- file-history blob 使用不读取内容的存在性检查。
- file-history blob 在校验和回放时不受 `ManagedFiles.read()` 默认 8 MB 上限限制。
- 保留其他 managed file 的默认 8 MB 读取保护。
- 增加大 blob 和版本化虚拟环境目录回归测试。

## Out of scope

- 不删除现有 `.cogent/file-history` 数据或本地虚拟环境备份。
- 不改变普通 memory、tool-result 等 managed file 的读取上限。
- 不提交、推送、部署或迁移数据库。

## Acceptance criteria

- [x] 超过 8 MB 的 file-history blob 可以完成去重检查和读取。
- [x] 普通 `ManagedFiles.read()` 仍默认拒绝超过 8 MB 的文件。
- [x] `.venv-*` 不进入 patch-only 副本、执行基线或历史快照。
- [x] 聚焦测试、完整 pytest 和 compileall 通过，或精确记录非本任务 blocker。

## Decisions

- 保留 `ManagedFiles.read()` 的 8 MB 默认限制，只允许调用方以 `limit=None` 显式读取
  无上限内容，当前仅由 file-history blob 校验路径使用。
- 新增 descriptor-relative、`O_NOFOLLOW` 的 `ManagedFiles.exists()`；blob 去重只检查
  常规文件是否存在，不再为了存在性判断完整读取数百 MB 内容。
- 执行工作区使用统一的 ignored-name 判定，在原有精确目录名之外排除 `.venv-*`，同时
  作用于 patch-only 复制、direct/worktree 基线和历史快照。
- 不清理已有虚拟环境备份或 `.cogent/file-history`；避免扩大到破坏性数据操作。

## Verification

- `.venv/bin/python -m pytest -q tests/test_cogent_file_history.py tests/test_sandbox_tools.py`
  - `30 passed`。
- `.venv/bin/python -m pytest -q tests/test_cogent_file_history.py tests/test_cogent_rewind.py tests/test_sandbox_tools.py tests/test_cogent_runtime.py tests/test_execution_context.py tests/test_cogent_tool_results.py`
  - `109 passed, 1 failed`；唯一失败是未由本任务修改的既有
    `test_result_storage_never_follows_symlinked_parent` 异常类型契约。
- `.venv/bin/python -m pytest -q`
  - `820 passed, 12 skipped, 1 failed, 108 subtests passed`；唯一失败同上。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 24 个 Markdown 和 44 个
  capability；仅报告共享工作树中已有的 evidence review warnings。
- `git diff --check`：通过。

## Result

已修复两个导致命令执行后 Run 失败的边界：FileHistory 不再通过默认受限读取检查 blob
是否存在，并可显式读取超过 8 MB 的历史 blob；执行工作区复制、基线和历史快照统一排除
`.venv-*`。普通 managed file 的默认限制不变。新增测试覆盖 8,000,001 字节 blob 以及
版本化虚拟环境目录在 patch-only/direct 路径上的排除行为。

README、双语技术参考、Interview Notes 与事实映射已经同步。本任务没有删除本地数据，
没有提交、推送、部署或执行数据库迁移。仓库必跑 pytest 仍被任务外既有 symlink 异常
类型契约阻塞，因此工作流不能标记为全量 green。
