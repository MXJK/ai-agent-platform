# ADR 0001: MCP 客户端采用官方 Python SDK v2

- 状态：Accepted
- 日期：2026-08-12
- 任务：MCP-LIFECYCLE-V2

## Context

平台原有客户端自行实现固定 `2025-06-18` 的 newline-delimited JSON-RPC stdio，会话初始化、请求匹配和 `tools/list` 分页均耦合在一个类中。它没有 Streamable HTTP、当前协议协商、Server 级生命周期、缓存提示或 HTTP 安全边界。

截至本 ADR，MCP 当前稳定协议为 `2026-07-28`：当前 HTTP 路径为无会话的 Streamable HTTP，请求携带协议/路由元数据，列表结果支持 `ttlMs` 与 `cacheScope`。官方 Python SDK `2.0.0` 是稳定 v2，支持当前协议并能向下协商早期协议；旧 HTTP+SSE 已弃用但 SDK 仍提供兼容传输。

## Decision

平台精确锁定 `mcp==2.0.0`，让官方 SDK 负责协议模型、协商、标准 stdio/Streamable HTTP 传输、分页结果、取消信号和缓存提示。平台继续拥有以下策略，不把它们委托给 SDK：

- 每 Server 的同步生命周期门面、超时、重试、熔断、关闭、readiness 与脱敏诊断；
- `SecretStore` 引用解析和凭据分区；
- HTTP host allowlist、私网/明文显式开关、禁重定向、禁代理环境、Header 校验；
- stdio 最小继承环境、危险变量拒绝和 Secret 环境注入；
- MCP 注解到内部权限的 `PermissionResolver`；
- ToolRegistry 的稳定 call ID、错误码和审批语义。

现有 `MCPStdioClient` 保留为 `2025-06-18` 兼容适配器，仅由 `stdio_2025_06_18` 显式选择。旧 HTTP+SSE 仅由 `legacy_sse` 且 `legacy_compatibility=true` 显式选择；它不是默认回退路径。

## Consequences

- 平台不再自行追赶协议 wire schema 和生命周期变更，当前 stdio/HTTP 可共用同一 SDK 客户端语义。
- SDK 是新的运行时依赖，因此必须精确锁定并在升级时重新运行 fake Server 兼容测试。
- v2 SDK 自身为异步 API；平台使用每 Server 独立事件循环线程适配当前同步 ToolRegistry。该线程与连接由 Server 独占并在关闭时回收。
- 动态恢复的可选 Server 不在本阶段热注册新工具；显式 refresh 可恢复连接和目录缓存，但启动时不可用的工具需下一次运行时装配后进入 ToolRegistry。

## References

- MCP 2026-07-28 发布说明：<https://blog.modelcontextprotocol.io/posts/2026-07-28/>
- 官方 Python SDK v2 客户端：<https://py.sdk.modelcontextprotocol.io/client/>
- 官方 Python SDK v2.0.0：<https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0>
- 2025-11-25 Streamable HTTP/旧 SSE 兼容说明：<https://modelcontextprotocol.io/specification/2025-11-25/basic/transports>
