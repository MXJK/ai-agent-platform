# AGENT-LAYERED-CONTEXT-COMPACTION: 四层渐进式 Agent 上下文压缩

## Goal

仅针对单个代码 Agent Run 的模型可见工具转录，实现由轻到重的 L1 Artifact 外置、
L2 模型主动 Snip、L3 时间衰减 Micro-Compact 和 L4 Auto-Compact 全量结构化摘要；
保留普通 Chat 滚动摘要，不实现 Context Collapse。

## In scope

- 大型内置/MCP 工具结果立即外置为当前 checkpoint 继承的 Run Artifact。
- 受压力门槛与安全策略约束的 `agent.snip_context` 正常回合工具。
- 基于 checkpoint 时间戳、只处理幂等只读结果的 Micro-Compact。
- 同模型、无工具、九段严格 JSON 的 Auto-Compact 与确定性兜底。
- `/compact [关注点]`、Query Kernel/API/网页入口及 pending 请求持久化。
- 分层 context 事件、Usage Ledger 分类、README/Interview Notes/facts 同步。

## Out of scope

- Context Collapse 或读时投影。
- 普通 Chat 的摘要策略变更。
- 本机路径绑定的 Artifact body store、部署、提交或推送。

## Acceptance criteria

- [x] L1 对超限内置和 MCP 结果立即外置，精确分页回读并校验哈希。
- [x] L2 只暴露安全、稳定的 ContextBlock 候选；非法/过期选择 fail closed，保持工具配对。
- [x] L3 在 3600 秒边界和 Auto-Compact 前执行，保留最近 5 个完整可恢复结果。
- [x] L4 使用统一触发公式、九段 JSON、冻结 RunContext、逐字用户指令和最多三次熔断。
- [x] 每层重新计量，轻层释放足够空间后不进入重层。
- [x] `/compact` 在 running/paused 安全续跑，其他状态与重复请求返回冲突。
- [x] Memory/SQLite/PostgreSQL repository、SQLite v3 与 Alembic 0026 一致支持 pending 请求。
- [x] 分层事件不泄露正文；压缩调用与普通推理 usage 分列，缓存原始指标不重写。
- [x] rollback/fork/恢复、跨 Run 拒绝、旧 checkpoint、Prompt Cache 稳定前缀均有回归。
- [x] pytest、compileall、Node 测试、文档事实校验与 `git diff --check` 通过。

## Decisions

- Run Artifact 继续存进 LangGraph checkpoint 的 `artifacts` state，由 Memory/SQLite/
  PostgreSQL checkpointer 负责持久化；不把运行时契约绑定到进程本地文件路径。
- `agent.snip_context` 只在消息压力达到 60% 且至少存在两个安全候选时，作为动态 Prompt
  后缀与临时 ToolSpec 暴露；它不进入稳定 system/tool 前缀，也不单独调用模型。
- Micro-Compact 按冻结 Effective Tool Pool 判断历史结果的只读与幂等属性，因此恢复的旧
  checkpoint 即使不再向模型广告某工具，也能安全处理其历史结果。
- Auto-Compact 的摘要输入保存为 `context_transcript_*` Artifact；即使模型摘要失败，
  Artifact 仍随同 fallback 写进同一次 state update。成功后从当前 checkpoint 重建 system、
  任务、权限和工具 Profile，文件证据只保留路径、范围、哈希与短摘要，不重新读磁盘。
- 所有 role=user 消息逐字保留在摘要外；普通 Chat rolling summary 不变；Context Collapse
  明确不实现。原有 pair-safe eviction/fold/drop/truncate 仅作为最后确定性兜底。
- `/compact` 是 Run 控制命令，网页与 CLI 都走 `QueryCommand.COMPACT` / `QueryService`；
  不创建会话消息。PostgreSQL 0026 只随代码交付，本任务不执行数据库迁移。

## Budget table

| 层级 | 默认预算/门槛 | 保护边界 |
|---|---:|---|
| L1 大结果外置 | 单结果 2,000 Token | 完整 canonical JSON + SHA-256 Artifact |
| L2 Snip | 消息预算 60%，最近 4 个闭合组 | 仅只读幂等、模型选择后二次校验 |
| L3 Micro-Compact | 空闲 3,600 秒，保留最近 5 份 | 不处理写入、命令、失败、审批、Artifact 页面 |
| L4 Auto-Compact | 输出 4,096 + safety 2,048；最少回收 2,048 | 最多成功 3 次，连续失败 3 次熔断 |

## Verification

- 隔离提交快照执行 `.venv/bin/python -m pytest -q`：`755 passed, 125 subtests passed`。
- `.venv/bin/python -m compileall -q ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs`：
  `20 passed`。
- `.venv/bin/alembic heads`：唯一 head 为 `20260831_0026`。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 和 46 个 capability；
  exit 0。输出的 evidence review warnings 来自共享工作树中其他未提交改动，不是事实校验失败。
- `git diff --check`：通过。
- 定向回归覆盖 L1 精确 Artifact 回读、稳定 Snip ID/fail closed、3600 秒边界、Auto 前
  Micro 梯度停止、九段摘要、冻结 seed、三次失败熔断、running/paused 手动入口、SQLite v3、
  PostgreSQL row mapping、CLI/网页 Query Kernel 和事件正文隔离。

## Result

完成四层渐进式 Agent Run 上下文压缩。大结果立即外置，远古安全块可由模型在正常回合
Snip，空闲恢复先做本地 Micro-Compact，压力仍未解除时才调用同一模型生成九段摘要；每层后
重新计量，轻层成功不会进入重层。手动 `/compact [关注点]` 已贯通 API、网页、CLI、运行时
安全边界和三种 Run repository，并增加 SQLite v3 / Alembic 0026。

README、Agent/LangGraph、工具安全、持久化手册与 `facts.json` 已同步，旧的“不支持
`/compact`”说明已删除。未实现 Context Collapse，未修改普通 Chat 滚动摘要。共享工作树
原有的其他改动与两个文档删除保持原状；本任务未执行 PostgreSQL 迁移，提交与推送由当前
交付步骤完成。
