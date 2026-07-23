# HARDEN-GOOGLE-STREAMING: 修复 Gemini 流式截断与超时

## Goal

让 Gemini 3.5 流式对话在合理输出额度下稳定生成，并将思考配置、截断原因、真实 token 用量、超时和空闲心跳完整传递到前端。

## In scope

- 调整默认 LLM 输出额度，并通过真实 Gemini 3.5 请求验证合理值。
- 升级 `google-genai` 最低版本并支持 Gemini `thinking_level`。
- 在前端设置中允许选择默认、minimal、low、medium、high 思考等级。
- 检查 Google `finish_reason`，不再把 `MAX_TOKENS` 等异常终止误报为正常完成。
- 将 `thoughts_token_count` 纳入 usage 和指标。
- 为 Google SDK 请求应用 `LLM_TIMEOUT_SECONDS`。
- 为 SSE 输出增加心跳，避免上游静默期间连接无反馈。
- 增加配置、适配器、API 和前端契约测试。

## Out of scope

- 修改其他 provider 的模型语义或 API key 管理。
- 引入前端构建工具或框架。
- 部署、发布、数据库迁移、提交或推送。

## Acceptance criteria

- [x] 默认输出额度有真实 Gemini 3.5 诊断依据，长回答不再因默认思考量只输出几十个 tokens。
- [x] Chat 请求可携带可选 `thinking_level`，且前端可手动选择。
- [x] Google `MAX_TOKENS` 等非正常 `finish_reason` 会产生明确的流事件和用户反馈。
- [x] usage 同时呈现输入、可见输出、思考和总 token 数。
- [x] Google 请求使用配置的超时，SSE 在等待模型时周期性发送注释心跳。
- [x] 现有 fake、OpenAI、Anthropic 契约保持兼容。
- [x] 项目规定的 pytest、compileall、JavaScript 语法检查和 diff 检查通过。

## Decisions

- 用同一条 20 点 SSE 长回答提示实测额度：`low + 2048` 在 970 个可见输出 tokens、1074 个思考 tokens 时以 `MAX_TOKENS` 结束；`low + 4096` 和 `medium + 4096` 均以 `STOP` 正常结束。因此默认额度从 1024 提高到 4096，默认思考等级设为 `low`。
- 升级到 `google-genai>=2.14.0,<3.0.0`。该版本要求 Python 3.10 及以上，因此项目 Python 基线同步从 3.9 提升到 3.10；当前本地旧 `.venv` 不原地覆盖，避免中断仍在运行的 Python 3.9 开发服务。
- Gemini 3 默认应用服务端 `LLM_THINKING_LEVEL`，Chat 和 RAG 请求可用 `thinking_level` 覆盖；非 Gemini 3 模型不会隐式应用该默认值。
- Google SDK 通过 `HttpOptions.timeout` 使用毫秒级 `LLM_TIMEOUT_SECONDS`。SSE 生成器用后台生产线程和队列解耦上游阻塞，每逢 `SSE_HEARTBEAT_SECONDS` 空闲即发送 `: heartbeat` 注释。
- Google usage 将 `thoughts_token_count` 映射为独立字段，并计入 SSE 总量和 `llm_thoughts_tokens_total` 指标。现有持久化 `token_usage_records` 结构保持不迁移，仍存输入和可见输出 tokens。
- `STOP` 视为正常完成；`MAX_TOKENS` 映射为 `max_output_tokens` 错误，其他非正常 finish reason 映射为 `llm_finish_reason`。前端保留已生成文本并显示截断提示，不再误显示“已完成”。

## Verification

- 真实 Gemini 3.5 额度探测（`google-genai 2.14.0`）：
  - `low + 2048`：`MAX_TOKENS`，970 output、1074 thoughts、2076 total。
  - `low + 4096`：`STOP`，2074 output、725 thoughts、2831 total。
  - `medium + 4096`：`STOP`，2280 output、990 thoughts、3302 total。
- 修改后的 `llm.py` 真实 Gemini 回归：62 个 delta 后收到 usage 和 done；27 input、1560 output、1115 thoughts、2702 total。
- `.venv/bin/python -m pytest -q`：89 passed；保留 1 个第三方 LangGraph pending deprecation warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过；首次沙箱执行因 macOS Python 外部缓存目录权限失败，授权后原命令通过。
- `/Users/mxjk/.local/bin/python3.11 -m compileall -q ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。

## Result

Gemini 3.5 流式对话已从默认 1024 tokens/隐式思考升级为 4096 tokens/默认 low thinking，并支持前端手动选择思考等级。Google SDK 现在检查 finish reason、呈现真实思考用量、使用请求超时并通过 SSE 心跳维持空闲连接；额度截断会保留部分回答并给出明确反馈。依赖契约已升级到 google-genai 2.14 和 Python 3.10+，未执行部署、提交或当前运行中虚拟环境的破坏性替换。
