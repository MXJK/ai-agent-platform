# COGENT-SKILLS-PATH-UNIFICATION: 统一用户级 Skills 目录

## Goal

让本地配置、自托管 Docker Compose 和 Web UI 都把用户级 Cogent 状态统一到宿主机
`~/.cogent`：用户记忆写入 `~/.cogent/memory`，用户 Skill 写入 `~/.cogent/skills`，
且旧 `~/.ai-agent-platform/skills` 不再参与运行时发现。

## In scope

- 将 Docker Compose 的 `SKILLS_DIRECTORY_PATH` 指向 `/home/app/.cogent/skills`。
- 将 `.env.example` 与 Web UI 的回退显示统一为 `~/.cogent/skills`。
- 将 Compose 的 `/home/app/.cogent` 改为宿主机 `${HOME}/.cogent` bind mount。
- 将现有 `cogent_user_state` 用户记忆与旧用户 Skills 复制到宿主机 `~/.cogent`。
- 移除旧用户 Skills 目录的发现回退，避免已删除 Skill 被旧副本重新加载。
- 取消 Skill `description` 的独立 500 字符限制，继续使用全局文件与上下文预算。
- 更新自托管配置回归测试，验证用户记忆与 Skills 都由宿主机目录持久化。
- 复核中英文技术参考仍准确描述新写入路径和旧路径兼容边界。

## Out of scope

- 不删除旧用户 Skill 目录或旧 Docker 命名卷，保留为回滚备份但不参与运行时发现。
- 不修改 MCP 配置目录。
- 不提交或推送。
- 不修改项目 > 用户 > 内置的 Skill 发现优先级、安全或权限逻辑。

## Acceptance criteria

- [x] 默认配置、Compose、示例环境变量及 Web UI 回退均使用 `.cogent/skills`。
- [x] Compose 中 `/home/app/.cogent` 绑定到宿主机 `~/.cogent`。
- [x] 现有用户记忆与旧用户 Skills 已复制并校验到 `~/.cogent`。
- [x] 重建后的 App 从 bind mount 读取记忆，并把新用户记忆与 Skill 写入其中。
- [x] `~/.ai-agent-platform/skills` 不再参与运行时发现。
- [x] 超过 500 字符的非空 Skill `description` 可通过发现和 Registry 校验。
- [x] 聚焦测试、配置渲染、compileall 和完整 pytest 通过，或精确记录任务外 blocker。
- [x] 文档影响已评估并记录。

## Decisions

- 使用 `${HOME}/.cogent:/home/app/.cogent`，让 Docker 与宿主机 CLI 共享同一用户级状态。
- 迁移采用复制并校验，不删除旧目录或命名卷；回滚时仍可恢复原挂载。
- `SkillDiscovery` 只读取项目 `.cogent/skills`、用户 `~/.cogent/skills` 和内置 Skills；
  旧目录仅作为磁盘上的回滚备份。
- `description` 不设置字段级长度上限；64 KiB 单文件、128 KiB 总发现内容和上下文预算
  继续约束资源占用。

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
- 旧路径复核：产品配置、运行时发现与 Web UI 均不再读取或写入
  `~/.ai-agent-platform/skills`；磁盘目录保留但不会让已删除 Skill 复活。
- 宿主机迁移与真实运行验收：
  - 暂停 App 后，将 `ai-agent-platform_cogent_user_state` 中的用户记忆复制到
    `~/.cogent/memory`，将旧 `~/.ai-agent-platform/skills` 中的 `code-review`、
    `bug-triage`、`test-design` 复制到 `~/.cogent/skills`；未复制 `.DS_Store`。
  - 迁移前后 2 个记忆文件、1 个 lock 文件和 3 个 `SKILL.md` 的 SHA-256 全部一致；
    `~/.cogent` 及子目录权限收紧为仅当前用户可访问，未发现符号链接。
  - `docker compose up -d --no-deps --force-recreate app` 成功，App 健康；容器 mount 为
    `bind /Users/mxjk/.cogent -> /home/app/.cogent`。
  - 容器解析根为 `/home/app/.cogent/memory` 与 `/home/app/.cogent/skills`；Skills API
    从新目录发现 `bug-triage`、`code-review`、`test-design`，Memory API 从新目录发现
    `language-preference`。
  - 通过真实 API 创建临时用户记忆和临时 Skill，宿主机对应文件立即出现在
    `~/.cogent/memory` 与 `~/.cogent/skills`；删除测试条目后原 `MEMORY.md` 哈希恢复一致。
  - 旧 Docker 命名卷和旧用户 Skills 目录均保留，未删除。
- 迁移后验证：
  - 聚焦 pytest：`110 passed, 8 subtests passed`。
  - Compose MCP profile 配置、compileall、Interview Notes 校验与 `git diff --check` 通过。
  - 完整 pytest：`825 passed, 12 skipped, 1 failed, 108 subtests passed`；唯一失败仍为上述
    任务外 `ManagedFiles` symlink 异常类型契约。
- 删除回退与长描述跟进验证：
  - 聚焦 pytest：`70 passed, 8 subtests passed`；增加长 description、旧目录不回退及
    根目录 `README.md` 不误判的回归覆盖。
  - `.venv/bin/python -m compileall ai_agent_platform tests evals`、
    `.venv/bin/python INTERVIEW_NOTES/validate.py`、`docker compose config --quiet` 和
    `git diff --check` 通过；Interview Notes 仅有共享工作树既有 evidence review warnings。
  - 完整 pytest：`828 passed, 12 skipped, 1 failed, 108 subtests passed`；唯一失败仍为上述
    任务外 `ManagedFiles` symlink 异常类型契约，本次 Skills 测试没有新增失败。
  - `docker compose up -d --no-deps --force-recreate app` 后 App healthy；真实 `/skills`
    页面显示 `agent-reach`、`ego-browser`、内置 `skill-creator` 共 3 项，两个用户 Skill
    的 908/977 字符 description 均完整通过后端发现。旧 `bug-triage`、`code-review`、
    `test-design` 不再出现，校验提示隐藏。
  - 当前 `.workflow/state.yaml` 的 active task 是共享工作树中另一个
    `INSTALL-RECOMMENDED-MCP-SERVERS` 任务；为避免覆盖并行状态，本跟进只更新本任务记录。

## Result

用户级 Cogent 状态已统一到宿主机 `~/.cogent`。Compose 把该目录绑定为容器
`/home/app/.cogent`；用户记忆写入 `~/.cogent/memory`，用户 Skill 写入
`~/.cogent/skills`，宿主机 CLI 与容器共享同一份数据。现有用户记忆和三个旧用户 Skill
已无损复制并通过 SHA-256 与真实 API 写入验收；App 已使用新挂载重建并处于健康状态。

中英文 README 与技术参考已同步为宿主机 bind mount。旧 Docker 命名卷和旧
`~/.ai-agent-platform/skills` 保留作回滚备份但不再发现，没有删除。Skill `description`
不再受独立 500 字符限制，仍受发现文件与上下文总预算保护。功能范围和真实部署验收均完成，
但仓库必跑 pytest 仍被任务外既有 symlink 异常类型契约阻塞，不能单独据此声明全套绿色。
