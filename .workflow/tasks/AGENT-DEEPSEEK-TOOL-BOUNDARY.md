# AGENT-DEEPSEEK-TOOL-BOUNDARY: Agent 变更授权与 DeepSeek 工具协议加固

## Goal

修复建议/方案类请求被误判为代码变更、DeepSeek DSML 工具协议作为最终文本泄漏、
以及最终回答在协议校验前公开流式输出的问题，使任务授权、协议解析、执行权限和回答
提交保持相互独立且默认拒绝不明确的写操作。

## In scope

- 将请求语义分类与工作区修改授权分离，只有明确要求实施的请求才能获得写工具。
- 让任务形状、证据合同和冻结工具集使用服务端确定的修改授权。
- 在执行权限层再次拒绝未获任务授权的非只读工具调用。
- 在 DeepSeek provider 边界兼容 ASCII、标准全角 DSML 和双全角 DSML 变体。
- 最终回答阶段禁止执行任何文本协议调用，并阻止协议残留进入公开回答流。
- 覆盖建议请求、明确实施请求、完整/截断 DSML、流式分片和权限失败测试。
- 同步 README 与 Interview Notes 中相关能力边界。

## Out of scope

- 修改模型供应商或默认模型。
- 修改现有审批策略、沙箱隔离级别或部署配置。
- 自动执行提交、推送、部署或迁移。
- 修改并行进行的 RAG-BGE-M3 评测实现。

## Acceptance criteria

- [x] “建议添加什么小游戏”保持只读/建议任务，不要求 `applied_change`，写工具不可见。
- [x] “请直接添加扫雷并修改代码”仍被识别为已授权变更任务。
- [x] 未获修改授权的非只读调用在执行权限层 fail closed。
- [x] DeepSeek ASCII、标准全角和双全角 DSML 能在工具阶段转换为结构化调用。
- [x] 截断、未知工具、参数不合法或 finalization 中的 DSML 不执行且不泄漏到最终回答。
- [x] DeepSeek 协议标记和参数不进入公开增量；若已有普通前言，finalization 拒绝时发送
  `answer_reset` 并改用安全降级回答。
- [x] 聚焦测试、完整 pytest、compileall、Interview Notes 校验和 `git diff --check` 通过。

## Decisions

- 不再把模型返回的 `change_planning` 当作修改授权。服务端从用户原始请求冻结
  `request_mode` 与 `mutation_authorized`；仅明确要求实施、修改、修复的请求开放写工具，
  简短“继续”只继承最近一次明确用户授权。
- 在冻结工具 Profile 和执行时的有效工具视图两层过滤 `sandbox.apply_patch`、
  `sandbox.write_file`，审批策略不能扩大本次 Run 的修改权限。
- DSML 属于 DeepSeek provider 协议兼容层：只在工具阶段把完整闭合、当前工具池允许且
  参数通过 JSON Schema 的 envelope 转为结构化调用；finalization 的有效工具集为空。
- 流式层跨任意 chunk 边界暂存可能的 DSML 前缀。协议残留、截断协议、未知工具和非法参数
  均 fail closed；已公开的普通前言通过 `answer_reset` 撤销后输出确定性降级回答。
- 保留旧 checkpoint 兼容：缺少新授权字段的历史状态仍按旧 `change_planning` 语义恢复，
  新 Run 一律使用服务端授权字段。

## Verification

- `.venv/bin/python -m pytest -q tests/test_task_shaped_budgets.py tests/test_permissions.py tests/test_model_registry.py tests/test_native_tool_calling.py tests/test_agent_runtime_framework.py tests/test_agent_context_routing.py tests/test_agent_change_loop.py tests/test_agent_loop_characterization.py`
  - `142 passed, 44 subtests passed`
- `.venv/bin/python -m pytest -q`
  - `802 passed, 135 subtests passed in 44.12s`
- 从 `HEAD` 应用精确暂存补丁后的独立 worktree：`.venv/bin/python -m pytest -q`
  - `790 passed, 135 subtests passed in 44.95s`；未包含并行 RAG 工作区的新测试。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`
  - 共享工作区与独立暂存快照均通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`
  - `Validated 24 Markdown files and 46 capabilities`；证据变更复核提醒符合共享脏工作区预期。
- `git diff --check`
  - 通过。
- `.venv/bin/python -m json.tool INTERVIEW_NOTES/facts.json`
  - 通过。

## Result

- 已完成。建议/解释/方案请求不再因模型过分类而获得写能力；明确实施请求仍可进入受治理的
  修改循环。DeepSeek ASCII、单全角及双全角 DSML 在 provider 边界结构化，异常或最终回答
  中的协议文本不会执行，并由流式 reset + 安全降级阻止其成为最终公开答案。
- 已同步 `README.md`、根面试手册、相关 Part 与 `facts.json`。`INTERVIEW_NOTES.md` 和
  `INTERVIEW_NOTES/` 按仓库现有 `.gitignore` 为本地知识库文件，因此同步内容不出现在 Git diff。
- 共享工作区中既有 RAG/BGE-M3 改动未回退、未重写；本任务未提交、未推送。
