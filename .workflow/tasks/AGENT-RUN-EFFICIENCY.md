# AGENT-RUN-EFFICIENCY: 降低简单 Agent Run 的重复探索与 Token 消耗

## Goal

让普通仓库概览、代码诊断和小型修改任务按证据复杂度收敛，避免确定性预探索、
原生工具循环和模型重试重复消费同一批上下文，同时让运行指标如实呈现模型请求与重试。

## In scope

- 原生工具调用可用时限制种子探索轮数，并保留非原生/空工作区的既有兜底能力。
- 扩展通用仓库概览识别，轻量请求优先列举并读取入口文件。
- 原生工具循环复用预探索证据，抑制无范围的重复文件读取和重复仓库列举。
- `tool_output_truncated` 默认不在同一模型上重放长上下文；显式重试策略仍可覆盖。
- Agent 指标增加模型请求和模型重试，`retry_count` 包含模型网关重试。
- 增加聚焦测试，并同步 README、Interview Notes 与 facts 证据。

## Out of scope

- 修改 Provider 定价、模型目录质量分或用户已登记的模型能力。
- 删除原生工具循环、审批、ChangeSet、上下文压缩或 Usage Ledger。
- 以历史数据回填方式重算已有 Run 的工具或 Token 指标。
- 自动部署、重启生产容器或重放历史任务。

## Acceptance criteria

- [x] “仓库有哪些文件/说明项目结构”被识别为通用概览，最多两轮种子探索即可获得入口证据。
- [x] 原生工具任务的种子探索不再无条件跑满四轮；非原生空仓库仍按配置预算停止。
- [x] 原生工具循环不会再次执行已作为完整种子证据读取的同一路径，也不会重复根目录列举；明确行范围读取仍允许。
- [x] `tool_output_truncated` 默认直接进入候选模型 fallback；显式 `LLM_RETRY_POLICY_JSON` 可恢复同模型重试。
- [x] Agent result/API/UI 分开展示模型请求、模型重试和总重试，已有 Token 指标语义保持不变。
- [x] 聚焦测试、完整 pytest、compileall、前端语法和文档事实校验通过。

## Decisions

- 原生工具可用时，确定性探索只是种子证据阶段，最多执行两轮；后续缺口由同一原生循环按需补齐。
- 重复读取抑制只覆盖无 `start_line`/`end_line` 的整文件请求；更窄的定点复核不视为重复。
- 模型请求/重试按实际 Provider 调用计数；fallback 也属于模型重试，但 Token 仍以 Provider usage 聚合为准。

## Verification

- `.venv/bin/python -m pytest -q tests/test_workspace_agent.py tests/test_native_tool_calling.py tests/test_model_router.py tests/test_agent_runtime_framework.py tests/test_api.py`
  - `129 passed, 20 subtests passed`。
- `.venv/bin/python -m pytest -q`
  - `701 passed, 121 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`
  - 通过。
- `node --check ai_agent_platform/static/app.js`
  - 通过。
- `node --test tests/test_chat_message_ui.mjs`
  - `15 passed`。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`
  - `Validated 24 Markdown files and 43 capabilities`；仅输出基于旧 verified commit 的 evidence review 提示。
- `git diff --check`
  - 通过。

## Result

- 通用仓库概览识别覆盖实际中文问法；原生工具种子探索有证据后最多两轮，非原生兜底预算不变。
- 首轮原生循环复用已完成的根目录与整文件种子证据，并以 `seeded_evidence` 回灌重复调用；
  显式行范围和后续失败恢复仍可执行。
- `tool_output_truncated` 默认跳过同模型重放，显式错误码策略仍可覆盖；Provider usage 聚合语义不变。
- Agent 指标按实际 ToolResult 计算工具数量，并公开真实模型请求、模型重试和包含工具重试的总重试；
  API 与消息内指标同步展示，静态资源版本已更新。
- README、面试手册相关 Parts 与 facts 证据已同步；本次行为/API/可观测性有文档影响。
- 共享工作区中已有的多 Provider 扩展与 `.impeccable/` 未被回退且不纳入本提交；
  DeepSeek 方法兼容转发与该 Provider 扩展一同保留在未暂存工作区。
- 未提交、未推送、未重启容器，未重放或回填历史 Run。
