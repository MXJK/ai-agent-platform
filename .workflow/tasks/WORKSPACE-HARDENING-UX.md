# WORKSPACE-HARDENING-UX: 工作区安全边界与上下文体验

## Goal

修复工作区注册、授权、会话继承和前端状态不一致问题，并在现有 Agent 工程控制台
视觉基线上建立清晰、可验证的工作区选择与管理体验。

## In scope

- 目录浏览必须遵守可信身份边界。
- 规范化根路径只能由一个 workspace 标识，防止使用新 ID 重复认领目录。
- 会话配置和用户默认工作区必须验证当前用户的 workspace 访问权。
- Agent 写工具在执行前必须具备 editor 权限，人工审批不替代角色授权。
- 统一前端 active/default/draft workspace 状态，删除虚假的 `workspace_main` 回退。
- 增加持久工作区上下文条、快速切换、明确的空状态与注册确认。
- 工作区列表和 Token 用量分别加载，单个用量请求失败不污染注册结果。
- 同步测试、README、Interview Notes 与 facts 证据。
- 保留并完成当前 worktree 已存在的 `OPTIMIZE-FRONTEND-DESIGN-SYSTEM`
  视觉改动；不回退用户已有工作。

## Out of scope

- 工作区删除、归档、邀请成员或新的平台级管理员管理页。
- 自动合并分支、部署、发布或执行目标数据库迁移。
- 引入前端框架、构建链、外部字体或第三方 UI 依赖。

## Acceptance criteria

- [x] trusted-header 模式下匿名目录浏览返回 401。
- [x] 不同 workspace ID 不能注册到同一个规范化根路径；同 ID 同路径仍幂等。
- [x] 无 workspace 权限的用户不能将其保存到会话或默认设置。
- [x] viewer 可执行只读 Agent，但不能批准或执行写工具；editor/admin 可按现有审批流继续。
- [x] 前端不再显示或请求不存在的 `workspace_main` 回退。
- [x] 会话切换、清空选择和启动恢复不会残留旧 workspace ID 或根路径。
- [x] Agent 缺少可用工作区时在前端被阻止，并提供直接选择入口。
- [x] 注册表单草稿不改变当前运行工作区，注册成功后才显式激活。
- [x] 工作区用量加载失败不会把成功注册显示为失败。
- [x] 桌面、平板、手机宽度下工作区上下文可见、可达且无横向溢出。
- [x] pytest、compileall、JavaScript 语法、迁移 head 和 diff 检查通过；Interview
  Notes 已由仓库在 `ca71b1e6` 停止跟踪，当前 checkout 不含手册或校验脚本，因此不适用。

## Decisions

- canonical root 是 workspace 的资源身份：一个规范化根路径对应一个 workspace ID。
- 本地 `AUTH_MODE=disabled` 保留目录浏览与注册；可信身份模式要求有效网关身份。
- 当前会话 workspace 与新会话默认 workspace 分开保存，界面不再一次操作隐式修改两者。
- 设计沿用 Orbit Navy、Signal Iris、Mineral Teal 和 Alert Gold；工作区 ID/路径使用
  SF Mono。唯一视觉签名是可点击的“代码上下文条”，不引入第二套设计语言。
- 当前 worktree 已有设计系统修改视为本任务的输入基线，最终验证覆盖全部保留改动。

## Verification

- `220 passed, 1 warning, 4 subtests passed`：使用主工作树现有 `.venv` 在本 worktree
  运行完整 `pytest -q`；唯一警告是既有 FastAPI TestClient 的 httpx2 弃用提示。
- `compileall -q ai_agent_platform tests evals migrations` 通过。
- `node --check ai_agent_platform/static/app.js` 通过。
- `python -m alembic heads` 返回单一 `20260807_0015 (head)`。
- `git diff --check` 通过。
- 合并 `origin/main` 的前端设计系统提交后重新运行上述完整验证；冲突解决保留了主线的
  间距、层级和一次性状态动画基线，以及本任务的工作区上下文与访问控制改动。
- 浏览器真实渲染验证：桌面空状态、目录选择、登记成功、重复根路径 409、Agent 快速
  切换均通过；390×844 首屏无横向溢出，选择工作区后 Agent 提交按钮恢复可用；控制台
  无 error/warning。
- 文档影响已同步 `README.md` 与 `README.en.md`。仓库已停止跟踪 Interview Notes，
  因而没有可更新的 `INTERVIEW_NOTES.md`、Parts、`facts.json` 或 `validate.py`。

## Result

- 工作区目录浏览现在进入 trusted-header 身份边界；会话配置和用户默认工作区会验证
  viewer 访问权。
- 规范化根路径成为唯一资源身份：服务层预检、PostgreSQL 唯一约束和并发冲突转换共同
  防止不同 ID 重复认领目录。迁移遇到既有重复数据会明确中止，未在本任务中执行升级。
- Agent 仍允许 viewer 发起只读分析，但批准写入/外部副作用计划要求 editor，并在
  Worker 执行前再次检查，避免排队期间权限撤销后继续执行。
- 工作区响应新增 `status`、`role`、`can_update`；前端据此显示路径健康度、角色和可编辑
  能力。
- 前端已拆分 active/default/draft 状态，移除虚假 `workspace_main`，新增 Agent 代码
  上下文条、快速切换、登记目录卡片和内联结果；Token 用量请求使用隔离失败语义。
- 保留并覆盖验证了进入 worktree 时已有的设计系统改动；没有回退用户文件。
- 已解决与 `origin/main` 的工作流记录、设计系统样式和静态资源测试冲突；未使用强制
  推送，也未丢弃两侧已验证的功能。
- 用户已明确授权提交并推送本 worktree 的专用分支；未合并、未部署、未执行数据库迁移。
  后续需审阅远端分支，并在确认重复根路径已清理后另行批准 Alembic 升级。
