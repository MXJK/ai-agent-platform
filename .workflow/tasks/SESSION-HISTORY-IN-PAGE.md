# SESSION-HISTORY-IN-PAGE：会话记录页内查看

## Goal

点击会话记录页中的会话时，在本页展示该会话的消息、摘要和用量，不自动跳转对话工作台。

## Scope

- 复用现有消息详情区和 `loadSession` 的 `navigate` 选项，仅调整历史列表入口。
- 保留最近会话、URL 启动恢复和其他显式工作台入口的原有行为。
- 不变更服务端 API、持久化或会话配置恢复逻辑，不执行部署或迁移。

## Acceptance criteria

- [x] 活跃和已归档历史会话均在会话记录页展示消息、摘要与用量。
- [x] 最近会话等直接加载入口仍跳转到工作台。
- [x] 回归测试、必要校验与浏览器验证通过，说明文档同步。

## Decisions

- 使用已有 `loadSession(..., { navigate: false })`，不复制会话加载和渲染逻辑。
- 保留既有选中会话状态、草稿、配置恢复和流式期间切换保护。
- 文档有影响：同步 README、Interview Notes 首页、Part 07 和 facts 证据。

## Verification

- `.venv/bin/python -m pytest -q`：678 passed，107 subtests passed。
- `node --test tests/*.mjs`：10 passed；新增活跃/归档历史页内查看和默认工作台入口 3 项回归。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`、`git diff --check`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：24 个 Markdown 文件、43 项能力通过；
  保留相对旧 verified commit 的 evidence review warnings。
- Impeccable detector：改动目标无报告项；HTML parser 缺失，使用降级正则检查，
  不作为完整无障碍或对比度验证结论。
- ego-browser + 当前源码 + 临时内存 API（127.0.0.1:8765）：桌面 1280×900 点击
  活跃历史后 `currentView=sessions`、hash 为 `#sessions`，消息/摘要/用量可见、
  选中项正确且工作台隐藏；最近会话点击后回到 `chat`。
- 手机 390×844：关闭最近会话抽屉后筛选已归档会话，选中后保持 `sessions`，
  消息属于所选归档会话；摘要、刷新操作后仍留在本页；无页面横向溢出。
- 浏览器验收只使用临时内存样例，没有访问真实 Provider 或修改现有服务数据。
- 提交前在 `4b734933` 加本任务四个文件改动的独立快照中重新验证，排除并行任务
  未提交修改：pytest 为 678 passed、107 subtests passed；全部 10 项前端测试、
  JavaScript 语法和 compileall 均通过，确认 Python 从快照路径导入源码。

## Result

- 会话记录入口显式传入 `navigate: false`，沿用既有消息详情区；其他加载入口不变。
- 页面说明、README 和本地忽略跟踪的 Interview Notes/facts 已同步。
- 用户已授权在 `main` 提交推送；提交范围仅含本任务代码、测试、README 和工作流记录，
  不包含并行任务的修改。未部署、未执行迁移。现有 Docker 镜像没有挂载源码，需获用户确认后
  重建应用才会让当前运行服务生效；先前 MODEL-LATENCY-PROBES 记录的迁移与
  根运行栈验收前置条件仍须独立处理，不由本任务代为执行。
- 提交前验证覆盖独立候选快照；共享工作区仍有其他修改，不修改 `last_verified_commit`。
- 收尾检查出现其他并行任务的 project memory 服务/仓储/测试及任务说明变更，
  本任务未修改或撤销它们；上述全量测试是执行时的快照，不声称覆盖之后的并行改动。
