# COGENT-SKILLS-PATH-UNIFICATION: 统一用户级 Skills 目录

## Goal

让本地配置、自托管 Docker Compose 和 Web UI 都把用户级 Skill 的新写入目录统一为
`~/.cogent/skills`，同时保留对旧 `~/.ai-agent-platform/skills` 的只读发现兼容。

## In scope

- 将 Docker Compose 的 `SKILLS_DIRECTORY_PATH` 指向 `/home/app/.cogent/skills`。
- 将 `.env.example` 与 Web UI 的回退显示统一为 `~/.cogent/skills`。
- 更新自托管配置回归测试，验证 Skills 路径由现有 `cogent_user_state` 卷持久化。
- 复核中英文技术参考仍准确描述新写入路径和旧路径兼容边界。

## Out of scope

- 不自动迁移或删除旧用户 Skill。
- 不修改 MCP 配置目录。
- 不部署、提交或推送。
- 不修改 Skill 发现优先级、解析、安全或权限逻辑。

## Acceptance criteria

- [x] 默认配置、Compose、示例环境变量及 Web UI 回退均使用 `.cogent/skills`。
- [x] Compose 中用户 Skill 由 `cogent_user_state` 持久化。
- [x] `~/.ai-agent-platform/skills` 仅作为旧 Skill 的只读兼容来源。
- [x] 聚焦测试、配置渲染、compileall 和完整 pytest 通过，或精确记录任务外 blocker。
- [x] 文档影响已评估并记录。

## Decisions

- 继续使用现有 `cogent_user_state:/home/app/.cogent` 命名卷，不把容器状态改为宿主机
  bind mount。
- 不迁移旧目录；`SkillDiscovery` 已在新目录为默认值时自动启用旧目录兼容读取。

## Verification

- `.venv/bin/python -m pytest -q tests/test_self_hosted_compose.py tests/test_config.py
  tests/test_skill_discovery.py tests/test_skill_registry.py`
  - `48 passed, 8 subtests passed`；仅有 Starlette `BlockingPortal` 既有弃用警告。
- `node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs`
  - `26 passed`。
- `node --check ai_agent_platform/static/app.js`：通过。
- `docker compose config --quiet`：通过。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`
  - 校验 24 个 Markdown 与 44 个 capability 通过；仅输出共享工作区既有 evidence
    review warnings。
- `git diff --check`：通过。
- `.venv/bin/python -m pytest -q`
  - `823 passed, 12 skipped, 1 failed, 108 subtests passed`。
  - 唯一失败仍为任务开始前已记录的
    `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`：
    测试期望 `OSError`，`ManagedFiles.read()` 对 ELOOP/ENOTDIR 抛出 `ValueError`；本任务
    未修改对应实现或测试。
- 旧路径复核：产品配置与 Web UI 不再把 `~/.ai-agent-platform/skills` 作为新写入路径；
  该路径仅保留在 `SkillDiscovery` 的兼容读取装配、技术参考和反向回归断言中。

## Result

用户级 Skill 的新写入目录已统一为 `~/.cogent/skills`。自托管容器使用
`/home/app/.cogent/skills`，并由既有 `cogent_user_state:/home/app/.cogent` 命名卷持久化；
本地 `.env.example` 与 Web UI 初始/失败回退显示也使用同一路径。旧
`~/.ai-agent-platform/skills` 不迁移、不删除，仍由运行时作为只读兼容来源。

中英文技术参考已经准确描述新路径、发现优先级与旧目录兼容，无需重复修改。未重建或重启
当前 Compose 服务；配置会在下一次经用户授权的容器重建/重建实例时生效。功能范围已完成，
但仓库必跑 pytest 仍被任务外既有 symlink 异常类型契约阻塞，因此工作流不能标记 done。
