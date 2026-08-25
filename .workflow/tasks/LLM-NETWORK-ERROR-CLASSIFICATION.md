# LLM-NETWORK-ERROR-CLASSIFICATION: LLM 底层网络异常分类

## Goal

将 LLM HTTP 请求当前统一的超时/传输错误拆分为稳定、可观测的错误分类，使 Agent Run
能够直接区分 DNS、TLS、连接、代理、读写、协议和各阶段超时，同时保持既有重试配置兼容。

## In scope

- 普通 JSON 请求与 SSE 流式请求共享同一套 `httpx` 异常归一化。
- 为超时、DNS、TLS、连接、代理、读写/关闭、协议和解码失败提供稳定错误码与安全文案。
- 既有 `llm_timeout`、`llm_transport_error` 重试覆盖继续作用于对应的新细分类。
- 聚焦回归、完整测试及相关配置/运行行为文档同步。

## Out of scope

- 改变 Provider 路由、熔断阈值或默认重试次数。
- 暴露可能包含地址、证书、代理凭据或请求内容的原始异常文本。
- 部署、迁移、重启根 checkout Docker 栈或发起真实付费模型请求。

## Acceptance criteria

- [x] JSON 与 SSE 请求对同一种底层异常产生相同稳定错误码。
- [x] 连接/读取/写入/连接池超时不再统一为 `llm_timeout`。
- [x] DNS、TLS/证书、连接、代理、读写/关闭、远端/本地协议和解码错误可区分。
- [x] 错误文案不包含底层异常原文或敏感连接细节。
- [x] 新错误保持合理的 retryable 语义；本地协议和证书校验错误不可重试。
- [x] 旧分组重试键继续控制新细分类，新细分类也可单独覆盖。
- [x] 聚焦测试、完整 pytest、compileall、文档校验和 diff check 通过。

## Decisions

- 以稳定业务错误码作为持久化和 UI 诊断契约，不保存原始 `httpx` 异常消息。
- 新细分类先查精确重试键，再回退到 `llm_timeout` 或
  `llm_transport_error`，最后回退 `default`/`LLM_MAX_RETRIES`。
- DNS、TLS 与证书分类通过异常 cause chain 的类型判断，不依赖可能泄密且不稳定的
  异常字符串。

## Verification

- 回归测试先观察到统一 `llm_timeout` / `llm_transport_error`、解码错误未捕获和新重试键
  被配置校验拒绝，共 18 个预期失败。
- LLM/原生工具/配置/路由聚焦套件：`93 passed, 24 subtests passed`。
- `.env` / Compose 聚焦套件：`5 passed`。
- 功能分支完整测试：`663 passed, 107 subtests passed`。
- 合并后的最新 `main`（包含并行 Token 百分比任务）完整测试：
  `663 passed, 107 subtests passed`。
- `/Users/mxjk/programming/vs code project/ai-agent-platform/.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `INTERVIEW_NOTES/validate.py`：24 个 Markdown、43 项能力通过；唯一 warning 是既有
  `AGENT-SSE-LIVE-EVENTS.md` 相对事实基线的 evidence review，与本任务无关。
- `git diff --check`：通过。

## Result

普通 JSON、SSE 和 Google SDK 包装的 `httpx` 失败现在统一进入同一细分类器。Run 错误可
直接区分四种超时、DNS、TLS/证书、连接、代理、读写关闭、远端/本地协议、解码和未知
transport；只持久化安全文案，不回显底层异常原文。证书校验和本地协议错误不可重试，
其他瞬时网络错误保持可重试。

重试策略先匹配新错误码，再兼容回退到 `llm_timeout` / `llm_transport_error`，最后使用
`default` / `LLM_MAX_RETRIES`。双语 README、dotenv 示例和本地面试手册 Part 02/
`facts.json` 已同步。功能提交为 `3cb07d72`，合并提交为 `9cac81d2`；二者都已包含在
最新 `main` 并通过完整自动化验证。未部署、未迁移、未调用真实付费模型。
