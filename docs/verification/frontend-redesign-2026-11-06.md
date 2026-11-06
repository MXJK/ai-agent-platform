# Cogent 分栏工作台改版验收 — 2026-11-06

## 实施范围

以根目录 `design.md` 的 C 分栏方向实施，沿用 Cogent 和原有九个 View。

- 208px 导航、48px 顶栏、默认 384px 当前 Run 详情；最近 5 个会话迁至左侧。
  导航可在 188–260px 调节，详情可在 320–480px 调节并约束主区至少 560px。
  拖动、方向键、Home/End 均沿用同一宽度约束。
- 深色、浅色、跟随系统使用相同几何布局及语义色板；HTML 在首次绘制前解析主题，
  原生控件设置 `color-scheme`。`cogent.appearance` 仅保存外观，本地存储失败时仍可切换。
- 对话采用平面消息流、独立滚动与底部 Composer；保留自动选模、路由策略、手动模型、
  fallback、权限、斜杠菜单、8,000 字符限制、输入法组合输入和历史阅读位置。
  暂停/继续与取消为独立操作，取消使用既有 `/agent/runs/{id}/cancel`，不会仅中断客户端显示。
- 右侧“变更 / Trace / 上下文”只投影控制器的当前 Session/Run/ChangeSet。
  当前变更文件点击定位 diff；历史 Run 保留就地展开完整 diff。
  Trace 复用 SSE/刷新状态，面板不新建轮询；恢复已结束 Run 时补读一次事件历史。
  展示最近 100 条，完整审计仍保留既有每页 100 条及增量游标边界。
- 模型 Provider/已注册模型、工具 Skill/MCP 使用 Tab；新建/编辑为原生模态弹窗，
  关闭与 Tab 切换不销毁草稿。模型页改正 Secret Store 文案。
  会话、知识库、记忆、评测、审计及运行中心统一紧凑标题、工具栏、列表、语义状态和代码主题。
- 记忆主题切换保护未保存草稿，刷新不自动覆盖新主题输入；文档详情关闭/切换保护元数据草稿。
  窄屏导航和详情采用覆盖抽屉，Tab/方向键/Escape 和关闭后焦点返回均保留。

没有新增品牌、虚构指标、分支管理能力、后端 API 或数据库迁移。
React 外壳、列表和 RunInspector 与命令式领域控制器并存；不是全量 React 状态迁移。

## 自动化结果

| 检查 | 结果 |
| --- | --- |
| `npm --prefix frontend run build` | TypeScript 与 Vite 通过，静态资源已更新；主 JS chunk 约 523 kB，Vite 提示超过 500 kB 建议阈值 |
| `npm --prefix frontend test` | 20 passed / 5 files |
| `node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs tests/test_trace_audit_ui.mjs` | 31 passed |
| `.venv/bin/python -m pytest -q` | 开放本机临时端口后：882 passed、12 skipped、1 failed；108 subtests passed |
| `.venv/bin/python -m pytest -q tests/test_api.py` | 最终静态资源与 API 契约回归：39 passed |
| `.venv/bin/python -m compileall ai_agent_platform tests evals` | Passed |
| `.venv/bin/python INTERVIEW_NOTES/validate.py` | Passed：24 Markdown / 46 capabilities；保留证据漂移提醒 |
| `git diff --check` | Passed |

唯一 Python 失败是已有的
`tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`：
测试期望 `OSError`，现行 managed-file reader 将符号链接路径拒绝转换为 `ValueError`。
相关后端代码本轮未修改；[11 月 4 日验证记录](frontend-react-2026-11-04.md)已记录同一失败。
普通沙箱另会拒绝 HTTP hook 测试绑定本机端口；放开本地端口后的全量结果如上。

新增前端测试覆盖主题跟随/显式覆盖/存储不可用、草稿保留、跨会话详情隔离、终态事件保留、
面板约束和偏好、Tab 键盘操作、记忆草稿保护、审批失败后的重试输入、结构化追问、
暂停/继续/取消的独立控件及取消 API、新 Run 清除旧 ChangeSet。原有控制器测试继续覆盖消息语义、SSE 恢复与审计增量刷新。

## 浏览器证据与范围

使用 Ego 浏览器连接临时 FastAPI 服务，`llm_provider=fake`、本地 embedding、memory Secret Store，
工作区、会话和存储均隔离于临时目录。没有保存真实 Provider 凭据，也没有产生付费模型请求。

已通过真实页面操作验证：

- 注册并选择临时工作区，新建会话、发送提示，观察 fake 回答与完成态 Run。
- 新建会话清除旧 Run 绑定；切回原会话后恢复 12 条真实事件及未发送草稿。
- 九个入口完整可达；两种主题与页面切换后保留未发送 Composer 草稿。
- 刷新会话后 Trace 恢复真实持久事件；移动端详情可切换 Tab，Escape 关闭后焦点回到详情按钮。
- 390px 导航抽屉可打开、选择页面并关闭，详情遮罩位于面板下方。

- Skill 编辑弹窗在 390px 下可用，关闭 → 切换 MCP / Skill → 重新打开后草稿仍在。
- 修复长 Skill 目录路径撑开移动页、知识库表格负边距溢出，以及窄屏详情遮罩盖住面板的问题。

两种主题共 42 项布局检查全部通过：桌面对话覆盖 1440×900、1280×800、1920×1080；
全部九页覆盖 390×844 与 720×450；页面与主区均无横向溢出。
另已逐页检查两种主题下 1200×677 的九个页面。

[逐项布局结果](workbench-2026-11-06/layout-checks.json) ·
[深色桌面](workbench-2026-11-06/desktop-dark.png) ·
[浅色桌面](workbench-2026-11-06/desktop-light.png) ·
[深色移动端](workbench-2026-11-06/mobile-dark.png) ·
[浅色移动端](workbench-2026-11-06/mobile-light.png)。截图含临时 fake-provider 数据；
右侧粉色悬浮按钮属于浏览器翻译扩展，不属于产品界面。

浏览器快捷键未改变 Ego 的页面布局缩放，因此 200% 检查使用 1440×900 的等效
720×450 CSS 视口，不能声称完成了浏览器原生 200% 缩放验收。

手册校验通过 24 份 Markdown 和 46 项能力；校验器仍提示相对旧证据基线的源码漂移。
本次已更新前端能力描述与证据路径，没有将这次 UI 工作记为全部后端能力重新核验。

## 能力与验证边界

- fake-provider 发送验证不等于真实 Provider 的长时间运行、生产流量或容量验收。
- 审批/追问/控制动作有真实 React DOM + 控制器的自动化验证；不将模拟响应测试描述为
  浏览器中真实模型完成了写入审批、取消竞态或文件回滚。
- 设计提到附件，但改版前 Composer 没有通用文件附件上传契约/控件，本轮没有伪造上传入口；
  既有知识库文档上传和斜杠能力保留。不能把参考图的附件示意当作已实现业务能力。
- 模型、权限和工作区摘要来自当前选择与既有计数；未提供的实际 Prompt 文件清单不展示。
- Trace 首次恢复仍下载完整事件历史；最近 100 条是显示边界，不能称为后端分页或恒定内存。
- 面试手册已同步在本地；仓库原 `.gitignore` 忽略该手册，不改变其跟踪策略。
- 未提交、合并、发布、部署或执行数据迁移。
