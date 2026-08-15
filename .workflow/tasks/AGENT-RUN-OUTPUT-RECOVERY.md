# AGENT-RUN-OUTPUT-RECOVERY: Agent 代码产出与终态恢复修复

## Goal

修复空 Workspace 的代码创建任务在审批恢复后不写文件、错误进入产物收集、异常被
`config` 未初始化错误遮蔽，以及终态被轮询读取回写为 `running` 的完整故障链；通过真实
前端完成创建、审批、ChangeSet 审查/应用和真实文件验证。

## In scope

- 修复 `CodingAgentRuntime.resume()` 异常路径，确保原始错误、checkpoint、ChangeSet 和清理
  可以稳定持久化。
- 移除 Agent Run 查询路径的写时副作用，防止并发轮询把终态覆盖为旧的运行态。
- 调整原生工具循环的 artifact checkpoint 判定，让失败的诊断命令不会被当成已完成验证，
  空 Workspace 仍可继续规划并调用 Sandbox 写入工具。
- 向模型明确 Sandbox 命令白名单和空 Workspace 创建约束，减少无效 `ls`/搜索调用。
- 增加恢复异常、终态竞态、失败诊断命令和空 Workspace 创建 ChangeSet 的回归测试。
- 同步 README、访谈手册及事实映射中受影响的运行状态和代码变更边界。
- 在真实 PostgreSQL/Celery/FastAPI 前端运行空 Workspace 创建任务，完成审批、ChangeSet
  应用并检查真实 Workspace 文件。

## Out of scope

- 数据库迁移、部署、提交、推送、PR 或合并。
- 放宽 Workspace 根路径、身份、审批、Sandbox 或 ChangeSet 权限边界。
- 默认绕过 ChangeSet 直接写真实 Workspace。
- 重写 Agent Graph、替换模型 Provider 或改变现有 HTTP URL/响应 Schema。

## Acceptance criteria

- [x] `resume()` 在 checkpoint 执行抛错时不再产生 `UnboundLocalError`，原始异常被记录，
      Sandbox 变更在清理前仍可捕获。
- [x] GET Run/latest/events 为只读；并发的旧 running 快照不能覆盖 `failed` 等终态。
- [x] 失败的只读/诊断 `sandbox.run_command` 不触发 artifact checkpoint；只有实际变更或
      变更后的验证才收集最终 Diff。
- [x] 空 Workspace 创建任务可从零调用 `sandbox.write_file`/`sandbox.apply_patch`，生成非空
      ChangeSet，而不是因无源码证据直接结束。
- [x] 模型可见的 `sandbox.run_command` 契约包含允许命令，且推荐使用 repository 工具列目录。
- [x] API/Worker 恢复、Run 事件、最终消息和 pending approval 状态保持一致且幂等。
- [x] 真实前端验证完成：新 Run 审批后生成 ChangeSet，用户界面可审查并应用，真实 Workspace
      出现可运行的贪吃蛇项目文件。
- [x] 相关专项测试、全量 pytest、compileall、前端语法和访谈手册校验通过。

## Decisions

- 产品状态存储是 Run 终态真相；checkpoint 只补充运行中 Trace，不得在读路径反向改写状态。
- `sandbox.run_command` 只有在已发生变更或明确验证阶段才计入最终验证/产物门槛；普通诊断失败
  返回模型继续规划并受既有 no-progress/consecutive-failure 预算约束。
- 真实 Workspace 仍只通过经审查的 ChangeSet 应用；本任务的 UI 应用属于用户明确要求的本地测试。

## Verification

- `.venv/bin/python -m pytest -q`：`386 passed, 49 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，校验 12 个 Markdown 文件和
  37 项能力；输出仅包含既有的 evidence review 提醒。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- 专项回归：Agent/Workspace/PostgreSQL/Sandbox/native tool calling 共 66 项通过。
- 真实前端 Run `run_b706aa1a371e`：空 Workspace 经计划审批后提出
  `sandbox.write_file`，生成并应用 ChangeSet `chg_310a82cf94ec`；ChangeSet 状态为
  `applied`，真实目录出现 `index.html` 与 `README.md`。
- 浏览器验收：通过 HTTP 打开生成的 `index.html`，实际执行开始、撞墙结束、第二局四向
  转向、空格暂停/恢复；页面最终显示“已暂停”，控制台 warning/error 为 0。

## Result

已完成。根因不是单点故障，而是失败诊断被误记为验证、零 mutation 允许文本终答、Google
fallback 的 Token 计数和工具历史不兼容、`resume()` 异常路径局部变量未初始化，以及 GET
Run 把 checkpoint 旧快照写回产品终态共同造成。实现现已只把成功 mutation 或变更后验证
视为收尾依据，并为 change task 增加成功 mutation 完成门；GET checkpoint overlay 保持只读，
内存与 PostgreSQL Store 都拒绝旧 running 覆盖终态，恢复路径保留原始异常。

文档影响已处理：README 中英文版同步运行状态、空 Workspace、命令契约和 Google fallback
边界；本地访谈手册及 `facts.json` 同步对应实现与测试证据。原故障 Run
`run_88b3fca28e26` 已依据持久化 `run_failed` 事件纠正为 `failed` 且清空 pending approval。
未执行提交、推送、部署或数据库迁移。
