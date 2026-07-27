# SELECT-LOCAL-WORKSPACE: 从本机目录选择 Agent 工作区

## Goal

让用户无需手工输入绝对路径，即可在前端浏览服务端允许访问的本机目录，
选择一个文件夹并将其注册为代码 Agent 工作区。

## In scope

- 新增受 `WORKSPACE_ALLOWED_ROOTS` 约束的目录浏览 API。
- 在设置页增加文件夹选择器，支持进入子目录、返回上级和确认当前目录。
- 选择目录后自动生成可编辑的工作区 ID，并注册或更新工作区。
- 保留现有手工输入和已注册工作区选择能力。
- 补充目录边界、安全性、API 和前端契约测试。

## Out of scope

- 读取或上传所选目录中的文件内容。
- 绕过 `WORKSPACE_ALLOWED_ROOTS` 浏览整台电脑。
- 引入 Electron、浏览器扩展或新的前端框架。
- 部署、迁移、提交或发布。

## Acceptance criteria

- [x] 用户可从设置页点击“选择文件夹”打开目录选择器。
- [x] 选择器可浏览允许根目录及其子目录，并可返回允许范围内的上级。
- [x] API 不返回或接受允许根目录之外的路径，并过滤越界符号链接。
- [x] 确认目录后自动填充并注册工作区，Agent 可直接使用它。
- [x] 现有手工注册和已注册工作区流程保持可用。
- [x] 桌面端与移动端布局可用，并具有加载、空目录和错误反馈。
- [x] 项目规定测试、Python 编译和 JavaScript 语法检查通过。

## Decisions

- 浏览器安全模型不会向普通网页暴露本机绝对路径，因此使用服务端目录浏览
  API，而不是 `showDirectoryPicker()` 或 `webkitdirectory`。
- 目录浏览严格复用 `WorkspaceService` 的规范化路径和
  `WORKSPACE_ALLOWED_ROOTS` 边界，根目录之外的直接路径被拒绝，越界符号链接
  不出现在列表中。
- 初始视图只显示允许根目录；用户可进入子目录、返回上级或回到允许位置。
- 确认目录时优先复用根路径相同的已注册工作区，否则根据文件夹名称生成合法、
  不冲突且仍可编辑的工作区 ID，并调用现有注册接口。
- 保留原有路径输入、工作区 ID 编辑、手工注册和已注册工作区下拉选择。
- 目录 API 只返回目录名称与规范化路径，不读取或上传文件内容。

## Verification

- `.venv/bin/python -m pytest -q`：99 passed，保留 1 个第三方 Starlette
  弃用 warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- API 回归覆盖允许根目录、子目录、上级路径、普通文件过滤、越界路径拒绝及
  越界符号链接过滤。
- 真实浏览器完成从允许位置进入
  `/Users/mxjk/programming/vs code project/ai-agent-platform`、确认选择、
  自动生成 `ai-agent-platform` ID 和注册流程。
- 注册后直接启动 Agent，运行状态完成，未再出现 `workspace not found`。
- 390×844 移动端验收：选择器宽 356px、页面横向溢出为 0、目录列表可滚动，
  控制台 error/warn 为 0。
- 使用内存 Repository、内存向量库、进程内队列和 fake provider 启动临时
  验收服务；验收后已停止，未执行数据库迁移、部署或外部写入。

## Result

设置页现已提供“选择文件夹”入口。用户可以在受限目录选择器中浏览本机文件夹，
确认后自动填入根路径、生成或复用工作区 ID，并立即注册供代码 Agent 使用。
目录访问仍受 `WORKSPACE_ALLOWED_ROOTS` 保护，现有手工工作区配置流程保持兼容。
