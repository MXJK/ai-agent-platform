# AI Agent Platform

**简体中文** | [English](README.en.md)

基于 FastAPI 的 AI Agent 平台，提供流式对话、任务驱动的代码 Agent、托管文档
知识库、工作区级项目记忆，以及带审批机制的沙箱执行能力。项目可选接入
PostgreSQL、Celery、Redis 和 Qdrant，并通过原生浏览器工作台与可选的 Go 网关
划分产品和流量边界。

代码 Agent 不会预先索引代码仓库，也不依赖向量嵌入。每次运行都会捕获已注册的
工作区根目录，围绕当前任务搜索实时文件系统，只读取必要的源码区间，并把原始
片段直接放入当前模型上下文。

## 目录

- [本地启动](#本地启动)
- [主要能力](#主要能力)
- [Gemini 流式输出](#gemini-流式输出)
- [模型路由](#模型路由)
- [模型白名单与 Token 预算](#模型白名单与-token-预算)
- [代码 Agent 流程](#代码-agent-流程)
- [工作区 API](#工作区-api)
- [项目记忆](#项目记忆)
- [独立知识库](#独立知识库)
- [存储与数据库迁移](#存储与数据库迁移)
- [可选 Go 网关](#可选-go-网关)
- [验证](#验证)

## 本地启动

Google Gen AI SDK 要求 Python 3.10 或更高版本：

```bash
python3.10 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp -n .env.example .env
# 将 POSTGRES_PASSWORD 与 DATABASE_URL 中的示例 PostgreSQL 密码
# 替换为同一个仅供本地使用的随机密码。
./scripts/start.sh
```

首次完成环境初始化后，本地启动只需要运行 `./scripts/start.sh`。脚本会验证持久化
配置，启动 PostgreSQL、Qdrant 和 Redis，等待服务就绪，执行尚未应用的 Alembic
迁移，然后同时启动 Celery Worker 与 FastAPI。按 `Ctrl+C` 会停止 API 和 Worker，
持久化数据库容器仍会继续运行。

常用启动选项：

```bash
./scripts/start.sh --check  # 仅检查依赖和配置，不执行写操作。
APP_RELOAD=0 ./scripts/start.sh
APP_PORT=8001 ./scripts/start.sh
```

Web UI 默认地址为 <http://127.0.0.1:8000>。页面由 FastAPI 直接提供，不需要单独
构建前端。示例配置使用 Fake LLM 和本地嵌入提供方，不需要 API Key。
当 `AUTH_MODE=disabled` 时，启动脚本会直接拒绝非回环地址的 `APP_HOST`，而不是
只输出运维警告。

## 主要能力

统一输入框提供两种模式：

- `快速对话`：直接返回模型的 SSE 流式响应；
- `代码 Agent`：围绕任务探索工作区，展示审批、进度和产物；
- 两种模式共享会话历史和持久化滚动摘要。压缩后的历史与数量受控的近期消息可以
  共同参与 Chat、Agent 探索和原生工具选择，同时保留原始消息。

两种响应都会在消息内展示执行过程和用量指标。Chat 使用模型提供方的 SSE 用量；
Agent 汇总结构化规划和答案生成阶段由提供方上报的用量。界面会显示每条响应的
输入、输出、思考和总 Token。Agent 轮询还会合并实时 LangGraph checkpoint Trace，
因此即使任务很快完成，前端也能按顺序播放已完成阶段，再展示最终答案。

所有模型调用都写入同一本用量账本。Chat、Agent 模型轮次、语义会话压缩、RAG
Ask 和嵌入调用都会记录：

- `operation` 与资源；
- 可用时的会话和工作区归属；
- 请求与实际使用的 Provider/Model；
- 输入计数方式；
- 预算决策。

会话页展示每个对话累计的输入、输出、思考和总 Token，以及当前受控会话上下文的
估算大小和最近一次最终 Prompt 由提供方计数的输入 Token。运行页则按已注册工作区
展示同类汇总、操作分布和预算状态。

上下文卡片仍采用本地 `unicode_heuristic_v1` 预估，因为它还不是一次真实的提供方
请求。实际调用前，在历史、记忆、RAG 引用和工具 Schema 组装完毕后，系统会使用
OpenAI Responses 的 `input_tokens`、Anthropic Messages 的 `count_tokens` 或 Gemini
的 `models.count_tokens` 统计最终 Provider 形态的 Prompt。提供方在完成响应中返回的
用量仍是账本中的权威值；预检计数仅作为审计方式记录，只有在完成用量缺失输入
Token 时才作为后备。

浏览器工作台还包括：

- 托管知识库目录、多文件上传、混合检索、问答、引用和索引任务状态；
- 项目记忆治理页，包括模式、状态和类型筛选，证据、置信度、乐观锁编辑、确认、
  拒绝、遗忘和索引修复；
- 受 `WORKSPACE_ALLOWED_ROOTS` 约束的本地工作区目录选择；
- Agent 运行详情、审批风险、验证产物、错误和指标；
- 安全 Markdown 渲染、响应取消、响应式导航和无障碍文字状态。

### 持久化会话与重启恢复

PostgreSQL 是可跨重启恢复会话的事实来源。会话记录保存自动生成或手工修改的标题、
归档状态、最后更新时间、工作区和模型配置；`user_preferences` 保存未来会话的默认值
和最后活跃会话。API Key、数据库 URL 和允许访问的文件系统根目录始终是服务端配置，
不会进入会话或偏好记录。

`GET /api/v1/sessions` 按最近更新时间返回包含 `message_count` 和
`last_message_preview` 的列表，支持标题或正文子串搜索、活跃或归档筛选，以及不透明
游标分页。新分配的会话在第一条消息持久化前只是本地草稿：零消息记录不会出现在
历史或搜索中，也不会替换最后活跃会话。

`PATCH /sessions/{id}` 可重命名、归档、恢复或修改单个会话，并可选择把配置复制为
用户默认值，而不重写旧会话。归档会话仍可读取，但恢复之前，Chat、消息写入和
Agent 执行都会返回 `409`。

桌面端可收起的右侧检查器会在运行详情上方展示最近 12 个活跃会话，并按今天、过去
七天和更早分组；左侧栏只保留主导航。启动恢复依次检查 URL 中的会话、
`last_active_session_id` 和最近活跃会话，并跳过过期的零消息候选。如果没有有效会话，
页面会停留在欢迎页，不会再创建空记录。加载会话时会恢复消息、摘要，以及该会话
自己的模型、工作区和输入模式。

浏览器 `localStorage` 只保存设备级 UI 状态和未认证本地用户 ID，不重复保存会话
配置。健康检查接口会暴露 `session_storage` 和 `persistent_sessions`；如果使用内存
模式，界面会明确标记为临时存储。

### 全局模型注册中心

这是一个本地单用户应用，所有工作区共享同一个全局模型注册中心。模型管理页可以
统一配置 OpenAI、DeepSeek、Anthropic 和 Google，再为每个 Provider 注册多个模型。
API Key 只能写入：PostgreSQL 仅保存 secret reference，密钥值进入操作系统钥匙串。
已有环境变量仍可作为启动凭据，并且绝不会由 API 返回。

保存 Provider 后，模型管理页会调用该 Provider 的官方模型列表接口，过滤出当前
API Key 可用的文本生成模型供用户选择，并标记已经注册的条目。注册只需要选择
Provider/模型以及是否启用、是否参与自动路由；显示名称、上下文、能力和冷启动
路由画像由 Provider 元数据与后端先验合成。发现接口暂时没有目标模型时仍可手动
填写模型 ID，但不再要求用户填写质量、价格或延迟。

统一输入框会暴露供 Chat、整次代码 Agent 运行（包括恢复执行）和 RAG Ask 使用的
会话偏好：

- 自动 `smart`、`quality`、`cost` 或 `latency` 路由；
- 手动首选模型、显式回退开关，以及按延迟排序的自定义选择器；选择器显示精确毫秒值
  和绿/黄/红延迟等级（`≤1000 ms`、`≤3000 ms`、`>3000 ms`）；
- 每个模型的可用性，以及观测到的首 Token 和总延迟 P50/P95。

`smart` 不会额外调用一次 LLM，而是生成确定、可解释的任务画像。简单任务更重视
成本和延迟，困难任务更重视后端质量画像。后台嵌入、会话压缩和记忆抽取继续使用各自
独立的服务策略。连接测试只由用户触发；正常状态和延迟来自真实请求的被动观测，
不会通过周期性付费探测获得。

## Gemini 流式输出

如需使用 Gemini，请创建本地 `.env`：

```dotenv
LLM_PROVIDER=google
LLM_MODEL=gemini-3.5-flash
LLM_MAX_OUTPUT_TOKENS=4096
LLM_THINKING_LEVEL=low
LLM_TIMEOUT_SECONDS=30
SSE_HEARTBEAT_SECONDS=10
GOOGLE_API_KEY=your_google_ai_studio_key
```

Gemini 3 请求的 `thinking_level` 支持 `minimal`、`low`、`medium` 或 `high`，界面可以
覆盖服务端默认值。当 Provider 暂时没有输出时，SSE 会发送心跳；思考 Token 会单独
统计。如果 Gemini 以 `MAX_TOKENS` 结束，接口会返回明确的 `max_output_tokens` 错误，
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
→ 调用 Provider；首个 delta 前失败时可尝试下一个跨 Provider 候选
```

持久化注册中心是运行时模型表，更新后无需重启即可影响路由器。
`LLM_MODEL_CATALOG_JSON` 仍作为启动和兼容数据源：当数据库中没有持久化记录时，
应用会导入其中的条目，或根据 `LLM_PROVIDER`、`LLM_MODEL` 和
`LLM_MODEL_CONTEXT_WINDOW_TOKENS` 推导一条保守记录。下面为了可读性进行了换行，
实际 `.env` 中的 JSON 数组必须保持单行：

```json
[
  {
    "provider": "google",
    "model": "your-quality-model",
    "capabilities": {"tool_calling": true, "structured_output": true},
    "context_window_tokens": 200000,
    "input_cost_per_million": 2.0,
    "output_cost_per_million": 8.0,
    "quality_score": 0.92,
    "latency_ms": 900,
    "enabled": true
  },
  {
    "provider": "openai",
    "model": "your-low-latency-model",
    "capabilities": {"tool_calling": true, "structured_output": true},
    "context_window_tokens": 128000,
    "input_cost_per_million": 0.4,
    "output_cost_per_million": 1.6,
    "quality_score": 0.76,
    "latency_ms": 220,
    "enabled": true
  }
]
```

通过前端发现并注册的模型由后端生成质量/成本等级和数值路由先验；它们不是在线
质量评测或 Provider 官方实时报价。`LLM_MODEL_CATALOG_JSON` 兼容入口仍允许显式
提供这些底层数值。延迟只在冷启动时使用后端先验，一旦存在成功请求样本，运行时
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

重启安全的全局配置应使用 `MODEL_REGISTRY_STORE=postgres`，API Key 应使用
`MODEL_SECRET_BACKEND=keyring`。写接口仅在本地 `AUTH_MODE=disabled` 模式开放，
此模式会强制绑定回环地址。内存后端只应用于测试或明确的临时运行。

## 模型白名单与 Token 预算

任何计数或生成请求开始前，Provider 选择和精确的 Provider/Model 组合都必须通过
白名单。白名单环境变量为空时采取安全默认值：系统只允许当前主 LLM、嵌入模型和
可选预算回退模型。允许多个明确选项时，需要同时配置：

```dotenv
MODEL_PROVIDER_ALLOWLIST=openai,anthropic,google
MODEL_ALLOWLIST=openai:gpt-5-mini,anthropic:claude-haiku-4-5,google:gemini-embedding-001
```

会话和工作区预算会统计归属于对应范围的所有账本记录：

```dotenv
SESSION_TOKEN_BUDGET=100000
WORKSPACE_TOKEN_BUDGET=1000000
TOKEN_BUDGET_ACTION=reject
```

`0` 表示关闭对应范围。使用 `reject` 时，如果请求无法至少保留一个输出 Token，系统
会在提交用户消息或调用模型之前拒绝请求；允许执行的请求也会把 Provider 输出上限
限制到剩余硬预算。API 返回 `429` 和 `code=token_budget_exceeded`。

使用 `downgrade` 时，应配置一个已进入白名单的低成本组合：

```dotenv
TOKEN_BUDGET_ACTION=downgrade
TOKEN_BUDGET_FALLBACK_PROVIDER=openai
TOKEN_BUDGET_FALLBACK_MODEL=gpt-5-nano
```

超预算调用会继续在回退模型上执行，并暴露 `budget_decision=downgraded`、请求模型和
实际模型元数据。这是软预算，回退模型的用量仍会继续累积。预算预检读取已经提交的
账本记录，项目不会宣称具备严格的跨进程预算预留能力。

路由与治理属于同一条流水线：路由器先筛选、排序符合条件且健康的模型；每次真实的
计数或生成尝试前，选中的 Provider/Model 都必须通过白名单和 Token 预算预检。预算
降级目标必须重新通过目录能力与健康校验。跨 Provider 回退会为新候选重复执行对应
Provider 的计数与授权，并且仍仅限于首个 delta 之前。

OpenAI、Anthropic 和 Google 使用各自的计数 API；DeepSeek 目前在预检阶段采用保守
估算，最终仍以 Provider 返回的实际用量写入账本。

## 代码 Agent 流程

LangGraph 链从以下节点开始：

```text
setup_workspace
→ load_project_instructions
→ classify_request
→ decide_context_source
→ retrieve_project_memory（与 repo/RAG 路由正交）
   ├─ repo   → plan_exploration → execute_exploration → assess_context
   ├─ rag    → retrieve_knowledge
   ├─ hybrid → retrieve_knowledge → repository exploration
   └─ none
→ merge_evidence
```

分类器只接收一个受控目录，其中包括知识库 ID、名称、描述和标签。它会选择 `none`、
`repo`、`rag` 或 `hybrid`，最多选中三个托管知识库。仓库证据仍来自实时文件，文档
证据复用独立的 RAG 搜索栈。项目记忆最多贡献六条当前 revision 的活跃记录，总预算
为 3,000 字符。

`merge_evidence` 会在工具或变更规划、答案生成之前保留所有证据来源。变更任务继续
使用人工审批、每次运行独立的沙箱副本、验证、一次有界修复，以及 Diff/测试产物；
注册的源工作区永远不会被 Agent 直接修改。

运行中的 Agent 状态同时读取产品运行存储和最近的 LangGraph checkpoint，因此 API
可以在任务仍执行时暴露已经完成的 Trace 节点。最终指标包括耗时、节点和工具数量、
修改文件、已恢复错误，以及 Provider 上报的输入、输出、思考和总 Token。

默认探索预算：

- 4 轮探索；
- 每轮 6 个只读工具；
- 12 个不同源码文件；
- 32,000 个源码证据字符；
- 16,000 个项目指令字符。

重复工具调用、相同行区间和重复内容不会再次消耗证据预算。预算耗尽时，Agent 会基于
已有证据作答，并明确标注不确定性。

### 原生工具调用循环

OpenAI、Anthropic 和 Google 适配器会通过各 Provider 原生的 Function/Tool Calling
API 发送 `ToolSpec`。生产模型不再通过 Prompt 文本生成 JSON 工具计划。Provider
特有的函数调用会被标准化为 `LLMToolDecision`，其中包含稳定的 tool-call ID；带点号
的注册名会转换为 Provider 安全别名，并在执行前映射回原名。Fake Provider 保留
确定性规则规划，用于离线测试。

只读分析采用有界的“观察—重规划”循环：

```text
原生工具调用
→ ToolRegistry 校验并执行
→ 通过 call ID 关联结果或错误
→ Provider 原生工具结果消息
→ 模型观察后继续调用工具或作答
```

默认每次运行最多四轮模型工具调用和十二次调用，可通过 `AGENT_MAX_TOOL_ROUNDS` 和
`AGENT_MAX_TOOL_CALLS` 配置。重复相同工具名和参数会被视为没有进展。

`ToolRegistry` 在注册时校验完整的 Draft 2020-12 JSON Schema，并在执行时校验输入
和输出。工具规格还声明超时、重试和幂等行为。只有幂等工具遇到可重试失败时才会重试；
相同 `run_id + call_id` 会重放缓存结果，参数变化则会被拒绝。MCP 工具使用同一注册
契约：优先读取 `structuredContent`，文本块会被标准化，`isError=true` 会变成稳定的
工具失败，而不是成功载荷。

### 项目指令

Agent 会从工作区根目录到目标文件所在目录逐级加载 `AGENTS.md`。同目录下的
`AGENTS.override.md` 会替代 `AGENTS.md`；越靠近目标文件的规则越晚加载，也更具体。
涉及多个目录的任务会保留每条规则的适用路径。

README 文件和目录不会自动注入上下文，只有任务驱动搜索选中时才会读取。

会话历史分两层：旧轮次进入增量压缩的滚动摘要，最新消息保持未压缩。成功完成 Chat
或 Agent 响应后才会触发压缩；原始消息会被保留，类似凭据的值会被脱敏，同时保存
乐观锁版本和最后已摘要消息。摘要有长度限制、允许有损，并作为不可信历史上下文注入；
当前请求和实时证据始终优先。

项目记忆是独立的工作区长期子系统，不等同于会话历史或 LangGraph checkpoint，也
不会自动吸收知识库文档。记忆只作为历史线索；系统/项目指令、当前请求和实时源码
始终优先。

## 工作区 API

`workspace_id` 只允许字母、数字、`_`、`-` 和 `.`。根路径必须是规范化绝对路径，
解析符号链接后仍要位于 `WORKSPACE_ALLOWED_ROOTS` 内。

```bash
curl -X PUT http://localhost:8000/api/v1/workspaces/project \
  -H 'content-type: application/json' \
  -d '{"root_path":"/absolute/path/to/project"}'

curl http://localhost:8000/api/v1/workspaces
curl http://localhost:8000/api/v1/workspaces/project
curl http://localhost:8000/api/v1/workspaces/project/token-usage
curl http://localhost:8000/api/v1/sessions/{session_id}/token-usage
```

v1 有意不提供工作区删除接口。更新根目录会递增 `workspaces.revision`，旧 revision 的
记忆不再参与检索。管理员可以明确确认一条旧记录，把它复制到当前 revision；历史记录
本身不会变化。每一条 `agent_runs` 都保留运行开始时捕获的 `workspace_root`。

启动 Agent 运行：

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

响应会暴露 `context_route`、`selected_knowledge_base_ids` 和 `context_sources`。知识块
使用 `kind=knowledge_chunk`，并包含可选的 `knowledge_base_id`、`document_id` 和
`score` 来源字段。已经移除的 `repository_id` 和 `rag_context` Agent 字段不会被接受
或返回。

### 实时源码工具

- `repo.find_files`：按文件名或路径片段定位文件；
- `repo.list_files`：列出工作区相对目录下的路径；
- `repo.search_code`：使用遵循 `.gitignore` 的 `rg`，不可用时回退到 Python；
- `repo.read_file`：读取 UTF-8 文件的指定行区间，并返回真实行号和哈希。

这些工具会拒绝绝对路径、目录穿越、逃逸符号链接、二进制或超大文件、依赖和构建
目录、真实 `.env`、私钥及常见凭据文件。

### 沙箱边界

变更任务会把普通、非敏感工作区文件复制到每次运行独立的目录。真实 `.env`、凭据、
私钥、符号链接、不可读路径、Socket、FIFO 和其他特殊文件会被跳过，并记录在
`copy_warnings` 中。完成、失败或拒绝的运行会删除沙箱；启动时还会清理超过
`SANDBOX_WORKSPACE_TTL_SECONDS` 的目录。

`SANDBOX_MODE=local` 只适用于由本地用户拥有并信任的仓库。它在最小环境中执行
`SANDBOX_ALLOWED_COMMANDS` 里的可执行文件基本名，使用固定最大超时、有界输出捕获和
进程组终止。`sh -c`、`bash -c` 等 Shell 包装器会被拒绝。进入白名单的解释器仍能
执行任意受信仓库代码，所以本地模式不是面向恶意代码的宿主隔离边界。

Docker 模式还会禁用网络、使用只读容器根目录和调用方的非 root UID/GID，移除 Linux
capability，启用 `no-new-privileges`，并限制 PID、CPU、内存及 tmpfs。只有复制后的
`/workspace` 挂载点可写。

## 项目记忆

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
- `auto`：高置信度且有权威来源的候选可以自动转为活跃状态。

用户创建的记录和明确的“记住”请求以 `1.0` 置信度直接生效。其他候选低于 `0.60`
会被丢弃，`0.60` 至 `0.84` 保持待审；在 `auto` 模式下，权威且不低于 `0.85` 的内容
可以生效。只有助手推断的内容永远不会自动生效。

规范化内容相同会追加证据；权威冲突会替代旧记录，不确定冲突继续作为候选。带源码
依据的可变事实会在注入前检查哈希，源码变化后转为 `stale`。长期未确认的记录只会
降低排序，不会仅因时间流逝被删除。

检索使用加权 RRF 合并 Qdrant 稠密召回和 PostgreSQL 全文召回，然后从 PostgreSQL
重新加载每条结果，验证工作区、revision、状态、过期时间和版本。每个候选都有可解释
的最终分数：

```text
0.65 × 归一化相关性
+ 0.20 × 指数时间新鲜度
+ 0.15 × 归一化重要性
```

时间新鲜度使用 `last_confirmed_at`（缺失时回退到 `updated_at`），默认半衰期为 180
天。候选会先全局排序，再应用六条结果和 3,000 字符预算；Chat/Agent 来源信息会暴露
最终分数及三个组成项。Qdrant 失败时降级为词法检索，记忆失败不会导致主 Chat 或
Agent 回答失败。

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

`ChatStreamRequest.workspace_id` 对旧客户端仍是可选字段。传入后，Chat 会在答案
Token 前发送 `memory_context`，并在响应完成后排队抽取任务。Agent 在上下文路由后
执行 `retrieve_project_memory`，只有运行成功完成后才按 `run_id` 排队抽取。

默认配置保持该子系统关闭：

```dotenv
PROJECT_MEMORY_ENABLED=false
PROJECT_MEMORY_MODE=off
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
```

会话压缩单独配置：

```dotenv
CONVERSATION_SUMMARY_ENABLED=true
CONVERSATION_SUMMARY_TRIGGER_MESSAGES=12
CONVERSATION_SUMMARY_KEEP_RECENT_MESSAGES=6
CONVERSATION_SUMMARY_MAX_CHARS=2000
CONVERSATION_SUMMARY_MAX_SOURCE_CHARS=12000
```

## 独立知识库

导入文档前，先创建目录元数据：

```text
POST   /api/v1/knowledge-bases
GET    /api/v1/knowledge-bases
GET    /api/v1/knowledge-bases/{knowledge_base_id}
PUT    /api/v1/knowledge-bases/{knowledge_base_id}
DELETE /api/v1/knowledge-bases/{knowledge_base_id}
```

目录记录包含不可变 ID，以及可编辑的名称、描述和标签。删除时先移除向量，再级联删除
PostgreSQL 文档与分块。文档 RAG 接口可以独立使用：

```text
POST /api/v1/knowledge-bases/{knowledge_base_id}/documents
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

只有文档接口会使用分块、嵌入和配置的向量存储。Agent 的 RAG 路由调用同一套搜索
实现；检索不可用时会降级为证据警告。

每次上传都会生成可查询的索引任务，状态依次为
`pending → parsing → embedding → vector_written → active`。任意非终态失败会记录为
`failed`，并保存长度受控的错误消息。向量替换会先写入新 Point，再删除旧 Point，
因此嵌入失败不会提前删除上一版可搜索数据。

搜索包含两个独立召回通道：

1. 从已配置向量存储执行稠密相似度召回；
2. 本地使用内存 BM25，持久化运行时使用 PostgreSQL 全文搜索进行词法召回。

两路排序通过加权倒数排名融合（RRF）合并，再由可选 CrossEncoder 重排器选出最终
结果。响应会暴露原始稠密/词法分数、`dense_rank`、`lexical_rank`、`fusion_score`
和可选重排分数。`RAG_LEXICAL_WEIGHT` 控制 RRF 通道权重，`RAG_RRF_K` 控制排名
平滑。

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

历史迁移会继续保留在 revision 链中。只有 PostgreSQL 结果加载器会兼容含有
`repository_id`/`rag_context` 的历史 JSON；新 API 和新运行只暴露 workspace 契约。

Celery 运行要求 API 和 Worker 使用共享存储、相同挂载及相同允许根目录：

```dotenv
TASK_QUEUE_BACKEND=celery
SESSION_REPOSITORY=postgres
AGENT_RUN_STORE=postgres
DOCUMENT_STORE=postgres
WORKSPACE_STORE=postgres
LANGGRAPH_CHECKPOINTER=postgres
RAG_VECTOR_STORE=qdrant
PROJECT_MEMORY_ENABLED=false
PROJECT_MEMORY_MODE=off
WORKSPACE_ALLOWED_ROOTS=/srv/workspaces
```

持久化运行时各数据库职责如下：

| 组件 | 职责 |
| --- | --- |
| PostgreSQL | 会话/消息、用户默认值、会话配置和滚动摘要、Agent 运行与不可变模型快照、工作区/知识库目录、项目记忆事实/证据/任务/Outbox/审计、文档/分块元数据、词法搜索和 LangGraph checkpoint |
| Qdrant | 相互独立的知识库和项目记忆向量集合；项目记忆载荷最小且可重建 |
| Redis | Celery Broker 和结果后端；不是业务记录的事实来源 |
| Chroma | 可选的嵌入式/单节点向量存储，用于替代 Qdrant |

`RAG_VECTOR_STORE` 在 Qdrant 和 Chroma 中二选一。二者实现同一向量存储边界，不会
被同时写入。仓库示例和当前持久化运行时使用 Qdrant；内存 Repository 只作为显式
测试替身。

启动 API 和 Celery Worker 前，先启动依赖并应用数据库迁移：

```bash
docker compose up -d postgres adminer qdrant redis
.venv/bin/alembic upgrade head
.venv/bin/celery -A ai_agent_platform.workers.celery_app:celery_app worker
```

Compose 中 Gateway、PostgreSQL、Qdrant、Redis 和 Adminer 的端口都绑定到
`127.0.0.1`。PostgreSQL 凭据来自 `.env`；`scripts/start.sh` 会根据 `DATABASE_URL`
推导 Compose 变量，直接运行 `docker compose` 则需要 `.env.example` 所示且相互匹配
的 `POSTGRES_DB`、`POSTGRES_USER` 和 `POSTGRES_PASSWORD`。

### 使用 Adminer 浏览 PostgreSQL

Compose 栈包含仅绑定本机的 Adminer Web 界面。启动 `postgres` 和 `adminer` 后打开
<http://localhost:8081>，填写：

| 字段 | 值 |
| --- | --- |
| 系统 | PostgreSQL |
| 服务器 | `postgres` |
| 用户名 | 本地 `POSTGRES_USER` 的值 |
| 密码 | 本地 `POSTGRES_PASSWORD` 的值 |
| 数据库 | 本地 `POSTGRES_DB` 的值 |

服务器名必须填写 `postgres` 而不是 `localhost`，因为 Adminer 通过 Compose 内部网络
连接 PostgreSQL。此本机端口绑定只面向开发；未添加适当访问控制前不要公开 Adminer。

Worker 会注册 Agent 启动/恢复、幂等会话压缩、记忆抽取和独立的项目记忆索引 Outbox
消费任务。运行开始时捕获的根目录无法访问时，任务会以结构化
`workspace_unavailable` 消息失败。失败的记忆抽取任务保留尝试次数，Celery 可以重试
同一个来源；已完成的 `source_type + source_id` 保持幂等。

## 可选 Go 网关

`gateway/` 服务提供请求准入、Request ID 传递、可选的 RS256 JWKS OIDC/JWT 校验、
健康与就绪探针、SSE 安全代理和优雅停机：

```bash
go run ./gateway/cmd/gateway
go test ./gateway/...
go vet ./gateway/...
```

生产环境应配置 `GATEWAY_AUTH_MODE=oidc`、Issuer、Audience、JWKS URL 和共享的
`GATEWAY_TRUST_SECRET`；FastAPI 则配置 `AUTH_MODE=trusted_header` 和相同 Secret。
网关会移除伪造身份 Header、验证 Bearer Token、剥离 Token，再注入可信 Subject。
本地开发可以让两侧认证模式都保持关闭。

这是会话和工作区记忆的可信身份边界，并不代表每一个旧版知识库接口都已具备完整的
多租户授权。

## 验证

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall ai_agent_platform tests evals
node --check ai_agent_platform/static/app.js
go test ./gateway/...
git diff --check
```

运行离线 Agent 评估：

```bash
.venv/bin/python evals/run_evals.py
.venv/bin/python evals/run_memory_evals.py
```

---

[查看英文版 README](README.en.md)
