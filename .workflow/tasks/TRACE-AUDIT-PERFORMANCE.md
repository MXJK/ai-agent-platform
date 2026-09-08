# TRACE-AUDIT-PERFORMANCE

## Goal and scope

按用户指定实现 Trace 时间线分页、payload 按需生成、after 增量轮询。保持现有 API 和审计事实语义；不提交、部署或修改运行数据。

## Acceptance criteria

- 每页最多 100 条，翻页与筛选可用。
- 未展开 payload 不格式化；轮询复用未变化 DOM，保留展开状态。
- after 只来自持久事件；去重、切换 Run 与异步响应隔离有覆盖。
- 真实页面验证，并运行仓库要求的检查。

## Decisions

前端分页保留完整筛选和计数；首次下载仍全量，Run 详情仍完整，不宣称服务端分页。缓存当前 Run 持久事件，补录事件单独重建且不推进 cursor。静态资源版本更新。README、手册入口、Part 07 和 facts 同步能力边界。

## Verification

- Node：`node --test tests/test_trace_audit_ui.mjs tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs`，31 passed。
- 全量 pytest 最终：829 passed，12 skipped，108 subtests passed，1 failed。唯一失败为原有 `test_result_storage_never_follows_symlinked_parent`，期望 OSError，实际 ValueError；未修改该边界。
- compileall、node --check、git diff --check 通过；手册校验 24 Markdown / 44 capabilities 通过，有既有 evidence-review warnings。
- ego-browser 真实本地页面，同一 Run 4,729 条事件：修改前加载/渲染 2638ms，DOM 后代 75,631；修改后首次加载 259ms，100 行、1,599 后代。单次本机测量，非正式性能基准。
- 修改后未变化页复用渲染约 0.1ms；payload 初始为空、展开后生成，增量刷新保留相同 DOM 与展开状态；请求携带 after=667694。
- 下一页为 2/48，工具筛选回到 1/1、84 条；390px 视口 scrollWidth=390，分页宽 330px，按钮高 44px。
- Impeccable 检测器退化为 regex，报告既有页面样式建议，不能据此宣称完整样式审计通过。
- 静态资源版本断言已同步；最终全量运行不再出现该失败。

## Result

指定的三项修改已实现并完成浏览器与聚焦回归验收。首次事件历史仍全量获取，后端分页不在本次实现范围。完整 pytest 因上述任务外既有失败保持 blocked。没有提交或部署，没有更新 last_verified_commit。README 与手册已同步；手册文件受当前 .gitignore 排除，修改仅在本地保留。
