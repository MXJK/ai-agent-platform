# ACTUAL-CONTEXT-TOKEN-USAGE: 展示真实模型上下文输入

## Goal

让对话输入区展示最近一次前台模型请求实际记录的 Prompt 输入 Token，避免把仅包含
会话消息/摘要的本地估算误称为完整上下文占用。

## In scope

- 从会话 Token 账本中选择最近一次用户可见的 Agent/Chat/RAG 请求记录。
- 以该请求的实际 `input_tokens` 作为上下文主指标；后台记忆提取、压缩、Embedding
  等记录不得覆盖它。
- 最近请求的模型窗口按该请求的实际 Provider/Model 解析，主占比使用完整模型窗口。
- 保留会话历史/摘要估算，但降级为明确标注的辅助信息。
- 修正上下文详情的键盘、触摸和窄屏可访问性。
- 同步 API 契约、README、Interview Notes、事实映射与回归测试。

## Out of scope

- 远程 Provider 调用、计费估算、生产数据库迁移或历史记录回填。
- 修改 Provider 原始 usage 数值或把多个模型请求累计值伪装成单次上下文占用。
- 提交、推送、部署或发布。

## Acceptance criteria

- [x] 主指标使用最近一次前台模型请求的 `input_tokens`，不再使用历史估算作为分子。
- [x] 后台操作晚于 Agent 请求完成时，主指标仍指向最近 Agent/Chat/RAG 请求。
- [x] 百分比只在最近请求与预算 Provider/Model 匹配时出现；否则只展示实际绝对值。
- [x] 历史消息估算、累计会话消耗和实际 Prompt 输入具有不同且准确的标签。
- [x] 上下文详情可由鼠标、键盘和触摸打开，窄屏不丢失信息。
- [x] 聚焦测试、完整 pytest、compileall、前端语法、文档校验和浏览器验收通过，
      或将非本任务失败精确记录为 blocker。

## Decisions

- “真实上下文占用”定义为最近一次已经提交给前台 Agent、Chat 或 RAG Ask 模型请求的
  `input_tokens`。发送下一次请求前，系统不能把尚未发生的 Prompt 称为实际值。
- 会话累计 Token 继续包含所有账本记录；记忆提取、压缩等后台操作不参与“最近前台
  Prompt”选择。
- 有最近前台 Prompt 时，模型窗口与输入预算都按该记录的实际 Provider/Model 解析；
  主百分比为 `input_tokens / context_window_tokens`，平台输入预算仅作辅助安全线。
- `budget_provider`/`budget_model` 只扩展 Token Usage API 响应，不进入通用领域上下文
  对象，避免污染上下文装配与 golden 快照。
- 原 `estimated_tokens` 保留为“历史消息/摘要估算”详情，不再作为完整 Prompt 分子；旧的
  `context_shares` 图例不再作为当前 Cogent 主指标展示。

## Verification

- `node --check ai_agent_platform/static/app.js`：通过。
- `node --test tests/test_chat_message_ui.mjs`：22/22 通过。
- Token Usage/API/context focused pytest：通过；characterization golden 在响应层隔离修正后
  通过。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：24 个 Markdown、45 个 capability 通过；
  evidence review warnings 为共享工作区的变更提醒。
- `git diff --check`：通过。
- 浏览器桌面实跑：Agent 记录输入 `2,135`，历史消息估算 `50`，UI 主指标显示
  `2,135 / 68,608 · 3.11%`。
- 浏览器记忆实跑：Agent 记录输入 `2,142`，随后 `cogent_memory_extract` 输入 `256`；UI
  仍显示最近前台 Prompt `2,142`，会话累计正确显示 `2,547`。
- 浏览器可访问性：键盘聚焦可打开、Escape 可关闭、移动端点击可打开；390px 下
  `scrollWidth == innerWidth == 390`。
- `.venv/bin/python -m pytest -q`：`881 passed, 12 skipped, 111 subtests passed`，仅
  `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent` 失败；
  该共享工作区既有问题期望 `OSError`，当前实现抛出 `ValueError`，不属于本任务修改。

## Result

- 功能、API、文档、聚焦验证和真实浏览器验收已完成。
- 仓库级关闭仍被上述既有 symlink 异常类型测试阻塞；本任务未扩大范围修改该文件安全
  契约。

## Follow-up: 移动端布局与百分比（2026-09-06）

### Problem

- 旧移动布局把 Workspace、Model 和 Token 压在同一行，390px 下 Token 仅约 158px。
- 移动端 CSS 直接隐藏圆形百分比，剩余 9px 文案省略预算分母，数值关系不清晰。

### Changes

- 移动端将 Workspace、Model 保持在第一行，Token 信息独占第二行。
- Token 行同时展示本会话累计、最近 Prompt 输入/预算，以及独立精确百分比徽标。
- 无预算显示“无预算”，模型与预算不可比较时显示“不可比”，不再静默空缺。
- 桌面继续保留圆环和完整输入/预算数值；静态资源版本同步更新以避免旧 CSS 缓存。

### Verification

- `node --check ai_agent_platform/static/app.js`：通过。
- `node --test tests/test_chat_message_ui.mjs`：22/22 通过。
- `tests/test_api.py -k serves_unified_chat_and_workspace_agent_frontend`：通过。
- 浏览器 390px：Token 行宽 350px，显示 `最近 Prompt 2202 / 6.9万` 与 `3.21%`；
  详情可点击打开，左右边界均在视口内。
- 浏览器 320px：Token 行宽 290px，百分比徽标完整显示，`scrollWidth == innerWidth == 320`。
- 浏览器 1200px：桌面三列布局和圆环保留，精确 `3.21%` 继续显示在完整标签中。
- Impeccable detector 以降级正则模式运行；报告均为既有页面级 advisory，无本次新增命中。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python -m pytest -q`：`873 passed, 12 skipped, 111 subtests passed`；失败项为
  既有 symlink 异常类型测试，以及并发 `REMOVE-OBSOLETE-COMPATIBILITY-CODE` 工作中的
  `QueryService` 测试导入缺失，均不在本次 UI 修改范围。
- `INTERVIEW_NOTES/validate.py` 当前受并发删除的 7 个旧 evidence path 阻塞；本次只调整
  响应式呈现，没有改变 Token 语义或文档契约。

### Result

- 移动端 Token 布局与百分比显示已修复并完成 320px、390px、桌面浏览器验收。
- 共享工作区在验收期间切换到 `REMOVE-OBSOLETE-COMPATIBILITY-CODE`，因此未覆盖其
  `.workflow/state.yaml` 状态，也未修改或回退该任务的并发文件删除。

## Follow-up: 自动路由下的 Prompt 预算同源性（2026-09-06）

- Docker 中最近 Prompt 为 `deepseek/deepseek-v4-flash`，但会话仍为 auto 偏好，旧 API
  把自动路由选出的 `doubao/doubao-seed-evolving` 预算返回给前端，所以 UI 正确地
  拒绝了跨模型百分比，显示“不可比”。
- Token Usage API 现在优先用最近前台 Prompt 记录的实际 Provider/Model 解析输入预算；
  没有前台 Prompt 记录时，才回退到当前会话选择。

### Verification

- `tests/test_model_registry.py -k session_token_usage_budget_uses`：2/2 通过；覆盖当前手动
  模型预算，以及最近 Agent Prompt 之后存在后台记忆记录时仍按 Prompt 实际模型取预算。
- `node --test tests/test_chat_message_ui.mjs`：22/22 通过；`node --check` 与 `compileall`
  通过。
- 完整 pytest：`757 passed, 12 skipped, 104 subtests passed, 62 failed`。失败主要来自并行
  `REMOVE-OBSOLETE-COMPATIBILITY-CODE` 将默认密钥后端改为 encrypted-file 后，既有测试
  未注入测试密钥路径而尝试写用户目录；另有并行删除后的配置/文档断言和既有 symlink
  异常类型失败，均未指向本次 Token 预算代码。
- Interview Notes 校验受并行任务已删除但尚未同步的 46 个 evidence/link 错误阻塞。
- Docker 镜像重建被 Codex 账户使用限额拒绝，实际容器与浏览器验收待显式授权或限额于
  2026-09-07 03:58 后恢复。

### Result

- API 的分子、分母已改为同一个最近前台 Prompt 的实际 Provider/Model；前端不再因 auto
  路由选出另一模型预算而错误显示“不可比”。
- 当前运行中的 Docker 仍是重建前镜像；只有完成镜像重建并重启 app 后页面才会出现新的
  百分比。本 follow-up 因容器验收未执行而不标记为完整关闭。

## Follow-up: 主占比改为完整模型窗口（2026-09-07）

- Token Usage API 新增 `context_window_tokens` 与 `reserved_output_tokens`，保留
  `budget_tokens` 作为平台输入预算。
- UI 主占比改为“最近 Prompt 输入 / 该模型完整上下文窗口”；平台输入预算、
  超出量与预留输出改为详情中的辅助信息。
- 对 `94,305 / 128,000` 场景，主百分比为 `73.68%`，不再因输入预算
  `68,608` 而将圆环显示为 `100%`。

### Verification

- `node --check ai_agent_platform/static/app.js`：通过。
- `node --test tests/test_chat_message_ui.mjs`：22/22 通过。
- `tests/test_model_registry.py -k session_token_usage_budget_uses`：2/2 通过。
- `tests/test_api.py -k serves_unified_chat_and_workspace_agent_frontend`：通过。
- Context budget 相关 Python 测试：62 项与 4 个 subtests 通过。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，24 个 Markdown 文件与 44 项能力
  校验成功。
- 完整 pytest：`818 passed, 12 skipped, 108 subtests passed, 1 failed`；唯一失败仍是既有
  symlink 父目录场景把 `ValueError` 断言为 `OSError`，不涉及本次 Token API/UI。
- 已重新构建 `ai-agent-platform-app` 镜像并重建 app 容器；容器健康。
- 真实浏览器桌面验收：目标会话显示 `94,305 / 128,000 · 73.68%`，圆环显示 `74%`。
- 真实浏览器 390px 验收：显示 `最近 Prompt 9.4万 / 12.8万` 与 `73.68%`，Token 区域
  宽 350px，页面 `scrollWidth == innerWidth == 390`；详情完整展示 128.0k 模型窗口、
  68.6k 平台输入预算、+25.7k 超出量与 8.2k 预留输出。

### Result

- 主百分比已按完整模型上下文窗口计算，平台输入预算仅作为辅助安全线展示。
- 静态资源版本更新为 `20260907-context-window-r3`，避免浏览器继续复用旧 JS/CSS。
- 共享工作区当前仍由 `REMOVE-OBSOLETE-COMPATIBILITY-CODE` 占用 workflow active task，
  因此未覆盖 `.workflow/state.yaml`，也未修改该并发任务的范围。
