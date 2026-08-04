# RIGHT-INSPECTOR-RECENT-SESSIONS: 最近会话迁入右侧栏

## Goal

把桌面端“最近会话”从左侧主导航迁入右侧 Inspector，与运行详情组成统一的辅助
侧栏，并沿用右侧栏的展开/收起交互，减少左侧拥挤并保持会话切换可见。

## In scope

- 调整工作台 HTML 结构，把最近会话放入右侧 Inspector。
- 重排右栏为最近会话区与运行详情区，保证各自滚动和有限高度下可用。
- 更新右侧栏按钮、关闭按钮和区域的无障碍标签。
- 保留最近会话分组、点击续聊、查看全部与 SSE 切换保护行为。
- 桌面/窄屏/移动端响应式视觉回归、静态契约测试与文档同步。

## Out of scope

- 改变会话查询、排序、分页或持久化契约。
- 新增会话置顶、标签、文件夹或删除。
- 修改运行详情的数据内容、Agent 流程或模型注册中心。

## Acceptance criteria

- [x] 左侧主导航不再包含常驻最近会话区域。
- [x] 最近会话与运行详情位于同一个右侧栏，右侧栏可整体展开/收起。
- [x] 最近会话保留分组、激活状态、点击加载和“查看全部”。
- [x] 右栏在桌面和窄屏下不会挤压或遮挡主要操作，移动端仍可访问会话记录。
- [x] 右侧栏显示状态继续保存在设备级 UI 偏好中。
- [x] 前端契约、完整测试和项目规定验证通过。

## Decisions

- 使用统一右侧辅助栏，而不是新增第四列或浮动会话抽屉。
- 最近会话区设置有限高度并独立滚动，运行详情占用剩余空间。
- 右侧栏的顶栏按钮控制两个区域整体显隐，沿用已有 `inspectorHidden` 偏好。

## Verification

- `.venv/bin/python -m pytest -q`：208 passed，1 个既有 Starlette/httpx 弃用
  警告，另有 4 个 subtests 通过。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/alembic heads`：单一 `20260804_0014 (head)`。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：12 个 Markdown、28 项能力通过；
  evidence review warnings 仅提示当前未提交工作区变化。
- `git diff --check`：通过。
- 页面契约验证最近会话位于 Inspector 后代、左侧旧区域已移除；CSS 契约验证桌面
  高度分配、运行详情剩余空间与移动端底部导航避让。
- 已按 Browser 技能尝试 1280×720 和移动端视觉回归；本地地址被当前 Browser Use
  访问策略阻止，未绕过该边界，因此本次没有可声明为成功的截图级视觉验收。

## Result

- 左侧栏只保留七个主导航入口与工作上下文；最近会话迁入右侧“会话与运行”辅助栏，
  保留分组、激活态、点击续聊、“查看全部”和 SSE 期间禁止切换的既有行为。
- 右栏上方最近会话使用 `clamp` 有限高度并独立滚动，下方运行详情占用剩余空间；
  顶栏面板按钮和栏内关闭按钮同时控制两个区域，继续复用 `inspectorHidden`。
- 1120px 以下右栏沿用右侧浮层，900px 以下扣除底部导航与安全区高度，避免关键
  控件被遮挡；完整会话记录仍保留在移动端底部导航。
- README、产品工作台 Interview Notes 与 facts 索引已同步。未改变会话 API、
  持久化、Agent 数据流或模型注册中心行为。
