# ADD-DOMESTIC-MODEL-PROVIDERS: 新增国产模型 Provider（智谱 GLM、MiniMax、豆包）

## Goal

模型注册中心新增三个国产 Provider：`glm`（智谱）、`minimax`、`doubao`（火山方舟），
复用现有 OpenAI chat-completions 兼容层，支持连接配置、模型发现、注册画像、
SSE 流式对话、原生工具调用、token 预检估算与跨 Provider fallback。

## In scope

- provider 枚举扩展：`model_registry/models.py`、`schemas/model_registry.py`、
  `schemas/chat.py`、`schemas/session.py`、`core/config.py`（`llm_provider` 与
  `token_budget_fallback_provider` 选项）、`api/routes/evals.py`。
- 模型发现：`model_registry/discovery.py` 三家 OpenAI 兼容 `/models` 端点、
  GLM/MiniMax 前缀过滤与豆包三模型精确白名单、humanize/固定别名。
- 注册画像：`model_registry/profiles.py` 三家冷启动先验；`air` 归入 efficient 档。
- LLM 运行时：`integrations/llm.py` 新增 `OPENAI_CHAT_COMPLETION_ENDPOINTS`
  常量，`_decide_deepseek_tools`/`_stream_deepseek` 泛化为
  `_decide_chat_completions_tools`/`_stream_chat_completions`（DeepSeek URL、
  payload、usage source 逐字节保持不变，DSH XML 兜底保持 deepseek 专属）；
  token 计数与 fallback 资格判定改为 membership；`_deepseek_tool_messages`
  的 provider_items 回放扩展到全部 chat-completions provider。
- `integrations/native_streaming.py`：解析器分派与 usage/tool_calls 重组改为
  `CHAT_COMPLETIONS_PROVIDERS` membership。
- 前端：`static/app.js` `MODEL_PROVIDERS`、`static/index.html` 注册表单
  provider select；glm 默认最大输出 8192。
- PostgreSQL：新增后续 Alembic migration，扩展 Provider 检查约束；连接记录写入失败时
  删除新密钥或恢复旧密钥，避免密钥与数据库状态分裂。
- 测试：discovery 归一化、glm 流式、glm 原生工具调用与历史回放、国产
  provider 凭据/画像先验、迁移约束与密钥补偿。
- 文档：README.md 四处 provider 枚举、INTERVIEW_NOTES Part 02/04/05/07。

## Out of scope

- MiniMax 旧版 chatcompletion_v2 / GroupId 双密钥协议（只接 OpenAI 兼容新协议）。
- 三家更细粒度的 thinking/reasoning 开关与展示；仅保留防止 MiniMax 私有思考进入
  公开回答流所必需的 `reasoning_split` 和历史回放处理。
- `stream_options.include_usage` 推广到新 provider（保持 DeepSeek 专属，待逐家确认）。
- 新依赖/公共 API 语义变更。

## Acceptance criteria

- [x] `glm`/`minimax`/`doubao` 出现在连接页、注册表单、会话与评测 provider 枚举中
- [x] 三家可保存凭据、发现模型（或手动注册兜底）、注册画像
- [x] 豆包发现与手动注册仅允许 Evolving、2.1 Turbo、2.0 Lite 三个统一模型 ID
- [x] 三家走 chat-completions 层完成 SSE 流式与原生工具调用
- [x] PostgreSQL 约束允许三家 Provider，连接写入失败不会遗留或覆盖有效密钥
- [x] DeepSeek 既有行为不变（全量既有测试通过）
- [x] 文档同步并通过 `INTERVIEW_NOTES/validate.py`

## Decisions

- Provider ID 用 `glm`/`minimax`/`doubao`；显示名 Zhipu GLM / MiniMax / Doubao。
- 端点：智谱 `open.bigmodel.cn/api/paas/v4`、MiniMax 国内域
  `api.minimaxi.com/v1`、豆包 `ark.cn-beijing.volces.com/api/v3`。
- 不新写三套适配器：DeepSeek 路径参数化为通用 chat-completions 层，
  以既有 deepseek 测试为 characterization 套件。
- 三家 `/models` 列表端点为尽力而为：失败返回已消毒错误，UI 走手动模型 ID 注册；
  豆包手动兜底仍受精确白名单约束。
- 豆包产品白名单固定为 `doubao-seed-evolving`、`doubao-seed-2.1-turbo`、
  `doubao-seed-2.0-lite`。用户提供统一别名作为本应用范围；火山方舟公共模型页用于
  核验族级上下文/最大输出与工具、结构化输出能力，但页面中的带日期发布 ID、往期
  模型和其他模态模型不自动映射为本应用可注册模型。
- Agent 禁用工具的收尾轮次对新增三家直接省略 `tools`/`tool_choice`；DeepSeek 继续
  保持既有 `tools + tool_choice:none` 请求不变，避免智谱仅支持 `auto` 时拒绝最终回答。
- Chat Completions 原始 assistant 消息只回放给产生它的同一 Provider；跨 Provider
  fallback 重建标准工具调用消息，不透传上一家的私有推理字段。MiniMax 显式启用
  `reasoning_split`，流式 `reasoning_details` 只保存在 Provider transcript 中。
- Provider 代码枚举和 PostgreSQL 检查约束必须由后续 migration 同步演进，不能改写
  已经在现有环境执行过的 0013；跨 Secret Store/数据库的写入失败采用补偿恢复，
  不把两套独立存储描述为原子事务。
- 通用先验（USD/百万 token，冷启动）：glm (200k, 8k, 0.60/2.20, q0.78)、
  minimax (200k, 16k, 0.30/1.20, q0.76)、doubao fallback
  (128k, 16k, 0.15/0.60, q0.74)；三个豆包白名单模型的上下文/输出规格覆盖 fallback。

## Verification

- 复查聚焦回归：`.venv/bin/python -m pytest -q tests/test_llm_streaming.py
  tests/test_native_tool_calling.py tests/test_model_discovery.py tests/test_model_registry.py`：
  `95 passed, 41 subtests passed`。
- `.venv/bin/python -m pytest -q`：`775 passed, 134 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals` 通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py` 通过（EXIT 0）。
- `node --check ai_agent_platform/static/app.js` 与 `git diff --check` 通过。
- 对照智谱、MiniMax 与火山方舟当前官方 API 文档核验固定端点和 Function Calling
  契约；未使用真实用户 API Key 发起付费请求。
- PostgreSQL 注册修复聚焦回归：`.venv/bin/python -m pytest -q
  tests/test_model_registry.py tests/test_model_registry_migrations.py
  tests/test_model_discovery.py`：`37 passed, 10 subtests passed`。
- migration 离线验证：`.venv/bin/alembic upgrade
  20260831_0026:20260901_0027 --sql` 成功生成事务化 DDL，新约束包含全部八个
  Provider；`.venv/bin/alembic heads` 返回 `20260901_0027 (head)`。
- 修复后完整回归：`.venv/bin/python -m pytest -q`：
  `778 passed, 134 subtests passed`；`compileall`、Interview Notes 事实校验、JS 语法
  与 `git diff --check` 均通过。
- 豆包目录收敛聚焦回归：`.venv/bin/python -m pytest -q
  tests/test_api.py::ApiTests::test_serves_unified_chat_and_workspace_agent_frontend
  tests/test_model_discovery.py tests/test_model_registry.py
  tests/test_model_registry_migrations.py`：`39 passed, 9 subtests passed`；修复后完整
  回归 `781 passed, 133 subtests passed`，compileall、Interview Notes 校验、JS 语法
  和 `git diff --check` 均通过。

## Result

实现完成（2026-08-30）。新增 6 个测试：
`test_model_discovery.py::test_domestic_provider_catalogs_are_normalized`、
`test_llm_streaming.py::ChatCompletionsStreamingTests`（2 个）、
`test_native_tool_calling.py::test_glm_native_tool_decision_uses_chat_completions_layer`、
`test_model_registry.py` 凭据共享循环扩展 + 新增画像先验测试。
全量 pytest 通过；`stream_options` 保持 DeepSeek 专属。文档同步完成。
备注：本任务与 AGENT-EVIDENCE-EXECUTOR、AGENT-LAYERED-CONTEXT-COMPACTION 等
任务共享同一工作区，`.workflow/state.yaml` 由并行会话频繁更新，故未改写，
由人工汇总时统一收口。

2026-09-01 复查修复：发现原实现把 DeepSeek 的工具收尾和历史回放约束直接推广到
三家新增 Provider。智谱文档明确 `tool_choice` 仅支持 `auto`，原来的
`tool_choice:none` 会让 Agent 工具执行后的最终回答失败；MiniMax 默认会把 thinking
放进公开 `content`，且分离格式要求完整保存 `reasoning_details`。现已按 Provider
隔离这些契约，并补充 4 个测试场景（MiniMax 普通流、国产 Provider 收尾、跨 Provider
历史清洗、MiniMax 原生工具流）。README 与 Interview Notes 已经描述“国产 Provider
支持 SSE/原生工具调用”的用户契约，本次只修正内部协议兼容性和隐私边界，无需改写
能力说明；任务记录已补充实际边界与验证证据。

2026-09-01 PostgreSQL 注册修复：运行环境复现 `doubao`/`glm` 连接写入触发
`ck_model_connections_provider_supported`，原因是代码枚举已经扩展，但 0013 的历史
数据库约束仍只允许五个旧 Provider。新增 `20260901_0027` migration，在不改写历史
migration 的前提下重建检查约束，并新增迁移调用断言。服务层同时补偿“密钥先写、
连接后写”的跨存储失败：首次保存失败删除新密钥，更新失败恢复旧密钥。聚焦回归
`37 passed, 10 subtests passed`，全量回归 `778 passed, 134 subtests passed`；Alembic
离线 SQL、compileall 与 diff 检查通过。当前 Compose PostgreSQL 尚未执行 migration，
需人工确认后运行 `alembic upgrade head`；`.workflow/state.yaml` 继续保留并行 RAG
任务状态。

2026-09-01 豆包目录收敛：原实现只按 `doubao-` 前缀过滤，会把历史、内部或其他
Chat 不应暴露的条目带入注册页，且手动兜底可绕过发现过滤。现改为三个统一模型 ID
的共享后端目录，发现结果精确取交集，手动注册也复用同一白名单；Evolving 使用
1024k/256k、Turbo 使用 256k/256k、Lite 使用 256k/128k 上下文/最大输出画像，三者
标记工具调用与结构化输出。注册 Schema 尚无独立 thinking 能力字段，因此没有把
“默认 thinking、可关闭”写成未实现的注册能力或运行时保证。

2026-09-01 提交前隔离验证：从 Git 暂存索引导出独立快照，确认未包含并行的 Agent
最终模型预算、RAG pilot、架构图或工作流状态修改。快照内聚焦回归为
`100 passed, 40 subtests passed`，全量回归为 `768 passed, 133 subtests passed`；
`compileall`、`node --check`、`git diff --cached --check`、Alembic 离线 upgrade SQL
和 `alembic heads` 均通过。Interview Notes 在共享工作区重新校验通过（仅 evidence
review warnings）。`.workflow/state.yaml` 当前属于并行的 `AGENT-FINAL-MODEL-BUDGET-UI`
任务，本次不覆盖其状态；PostgreSQL 实例也未执行 migration，仍需人工确认后升级。
随后将本提交移植到最新 `origin/main` 的 `62bdd7d4`，合并双方缓存版本断言和原生工具
测试后再次隔离复验：聚焦回归 `102 passed, 40 subtests passed`，全量回归
`770 passed, 133 subtests passed`，其余编译、JS、Alembic 与 diff 检查继续通过。
