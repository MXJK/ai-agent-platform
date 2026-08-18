# DIRECT-EXECUTION-WORKSPACES：直接执行工作区

## Goal

让代码 Agent 在服务端选定并登记的同一个 execution workspace 中完成读取、修改、验证与 Diff；支持 `direct`、`worktree`、`patch_only`，同时保留命令沙箱、权限审批和 ChangeSet 审计/安全撤销边界。

## In scope

- 服务端 execution workspace runtime、持久化记录、恢复与清理策略。
- workspace mode 配置、RunContext 冻结、API capability/request/response。
- repo/sandbox 工具统一 execution root，安全写入、冲突检测与 write-ahead journal。
- ChangeSet recorded/applied 语义、历史 Apply 兼容和安全 revert。
- Agent composer、change review、配置示例、中英文 README 与 interview handbook。
- 关键行为、边界、权限和回归测试。

## Out of scope

- 自动 commit、push、merge、部署、发布或实际数据库迁移。
- 客户端、模型、项目配置、Skill 或 MCP 指定任意 execution root。
- 自动删除保留给用户检查的 Git worktree。

## Acceptance criteria

- [x] `patch_only` 保持临时副本和历史 ChangeSet Apply 兼容，且不修改 source root。
- [x] `direct` 在已认证 editor/admin、功能开关和精确审批下立即修改 source root，并串行化同 Workspace 写入者。
- [x] `worktree` 对干净 Git checkout 创建并保留安全分支/worktree；非 Git 或 dirty checkout 明确失败。
- [x] 同一 Run 的 repo 读取、sandbox 写入、验证、状态和 Diff 使用同一 execution root，resume 不漂移。
- [x] 写操作具备基线/前后 SHA-256、上下文检查、原子替换、冲突保护和 write-ahead journal。
- [x] 所有发生修改的终态保存完整 ChangeSet；direct/worktree 标记已生效且 Apply 不重复落盘。
- [x] editor/admin 可按 change_set_id + patch_sha256 幂等安全撤销，后续用户修改会触发 conflict。
- [x] API/capabilities/frontend 展示、冻结并解释 workspace mode，change review 区分三种生命周期。
- [x] 默认配置只允许 `patch_only`，本机可信配置可默认 `direct`；旧配置映射有兼容测试。
- [x] README、README.en、INTERVIEW_NOTES、受影响 Parts、facts、`.env.example` 和 compose 配置同步。
- [x] 所有项目要求的测试与静态验证通过。

## Decisions

- `ExecutionWorkspaceManager` 由服务端创建并持有 Workspace/Run 到执行根的映射；`RunContext` schema v4 在读取 instructions、skills 和工具能力前冻结该映射。注册 source root 始终是授权边界，客户端不能指定执行路径。
- workspace mode 只决定文件落点；`SANDBOX_MODE` 继续独立决定命令隔离。repo 读取、安全写入、命令 cwd、状态和 Diff 都从同一 execution root 解析。
- `patch_only` 使用服务所有的临时副本并在终态清理；`direct` 使用 source root 并通过主机文件锁串行写入者；`worktree` 只接受干净 Git checkout，使用 `codex/` 分支和服务所有目录，并保留供检查。
- 文件写入和补丁应用使用基线/期望/写后 SHA-256、上下文校验、同目录原子替换、fsync 和 source 外 write-ahead journal；命令执行前也登记可恢复基线，避免任意命令变更绕过恢复记录。
- direct/worktree 的 ChangeSet 是“执行时已写入”的审计记录，状态直接为 `applied`，再次 Apply 只做摘要绑定的幂等返回；历史 ChangeSet 保留原 Apply 流程。Revert 仅在写后哈希仍匹配时反向应用，否则保留用户后续修改并报告冲突。
- 新配置 `AGENT_WORKSPACE_DEFAULT_MODE` / `AGENT_WORKSPACE_ALLOWED_MODES` 默认都收敛到 `patch_only`；旧 `CHANGE_SET_APPLY_MODE` 只在新配置没有显式来源时映射，避免旧环境覆盖新设置。
- 未新增数据库列或执行迁移；执行元数据复用既有 JSON/结果字段，历史 RunContext schema 继续可读。

## Verification

- `.venv/bin/python -m pytest -q`：402 passed，49 subtests passed。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：exit 0。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：校验 12 个 Markdown 文件和 37 项 capability，exit 0；输出的 changed-evidence review warnings 为仓库现有证据复核提示，不是校验失败。
- `node --check ai_agent_platform/static/app.js`：exit 0。
- `docker compose config --quiet`：exit 0。
- `git diff --check`：exit 0。

## Documentation impact

- 用户可见行为、配置、执行架构、数据流、权限边界和变更撤销语义均发生变化；已同步 `README.md`、`README.en.md`、`INTERVIEW_NOTES.md`、受影响 Parts、`INTERVIEW_NOTES/facts.json`、`.env.example` 和 `docker-compose.yml`。
- 无数据库迁移和部署操作；管理员若要开放 live mode，仍需显式配置允许模式、live write 开关和可信认证/角色。

## Result

完成。三个 execution workspace mode 已贯通后端、RunContext、工具、命令、ChangeSet/API 和前端，安全回滚与历史兼容已覆盖。验证针对当前工作树执行；按授权未 commit、push、merge、部署或执行迁移，因此没有更新 `last_verified_commit`。用户已有的无关工作树改动保持不变。
