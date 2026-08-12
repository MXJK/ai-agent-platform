# SKILL-DISCOVERY: Skill 发现与 Command 注册

## Goal

实现最小、安全的 Skill 系统，从 bundled、user、project 三种来源发现 `SKILL.md`，并转换为内部 `SkillDefinition` / `CommandDefinition`，供 Agent 上下文与 slash command 注册使用。

## In scope

- 发现 bundled、user、project 三种来源的 `SKILL.md`。
- 解析名称、描述、适用 Agent/模式、指令内容、上下文预算、slash command 元数据和所需工具名称。
- 定义确定性的来源优先级、命名空间、排序、覆盖与冲突诊断。
- 限制单文件大小、总字符数和发现数量，隔离单个损坏 Skill。
- 拒绝符号链接及真实路径逃逸。
- 将 project Skill 标记并包装为不可信项目上下文。
- 将发现结果接入运行时和 slash command 注册边界。
- 增加安全、预算、覆盖和容错测试，并同步用户与架构文档。

## Out of scope

- 从 `SKILL.md` 自动执行 Python、Shell 或任意代码。
- 通过 Skill 安装、注册、启用或授予工具权限。
- 绕过 `ToolUseContext`、Sandbox 或 allow/ask/deny 策略。
- 远程 Skill 下载、插件安装、动态依赖安装或外部写入。
- 未经单独授权的部署、迁移、提交、推送、PR 或合并。

## Acceptance criteria

- [x] bundled、user、project 三种来源均可发现合法 `SKILL.md`，并生成不可变的内部 Skill/Command 定义。
- [x] 来源优先级、命名空间、排序、覆盖和重复名行为确定且有稳定诊断。
- [x] 单文件大小、总字符数和发现数量均受硬限制，超限项只产生诊断。
- [x] 损坏 Markdown/元数据、未知字段和单个坏 Skill 不拖垮整体发现或启动。
- [x] 符号链接和真实路径逃逸被拒绝，project Skill 作为明确的不可信项目上下文注入。
- [x] Skill 仅声明所需工具；不会执行代码、注册新工具、提升权限或绕过既有工具策略。
- [x] slash command 元数据被转换并注册，命令冲突遵循同一确定性规则。
- [x] 测试覆盖发现、覆盖、重复名、损坏 Markdown、预算、路径逃逸和权限不可提升。
- [ ] README、访谈手册及 facts 同步；全量 pytest、compileall、手册校验和 diff check 通过。

## Decisions

- 使用冻结 dataclass 表达 `SkillDefinition`、`CommandDefinition`、诊断、目录和上下文
  来源；定义本身没有函数、脚本入口、动态 import 或权限字段。
- 三种来源的覆盖优先级固定为 `project > user > bundled`，限定名为
  `<source>:<name>`。同来源重名按相对路径字典序选择，跨来源覆盖和 command/alias
  冲突均保留稳定诊断，最终 Skill/Command 按规范化名称排序。
- 只接受带严格 YAML frontmatter 的 UTF-8 `SKILL.md`。安全 Loader 拒绝 Python
  对象标签、重复键和未知字段；硬上限为单文件 64 KiB、每轮 64 个候选、总计
  128 KiB 字符和单 Skill 16,000 字符上下文预算。
- 所有来源根、目录和 `SKILL.md` 都拒绝 symlink；读取前后校验普通文件和真实路径
  边界，并使用 `O_NOFOLLOW`（平台支持时）缩小换链竞态。
- project Skill 在 Run 入队前包装为 `untrusted_project_skill` 并冻结到
  `RunContextSnapshot`；Prompt 明确其不能覆盖上级指令、改变 Sandbox/审批或授权动作。
- `tools` 只做“已注册工具是否可用”的依赖检查，不调用 `ToolRegistry.register()`，
  不修改 tool selection，也不产生 allow。slash command registry 仅保存元数据映射。
- 运行时默认目录为包内 `bundled_skills`、`~/.ai-agent-platform/skills` 和已鉴权
  Workspace 的 `.agents/skills`；只有 `skills_allowed && skills_enabled` 时才发现。

## Verification

- `.venv/bin/python -m pytest -q`：308 passed、38 subtests passed；仅有既有
  Starlette/httpx 弃用警告。
- Skill/Run/启动/配置/工具/Sandbox 专项：55 passed、27 subtests passed。
- `.venv/bin/python -m compileall -q ai_agent_platform tests evals`：通过。
- `.venv/bin/python -m json.tool INTERVIEW_NOTES/facts.json`：通过。
- `git diff --check`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：未通过；当前 `main` 缺少手册已引用的
  `ai_agent_platform/integrations/permissions.py` 与 `tests/test_permissions.py`。这些前置
  实现在现有 `codex/run-context-boundary-hardening` 分支提交 `3d3d5b1e` 中；校验报告
  4 个缺失证据路径，与本任务新增 Skill evidence 无关。

## Result

- 已实现有界、确定、单项故障隔离的 bundled/user/project Skill 发现、严格
  `SKILL.md` 解析、命名空间覆盖诊断、不可变 Skill/Command 定义和 slash command
  metadata registry。
- project Skill 以明确的不可信来源进入持久化 Run 快照；工具依赖只针对现有 Registry
  检查，不执行 Skill 目录代码、不注册工具、不扩大 allowlist 或权限。
- 已同步中英文 README 及本机模块化访谈手册/facts，并增加发现、覆盖、重复名、损坏
  Markdown/YAML、预算、symlink/逃逸、适用范围、command 冲突和权限不可提升测试。
- 当前提交可供审阅，但任务保持 blocked：需要把本分支迁移到已经实现
  `PermissionResolver/ToolUseContext` 的 `codex/run-context-boundary-hardening` 基线上，
  解决可能冲突并重新执行完整验证，之后才能把最后一项验收和工作流状态标记完成。
