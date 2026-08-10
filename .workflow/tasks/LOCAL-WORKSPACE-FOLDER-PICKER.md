# LOCAL-WORKSPACE-FOLDER-PICKER: 本机文件夹可选范围

## Goal

让本机浏览器工作台点击“添加文件夹”时弹出操作系统原生文件夹选择窗口，由用户在 Finder 中选择并添加项目目录，而不是先进入网页目录浏览器。

## In scope

- 将本机默认 `WORKSPACE_ALLOWED_ROOTS` 从启动目录调整为用户主目录。
- 在本机、关闭认证的运行模式下提供 macOS 原生文件夹选择接口。
- “添加文件夹”和“重新关联”优先调用系统窗口；系统能力不可用时回退网页目录浏览器。
- 在目录选择列表中隐藏点目录；显式配置为允许根的隐藏目录仍可使用。
- 保留显式 `WORKSPACE_ALLOWED_ROOTS` 的安全边界和多根目录配置。
- 更新文件夹选择界面的说明、配置文档和回归测试。
- 验证目录浏览、工作区注册及现有功能无回归。

## Out of scope

- 绕过显式配置的工作区白名单。
- 让远端浏览器直接读取客户端文件系统。
- 修改工作区持久化模型、权限模型或执行数据库迁移。

## Acceptance criteria

- [x] 未配置或留空 `WORKSPACE_ALLOWED_ROOTS` 时，允许根目录是当前用户主目录。
- [x] 点击“添加文件夹”会弹出 macOS Finder 文件夹选择窗口，而不是先显示网页目录列表。
- [x] 系统窗口选择目录后可按现有流程注册、切换工作区或重新关联；取消选择不产生变更。
- [x] 非本机请求或启用认证的部署不能触发服务端系统窗口。
- [x] 系统选择能力不可用时仍可回退网页目录浏览器。
- [x] 显式配置 `WORKSPACE_ALLOWED_ROOTS` 时仍只允许配置的路径。
- [x] 主目录浏览不把 `.ssh` 等点目录显示为普通项目候选。
- [x] UI 和文档清楚说明默认范围及生产配置边界。
- [x] pytest、compileall、前端语法及文档校验通过。

## Decisions

- 浏览器原生文件输入不会向后端暴露可供 Agent 直接访问的绝对路径，因此由同机 FastAPI 服务调用 macOS 原生文件夹窗口，并把选择结果返回前端。
- 系统窗口接口只允许 `AUTH_MODE=disabled` 且请求来自 loopback；远端或多用户部署继续使用受控网页目录浏览器。
- macOS 系统选择能力失败或不可用时自动回退现有网页目录浏览器，避免其他环境失去工作区管理能力。
- 显式环境配置优先于本机默认值，容器和多用户部署仍需配置最小允许根目录。
- 默认目录列表隐藏点目录，减少误选系统或敏感配置目录的风险。

## Verification

- `.venv/bin/python -m pytest -q` — rebase 到最新 `origin/main` 后 268 passed，11 subtests passed；仅有 1 条既有 Starlette/httpx2 弃用警告。
- `.venv/bin/python -m compileall ai_agent_platform tests evals` — 通过。
- `node --check ai_agent_platform/static/app.js` — 通过。
- `git diff --check` — 通过。
- 专项系统选择接口、macOS 命令、目录边界和静态前端测试 — 7 passed，25 deselected。
- 真实 Finder 取消流程 — 点击“添加文件夹”后出现 Finder 的“选取文件夹”系统窗口，起始位置为用户主目录；取消后接口返回 200、备用网页选择器未出现、没有新增工作区、按钮恢复可用且控制台无错误。
- 真实 Finder 选择流程 — 再次打开系统窗口并选中 `ai-agent-platform`，接口返回 200，随后工作区 PUT、列表、用量和默认偏好请求均成功；设置页显示该目录为当前/新会话默认工作区，备用网页选择器未出现且控制台无错误。
- 系统能力失败回退冒烟 — 测试中止 Finder 子进程后原生接口返回 503，前端自动请求受控网页目录列表并展开允许根。
- `.venv/bin/python INTERVIEW_NOTES/validate.py` — rebase 获取远端 ChangeSet 交付后通过，验证 12 个 Markdown 文件和 30 项能力；evidence review 仅为信息性提示。
- 推送前整合 — 初次推送因 `origin/main` 新增 ChangeSet 合并而被非快进拒绝；获取远端后将 Finder 提交 rebase 到 `854c652c`，唯一代码提交重写为 `7a026d51`。共享代码和文档自动合并，仅工作流状态人工保留两侧语义；未强制推送。
- Diff 复核 — 任务改动限于本机系统目录选择集成、loopback/认证 API 边界、默认工作区根、网页回退、UI 文案、配置/产品文档、测试、`.gitignore` 和工作流记录；无凭据、任务生成文件或迁移变更。同期出现的 `harness讲义.pdf` 与本任务无关，已按用户要求加入 `.gitignore`，`git check-ignore` 确认生效且不会进入提交。

## Result

实现已完成：本机 macOS 点击添加或重新关联时，由同机 FastAPI 通过 Finder 打开原生“选取文件夹”窗口；取消无副作用，选中后继续复用现有注册、切换和默认偏好流程。接口仅允许 `AUTH_MODE=disabled` 的 loopback 请求，选择结果仍受 `WORKSPACE_ALLOWED_ROOTS` 约束；能力不可用或启动失败时回退网页目录浏览器。空配置默认根为用户主目录，网页回退隐藏点目录。

README、中英文说明、访谈手册根页、相关 Part 和 facts 证据均已同步。Finder 功能提交 `7a026d51` 已由 rebase 后的全量测试、编译、前端语法、diff、访谈手册校验和真实 Finder 交互覆盖，任务状态可安全设为 done。用户已明确授权提交并推送本次 Finder 工作区选择改动及对应 `.gitignore`；`harness讲义.pdf` 已被忽略且不纳入提交。未执行迁移、部署或强制推送；远端 ChangeSet 迁移 `20260809_0019` 仍需单独人工批准后方可应用。
