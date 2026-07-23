# ADD-GO-GATEWAY: 增加 Go 高并发接入网关

## Goal

在不迁移 Python Agent/RAG 业务的前提下，增加一个可独立部署的 Go 接入网关，负责适合由 Go 承担的网络流量治理与 SSE 透明代理。

## In scope

- 使用 Go 标准库实现到 FastAPI 的单上游反向代理。
- 支持普通 HTTP 与 SSE 流式响应，不设置会截断长流的全局响应超时。
- 增加请求 ID、请求体大小限制、并发准入、可选全局速率限制、上游错误映射和访问日志。
- 增加存活/就绪探针、连接池配置、信号处理与优雅停机。
- 增加 Go 单元/集成测试、Dockerfile、本地配置示例和 README 使用说明。
- 保持网关无持久化业务状态，使其可运行多个副本。

## Out of scope

- 将 Agent、LangGraph、RAG、MCP、数据库访问或 LLM 业务逻辑迁移到 Go。
- 实现具体身份认证体系、分布式全局配额或服务发现。
- 部署、发布、数据库迁移或修改外部环境。

## Acceptance criteria

- [x] 网关可代理 FastAPI 的普通 HTTP 请求并保留查询参数和必要请求头。
- [x] SSE 数据能够逐段刷新到客户端，而不是等待完整响应后再返回。
- [x] 请求 ID 可验证、生成、回传并传递给上游。
- [x] 超大请求、并发过载、速率过载和上游故障返回明确状态码。
- [x] `/healthz` 和 `/readyz` 分别反映进程存活与上游就绪状态。
- [x] 网关支持环境变量配置和优雅停机，且可通过 Docker 构建。
- [x] Go 测试、Python 测试和 Python compileall 均通过。
- [x] README 清楚说明职责边界、本地运行方式与多副本限制。

## Decisions

- Go 只承担流量平面；Python 继续承担智能平面和数据访问。
- 首版采用单一静态上游，服务发现和负载均衡交给部署平台。
- 速率限制为单实例保护机制；跨副本全局配额需要未来接入 Redis 或专用限流服务。
- 不引入第三方 Go 依赖，降低构建与供应链复杂度。
- 复用仓库根目录已有的 `go.mod`，避免嵌套 module。
- 将客户端提供的转发头视为不可信输入，由边缘网关重新生成 `X-Forwarded-*`。
- 不设置 HTTP `WriteTimeout`，避免截断 SSE；单独限制请求头读取、上游连接、上游响应头与停机时间。
- Docker 构建上下文通过 allowlist 式 `.dockerignore` 限制为 `go.mod` 和 `gateway/`，不传入 `.env` 或 Python 虚拟环境。

## Verification

- `go test -race ./gateway/...`（Go 1.26.5 临时官方工具链）：通过；网关包覆盖代理、SSE 刷新、请求 ID、伪造转发头、普通/分块请求体限制、并发准入、限速、探针和上游故障测试。
- `go vet ./gateway/...`：通过。
- `go build -o /private/tmp/ai-agent-platform-gateway ./gateway/cmd/gateway`：通过。
- `docker compose config --quiet`：通过。
- `docker build -f gateway/Dockerfile -t ai-agent-platform-gateway:local .`：通过；构建阶段同时执行 Go 测试。
- `.venv/bin/python -m pytest -q`：82 passed，保留 1 个第三方 LangGraph pending deprecation warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `git diff --check`：通过。

## Result

新增可独立运行和容器化的无状态 Go 流量网关：透明代理 FastAPI HTTP/SSE，提供请求 ID、可信转发头、连接池、请求体/并发/速率保护、502 错误映射、存活与就绪探针、JSON 访问日志和优雅停机。补充环境变量示例、Compose gateway profile、最小 Docker 构建上下文和 README 职责边界。Python Agent、RAG、MCP、LLM 与数据访问逻辑未迁移，未执行部署、发布、迁移或提交。
