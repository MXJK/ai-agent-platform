# FOCUS-CHAT-WORKBENCH-LAYOUT: 突出聊天工作区

## Goal

移除对话页重复的 Inspector 运行详情，压缩顶部栏与输入区，并让桌面端左右辅助栏可隐藏、可调整宽度，使聊天内容成为工作台的视觉中心。

## In scope

- 删除右侧辅助栏中的执行轨迹、原始数据与请求 ID 区域；最近会话继续保留。
- 将对话失败恢复入口改为独立 Trace 审计页。
- 压缩顶部栏、活动会话标题和聊天输入区的高度。
- 桌面端左右辅助栏均可隐藏，并支持鼠标、触控笔和键盘调整宽度。
- 在设备级 UI 偏好中保存左右栏显隐与宽度。
- 保持窄屏右侧抽屉和移动端底部导航可用。
- 更新前端契约测试和相关项目文档。

## Out of scope

- 修改 Trace 审计数据、Agent Run 生命周期或审批语义。
- 修改会话查询、排序、分页或持久化 API。
- 重做其他工作台页面的视觉系统。

## Acceptance criteria

- [x] 对话页不再显示 Inspector 运行详情，完整运行审计仍可从 Trace 审计页访问。
- [x] 顶部栏和 composer 比当前版本更紧凑，聊天消息获得更多可视高度。
- [x] 桌面端左右栏可独立显示/隐藏，控制具有正确的无障碍状态。
- [x] 桌面端左右栏可用指针拖拽和键盘方向键调整宽度，并记住宽度。
- [x] 1120px 以下右栏继续作为抽屉，900px 以下底部导航不被隐藏或拖拽逻辑破坏。
- [x] JavaScript、前端契约、完整测试及项目规定验证通过。

## Decisions

- 右栏只承载最近会话，不再复制 Trace 审计页已有的运行详情。
- 沿用现有侧栏视觉语言和本地偏好键，避免引入新的后端配置。
- resize handle 使用可聚焦 separator 语义；方向键每次调整 16px，Home/End 对应最小/最大宽度。

## Verification

- 合并后的本地 `main` 执行 `.venv/bin/python -m pytest -q`：663 passed，107 subtests passed。
- 聚焦前端契约 `tests/test_api.py -q`：36 passed。
- `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`、`git diff --check`：通过。
- 真实浏览器 1440×960：左右栏显隐正常，键盘调宽后刷新保留 236px/304px，聊天区无
  遮挡；composer 从 165.5px 压缩到 145.5px。
- 真实浏览器 390×844：无横向溢出；右侧抽屉范围为 top=56、bottom=774，不覆盖
  bottom=774 开始的移动导航；桌面隐藏偏好不会让移动导航进入 inert/aria-hidden 状态。
- Impeccable layout detector 以降级正则模式运行，返回 0 findings；缺少 HTML/CSS parser
  模块，因此不把结果声明为完整静态审计。
- `INTERVIEW_NOTES/validate.py`：24 个 Markdown、43 项 capability 通过；仅有既有
  evidence-review warnings。
- 与后续进入 `main` 的 Token 百分比功能发生 4 处文本冲突；解决时保留累计 Token /
  上下文上限展示与本任务布局，并在合并结果上重新通过完整验证。

## Result

- 右侧栏删除执行轨迹、原始数据、Request ID 和清空详情，只保留可滚动的最近会话；
  失败恢复按钮直接打开独立 Trace 审计页。
- 顶栏由 68px 压缩到 58px，活动会话标题、workspace 内边距、composer mode bar、
  textarea 和 footer 同步收紧，保留输入自动增高到 180px。
- 顶栏新增左栏显隐按钮；既有右栏按钮改为明确的最近会话语义。桌面两侧增加可聚焦
  separator，支持 pointer drag、ArrowLeft/ArrowRight、Home/End 和双击复位。
- `localStorage` UI 偏好新增 `sidebarHidden`、`sidebarWidth`、`inspectorWidth`；移动端
  始终保留底部导航，右栏继续使用带遮罩抽屉。
- README 中英文说明和个人面试手册已同步。无 API、Agent Run、持久化或迁移影响。
- 变更在 `codex/chat-layout` 完成验证，并在用户确认后提交、合并到本地 `main`；未部署。
