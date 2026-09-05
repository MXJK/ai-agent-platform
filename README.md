# Cogent

**简体中文** | [English](README.en.md)

Cogent 0.2.0 是现有平台外壳上的 Agent 内核替换式重构。普通对话统一进入
QueryService/CogentRuntime；模型管理、认证、Workspace、Run/Event、ChangeSet、
MCP 管理和独立 RAG 保留。内部 Python 包仍是 `ai_agent_platform`。

当前重构支持本地使用；实现与验收证据见
[重构任务](.workflow/tasks/COGENT-AGENT-REFACTOR.md)。以下部署命令需要操作者审阅迁移后执行，
不代表本次已部署或已迁移真实数据库。

## 目录

- [Docker 单实例启动](#docker-单实例启动)
- [CLI、REPL、SDK 与进程入口](#clireplsdk-与进程入口)
- [分层运行时配置](#分层运行时配置)
- [Skill 发现与 slash command](#skill-发现与-slash-command)
- [主要能力](#主要能力)
- [Gemini 协议支持](#gemini-协议支持)
- [模型路由](#模型路由)
- [模型白名单与 Token 预算](#模型白名单与-token-预算)
- [代码 Agent 流程](#代码-agent-流程)
- [工作区 API](#工作区-api)
- [项目记忆](#项目记忆)
- [独立知识库](#独立知识库)
- [存储与数据库迁移](#存储与数据库迁移)
- [保留的扩展实现](#保留的扩展实现)
- [验证](#验证)

## Docker 单实例启动

当前唯一公开支持的产品运行路径是单用户、单实例 Docker Compose 自托管。常驻服务只有
FastAPI/Web UI、PostgreSQL 和 Qdrant；Alembic 迁移由一次性 `migrate` 服务执行。后台
Agent 任务复用进程内有界队列，不启动 Go 网关、Redis、Celery Worker 或 Adminer。
App 镜像使用 `requirements.self-hosted.txt`，不安装当前拓扑不会加载的 Celery/Redis、
Chroma 和 OS keyring；会安装 Sentence Transformers/Torch，因为官方 Compose 的默认
文档 embedding 是 `BAAI/bge-m3`。

```bash
cp -n .env.example .env
mkdir -p workspaces
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml ps
```

浏览器入口是 <http://127.0.0.1:8000>。Compose 只把 App 发布到宿主机 loopback；
PostgreSQL 和 Qdrant 只在私有 Compose 网络可达。`WORKSPACE_HOST_PATH` 指向用户信任的
宿主机目录，并统一挂载到容器 `/workspaces`；页面复用现有网页目录浏览器登记项目，
不会尝试从容器打开 macOS Finder。

默认 `RUNTIME_PROFILE=custom` 组合现有 PostgreSQL Repository、Qdrant Vector Store 和
`in_process` TaskQueue。独立项目记忆和用户记忆开启；Provider API Key 只从模型管理页
录入，Compose 将其加密保存到私有 `app_state` 持久卷，PostgreSQL 只保存不透明引用。
持久化模型目录首次启动为空：请在模型管理页保存 Provider 连接，注册并启用支持工具调用的模型。Fake 仅用于内存测试。

`AUTH_MODE=single_user` 忽略请求体与 Header 中的用户声明，所有请求都归属固定
`SINGLE_USER_ID=owner`，并允许 owner 管理模型和 MCP。该模式没有公网认证能力；不得把
App 端口改为 `0.0.0.0`、局域网或公网地址。Sandbox 命令在 App 容器内执行，当前产品只
支持用户自己信任的仓库；代码 Agent 默认直接修改登记的源码根，不再让用户选择执行位置。

持久化安装从旧本地/可信网关身份切换到 `single_user` 时，启动过程会为固定 owner 补齐
所有既有工作区（包括软移除记录）的管理员关系，同时保留旧成员和项目数据。旧记录若
保存的是宿主机绝对路径，仍需在页面中重新关联到容器可见的 `/workspaces/...` 路径。

兼容的 SQLite、Celery、Go gateway、OIDC 和多 Worker 实现仍保留在代码中，供测试与后续
演进使用，但不属于当前 MVP 的支持部署面。旧 `start-local.sh` 仅转发到同一个 Compose
入口；可用 `./scripts/start.sh --check` 做静态配置检查。

已有安装升级时，先备份 PostgreSQL，再执行：

```bash
docker compose -f docker-compose.yml build app migrate
docker compose -f docker-compose.yml run --rm migrate
docker compose -f docker-compose.yml up -d app
docker compose -f docker-compose.yml logs --tail=80 app
```

`migrate` 必须成功到 `20260903_0028`；只看到健康接口成功并不代表数据库已升级。
启动命令显式指定基础 Compose 文件，避免本机开发 override 的旧镜像或热重载设置干扰。
首次安装也会自动等待迁移成功。保留数据时停止用 `docker compose -f docker-compose.yml down`，不要加 `-v`。

第一次使用：打开页面 → 模型管理中添加连接和模型 → 工作区登记 `/workspaces/项目目录` →
新建会话并选择该工作区 → 输入任务。默认逐次确认，写入/命令出现审批后再选择是否执行。
可先发送 `/help`、`/status` 或让 Agent 阅读 README；`/plan` 用于先做只读计划。
容器中的用户 Cogent 配置与文件记忆保存在 `cogent_user_state` 卷，重建 App 会保留。

## CLI、REPL、SDK 与进程入口

在源码目录安装开发入口：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps -e .
source .venv/bin/activate
```

入口为 `cogent` 和 `cogent-api`，也支持 `python -m ai_agent_platform.cli`。
本机入口读取本机配置，不会自动连接 Compose 内部数据库；官方持久化使用推荐前面的 Docker 网页路径。
容器内可直接运行 `docker compose -f docker-compose.yml exec app cogent --workspace /workspaces/项目目录`。

```bash
cogent --workspace /absolute/path/to/project
cogent --workspace /absolute/path/to/project --print "解释入口结构"
cogent-api --host 127.0.0.1 --port 8000
```

默认启动 Textual TUI；非交互输出、兼容 REPL 和 AgentSDK.query() 同样经过 QueryService。
Web 通过 POST /api/v1/agent/runs 与 Run SSE 订阅相同事件，/api/v1/chat/stream 返回 404。
审批、追问、取消、暂停和压缩共享运行时能力，不维护另一套 CLI Agent 循环。

共享命令为 /help、/status、/clear、/compact、/mcp、/memory、/session、
/skill（兼容 /skills）、/tools、/permissions、/resume、/plan、/review、
/rewind、/sandbox；/exit 仅用于 CLI 本地退出。

## 分层运行时配置

官方 Compose 使用 `RUNTIME_PROFILE=custom`，并在服务环境中锁定下面这组单实例产品
组合，避免用户 `.env` 意外切回另一套拓扑：

| 边界 | 当前 MVP |
| --- | --- |
| 结构化事实、Checkpoint、模型注册表 | PostgreSQL |
| 文档与项目记忆向量 | Qdrant |
| Agent、压缩和记忆任务 | API 进程内有界队列 |
| 身份 | 固定 `single_user` owner |
| Workspace | `/workspaces` bind mount + `direct` 源码修改 |
| Sandbox | App 容器内本地执行，仅限可信仓库 |

`local` 与 `production` 命名 profile 及其 Adapter 暂时保留为兼容实现，不再是公开启动
路径。Celery 的既有 fail-fast 校验仍要求全部事实使用共享 PostgreSQL/Qdrant；当前
Compose 不选择 Celery，因此不需要 Redis 或独立 Worker。

`ConfigResolver.resolve_process()` 只解析 `Settings` 默认值 → 用户 JSON → 环境变量/
`.env` → 显式入口覆盖，不从服务进程 cwd 自动发现项目文件。默认用户路径是
`~/.config/ai-agent-platform/config.json`；`AI_AGENT_PLATFORM_USER_CONFIG` 可改写该路径。
`AI_AGENT_PLATFORM_PROJECT_CONFIG` 仍兼容，但只表示显式、进程级受控项目输入，不参与
Workspace 自动发现。

创建 Run 时，`ExecutionContextFactory` 先从 Workspace catalog 取得并鉴权主 Workspace
root，再由 `ConfigResolver.resolve_workspace()` 读取该 root 下的
`.ai-agent-platform/config.json`。因此项目配置不会随 API/Worker/CLI 的 cwd 改变，也不会
泄漏到另一个 Workspace。配置文件只使用 Python 标准库 JSON，根对象分为
`process_security`、`runtime`、`project_session`；未知分区、未知字段和错误类型会在对应
进程解析或 Run 创建边界 fail closed：

```json
{
  "process_security": {
    "workspace_allowed_roots": ["/srv/code"],
    "mcp_allowed": true,
    "skills_allowed": true,
    "skill_allowlist": ["review"],
    "tool_allowlist": ["file_symbol_locator", "repo.search_code"],
    "sandbox_mode": "docker",
    "sandbox_allowed_commands": ["python", "pytest"]
  },
  "runtime": {
    "llm_model": "example-model",
    "session_token_budget": 50000
  },
  "project_session": {
    "project_instructions": ["先运行受影响的测试。"],
    "enabled_tools": ["file_symbol_locator"],
    "mcp_enabled": false
  }
}
```

用户文件和进程环境可以建立进程策略；项目文件不能修改数据库、认证、API Key/
Secret 后端、允许根目录、真实写入开关或 MCP 配置路径。沙箱模式、镜像、命令白名单、
超时、输出上限、workspace parent 和生命周期都在进程启动时构造执行器，因此全部属于
`process_security`，项目配置不能产生只在快照中变化、实际执行却不生效的覆盖值。项目层
仍可收紧审批，并从进程允许的工具/MCP 集合中选择更小子集，但不能越过
`mcp_allowed=false`、`skills_allowed=false` 或进程 allowlist。进程 `tool_allowlist` 只在启动
时裁剪全局能力上限。每个 Run 再从进程 Registry 建立带 base/local/MCP 来源和 namespace
的不可变 `ToolCatalog`，由共享 `ToolPoolBuilder` 将项目 `enabled_tools`、Agent/模式、模型
能力、Workspace role、中央 display deny、显式 deny、Sandbox 和 Skill 依赖求交为
`EffectiveToolPool`，不修改源 `ToolRegistry`。Skill 的启用与选择属于用户全局注册表及
进程 allowlist，不再由 Workspace 配置分区。未知工具、大小写冲突、保留 namespace
冒用、尝试突破进程上限或非法恢复都会 fail closed。非 Provider 凭据的环境变量
继续兼容既有无前缀名称和 `.env`，以及 `SESSION_REPOSITORY`/`AGENT_RUN_STORE` 的旧
回退关系；Provider API Key 不再是配置字段，相关环境变量会被忽略。存储回退只在目标 Store 支持该
后端时生效，因此 local profile 的 SQLite 不会传播给只支持 memory/PostgreSQL 的模型
注册表或 ChangeSet Store。Session 与 Run Store 还必须选择同一后端，否则配置解析阶段
直接失败，避免把 Query 原子启动问题拖到运行时装配。新的
`AI_AGENT_PLATFORM_<FIELD>` 命名空间提供未知字段检查。

解析结果是冻结的 `ResolvedConfig`，包含兼容 `settings` 视图、三个冻结分区以及每个
最终字段的来源。`Settings.from_env()` 仍返回 `Settings`；需要来源或入口覆盖时直接使用
`ConfigResolver`。`ResolvedConfig.safe_snapshot()` 是日志、Run 快照和配置诊断支持的
带 schema version、逐字段 source/detail 的序列化视图；新 Run 写 schema v3，并保存配置
内容哈希、catalog/pool contract version、脱敏规范化摘要、hash、选择 provenance 与安全
排除诊断。摘要只含工具定义和 Schema 的 hash，不保存 Secret、Header、连接凭据或完整
敏感参数。API/Worker 的 `RuntimeContainer` 只保存进程基线快照；结构化日志也会
递归遮蔽嵌套 API Key、Secret、Token 和带凭据连接串。

## Skill 发现与 slash command

发现优先级为项目 `.cogent/skills` > 用户 `~/.cogent/skills` >
兼容读取 `~/.ai-agent-platform/skills` > 内置。旧 Skill 不自动移动，
现有 CRUD API/UI 保留，但新写入目标为 ~/.cogent/skills。

支持 .md、SKILL.md、skill.yaml + prompt.md、参数替换、热加载及 slash command。
只支持 inline，mode/context=fork 返回不支持；不会降级执行，也不会读取 .agents/skills。
Codex 自身 Skill 和项目运行时 Skill 保持隔离。
Skill 内容是声明式上下文，不能授予工具、提升权限或越过 Workspace 边界。

## 主要能力

普通对话、代码任务、Skill、MCP 和 slash command 共用 Cogent Run，不再提供快速对话模式。
会话页面显示文本、活动、工具结果、审批、追问和可折叠思考区；模型选择界面保留原有行为。
知识库/RAG、项目记忆和个人记忆保留独立页面与 API，不注入 Agent。

### 持久化会话与重启恢复

会话历史、配置、模型偏好、用量和归档规则保持；Run 的状态与事件流由平台持久化。
新运行使用版本化 Cogent 状态和快照；旧运行只读。工具执行账本按 call ID 去重。
工具批次中的审批反馈、暂停恢复文本和追加指令会持久保存，等整批工具结果返回后再送给模型，
保证 `assistant(tool_calls) → tool × N → user` 连续配对。发送前会拒绝缺失、重复或孤立结果；
旧快照中已经齐全但被 user 消息隔开的结果，可在恢复时重新排序，不补造结果或重做工具。
当前通过了部分 SQLite 恢复测试，不应将其扩大为全存储、全中断点的生产可靠性承诺。

### 全局模型注册中心

这是一个本地单用户应用，所有工作区共享同一个全局模型注册中心。模型管理页可以
统一配置 OpenAI、DeepSeek、Anthropic、Google、智谱 GLM、MiniMax 和豆包（火山方舟），再为每个
Provider 注册多个模型。
API Key 只能写入：PostgreSQL 仅保存 secret reference，密钥值进入选定的 Secret Store。
原生本机运行使用操作系统钥匙串；官方 Compose 使用私有持久卷中的 Fernet 密文与
`0600` 随机本机密钥。Provider 环境变量和 `.env` 不再作为启动凭据，密钥绝不会由
API 返回。升级后若连接仍是遗留 `env:*` 引用，页面会要求重新录入，而不会读取环境变量。

保存 Provider 后，模型管理页会调用该 Provider 的官方模型列表接口，过滤出当前
API Key 可用的文本生成模型供用户选择，并标记已经注册的条目。注册只需要选择
Provider/模型、模型最大输出 token 以及是否启用、是否参与自动路由；显示名称、上下文、
能力和冷启动路由画像由 Provider 元数据与后端先验合成。发现接口暂时没有目标模型时仍可手动
填写模型 ID，但不再要求用户填写质量、价格或延迟。豆包是显式白名单例外：发现与手动注册
都只接受 `doubao-seed-evolving`、`doubao-seed-2.1-turbo` 和
`doubao-seed-2.0-lite`，避免把方舟目录中的历史、内部或其他模态条目暴露为 Chat 模型。

统一输入框会暴露供 Chat、整次代码 Agent 运行（包括恢复执行）和 RAG Ask 使用的
会话偏好：

- 自动 `smart`、`quality`、`cost` 或 `latency` 路由；
- 手动首选模型、显式回退开关，以及按延迟排序的自定义选择器；选择器显示精确毫秒值
  和绿/黄/红延迟等级（`≤1000 ms`、`≤3000 ms`、`>3000 ms`）；
- 每个模型的可用性、业务请求样本数、最近更新时间，以及观测到的首 Token 和总延迟
  P50/P95；模型卡片还可对精确模型执行一次固定短提示测速。

`smart` 不会额外调用一次 LLM，而是生成确定、可解释的任务画像。简单任务更重视
成本和延迟，困难任务更重视后端质量画像。后台嵌入、会话压缩和记忆抽取继续使用各自
独立的服务策略。模型管理页可见时每 60 秒只读刷新一次，Chat 或 Agent 请求结束后也会
刷新，不会因此调用模型。模型卡片的“测试延迟”和 Provider 级兼容连接测试会发送一次
固定最短回复请求；结果持久化在独立的探测统计中，不增加业务样本，也不改变路由 P50。

周期探测默认关闭。显式设置 `MODEL_PROBE_INTERVAL_SECONDS` 为不小于 60 的秒数后，只有
API 进程会在每个完整间隔后检查启用模型；如果该模型在间隔内已有真实成功或失败请求则
跳过。探测按模型串行、同一模型禁止并发，运行时关闭时停止。启用周期探测会产生 Provider
费用，固定探测结果仅用于状态与对比，不参与 `smart`/`latency` 排序：

```dotenv
# 默认关闭；例如每 15 分钟检查长期无真实流量的模型
MODEL_PROBE_INTERVAL_SECONDS=0
```

## Gemini 协议支持

如需使用 Gemini，必须在“模型管理”中保存 Google API Key、发现并注册目标模型；
`.env` 不选择或导入 Google/Gemini 模型。Provider API Key 不会从 `.env` 或进程环境
读取。`LLM_MAX_OUTPUT_TOKENS`、`LLM_THINKING_LEVEL`、`LLM_TIMEOUT_SECONDS` 和
`SSE_HEARTBEAT_SECONDS` 只控制通用运行策略，不承担 Provider/Model 注册。

Gemini 3 请求的 `thinking_level` 支持 `minimal`、`low`、`medium` 或 `high`；API 请求
和既有会话配置仍可覆盖服务端默认值，但个人工作区管理界面不提供这个选项。当
Provider 暂时没有输出时，SSE 会发送心跳；思考 Token 会单独统计。如果 Gemini 以
`MAX_TOKENS` 结束，接口会返回明确的 `max_output_tokens` 错误，
而不是把它当作正常完成。

## 模型路由

`LLMClient` 把模型选择委托给独立的 `ModelRouter`。每个请求按以下顺序处理：

```text
会话自动策略或手动首选模型
→ 用于 smart 路由的确定性任务复杂度画像
→ 请求能力要求
→ 能力筛选（工具调用、结构化输出、上下文窗口）
→ smart / quality / cost / latency 排序
→ Provider 健康度和熔断器筛选
→ 选中模型与路由 Trace
→ 按错误代码执行重试策略和有界退避
→ 调用 Provider；重试耗尽且首个 delta 前失败时可尝试下一个跨 Provider 候选
```

持久化注册中心是运行时模型表，更新后无需重启即可影响路由器。PostgreSQL 产品运行时
不会从 `LLM_PROVIDER`、`LLM_MODEL` 或 `LLM_MODEL_CATALOG_JSON` 导入候选；空注册中心
保持为空，直到本机 owner 从前端注册 Provider 和模型。静态/default catalog 只保留给
显式的 `memory` 测试或临时开发运行时。

通过前端发现并注册的模型由后端生成质量/成本等级和数值路由先验；它们不是在线
质量评测或 Provider 官方实时报价。延迟只在冷启动时使用后端先验，一旦存在成功请求样本，运行时
目录和 `latency`/`smart` 排序就自动使用被动观测的总延迟 P50。`quality` 最大化
后端质量画像，`cost` 最小化估算输入/输出成本；确定性的平局规则保证测试可复现。

旧客户端显式传入的 `provider`/`model` 仍是硬筛选条件。会话中的手动选择属于首选
模型，因此在 `fallback_enabled=true` 时仍可回退。

Provider 健康状态只在当前进程中维护。系统根据有界的近期结果窗口和连续失败次数
开启熔断；恢复超时后进入 `half_open`，探测成功后关闭熔断。Chat 会发送 `route` SSE
事件，并显示 `model_route` Trace 节点，其中包含所有候选、拒绝原因、健康快照、选择
原因、失败记录和最终模型。

首个非空文本 `delta` 之前的事件会被缓冲，所以 429、超时或传输失败可以安全地跨
Provider 回退。已经发出首个文本 delta 后，失败会以 `partial_response=true` 返回，
不会在其他模型上重放。

网关的可靠性策略借鉴 LiteLLM Router 的“错误分类、重试、冷却和 fallback 分离”设计，
但继续使用项目现有的轻量 `LLMClient`，不新增 LiteLLM 运行时依赖。默认情况下，
所有 `retryable` 错误仍使用 `LLM_MAX_RETRIES`；可用严格 JSON 映射按稳定错误代码
覆盖，例如：

```dotenv
LLM_MAX_RETRIES=2
LLM_RETRY_POLICY_JSON={"rate_limit":0,"llm_timeout":2,"llm_transport_error":2,"llm_server_error":1,"default":2}
LLM_RETRY_BASE_DELAY_SECONDS=0.2
LLM_RETRY_BACKOFF_MAX_SECONDS=2
LLM_RETRY_AFTER_MAX_SECONDS=60
LLM_RETRY_JITTER_SECONDS=0.1
```

底层网络错误会以安全文案和稳定细分类持久化，不暴露主机、证书、代理或请求内容：
连接、读取、写入和连接池等待超时分别使用 `llm_connect_timeout`、
`llm_read_timeout`、`llm_write_timeout`、`llm_pool_timeout`；DNS、TLS、证书校验、
普通连接和代理分别使用 `llm_dns_error`、`llm_tls_error`、
`llm_tls_certificate_error`、`llm_connection_error`、`llm_proxy_error`；连接后的读、写、
关闭、远端/本地协议和响应解码分别使用 `llm_read_error`、`llm_write_error`、
`llm_close_error`、`llm_remote_protocol_error`、`llm_local_protocol_error` 和
`llm_decoding_error`。证书校验和本地协议错误不可重试，其余上述错误默认可重试。

这些细分类都可作为 `LLM_RETRY_POLICY_JSON` 的精确覆盖键。为保持兼容，未配置精确键时，
四种超时先回退到 `llm_timeout`，其他网络错误先回退到 `llm_transport_error`，再回退到
`default` / `LLM_MAX_RETRIES`。此外仍支持 `rate_limit`、`llm_server_error`、
`token_count_failed`、三种工具输出纠错错误和 `llm_provider_error`；未知键、负数或非整数
会在启动时失败。
普通 HTTP 与 SSE 的 429/5xx 响应都会读取 delta-seconds 或 HTTP-date 形式的
`Retry-After`。建议值为正且不超过 `LLM_RETRY_AFTER_MAX_SECONDS` 时优先采用；
否则回落到有上限的指数退避。抖动同样受上限约束，避免错误 Header 长时间占用工作线程。
Route Trace 的 `retries` 数组记录候选模型、错误代码、重试序号、有效预算、等待秒数和
`retry_after` / `exponential_backoff` 来源。首个非空 delta 之后仍不会等待或重放。

当前 Compose 使用 `MODEL_REGISTRY_STORE=postgres` 持久化模型目录，并以
`MODEL_SECRET_BACKEND=encrypted_file` 将页面录入的 Provider API Key 加密写入私有
`app_state` 持久卷。密文文件和随机本机密钥均为 owner-only；数据库、API、日志和浏览器
存储都不持有明文。固定的 `single_user` owner 可以执行模型连接保存、测试/发现和模型
增删改，调用方提交的身份 Header 不会改变 owner。`memory` 仅用于测试，原生运行仍可用
OS keyring；多节点部署需要外部 KMS/Vault，而不是共享该单机文件后端。

## 动态模型准入与 Token 预算

持久化模型注册中心是聊天与 Agent 的唯一运行时模型准入来源。通过前端模型管理页
注册并启用模型、同时启用对应 Provider 连接后，该模型即可用于手动选择和自动路由，
无需在 `.env` 维护 Provider/Model 白名单。未注册、已停用的模型或已停用的 Provider
连接仍会在计数和生成请求发出前被拒绝。

PostgreSQL 产品运行时不会读取 `LLM_PROVIDER`、`LLM_MODEL` 或
`LLM_MODEL_CATALOG_JSON` 形成启动候选，也不会维护第二份静态准入策略；前端的
注册、启用和停用状态立即影响运行时目录，无需重启。

模型注册记录分别保存上下文窗口和最大输出 token。Cogent 普通模型调用使用
`LLM_MAX_OUTPUT_TOKENS`，截断恢复请求注册能力上限但最多 64K；上下文空间和 Usage Ledger
仍可进一步下调。旧规划/变更/最终回答阶段预算不再决定 Cogent 的模型循环。

会话和工作区预算会统计归属于对应范围的所有账本记录：

```dotenv
SESSION_TOKEN_BUDGET=100000
WORKSPACE_TOKEN_BUDGET=1000000
TOKEN_BUDGET_ACTION=reject
```

`0` 表示关闭对应范围。使用 `reject` 时，如果请求无法至少保留一个输出 Token，系统
会在模型生成前拒绝请求；允许执行的请求也会把 Provider 输出上限限制到剩余预算。
异步 Agent Run 进入 failed 并记录预算错误，已经接收的用户消息保留。

使用 `downgrade` 时，应配置一个会被导入注册中心的低成本组合：

```dotenv
TOKEN_BUDGET_ACTION=downgrade
TOKEN_BUDGET_FALLBACK_PROVIDER=openai
TOKEN_BUDGET_FALLBACK_MODEL=gpt-5-nano
```

超预算调用会继续在回退模型上执行，并暴露 `budget_decision=downgraded`、请求模型和
实际模型元数据。这是软预算，回退模型的用量仍会继续累积。预算预检读取已经提交的
账本记录，项目不会宣称具备严格的跨进程预算预留能力。

路由与治理属于同一条流水线：路由器先筛选、排序已注册、已启用、符合能力要求且健康
的模型；每次真实的计数或生成尝试前，选中的 Provider/Model 都必须再次通过注册中心
可用性和 Token 预算预检。预算降级目标必须重新通过目录注册状态、能力与健康校验。
跨 Provider 回退会为新候选重复执行对应 Provider 的计数与授权，并且仍仅限于首个
delta 之前。

OpenAI、Anthropic 和 Google 使用各自的计数 API；DeepSeek 与国产 Provider（智谱 GLM、
MiniMax、豆包）在预检阶段采用保守估算，最终仍以 Provider 返回的实际用量写入账本。

## 代码 Agent 流程

1. QueryService 冻结身份、会话、模型、配置、执行工作区和工具池；同会话只允许一个活动或挂起 Run。
2. Cogent 构造稳定提示，读取项目指令、inline Skill 和独立文件记忆。
3. canonical conversation 经 RegistryClient.stream 进入已有 Provider/模型管理层。
4. 完整响应落库后做批次权限预判；AskUserQuestion 和审批都形成持久等待点。
5. 相邻只读调用受并发上限约束，写入/命令串行。每个结果按 call ID 和参数哈希记账。
   Bash 描述保留实际可用的命令白名单；它只执行单个程序及参数，不解释管道或命令串联。
   文件查看和目录检索使用 ReadFile、Glob、Grep，审批不能把白名单外命令变为可执行命令。
6. 没有工具调用时结束；默认无语义迭代总配额，取消、暂停、超时、预算和权限仍生效。

输出截断最多恢复三次，耗尽为 `partial/output_limit_exhausted`；上下文溢出在可压缩时恢复，
否则 `partial/context_overflow`。大结果保存在 `.cogent/sessions/<run>/tool-results`，只允许
读取登记且哈希匹配的结果。压缩保留近期工具对，失败保持原历史。

权限模式：default、acceptEdits、plan、bypassPermissions；规则来自用户、项目和项目本地文件。
硬拒绝不能被 bypass 或旧审批覆盖。OS sandbox 尝试 Seatbelt/bubblewrap，默认禁网；不可用时
Bash 不能因此自动获准。官方容器没有 bubblewrap 时显示不可用，仍需命令审批和平台限制。

MCP 自动选择 eager、小目录全量加载；大目录在支持的官方 Anthropic 模型上使用原生搜索，
其他模型使用 dispatch/ToolSearch。也可用 `COGENT_MCP_LOADING` 显式设置；不兼容 native 自动
降级为 dispatch。原生搜索传递 deferred schema，普通工具执行仍受同一权限控制。
加载集合存入 Run 状态并在恢复/后续会话重建。

Cogent 项目记忆根为 `.cogent/memory`，用户记忆按身份隔离于 `~/.cogent/memory/.users/<hash>`。
MEMORY.md 限制 200 行/25KB。自动整理有 24 小时、至少 5 会话、扫描节流及锁门控；内部维护 Run
只处理这两处记忆，不获得普通 Workspace、Bash 或 MCP 权限。验证后的模型响应持久化，恢复不重调模型；
写入前核对整个计划和旧哈希，整理会去除重复/失效索引项，未引用的主题文件不会擅自删除。

/plan 仅允许读取与写当前计划；ExitPlanMode 要审批。/review 只读审查 Git diff。
/rewind 先列出文件快照和已完成 Run：`/rewind <run-id> conversation` 可纯对话回退，
文件回退使用 snapshot ID 与 all/files 模式。预览后审批，冲突拒绝，追加逻辑分支而不删除历史。
FileHistory 最多保存 100 个快照及前后像。

Run、事件和版本快照在同一数据库事务保存；SQLite 使用进程文件锁，PostgreSQL 使用 advisory lock。
启动恢复重新投递未完成 Run，审批/追问保持等待，已确认但未完成的 resume 从落库边界继续。
旧 `langgraph-v1` 记录只读，非终态在 API 中投影为 blocked/legacy_runtime_retired，不改原记录。

## 工作区 API

`workspace_id` 只允许字母、数字、`_`、`-` 和 `.`。根路径必须是规范化绝对路径，
解析符号链接后仍要位于 `WORKSPACE_ALLOWED_ROOTS` 内。同一个规范化根路径只能登记为
一个工作区；使用另一个 ID 重复登记会返回 `409`。列表与详情响应还会返回 `status`、
`role` 和 `can_update`，供客户端区分路径可用性和当前用户能力。官方 Compose 固定
`WORKSPACE_ALLOWED_ROOTS=/workspaces`，并把 `WORKSPACE_HOST_PATH` 映射到该目录；选择列表
默认隐藏点目录和越界符号链接。

容器无法代表浏览器用户打开宿主机 Finder，因此官方 Compose 固定
`NATIVE_DIRECTORY_PICKER_MODE=disabled`。前端直接使用受控网页目录浏览器浏览
`/workspaces`；任何选中路径仍必须通过 `WORKSPACE_ALLOWED_ROOTS` 校验。旧的 loopback
与 trusted-local-gateway 原生选择器实现仍留在代码中，但不属于当前产品入口。

Workspace 根路径属于实际执行 Agent 的文件系统。若未来控制面部署在云端而代码仍在
用户电脑上，需要本地 Agent/桌面 companion 负责目录授权和执行；云端服务自身的 Finder
无法选择浏览器所在电脑的目录。

```bash
curl -X PUT http://localhost:8000/api/v1/workspaces/project \
  -H 'content-type: application/json' \
  -d '{"root_path":"/absolute/path/to/project"}'

curl http://localhost:8000/api/v1/workspaces
curl http://localhost:8000/api/v1/workspaces/project
curl -X DELETE http://localhost:8000/api/v1/workspaces/project
curl http://localhost:8000/api/v1/workspaces/project/token-usage
curl http://localhost:8000/api/v1/sessions/{session_id}/token-usage
```

前端把当前运行工作区、用户默认工作区和新登记草稿作为三个独立状态：草稿输入不会
提前改变 Agent 上下文，登记成功后才显式激活；单个工作区 Token 用量加载失败也不会
把登记结果误报为失败。Agent 模式没有可用工作区时会在请求发送前阻止提交。

`single_user` 模式下所有会话和工作区操作都归属固定 owner；请求体、`X-User-ID` 与
`X-Authenticated-User` 不能切换身份。底层 Workspace RBAC 和 Worker 二次授权实现仍
保留，但多用户角色协作不是当前 MVP 的支持能力。启动时固定 owner 会获得所有持久化
工作区（含软移除记录）的管理员关系；这是单用户兼容接管，不删除既有成员，也不自动
改写旧根路径。

工作区响应中的 `available` 表示已保存路径当前是否仍可读取。`DELETE` 是软移除：它只
让工作区退出可选列表，不删除本地文件，也不级联删除历史会话、用量或项目记忆；再次
以相同 ID 注册即可恢复。相同路径恢复保持原 revision，只有根路径真正变化时才递增
`workspaces.revision`，旧 revision 的记忆不再参与检索。管理员可以明确确认一条旧
记录，把它复制到当前 revision；历史记录本身不会变化。每一条 `agent_runs` 都保留
运行开始时捕获的 `workspace_root`。

启动 Agent 运行：

浏览器在展示 Slash 能力前先读取有效目录：

```text
GET /api/v1/agent/composer-capabilities?conversation_id=sess_xxx&workspace_id=project
```

响应只包含本次上下文可调用的 Skill command 与 MCP 工具。显式调用时，POST body 可在
普通字段之外携带 `skill_name`、`skill_arguments` 或 `preferred_tool_name`；其中工具
偏好不是授权，也不会绕过工具池和审批。

```bash
curl -X POST http://localhost:8000/api/v1/agent/runs \
  -H 'content-type: application/json' \
  -d '{
    "conversation_id":"sess_xxx",
    "workspace_id":"project",
    "message":"解释 WorkspaceService 的路径校验",
    "focus_files":["ai_agent_platform/services/workspace_service.py"]
  }'
```

运行创建后可使用以下生命周期接口：

```text
GET  /api/v1/agent/runs/{run_id}
GET  /api/v1/agent/runs?limit=50
GET  /api/v1/sessions/{conversation_id}/agent/runs/latest
GET  /api/v1/agent/runs/{run_id}/events?after={cursor}
GET  /api/v1/agent/runs/{run_id}/events/stream?cursor={cursor}
GET  /api/v1/agent/runs/{run_id}/checkpoints?limit=100
POST /api/v1/agent/runs/{run_id}/checkpoints/{checkpoint_id}/restore  # 历史兼容路由，不再执行旧图恢复
POST /api/v1/agent/runs/{run_id}/pause
POST /api/v1/agent/runs/{run_id}/continue   {"message":"paused Run 的可选补充方向"}
POST /api/v1/agent/runs/{run_id}/continue   {"answers":[{"id":"问题 ID","selected":["候选项"],"custom":null,"skipped":false}]}
POST /api/v1/agent/runs/{run_id}/steer      {"message":"新的执行方向"}
POST /api/v1/agent/runs/{run_id}/cancel
POST /api/v1/agent/runs/{run_id}/resume     {"approved":true,"feedback":"审批说明"}
GET  /api/v1/agent/runs/{run_id}/changes
POST /api/v1/agent/runs/{run_id}/changes/reject {"change_set_id":"chg_xxx"}
POST /api/v1/agent/runs/{run_id}/changes/apply  {"change_set_id":"chg_xxx","patch_sha256":"<64 hex>"}
```

轮询、SSE 与 QueryService 异步迭代器读取同一个 append-only EventStore，
由 AgentEventEncoder 编码并使用 sequence cursor 续传。新运行输出 answer_delta、
thinking_delta/thinking_completed、tool_started/tool_result、permission_required、
compact_completed、retry、turn_completed 和 usage；reasoning_summary 仅用于旧记录。
终态以完整 Run 结果为准，用户归属校验和最终助手消息 source_run_id 幂等约束保留。

新 Cogent 结果不返回 context_route、selected_knowledge_base_ids 或 context_sources，
请求不得选择知识库。历史 JSON 仍可只读展示。checkpoint 接口用于查看快照，
旧图 restore 不再提供执行能力；回退必须走 /rewind 的预览、审批及哈希复核。
Run 状态继续返回服务端冻结的执行模式与 execution root，不能逐 Run 覆盖。

ChangeSet 是模型工具审批之后的独立审计与恢复边界。读取/拒绝/历史应用/回滚也进入同一
`PermissionResolver` 的 Workspace role/root 判定，同时保留服务内纵深校验。它保存不可
截断补丁、SHA-256、变更文件、
写前/写后哈希、source/execution root、workspace revision、Git/分支/worktree、验证摘要
和状态。viewer 可查看，editor 才能拒绝或回滚。新 `direct`/`worktree` 记录捕获即为
`applied`，不能二次应用；回滚复核摘要和写后哈希，冲突不会覆盖用户的新修改，重复回滚
幂等。旧版 ready ChangeSet 保留 apply 兼容。系统不会自动 commit、push、建 PR、merge
或部署。

### 实时源码工具

- `repo.find_files`：按文件名或路径片段定位文件；
- `repo.list_files`：列出工作区相对目录下的路径；
- `repo.search_code`：使用遵循 `.gitignore` 的 `rg`，不可用时回退到 Python；
- `repo.read_file`：读取 UTF-8 文件的指定行区间，并返回真实行号和哈希。

这些工具会拒绝绝对路径、目录穿越、逃逸符号链接、二进制或超大文件、依赖和构建
目录、真实 `.env`、私钥及常见凭据文件。文件列举会逐项跳过符号链接、越界解析目标
及 `.venv-*` 等忽略目录，不会因单个不安全条目让整个目录发现失败。

### 执行工作区与命令隔离边界

每个 Run 在读取项目指令和构建工具池之前就确定一个执行工作区，并把来源根、模式、
执行根、Git HEAD、分支和清理策略冻结到 RunContext v4。仓库读取、文件写入、命令、
状态和 Diff 始终使用同一个执行根；登记的来源根只负责授权边界，不能由模型或请求参数
替换。当前产品固定使用 `direct`；底层保留的三种审计模式是：

- `patch_only`：把普通、非敏感文件复制到每 Run 临时目录，终态导出 ChangeSet 后删除；
- `direct`：直接在登记源码根中读取、写入和执行，修改立即对其他本机进程可见；
- `worktree`：只接受干净 Git 仓库，从冻结 HEAD 创建并保留 `codex/` 分支 worktree，
  当前源码检出保持不变。

真实 `.env`、凭据、私钥、符号链接、不可读路径、Socket、FIFO 和其他特殊文件都会被
拒绝或跳过并记录。Cogent 的副作用先经过批次权限与精确审批。
写文件接受可选 `expected_sha256`，所有已存在目标继续校验
Run 基线和当前哈希；补丁还校验路径、上下文和写前哈希。写入使用同目录临时文件、`fsync`
和原子替换，原内容先持久化到服务端 mutation journal。`direct` 对同一 Workspace 实施
单写者锁，外部编辑和并发 Agent 冲突都会停止而不覆盖内容。

当前产品只支持 `direct`：代码 Agent 经精确工具审批后直接修改登记源码根，终态
ChangeSet 记录已经发生的写入并提供冲突安全的回滚。页面和 Run 请求都没有执行位置
选择。官方配置如下：

```dotenv
CHANGE_SET_STORE=postgres
LIVE_WORKSPACE_WRITES_ENABLED=true
AGENT_WORKSPACE_DEFAULT_MODE=direct
AGENT_WORKSPACE_ALLOWED_MODES=direct
CHANGE_SET_APPLY_MODE=patch_only  # 仅供旧 ChangeSet apply 配置兼容
CHANGE_SET_MAX_FILES=100
CHANGE_SET_MAX_PATCH_CHARS=1000000
CHANGE_SET_WORKTREE_PARENT=
CHANGE_SET_BRANCH_PREFIX=codex/
```

`patch_only` / `worktree` 与历史 apply 仍保留为持久化兼容代码，但官方 Compose 把唯一
允许模式锁定为 `direct`，不会向产品用户暴露模式选择器或逐 Run 覆盖字段。

`direct`/`worktree` 的 ChangeSet 是已发生写入的审计记录，捕获时即为 `applied`，不会
再次应用；对话会明确显示实际源码根或 worktree 路径/分支，并可调用
`POST /agent/runs/{run_id}/changes/revert`。回滚重新校验补丁摘要和写后文件哈希，冲突时
保留较新的用户内容，重复回滚幂等。历史 `patch_only` 明确标记尚未写入且不能推广；
旧版待应用 ChangeSet 仍保留原 apply 语义，不需要数据库迁移。

`SANDBOX_MODE=local` 在 App 容器内部执行，只适用于用户拥有并信任的仓库。它在最小环境中执行
`SANDBOX_ALLOWED_COMMANDS` 里的可执行文件基本名，使用固定最大超时、有界输出捕获和
进程组终止。`sh -c`、`bash -c` 等 Shell 包装器会被拒绝。进入白名单的解释器仍能
执行任意受信仓库代码，所以 App 容器不是面向恶意代码的强隔离边界；当前 Compose 不挂载
Docker Socket，也不声称支持不可信仓库。

`SANDBOX_MODE` 只决定命令进程隔离，不决定文件目标。Docker 模式会把同一个 Run 执行根
挂载为 `/workspace`，并禁用网络、使用只读容器根目录和调用方的非 root UID/GID，移除
Linux capability，启用 `no-new-privileges`，限制 PID、CPU、内存及 tmpfs。

## 项目记忆

本节是保留的独立平台记忆系统，不是 Cogent 文件记忆。治理 API、数据、索引和评测保留；
普通 Cogent 对话既不注入这些事实/画像，也不再触发旧 Chat/Agent 自动抽取链。

当前记忆架构采用 **L0 → L1 → L2 → L3**：L0 保存原始消息并支持按需搜索；L1 是当前
Workspace/revision 的原子项目事实；L2 按用户和 Workspace 将 active L1 确定性聚合为
可追溯的项目场景；L3 再把 L2 场景与用户级 active 事实合成为有界画像。任务进度和
执行状态仍以实时源码、`.workflow/tasks`、Agent Run、checkpoint 和 ChangeSet 为准，L2
只总结已生效 L1，不充当第二套任务状态。架构总览见
[`local-layered-memory-overview.drawio`](docs/architecture/local-layered-memory-overview.drawio)
和对应的 [`PNG`](docs/architecture/local-layered-memory-overview.png)；同目录还提供上下文组装、
写入治理和持久化三个可编辑细节图。

项目记忆由同一个 `workspace_id` 的授权成员共享，支持以下类型：

- `architecture_fact`；
- `constraint`；
- `decision`；
- `convention`；
- `task_outcome`；
- `incident_lesson`。

完整源码文件、临时讨论、助手推测、凭据、私钥、Token、连接字符串和完整环境变量值
都会被拒绝。

支持四种模式：

- `off`：不抽取也不检索；
- `shadow`：抽取待审候选，但不注入上下文；
- `review`：只检索活跃记录，通常需要人工确认；
- `auto`：通过敏感信息检查和候选质量门槛的抽取结果直接转为活跃状态。

用户创建的记录和明确的“记住”请求以 `1.0` 置信度直接生效。自动抽取结果低于
`PROJECT_MEMORY_CANDIDATE_THRESHOLD` 会被丢弃；`review` 下保留为 candidate，`auto`
下直接 active。切换到 `auto` 时，当前 revision 已有 candidate 也会被激活。敏感信息、
凭据式内容和 Prompt Injection 仍会在任何模式下被拒绝。

规范化内容相同会追加证据；权威冲突会替代旧记录，不确定冲突继续作为候选。带源码
依据的可变事实会在注入前检查哈希，源码变化后转为 `stale`。长期未确认的记录只会
降低排序，不会仅因时间流逝被删除。

提炼时，附加来源按“来源类型 + 来源任务 ID + 文件路径”去重，保留首条完整证据，
再取最多五条；同一文件的不同命中片段不会拼接行号或哈希。PostgreSQL 写入同时跳过
重复证据 ID 和已有来源唯一键冲突，其他错误仍使事务回滚。修复不会自动重放历史
失败任务；`completed` 且保存 0 条只表示本次没有落库记忆，不代表新增了记忆。

检索使用加权 RRF 合并稠密与词法召回，然后从配置的 L1 事实源重新加载每条结果，
验证工作区、revision、状态、过期时间和版本。PostgreSQL/Qdrant 仍可用于分布式部署；
单进程本地 profile 则使用 SQLite FTS5 BM25、标准库 `sqlite3` 和带模型/维度/记忆版本
的 float32 BLOB，在当前小型 workspace/revision 数据集内计算余弦相似度。FTS5 或向量
失败分别降级为受限 `LIKE` 或纯词法召回。每个候选都有可解释的最终分数：

```text
0.65 × 归一化相关性
+ 0.20 × 指数时间新鲜度
+ 0.15 × 归一化重要性
```

时间新鲜度使用 `last_confirmed_at`（缺失时回退到 `updated_at`），默认半衰期为 180
天。独立检索服务先全局排序，再应用六条结果和 3,000 字符预算。

管理接口：

```text
GET/PATCH /api/v1/workspaces/{workspace_id}/memory-settings
GET/POST  /api/v1/workspaces/{workspace_id}/memories
GET/PATCH /api/v1/workspaces/{workspace_id}/memories/{memory_id}
POST      /api/v1/workspaces/{workspace_id}/memories/{memory_id}/confirm
POST      /api/v1/workspaces/{workspace_id}/memories/{memory_id}/reject
DELETE    /api/v1/workspaces/{workspace_id}/memories/{memory_id}
GET       /api/v1/workspaces/{workspace_id}/memory-jobs
POST      /api/v1/workspaces/{workspace_id}/memories/reindex
```

PATCH、confirm 和 reject 必须携带当前 `version`。Viewer 可以检索和查看；Editor 可以
创建、编辑、确认和拒绝；Admin 可以修改模式、遗忘记录和修复索引。遗忘操作会硬删除
记忆、证据和向量数据，但不会删除来源会话。

Cogent 不调用本节的检索或抽取链；旧 /chat/stream 已删除。

### L0 对话搜索与 L2/L3 画像流水线

L0 不复制第二份聊天日志。SQLite 对原始 `messages` 使用写入/查询对齐的 CJK n-gram
FTS5 索引，升级到 schema v2 时会重建旧索引；PostgreSQL 使用受转义的 `ILIKE` 子串
检索。空关键词返回最近消息，因此前端进入 L0 即可看到内容；非空查询、最近消息列表
都始终按当前用户隔离，并可进一步限定 workspace/session。历史命中不会自动跨会话
注入。

L3 使用独立的 `UserMemory` 领域，固定为 `profile_fact`、
`communication_preference`、`tooling_preference`、`workflow_preference`、
`standing_goal` 和 `personal_constraint`。手工记录和明确的全局“记住/所有项目/以后”
请求直接 active；普通偏好在 `auto` 下也直接 active，在 `review` 下保留为 candidate。
每次 L1 新增、编辑、状态变化或删除都会异步重建对应的 `UserMemoryScene`（L2），再从
L2 场景和 active 用户事实确定性重建最多 1,500 字符的 `UserProfileSnapshot`（L3），
不调用 LLM。这些画像仅保留为平台独立数据；Cogent 不读取或注入它们。

L3 管理接口：

```text
GET/PATCH /api/v1/users/me/memory-settings
GET       /api/v1/users/me/memory-scenes
GET/POST  /api/v1/users/me/memories
GET/PATCH/DELETE /api/v1/users/me/memories/{memory_id}
POST      /api/v1/users/me/memories/{memory_id}/confirm
POST      /api/v1/users/me/memories/{memory_id}/reject
GET       /api/v1/users/me/profile
POST      /api/v1/users/me/profile/rebuild
GET       /api/v1/memory/conversations/search
```

前端“记忆工作台”按用户可直接查看和治理的对象划分为“项目记忆 / 个人记忆 / 对话记录”：
项目记忆和个人事实均使用左侧资产列表、右侧详情治理的双栏布局，显示 active/candidate
统计、状态/类型筛选、证据、版本和可用操作；个人摘要单独预览独立保存的确定性
快照（Cogent 不读取）；对话记录展示用户隔离的最近消息、搜索命中与原文详情。L2 场景仍在后台参与画像
生成和来源追溯，但不作为独立前端资产展示。L1 固定自动提炼，前端不再暴露工作区模式、
手动重建索引或刷新控件；进入页面即自动加载。

当前 Docker MVP 默认启用完整流水线：PostgreSQL 保存 L0 会话和 L1 事实、证据、任务与
Outbox，Qdrant 保存可重建 L1 向量，SQLite v3 保存 L2 场景、L3 用户画像和 Agent pending compact：

```dotenv
PROJECT_MEMORY_ENABLED=true
PROJECT_MEMORY_MODE=auto
PROJECT_MEMORY_STORE=postgres
PROJECT_MEMORY_VECTOR_STORE=qdrant
PROJECT_MEMORY_CANDIDATE_THRESHOLD=0.60
PROJECT_MEMORY_AUTO_THRESHOLD=0.85
PROJECT_MEMORY_RECALL_LIMIT=20
PROJECT_MEMORY_RESULT_LIMIT=6
PROJECT_MEMORY_MAX_CONTEXT_CHARS=3000
PROJECT_MEMORY_QDRANT_COLLECTION=project_memories
PROJECT_MEMORY_RELEVANCE_WEIGHT=0.65
PROJECT_MEMORY_RECENCY_WEIGHT=0.20
PROJECT_MEMORY_IMPORTANCE_WEIGHT=0.15
PROJECT_MEMORY_RECENCY_HALF_LIFE_DAYS=180
USER_MEMORY_ENABLED=true
USER_MEMORY_MODE=auto
USER_PROFILE_MAX_CONTEXT_CHARS=1500
```

默认 [`.env.example`](.env.example) 使用官方单实例 Compose 组合。
[`.env.local-memory.example`](.env.local-memory.example) 提供全部结构化数据均落 SQLite 的
单进程变体；Compose 则只把用户级 L2/L3 放在挂载到 `app_state` 的 SQLite 中。

Cogent 会话压缩与本节平台记忆无关；其近期保留、成对工具消息及摘要边界由新内核管理。

## 独立知识库

RAG answer、索引和评测直接使用独立 RAG service 与模型注册中心，不经过 Cogent 循环。
Agent 请求携带知识库选择将被明确拒绝；本次不重建现有索引或迁移 RAG 数据。

导入文档前，先创建目录元数据：

```text
POST   /api/v1/knowledge-bases
GET    /api/v1/knowledge-bases
GET    /api/v1/knowledge-bases/{knowledge_base_id}
PUT    /api/v1/knowledge-bases/{knowledge_base_id}
DELETE /api/v1/knowledge-bases/{knowledge_base_id}
```

目录记录包含不可变 ID，以及可编辑的名称、描述和标签。工作台左侧选择知识库，右侧
以“文档 / 检索问答 / 设置”管理当前上下文；设备会记住最近选择和标签页。窄屏改用
顶部选择器、文档卡片和全屏详情面板。删除非空知识库时，界面要求输入知识库 ID，
服务端先移除向量，再级联删除 PostgreSQL 文档与分块。

文档记录包含标题、原文件名、描述、标签、MIME、大小、内容哈希、分块数、索引状态
及创建/更新/索引时间。服务只保留解析结果、分块和上传元数据，不保存或提供原文件
下载。文档 API 可以独立使用：

```text
GET    /api/v1/knowledge-bases/{knowledge_base_id}/documents
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/bulk-delete
GET    /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
PATCH  /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
PUT    /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/content
DELETE /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
POST /api/v1/knowledge-bases/{knowledge_base_id}/search
POST /api/v1/knowledge-bases/{knowledge_base_id}/ask
GET  /api/v1/rag/capabilities
GET  /api/v1/knowledge-bases/{knowledge_base_id}/index-jobs
GET  /api/v1/knowledge-bases/{knowledge_base_id}/index-jobs/{job_id}
```

文档导入使用 multipart `file` 上传，最大 20 MiB：

```bash
curl -X POST http://localhost:8000/api/v1/knowledge-bases/product_docs/documents \
  -F 'file=@/absolute/path/to/manual.pdf'
```

支持 PDF、DOCX、Markdown、UTF-8 文本和配置文件，以及已有的 UTF-8 源码格式；不支持
旧 `.doc` 文件和需要 OCR 的扫描文档。

列表默认按更新时间倒序，每页 20 条，支持标题/文件名搜索、索引状态筛选和排序。
元数据修改不重新生成向量；内容替换使用显式 `PUT`，保留文档 ID、标题、描述和标签。
同一知识库出现同名文件时返回 HTTP 409 `document_filename_conflict` 和已有文档 ID，
不会静默覆盖。批量删除最多 100 条，返回 `deleted_ids` 与逐项 `failures`。

只有文档接口会使用分块、嵌入和配置的向量存储。Agent 的 RAG 路由调用同一套搜索
实现；检索不可用时会降级为证据警告。

每次上传都会生成可查询的索引任务，状态依次为
`pending → parsing → embedding → vector_written → active`。任意非终态失败会记录为
`failed`，并保存长度受控的错误消息。替换会在切换前完成解析、分块和 embedding，
并保存旧文档、分块和向量快照；失败时恢复旧内容并显示“替换失败，仍使用旧版”。
删除按向量后元数据的顺序执行，后续步骤失败时恢复快照，避免可检索孤儿。

搜索包含两个独立召回通道：

1. 从已配置向量存储执行稠密相似度召回；
2. 本地使用内存 BM25，持久化运行时使用 PostgreSQL 全文搜索进行词法召回。

两路排序通过加权倒数排名融合（RRF）合并，再由可选 CrossEncoder 重排器选出最终
结果。响应会暴露原始稠密/词法分数、`dense_rank`、`lexical_rank`、`fusion_score`
和可选重排分数。`RAG_LEXICAL_WEIGHT` 控制 RRF 通道权重，`RAG_RRF_K` 控制排名
平滑。

官方 Compose 默认使用真正的中英多语言语义 embedding `BAAI/bge-m3`。模型由
Sentence Transformers 在第一次文档摄取时延迟下载，并保存在 `model_cache` volume；
重建 App 镜像不会重复下载，删除该 volume 才会移除缓存。设备默认使用 CPU。切换
embedding Provider、模型或向量维度后，已有 Qdrant/Chroma 向量不会自动兼容，必须
重新索引对应知识库；`local/local-hashing` 只保留给确定性测试和轻量开发：

```dotenv
EMBEDDING_PROVIDER=sentence_transformer
EMBEDDING_MODEL=BAAI/bge-m3
SENTENCE_TRANSFORMER_EMBEDDING_DEVICE=cpu
```

知识库页面以无障碍按下/未按下按钮暴露 CrossEncoder。搜索和问答都接受可选的
`rerank_enabled`。省略时使用服务端 `RAG_RERANK_DEFAULT_ENABLED`，仓库默认值为
`false`。响应包含 `retrieval.rerank_requested`、`rerank_applied`、Provider/Model、
候选与结果数量，以及重排耗时。客户端明确要求重排但服务端未配置重排器时，接口会
返回 HTTP 409，而不是静默回退到 RRF。

折叠后的检索参数摘要始终显示当前采用 RRF 还是 CrossEncoder。搜索或生成答案期间，
页面会锁定相关控件、清除旧结果，并通过请求取消和 generation 检查避免旧响应覆盖
最新结果。

默认 Sentence Transformers 模型为中英双语 CrossEncoder
`BAAI/bge-reranker-base`。只有首次启用重排时才会延迟加载，因此正常启动和纯 RRF
请求不会下载或初始化模型。该双语模型比以前仅英文的 MiniLM 更大，首次下载和 CPU
预热会更慢。设备默认使用 `cpu`，以避免不受支持或不稳定的加速运行时（包括 PyTorch
MPS）造成进程崩溃；部署可以显式选择已验证的其他设备。将 Provider 设为 `none`
可以关闭此能力及页面按钮：

```dotenv
RAG_RERANKER_PROVIDER=sentence_transformer
SENTENCE_TRANSFORMER_RERANKER_MODEL=BAAI/bge-reranker-base
SENTENCE_TRANSFORMER_RERANKER_DEVICE=cpu
RAG_RERANK_DEFAULT_ENABLED=false
```

## 存储与数据库迁移

数据库 Schema 变更使用 Alembic：

```bash
.venv/bin/alembic upgrade head
```

新增 `20260903_0028` 保存 Cogent 版本化运行状态与快照，SQLite 对应 schema v4。
本次没有对真实数据库应用迁移；迁移前必须备份并由操作者确认。

主要迁移：

- `20260723_0006`：永久删除仓库索引表，将 repositories 重命名为 workspaces，迁移
  Agent 运行的工作区字段并保存捕获的根路径；
- `20260724_0007`：创建托管知识库目录，回填文档中的知识库 ID 并添加级联外键；
- `20260727_0008`：添加 PostgreSQL 词法搜索元数据、分块行号/符号来源和 RAG 索引
  任务日志；
- `20260730_0009`：添加工作区 revision、成员和设置、项目记忆与证据、抽取任务、
  事务型向量索引 Outbox 和不含正文的审计事件；PostgreSQL 是记忆事实来源，Qdrant
  只保存可重建的向量和最小标识；
- `20260730_0010`：添加持久化滚动会话摘要、摘要消息边界、来源大小统计和乐观锁版本；
- `20260730_0011`：为 Token 用量记录添加可空工作区归属和持久化思考 Token；
- `20260731_0012`：把 Token 用量记录扩展为统一模型调用账本，加入操作/资源、请求
  Provider/Model、输入计数方式和预算决策；
- `20260804_0014`：添加持久化会话标题、更新时间/归档状态、会话级模型/工作区/模式
  配置、用户偏好、近期会话索引和确定性回填，并为每次 Agent 运行保存不可变模型快照。
- `20260807_0015`：为规范化工作区根路径添加唯一约束；升级前必须先处理已有重复路径。
- `20260807_0016`：为知识库文档补充可管理元数据、分块统计和索引时间，回填旧记录，
  并添加知识库内文件名和更新时间索引；
- `20260807_0017`：为工作区添加 `removed_at`，支持保留会话、用量和项目记忆的软移除
  与同路径恢复。
- `20260808_0018`：添加 Agent 运行控制状态、事件与耐久工具调用执行账本。
- `20260809_0019`：添加每 Run 唯一的 `agent_change_sets`，持久化完整补丁、基线哈希、
  workspace 快照、验证与 apply/reject 状态。
- `20260810_0020`：为 `agent_runs` 添加不可变 `run_context_snapshot` JSONB，持久化身份、
  会话/摘要/模型、Workspace/Git/配置/指令/工具选择和已授权额外目录，供 Worker 按 Run ID
  恢复。该 revision 在本任务中**没有执行**；启动 PostgreSQL runtime 前必须先由操作者
  审阅并显式授权应用。
- `20260813_0021`：为消息添加 `source_run_id` 与每 Run/role 唯一约束，使 Query start 的
  用户消息/Run 事务和最终助手消息的恢复幂等都具有数据库约束；这是既有迁移；本次仅在隔离验收数据库执行迁移链，用户数据库需按升级步骤处理。
- `20260820_0022`：为每个已注册模型添加 `max_output_tokens` 能力上限；现有 DeepSeek
  记录回填为 8192、fake 为 4096，其余为 16384。该迁移随代码交付但未在当前数据库执行。
- `20260825_0025`：新增 `model_probe_stats`，将固定短提示的手动/周期探测与真实业务请求
  延迟样本分表持久化；该迁移随代码交付，未在当前数据库执行。
- `20260831_0026`：为 `agent_runs` 添加 `pending_compaction` JSONB，持久化当前 Run 唯一的
  手动压缩请求；本地 SQLite 对应 schema v3。迁移随代码交付，未在当前数据库执行。

历史迁移会继续保留在 revision 链中。只有 PostgreSQL 结果加载器会兼容含有
`repository_id`/`rag_context` 的历史 JSON；新 API 和新运行只暴露 workspace 契约。

当前单实例产品不启动 Celery。官方组合是：

```dotenv
TASK_QUEUE_BACKEND=in_process
SESSION_REPOSITORY=postgres
AGENT_RUN_STORE=postgres
CHANGE_SET_STORE=postgres
DOCUMENT_STORE=postgres
WORKSPACE_STORE=postgres
RAG_VECTOR_STORE=qdrant
PROJECT_MEMORY_ENABLED=true
PROJECT_MEMORY_MODE=auto
USER_MEMORY_ENABLED=true
USER_MEMORY_MODE=auto
PROJECT_MEMORY_STORE=postgres
PROJECT_MEMORY_VECTOR_STORE=qdrant
WORKSPACE_ALLOWED_ROOTS=/workspaces
```

持久化运行时各数据库职责如下：

| 组件 | 职责 |
| --- | --- |
| PostgreSQL | 会话/消息、用户默认值、会话配置和滚动摘要、Agent 运行/事件/工具账本/ChangeSet、不可变模型与 Run 上下文快照、工作区/知识库目录、项目记忆事实/证据/任务/Outbox/审计、文档/分块元数据、词法搜索和 Cogent runtime snapshot |
| Qdrant | 相互独立的知识库和项目记忆向量集合；项目记忆载荷最小且可重建 |
| SQLite | 保留的兼容/测试 Adapter；当前产品只可选用于尚未迁移的单实例用户记忆 |
| Redis/Celery | 保留的多 Worker 扩展实现；当前 Compose 不启动 |
| Chroma | 保留的可选嵌入式向量实现；当前 Compose 不选择 |

`RAG_VECTOR_STORE` 实现仍支持 Qdrant、Chroma 或 memory，但官方 Compose 锁定 Qdrant，
不会双写另一套向量存储。

产品启动时，一次性 `migrate` 服务复用现有 Alembic revision，成功后 App 才会启动：

```bash
docker compose up -d --build
docker compose ps
```

只有 App 发布 `127.0.0.1:${SELF_HOSTED_PORT}`；PostgreSQL 与 Qdrant 没有宿主机端口。
数据库凭据来自用户本机 `.env`，并只注入私有 Compose 网络。Adminer、Gateway、Redis 和
Celery 不在当前服务集合中。

进程内队列注册 Agent 启动/恢复、会话压缩、记忆抽取和项目记忆索引 Outbox 消费任务，
并复用与 Celery Adapter 相同的任务语义。运行开始前仍冻结 Workspace、配置和工具上下文；
应用重启会中断当时正在执行或排队的任务，这是当前单实例 MVP 的明确边界。持久化 Run、
事件和 Outbox 仍保留恢复与幂等证据，但当前版本不承诺自动恢复所有被重启打断的运行。

### 运行时装配与生命周期

FastAPI `create_app()` 与保留的 Celery Worker 进程适配器都通过
`build_runtime(settings, role=api|worker|cli)` 进入同一个 `ApplicationFactory`。
Repository、LLM、模型注册中心、Workspace、RAG、MCP、Tool Registry、Cogent runtime 和业务 Service 因此使用同一依赖图；正式 CLI print、REPL
和 SDK 也复用该容器与 Query Kernel。启动配置在进入工厂前只解析进程基线；Workspace
项目覆盖、配置指令和 Effective Tool Pool 在 Run 入队前按已鉴权主 Workspace root
解析并冻结。共享 `ToolPoolBuilder` 注入 ExecutionContextFactory、QueryService 与拆分后的
Agent Loop，但不拥有或关闭 Registry/MCP 资源。

返回的 `RuntimeContainer` 显式持有不可变解析结果、脱敏配置快照、
共享 `SecretStore`、`MCPConnectionManager`、`ExecutionContextFactory`、服务和资源，并按
顺序记录 `config_loaded`、
`stores_ready`、`mcp_ready`、`tools_ready`、`agent_ready` 启动检查点。正常的 FastAPI
lifespan、Worker shutdown 和部分启动失败都走同一个幂等 `close()`；清理回调严格按
创建登记的逆序执行且每个资源最多关闭一次。测试仍可向 `create_app()` 注入 LLM、RAG、
Agent runtime 和目录选择器，也可覆写 `ApplicationFactory` 的组件构造器。

## 保留的扩展实现

`gateway/`、Celery/Redis 和 local SQLite profile 的代码及测试仍保留，用于说明后续
多用户、多 Worker 或不同存储拓扑的演进边界；它们不在当前 Docker MVP 的 Compose
服务集合中。Go gateway 仍可独立运行和验证：

```bash
go run ./gateway/cmd/gateway
go test ./gateway/...
go vet ./gateway/...
```

`AUTH_MODE=trusted_header`、OIDC/JWKS、local gateway 证明和多 Worker 可靠性测试仍可以作为
面试中的已实现扩展能力说明，但不得描述为当前默认部署或真实生产规模经验。若未来恢复
多用户/公网部署，必须重新设计认证、租户授权、密钥、备份、可观测性和不可信执行隔离，
不能直接把 `single_user` Compose 暴露出去。

## 验证

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall ai_agent_platform tests evals
.venv/bin/python INTERVIEW_NOTES/validate.py
node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs
docker compose --env-file .env.example config --quiet
bash -n scripts/start.sh scripts/start-local.sh
node --check ai_agent_platform/static/app.js
git diff --check
```

修改保留的 Go gateway 兼容实现后，再额外运行 `go test ./gateway/...`。

运行离线 Agent 评估：

```bash
.venv/bin/python evals/run_evals.py
.venv/bin/python evals/run_rag_evals.py
.venv/bin/python evals/run_rag_evals.py --profile bge-m3
.venv/bin/python evals/run_rag_evals.py --profile bge-m3-rerank --hybrid-only
.venv/bin/python evals/run_rag_answer_evals.py --replay /path/to/prior-report.json
.venv/bin/python evals/run_trajectory_evals.py
.venv/bin/python evals/run_memory_evals.py
```

各套件分别回答不同问题：`run_evals.py` 默认跑 Agent 管道回归，
`--cases evals/platform_rag_cases.json` 单独跑原小型 RAG 检索门槛；
`run_rag_evals.py` 是独立的 30 条分级 RAG 试标集，按文件去重后报告
K=1/3/5/10 的 Recall、Precision、Core MRR、NDCG、Hit Rate，以及 hard negative、
无答案、冲突资料和检索 p50/p95。它默认只诊断，标注复核稳定前不会用草案门槛阻断；
显式传 `--enforce-gates` 才执行门槛。`--profile current` 会读取当前 chunk、embedding、
RRF 和 reranker 参数，但强制用临时内存索引，避免污染持久 VectorStore；
`--profile bge-m3` 固定使用 `BAAI/bge-m3`，在同一次 ingestion 后分别报告真正的
Dense-only、Lexical-only 和 Hybrid（weighted RRF）三组指标。
`run_rag_answer_evals.py` 复用同一批 30 条 query，但把标注的 oracle evidence 直接交给
生产 RAG prompt；hard negative 与低等级旧资料仍保留在上下文中。它使用模型注册表中
显式启用的真实 Provider/Model，关闭 fallback 并校验实际 route，输出事实覆盖、事实与
来源引用归属、引用编号合法性、无答案拒答、Token 和生成 p50/p95。该分数只回答
“拿到这组证据后能否可靠生成”，不能替代 BGE-M3 的检索指标或端到端 RAG 评测；
`--replay` 可对已保存原始回答离线重评分，避免标注修正重复触发付费调用。
`bge-m3-rerank` profile 固定使用 `BAAI/bge-reranker-base`；`--hybrid-only` 避免对
Dense/Lexical 诊断通道重复支付 CrossEncoder 成本，`--json-output` 的 Hybrid 排名可由
答案 runner 的 `--retrieval-report` 消费。本轮合成 pilot 中，重排把 hard-negative
Top-5 违规从 `1.000` 降到 `0.500`、冲突首选从 `0.333` 提到 `0.667`，但 NDCG
`0.979→0.973`、检索 p50 约 `49.6→590.8 ms`；10 条风险集两组 DeepSeek 答案均
10/10，通过率没有增益且 rerank 组输入 Token 增加，因此产品默认仍不启用重排。
`run_trajectory_evals.py` 是 L1 轨迹评测，用约束（必须/禁止出现的工具、偏序、
步数上限）而不是标准答案判定过程。工具调用按 proposed / accepted / executed /
succeeded / failed / suppressed / denied / pending approval 分层；只有能按 `call_id`
关联到真实 `ToolResult` 的调用才算 executed。无效动作率按“实际执行的精确重复 +
被抑制调用”除以“实际执行 + 被抑制”计算；没有分母时显示 `n/a`。
`run_memory_evals.py` 是项目记忆质量门槛。
30 条 RAG pilot 是虚构 AuroraDesk 知识库上的用户风格问题，不是真实生产查询或正式
holdout；其分数只能作为当前检索设置的起始基线。离线 Agent 套件使用 fake provider，
通过率不能当作最终答案质量证据。

同一套 L1 还能在应用内对着**已注册的真实模型**跑，入口是前端"评测"页：选一个
已注册的 Provider + Model 点运行，页面展示调用生命周期、三项引用指标、Token/耗时、
越界与相对基线的回归预警、历史和单例详情。评测使用显式隔离标志：不注入真实用户
画像/会话历史，不读写用户或项目记忆，不暴露全局知识库，只允许 suite 声明的 fixture
KB；case 结束删除临时 session，run 结束删除 workspace、临时成员和项目记忆状态。
Provider 凭据仍由应用内模型注册表解析，Secret 不进入评测记录。

真实模型运行按 Token 计费，一次只允许一个评测，本仓库不会自动发起。基线必须人工
固化，兼容键为 `provider + model + suite_id + evaluator_version`；首个 completed Run
不会自动成为基线。带 critical alert 的 Run 默认拒绝固化，只有 API 的显式
`force=true` 加 UI 二次确认才能覆盖。旧记录迁移为 `legacy` evaluator/schema，不会与
当前 v2 evaluator 静默比较。Token 与耗时只做相对基线预警，不是未经校准的硬门槛。
对应 API 为 `/api/v1/evals/catalogue`、`/api/v1/evals/runs`、
`/api/v1/evals/runs/{run_id}` 与 `/api/v1/evals/runs/{run_id}/baseline`；
配置项为 `EVAL_STORE`、`EVAL_FAULT_INJECTION_ENABLED`、`EVAL_WORKSPACE_ROOT`。

分层设计见 [evals/DESIGN.md](evals/DESIGN.md)，离线口径与旧实测数据的兼容边界见
[evals/README.md](evals/README.md)。

---

[查看英文版 README](README.en.md)

真实数据库/OS 验收可选项（只能指定隔离测试库）：

```bash
COGENT_TEST_POSTGRES_URL=postgresql://user:password@127.0.0.1:5432/cogent_test \
COGENT_TEST_OS_SANDBOX=1 .venv/bin/python -m pytest -q tests/test_cogent_acceptance.py
```

不设置变量时跳过需要真实 PostgreSQL/OS 权限的用例；常规内存和 SQLite 测试继续运行。
测试库须先使用项目 Alembic 迁移到 head。检索回归可单独运行
`evals/run_evals.py --cases evals/platform_rag_cases.json`，原分层 RAG pilot 数据保留不变。
