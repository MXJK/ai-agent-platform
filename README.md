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
- [CLI、REPL、SDK 与进程入口](#clireplsdk-与进程入口)
- [分层运行时配置](#分层运行时配置)
- [Skill 发现与 slash command](#skill-发现与-slash-command)
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
.venv/bin/python -m pip install -e .
cp -n .env.example .env
# 将 POSTGRES_PASSWORD 与 DATABASE_URL 中的示例 PostgreSQL 密码
# 替换为同一个仅供本地使用的随机密码。
./scripts/start.sh --apply-migrations
```

首次完成环境初始化后，先审阅待处理的 Alembic revision，再由操作者用
`./scripts/start.sh --apply-migrations` 显式授权升级。脚本会验证持久化配置，启动
PostgreSQL、Qdrant 和 Redis，等待服务就绪，执行迁移，然后同时启动 Celery Worker 与
FastAPI。未给出该参数时脚本会在迁移和 runtime 启动前停止；按 `Ctrl+C` 会停止 API 和
Worker，持久化数据库容器仍会继续运行。

常用启动选项：

```bash
./scripts/start.sh --check  # 仅检查依赖和配置，不执行写操作。
APP_RELOAD=0 ./scripts/start.sh --apply-migrations
APP_PORT=8001 ./scripts/start.sh
```

Web UI 默认地址为 <http://127.0.0.1:8000>。页面由 FastAPI 直接提供，不需要单独
构建前端。示例配置使用 Fake LLM 和本地嵌入提供方，不需要 API Key。
当 `AUTH_MODE=disabled` 时，启动脚本会直接拒绝非回环地址的 `APP_HOST`，而不是
只输出运维警告。

## CLI、REPL、SDK 与进程入口

可安装入口全部是 `RuntimeContainer` 和 `QueryService` 之上的薄适配器：

```bash
# 一个 Query；stdout 每行都是 AgentEvent 的 JSON，适合脚本消费。
.venv/bin/ai-agent --workspace /absolute/path/to/project print "解释入口结构"

# 同一 conversation 的多轮交互。
.venv/bin/ai-agent --workspace /absolute/path/to/project repl

# 只启动 FastAPI/uvicorn HTTP 入口；未认证模式仍强制 loopback。
.venv/bin/ai-agent-api --host 127.0.0.1 --port 8000
```

REPL 内置 `/skills`、`/tools`、`/mcp`、`/permissions`、`/resume` 和 `/exit`。
普通输入在同一个 session 中逐轮创建 Query；运行期间按 `Ctrl+C` 会向当前 Run 发送
`cancel`，不会把信号处理安装进 SDK、Service 或领域模型。`/resume [run_id]
[approve|deny] [message]` 对 `waiting_approval` 使用 resume，对 `waiting_input`/`paused`
使用 continue。print mode 和 REPL 都直接输出 `AgentEventEncoder` 的七字段事件 Schema，
不另造 CLI 事件模型。

Python SDK 同样返回领域契约：

```python
from ai_agent_platform.domain import QueryParams
from ai_agent_platform.sdk import AgentSDK

async def run() -> None:
    with AgentSDK.from_settings() as sdk:
        events = sdk.query(QueryParams(
            conversation_id="sess_existing",
            workspace_id="project",
            message="解释 RuntimeContainer",
        ))
        last = None
        async for event in events:       # AgentEvent
            last = event
        result = sdk.result(last.run_id) # QueryResult
```

`AgentSDK.query()`/`resume()` 返回 `AsyncIterator[AgentEvent]`，`control()`/`result()`
返回 `QueryResult`。SDK 不拥有进程信号；只有 `AgentSDK.from_settings()` 创建的 facade
会在 `close()` 时释放自己的容器。FastAPI 只在 lifespan 关闭容器；Celery 则在
`worker_process_init` 后创建每进程单例，并在 `worker_process_shutdown` 关闭，避免 fork
前创建连接或在 task handler 中重组依赖。`scripts/start.sh` 的 uvicorn target 也已收敛到
`ai_agent_platform.api.entrypoint:app`。

## 分层运行时配置

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
    "tool_allowlist": ["file_symbol_locator", "repo.search_code"]
  },
  "runtime": {
    "llm_model": "example-model",
    "sandbox_mode": "docker",
    "session_token_budget": 50000
  },
  "project_session": {
    "project_instructions": ["先运行受影响的测试。"],
    "enabled_tools": ["file_symbol_locator"],
    "skills_enabled": true,
    "enabled_skills": ["review"],
    "mcp_enabled": false
  }
}
```

用户文件和进程环境可以建立进程策略；项目文件不能修改数据库、认证、API Key/
Secret 后端、允许根目录、真实写入开关或 MCP 配置路径，也不能把 Docker sandbox
镜像交给项目选择、把 Docker 降为 local、放宽审批语义、扩张命令/工具/Skill allowlist，
或越过 `mcp_allowed=false`、
`skills_allowed=false`。项目层允许选择更小的权限集合；进程 `tool_allowlist` 只在启动
时裁剪全局能力上限。每个 Run 再从进程 Registry 建立带 base/local/MCP 来源和 namespace
的不可变 `ToolCatalog`，由共享 `ToolPoolBuilder` 将项目 `enabled_tools`、Agent/模式、模型
能力、Workspace role、中央 display deny、显式 deny、Sandbox 和 Skill 依赖求交为
`EffectiveToolPool`，不修改源 `ToolRegistry`。未知工具、大小写冲突、保留 namespace
冒用、尝试突破进程上限或非法恢复都会 fail closed。环境变量
继续兼容既有无前缀名称和 `.env`，包括 `GEMINI_API_KEY` 以及
`SESSION_REPOSITORY`/`AGENT_RUN_STORE` 的旧回退关系；新的
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

启用 `skills_enabled` 后，运行时只发现以下目录中的 `SKILL.md`：

- bundled：`ai_agent_platform/bundled_skills/<skill>/SKILL.md`；
- user：`~/.ai-agent-platform/skills/<skill>/SKILL.md`；
- project：已鉴权 Workspace 下的 `.agents/skills/<skill>/SKILL.md`。

来源优先级固定为 `project > user > bundled`，限定名分别是
`project:<name>`、`user:<name>` 和 `bundled:<name>`。同一来源的重复名称按相对路径
字典序选择第一项并产生错误诊断；跨来源覆盖、slash command/alias 冲突也产生稳定
诊断。最终 Skill 与 command 都按规范化名称排序。项目 Skill 即使覆盖同名用户或
bundled Skill，仍会标记为 `untrusted_project_skill`，在 Run 入队前以不可信项目
上下文冻结。

最小 `SKILL.md` 使用严格、无重复键的 YAML frontmatter：

```markdown
---
name: review
description: Review requested code changes
agents: [coding]
modes: [default]
context_budget: 4000
tools: [repo.search_code, repo.read_file]
command:
  name: review
  description: Review code in the current Workspace
  usage: "[path]"
  aliases: [rv]
---
Inspect live evidence before giving review findings.
```

第一版只接受上述字段。每个文件最多 64 KiB，每次发现最多 64 个候选、最多加载
128 KiB 字符；单个 Skill 的上下文预算上限为 16,000 字符，最终还受 Run 的项目
指令总预算约束。错误 UTF-8、损坏/重复 YAML、未知字段、超限文件和单个坏 Skill
只产生诊断，不会终止其余发现。来源根、子目录或 `SKILL.md` 中的 symlink 都不会被
跟随，真实路径必须留在对应来源根内。

Skill 是纯声明数据：系统不会执行同目录 Python/Shell，也不会从 Markdown 注册函数。
`tools` 只是所需工具名称；缺少本次 Run 已筛选工具时 Skill 不进入上下文，即使工具
存在，调用仍受既有 `ToolUseContext`、Sandbox 和 allow/ask/deny 规则约束。Skill
不能注册工具、降低审批、扩大 allowlist 或授予权限。Skill 指令的快照优先级低于
Workspace 指令文件和项目配置指令。REPL 对非内置 slash command 使用当前 Workspace 的
有效 Skill catalog 解析覆盖、启用列表、Agent/模式和 `required_tools`；成功后提交普通
`QueryParams(skill_name, skill_arguments)`，并在入队前把选中 Skill 指令与 invocation
元数据冻结。未知、禁用或缺依赖命令返回稳定诊断；注册表只保存元数据，不执行 Skill
目录代码，也不扩大工具池或绕过 `PermissionResolver`。

## 主要能力

统一输入框提供两种模式：

- `快速对话`：直接返回模型的 SSE 流式响应；
- `代码 Agent`：围绕任务探索工作区，展示审批、进度和产物；
- 两种模式共享会话历史和持久化滚动摘要。压缩后的历史与数量受控的近期消息可以
  共同参与 Chat、Agent 探索和原生工具选择，同时保留原始消息。

两种响应都会在消息内展示执行过程和用量指标。Chat 使用模型提供方的 SSE 用量；
Agent 汇总结构化规划和答案生成阶段由提供方上报的用量。界面会显示每条响应的
输入、输出、思考和总 Token。Agent cursor SSE 会直接驱动实时 LangGraph Trace，只在
终态读取一次完整 Run 快照；断流时才回退轮询。因此即使任务很快完成，前端也能用
有界的短暂回放按顺序展示已完成阶段，并稳定保留最终答案和终态。

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
- 受 `WORKSPACE_ALLOWED_ROOTS` 约束的本地工作区文件夹管理，可切换当前工作区、设置
  新会话默认值、重新关联失效路径和安全移除注册；统一输入框不重复展开代码上下文，
  当前工作区通过左侧工作上下文或设置管理，独立代码 Agent 输入区持续显示其可用状态
  与角色；本机 macOS 点击“添加文件夹”会打开 Finder 系统文件夹窗口，系统窗口不可用
  时才回退网页目录浏览器；未配置该变量时默认从当前用户主目录选择，部署环境应显式
  配置最小允许根目录；
- Agent 审批、追问和暂停检查点直接显示在对应的助手消息中，可就地确认、拒绝或补充
  要求；运行详情继续提供完整风险、验证产物、错误和指标；刷新或重新进入会话时会恢复
  该会话最近一次未完成 Run，避免把 `waiting_approval` 误认为卡死；
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

浏览器 `localStorage` 只保存设备级 UI 状态，不保存用户 ID，也不重复保存会话配置。
本地单用户模式使用内部身份，认证模式由可信网关注入身份。健康检查接口会暴露
`session_storage` 和 `persistent_sessions`；如果使用内存
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

## 动态模型准入与 Token 预算

持久化模型注册中心是聊天与 Agent 的唯一运行时模型准入来源。通过前端模型管理页
注册并启用模型、同时启用对应 Provider 连接后，该模型即可用于手动选择和自动路由，
无需在 `.env` 维护 Provider/Model 白名单。未注册、已停用的模型或已停用的 Provider
连接仍会在计数和生成请求发出前被拒绝。

`LLM_PROVIDER`、`LLM_MODEL` 和 `LLM_MODEL_CATALOG_JSON` 只负责空注册中心的启动导入与
兼容，不会形成第二份静态准入策略；持久化注册中心建立后，前端的启用/停用状态立即
影响运行时目录，无需重启。

会话和工作区预算会统计归属于对应范围的所有账本记录：

```dotenv
SESSION_TOKEN_BUDGET=100000
WORKSPACE_TOKEN_BUDGET=1000000
TOKEN_BUDGET_ACTION=reject
```

`0` 表示关闭对应范围。使用 `reject` 时，如果请求无法至少保留一个输出 Token，系统
会在提交用户消息或调用模型之前拒绝请求；允许执行的请求也会把 Provider 输出上限
限制到剩余硬预算。API 返回 `429` 和 `code=token_budget_exceeded`。

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
   │                    ↑ 零命中/失败/未读候选时换策略 ─────┘
   ├─ rag    → retrieve_knowledge
   ├─ hybrid → retrieve_knowledge → repository exploration
   └─ none
→ merge_evidence
```

分类器只接收一个受控目录，其中包括知识库 ID、名称、描述和标签。它会选择 `none`、
`repo`、`rag` 或 `hybrid`，最多选中三个托管知识库。仓库证据仍来自实时文件，文档
证据复用独立的 RAG 搜索栈。项目记忆最多贡献六条当前 revision 的活跃记录，总预算
为 3,000 字符。未显式指向知识库的通用“项目介绍”问题强制使用 `repo`，先发现并
读取 README、项目清单和入口文件，避免无关托管文档填补源码证据真空。

`merge_evidence` 会在工具或变更规划、答案生成之前保留所有证据来源。变更任务继续
使用人工审批、每次运行独立的沙箱副本、验证、一次有界修复，以及 Diff/测试产物；
终态会在沙箱清理前把完整补丁持久化为 ChangeSet。默认 `patch_only` 不修改源工作区；
只有显式启用真实写入、可信身份 editor 二次批准且补丁摘要与逐文件基线哈希均通过时，
服务才按 `direct` 或 `worktree` 模式应用。

运行中的 Agent 状态同时读取产品运行存储和最近的 LangGraph checkpoint，因此 API
可以在任务仍执行时暴露已经完成的 Trace 节点。最终指标包括耗时、节点和工具数量、
修改文件、已恢复错误，以及 Provider 上报的输入、输出、思考和总 Token。

默认探索预算：

- 4 轮探索；
- 每轮 6 个只读工具；
- 12 个不同源码文件；
- 32,000 个源码证据字符；
- 16,000 个项目指令字符。

探索采用 `targeted_search → broaden_file_inventory → read_discovered_entries` 等可审计
策略：零命中、工具错误或尚有未读候选都会继续观察并换策略；空计划本身不代表证据
充分。只有取得相关仓库证据或明确耗尽轮数/文件/字符预算才会离开探索循环。重复工具
调用、相同行区间和重复内容不会再次消耗证据预算；预算耗尽且仍无证据时会记录明确
警告，回答必须标注不确定性。

### 原生工具调用循环

OpenAI、Anthropic 和 Google 适配器会通过各 Provider 原生的 Function/Tool Calling
API 发送 `ToolSpec`。生产模型不再通过 Prompt 文本生成 JSON 工具计划。Provider
特有的函数调用会被标准化为 `LLMToolDecision`，其中包含稳定的 tool-call ID；带点号
的注册名会转换为 Provider 安全别名，并在执行前映射回原名。Fake Provider 保留
确定性规则规划，用于离线测试。

原生模型采用统一、有界的“观察—决策—执行”循环。读文件、写 Sandbox、运行验证、
获取状态与 Diff 都由同一次模型会话按原顺序选择，不再把读、写、验证拆成彼此看不到
结果的固定阶段：

```text
原生工具调用
→ EffectiveToolPool 只暴露本 Run 冻结且校验通过的 ToolSpec
→ PermissionResolver 以 ToolUseContext 判定 allow/ask/deny
→ ToolRegistry 在执行点复判并校验/执行
→ 通过 call ID 关联结果或错误
→ Provider 原生工具结果消息
→ 模型观察后继续调用工具或作答
```

Agent Loop 的实现按职责拆分：`graph_builder` 只声明既有节点和边，
`context_nodes` 负责上下文/检索，`tool_loop_nodes` 负责工具循环，`policies` 负责完成、
预算与控制策略，`tool_access` 负责每 Run 工具视图与权限投影，
`run_recorder` 负责 Run/事件/ChangeSet 收尾，
`checkpoint_coordinator` 负责 invoke/resume 和 checkpoint 查询。
`CodingAgentRuntime` 保留为兼容 facade；API、Worker、Service 和 Repository 只交换
`RunContextSnapshot`、`AgentRunRecord` 与 `AgentRunResult`，不接触 LangGraph state。
拆分由稳定轨迹 golden tests 约束，节点名、边、审批、预算、工具顺序和终态语义不变。

默认软预算是 12 轮/36 次工具调用，触发后只提示模型尽快收敛；硬预算是 24 轮/72 次，
另有 900 秒、连续三轮无进展和连续三次失败保护。硬停止会保留一次禁用工具的文本
最终总结，因此返回 `partial`/`blocked`，不会把预算耗尽误报为 `completed`。相关配置为
`AGENT_SOFT_TOOL_ROUNDS`、`AGENT_MAX_TOOL_ROUNDS`、`AGENT_SOFT_TOOL_CALLS`、
`AGENT_MAX_TOOL_CALLS`、`AGENT_MAX_ELAPSED_SECONDS`、`AGENT_NO_PROGRESS_ROUNDS` 和
`AGENT_MAX_CONSECUTIVE_FAILURES`。工具会话超过 `AGENT_NATIVE_CONTEXT_MAX_CHARS` 时按完整
assistant/tool 组压缩旧观察，避免拆断 call/result 对。图的独立保险由
`AGENT_GRAPH_RECURSION_LIMIT` 控制。

每次工具使用都构造不可变 `ToolUseContext`，携带已鉴权身份与 Workspace role、登记
root、进程能力上限、冻结的项目工具选择、审批策略以及当前调用身份。统一
`PermissionResolver` 返回 `allow`、`ask` 或 `deny`，并附匹配规则、原因和风险摘要。
进程 deny、Workspace root 边界和身份 RBAC 是不可覆盖硬拒绝；显式 deny 优先，项目配置
只能继续收紧。展示给模型的工具可先过滤 deny，但 `ToolRegistry` 执行前仍无条件复判，
避免展示和执行之间的 TOCTOU。MCP/Skill 的权限注解只作为不可信风险输入，不能自行
产生最终授权。

所有成功或失败结果都会按 call ID 回灌。相同 `(run_id, call_id)` 的完成结果可从内存或
PostgreSQL 工具执行账本重放，参数哈希变化会拒绝；PostgreSQL 还保存 append-only Run
事件。模型可调用 `agent.request_user_input` 进入 `waiting_input`。用户也可在安全工具边界
暂停、继续、取消或发送转向信息。审批策略通过 `AGENT_APPROVAL_POLICY=always|on_request|never`
配置；`never` 对需要 `ask` 的调用返回 `deny`，不是静默授权。批准精确绑定
`run_id + call_id + tool name + canonical arguments SHA-256`，跨 Run/调用/工具重放或参数
变化都会重新进入审批。项目把 `on_request` 改成 `always` 或 `never` 都可收紧；进程已为
`always` 或 `never` 时，项目不能切换到另一种策略造成部分调用重新放行。

`ToolRegistry` 在注册时校验完整的 Draft 2020-12 JSON Schema，并在执行时校验输入
和输出。工具规格还声明超时、重试和幂等行为。只有幂等工具遇到可重试失败时才会重试；
相同 `run_id + call_id` 会重放缓存结果，参数变化则会被拒绝。MCP 工具使用同一注册
契约：优先读取 `structuredContent`，文本块会被标准化，`isError=true` 会变成稳定的
工具失败，而不是成功载荷。

### MCP 生命周期与传输

MCP 默认通过精确锁定的官方 Python SDK `mcp==2.0.0` 协商当前 `2026-07-28`
协议。`stdio` 和无会话的 `streamable_http` 是当前路径；原有固定
`2025-06-18` 客户端仅由 `stdio_2025_06_18` 显式选择，旧 HTTP+SSE 仅由
`legacy_sse` 加 `legacy_compatibility=true` 启用，不参与默认回退。架构取舍记录在
[`docs/adr/0001-mcp-official-python-sdk-v2.md`](docs/adr/0001-mcp-official-python-sdk-v2.md)。

`MCPConnectionManager` 为每个 Server 独立拥有连接、事件循环、连接/请求超时、幂等
重试、指数退避、熔断、缓存、取消、关闭和脱敏状态。启动会隔离单 Server 故障；只有
`required=true` 且未就绪的 Server 会令 `/api/v1/health` 返回 `503` 和
`ready=false`，可选 Server 故障只使健康状态降级。`tools/list` 会遍历全部分页、拒绝
重复 cursor/工具名、按名称确定性排序，遵循 `ttlMs`/`cacheScope` 提示，并支持显式
refresh。工具调用沿用 ToolRegistry 的稳定 call ID；超时、取消、连接关闭、熔断和远端
工具失败映射为稳定错误码。

HTTP Server 必须使用显式 host allowlist；HTTPS 是默认要求，重定向和代理环境被禁用，
解析到私网/本机地址默认拒绝。凭据只能通过共享 `SecretStore` 引用注入
`header_refs`/`env_refs`，不会进入配置快照、诊断或 repr。Header 控制字符、保留
MCP/HTTP Header、危险 stdio 环境变量和继承的 API Key 都会被阻断。MCP 权限注解始终先
进入中央 `PermissionResolver`，缺失或高风险提示按外部副作用保守处理，不能直接授权。

本机管理模式可直接打开工作台的「MCP 连接」（`/#mcp`）注册、编辑、测试/刷新、启停
或删除 Server。前端支持当前 stdio、当前 Streamable HTTP 以及两条显式兼容路径，展示
独立连接状态、协议版本、重试错误和发现/注册工具数。启用界面写入至少要设置
`MCP_CONFIG_PATH=/path/to/mcp.json`；文件可以尚不存在，首次保存会以 `0600` 原子创建。
同时设置 `MCP_ENABLED=true` 时，保存会立即替换该 Server 的连接并同步 ToolRegistry；
否则配置会保存并标记为等待重启。普通环境变量/Header 与 Secret 输入分开，Secret 值只
写共享 `SecretStore`，配置文件和后续 GET 响应只保留引用或键名。管理写接口仅在
`AUTH_MODE=disabled` 的 loopback 本机模式开放。

管理 API 为 `GET /api/v1/mcp/servers`、
`PUT /api/v1/mcp/servers/{name}`、
`PATCH /api/v1/mcp/servers/{name}/enabled`、
`POST /api/v1/mcp/servers/{name}/test` 和
`DELETE /api/v1/mcp/servers/{name}`。这些变更只影响目标 Server；停用或删除会关闭其连接
并原子移除对应的动态工具，不会重建其他 Server 的生命周期。

最小配置示例：

```json
{
  "mcp_servers": {
    "local-tools": {
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "example_mcp_server"],
      "env_refs": {"EXAMPLE_TOKEN": "keyring:mcp/example"},
      "required": false
    },
    "remote-tools": {
      "transport": "streamable_http",
      "url": "https://mcp.example.com/mcp",
      "allowed_hosts": ["mcp.example.com"],
      "header_refs": {"Authorization": "keyring:mcp/remote"},
      "required": true
    }
  }
}
```

### 项目指令

Agent 会从工作区根目录到目标文件所在目录逐级加载 `AGENTS.md`。同目录下的
`AGENTS.override.md` 会替代 `AGENTS.md`；只有两者都不存在时才兼容读取 `CLAUDE.md`，
因此不会改变既有 AGENTS 优先级。越靠近目标文件的规则越晚加载，也更具体；涉及多个
目录的任务会保留每条规则的适用路径。配置中的 `project_session.project_instructions`
作为最低优先级 `config_instruction` 追加，文件指令先消费同一个
`AGENT_MAX_INSTRUCTION_CHARS` 预算；每个来源冻结 kind/path、priority、完整内容 hash 和
截断状态。

指令文件通过以可信 Workspace root 为锚的目录描述符读取，目录和文件打开都禁止跟随
symlink，并要求普通文件；读取前后还会核对 inode、类型、大小和时间元数据以及目录链。
symlink、路径逃逸、FIFO/socket 等非普通文件或读取过程替换都会阻断 Run，工作区外正文
不会进入快照。

`QueryService` 是 HTTP、CLI/SDK 适配器和 Worker 共用的入口无关命令内核。
`QueryParams` 固定 conversation/message、Workspace、focus files、模型/模式覆盖、可选
Skill invocation 和入口元数据；
`QueryCommand` 统一 start/resume/continue/steer/pause/cancel；`AgentEvent` 与 `QueryResult`
分别固定游标事件和终态/恢复结果。FastAPI 仍在 start/resume/continue 入队后立即返回 `202`，
不会等待 Agent Loop。

`ExecutionContextFactory` 在 start 入队前一次性冻结 Identity、受控会话历史/摘要/模型、
Workspace revision/root/cwd/Git 摘要、Workspace 有效配置版本、项目指令、schema v3
Effective Tool Pool 快照和额外目录，形成
可 JSON 往返的深度不可变 `RunContextSnapshot`。用户消息、queued Run 及其中的模型/配置/
上下文/工具快照在同一 Query UoW 中提交，提交成功后才派发只含 `run_id` 的 Worker 任务。
内建运行时要求 Session 与 Run store 使用同一受支持后端，无法建立原子 UoW 时启动即失败。
Worker 重启后从 Run store 恢复快照，不重新读取已经变化的会话历史、模型偏好或指令文件。
v3 恢复会校验 catalog/pool 摘要与 hash、工具顺序、当前 Registry 中的 callable 以及
Schema/provider/权限/超时/重试等定义；新增无关全局工具不会进入旧 Run，缺失、漂移或
篡改则在模型和工具执行前产生安全的 `tool_pool_restore_failed` 终态。v1/v2 历史快照
仍通过明确的 legacy `ToolRegistryView` 路径加载。
Git 缺失、非仓库、无 HEAD 或状态读取失败只记录诊断，不会无条件拒绝 Run。

README 文件和目录不会无条件注入上下文；通用项目概览会先列举工作区并优先读取
README/项目清单，其他任务仍只读取搜索或文件发现选中的路径。

会话历史分两层：旧轮次进入增量压缩的滚动摘要，最新消息保持未压缩。成功完成 Chat
或 Agent 响应后才会触发压缩；原始消息会被保留，类似凭据的值会被脱敏，同时保存
乐观锁版本和最后已摘要消息。摘要有长度限制、允许有损，并作为不可信历史上下文注入；
当前请求和实时证据始终优先。

项目记忆是独立的工作区长期子系统，不等同于会话历史或 LangGraph checkpoint，也
不会自动吸收知识库文档。记忆只作为历史线索；系统/项目指令、当前请求和实时源码
始终优先。

## 工作区 API

`workspace_id` 只允许字母、数字、`_`、`-` 和 `.`。根路径必须是规范化绝对路径，
解析符号链接后仍要位于 `WORKSPACE_ALLOWED_ROOTS` 内。同一个规范化根路径只能登记为
一个工作区；使用另一个 ID 重复登记会返回 `409`。列表与详情响应还会返回 `status`、
`role` 和 `can_update`，供客户端区分路径可用性和当前用户能力。本机未设置或留空
`WORKSPACE_ALLOWED_ROOTS` 时，默认允许当前用户主目录，因此“添加文件夹”可直接浏览
桌面、文稿和主目录下的其他项目；选择列表默认隐藏点目录。容器、多用户或远端部署
应显式收紧此配置。

本机 `AUTH_MODE=disabled` 且请求来自 loopback 时，前端会调用
`POST /api/v1/workspace-directory-picker`，由同机服务打开 macOS Finder 文件夹选择窗口。
取消窗口不会产生变更，选中路径仍必须通过 `WORKSPACE_ALLOWED_ROOTS` 校验；启用认证、
远端请求或系统选择能力不可用时不会触发服务器桌面窗口，前端会在能力不可用时回退
受控网页目录浏览器。

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

在 `AUTH_MODE=trusted_header` 下，目录浏览也要求可信网关身份；会话配置和用户默认值
只能引用当前用户至少拥有 viewer 权限的工作区。viewer 可以启动只读分析，但批准包含
写入或外部副作用工具的计划至少需要 editor 权限，且 Worker 执行前会再次授权。

工作区响应中的 `available` 表示已保存路径当前是否仍可读取。`DELETE` 是软移除：它只
让工作区退出可选列表，不删除本地文件，也不级联删除历史会话、用量或项目记忆；再次
以相同 ID 注册即可恢复。相同路径恢复保持原 revision，只有根路径真正变化时才递增
`workspaces.revision`，旧 revision 的记忆不再参与检索。管理员可以明确确认一条旧
记录，把它复制到当前 revision；历史记录本身不会变化。每一条 `agent_runs` 都保留
运行开始时捕获的 `workspace_root`。

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

运行创建后可使用以下生命周期接口：

```text
GET  /api/v1/agent/runs/{run_id}
GET  /api/v1/sessions/{conversation_id}/agent/runs/latest
GET  /api/v1/agent/runs/{run_id}/events?after={cursor}
GET  /api/v1/agent/runs/{run_id}/events/stream?cursor={cursor}
POST /api/v1/agent/runs/{run_id}/pause
POST /api/v1/agent/runs/{run_id}/continue   {"message":"补充方向或问题答案"}
POST /api/v1/agent/runs/{run_id}/steer      {"message":"新的执行方向"}
POST /api/v1/agent/runs/{run_id}/cancel
POST /api/v1/agent/runs/{run_id}/resume     {"approved":true,"feedback":"审批说明"}
GET  /api/v1/agent/runs/{run_id}/changes
POST /api/v1/agent/runs/{run_id}/changes/reject {"change_set_id":"chg_xxx"}
POST /api/v1/agent/runs/{run_id}/changes/apply  {"change_set_id":"chg_xxx","patch_sha256":"<64 hex>"}
```

轮询、SSE 与 QueryService 的异步迭代器都读取同一个 append-only EventStore，并通过同一
`AgentEventEncoder` 编码；事件的 sequence cursor 可用于断线恢复。浏览器工作台直接从
SSE 事件增量构造轨迹，在终态读取一次完整 Run 快照，连接失败或提前结束时回退到状态
轮询。终态包括 `completed`、
`partial`、`blocked`、`cancelled` 和 `failed`，交互暂停态包括 `waiting_approval`、
`waiting_input` 和 `paused`。加载历史会话时，前端通过会话级 latest Run 接口恢复最近
一次运行，并把审批、追问或暂停控件重新挂回原助手消息；生命周期观察器绑定 Run 和
会话 ID，切换会话不会让旧 Run 的晚到事件覆盖当前页面。最终助手消息用持久化
`source_run_id + role` 唯一键确保只写一次；Worker 重投可补写崩溃窗口内缺失的消息，已写入
时则安全跳过。

响应会暴露 `context_route`、`selected_knowledge_base_ids` 和 `context_sources`。知识块
使用 `kind=knowledge_chunk`，并包含可选的 `knowledge_base_id`、`document_id` 和
`score` 来源字段。已经移除的 `repository_id` 和 `rag_context` Agent 字段不会被接受
或返回。

ChangeSet 是模型工具审批之后的独立落盘边界。读取/拒绝/应用也进入同一
`PermissionResolver` 的 Workspace role/root 判定，同时保留服务内纵深校验。它保存不可
截断补丁、SHA-256、变更文件、
Sandbox 基线哈希、workspace root/revision、验证摘要和状态。viewer 可查看，editor 才能
拒绝或应用；apply 还会重新校验登记根、符号链接、敏感/二进制路径、文件并发修改和用户
确认的摘要。重复应用同一已完成 ChangeSet 返回相同结果；冲突不会覆盖用户的新修改。
`direct` 失败会恢复原文件，`worktree` 从捕获的 Git HEAD 创建受控 `codex/` 分支和
工作树，源目录保持不变。应用不会自动 commit、push、建 PR、merge 或部署。

### 实时源码工具

- `repo.find_files`：按文件名或路径片段定位文件；
- `repo.list_files`：列出工作区相对目录下的路径；
- `repo.search_code`：使用遵循 `.gitignore` 的 `rg`，不可用时回退到 Python；
- `repo.read_file`：读取 UTF-8 文件的指定行区间，并返回真实行号和哈希。

这些工具会拒绝绝对路径、目录穿越、逃逸符号链接、二进制或超大文件、依赖和构建
目录、真实 `.env`、私钥及常见凭据文件。文件列举会逐项跳过符号链接、越界解析目标
及 `.venv-*` 等忽略目录，不会因单个不安全条目让整个目录发现失败。

### 沙箱边界

变更任务会把普通、非敏感工作区文件复制到每次运行独立的目录。真实 `.env`、凭据、
私钥、符号链接、不可读路径、Socket、FIFO 和其他特殊文件会被跳过，并记录在
`copy_warnings` 中。完成、失败或拒绝的运行会删除沙箱；启动时还会清理超过
`SANDBOX_WORKSPACE_TTL_SECONDS` 的目录。发生变更时，清理前会先通过服务端内部导出器
保存完整 ChangeSet；展示用截断 Diff 不会被用于真实落盘。

真实写入默认关闭。最小配置如下；`LIVE_WORKSPACE_WRITES_ENABLED=true` 只能与
`AUTH_MODE=trusted_header` 一起使用：

```dotenv
CHANGE_SET_STORE=postgres
LIVE_WORKSPACE_WRITES_ENABLED=false
CHANGE_SET_APPLY_MODE=patch_only  # patch_only | direct | worktree
CHANGE_SET_MAX_FILES=100
CHANGE_SET_MAX_PATCH_CHARS=1000000
CHANGE_SET_WORKTREE_PARENT=
CHANGE_SET_BRANCH_PREFIX=codex/
```

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
  用户消息/Run 事务和最终助手消息的恢复幂等都具有数据库约束；该迁移同样尚未执行。

历史迁移会继续保留在 revision 链中。只有 PostgreSQL 结果加载器会兼容含有
`repository_id`/`rag_context` 的历史 JSON；新 API 和新运行只暴露 workspace 契约。

Celery 运行要求 API 和 Worker 使用共享存储、相同挂载及相同允许根目录：

```dotenv
TASK_QUEUE_BACKEND=celery
SESSION_REPOSITORY=postgres
AGENT_RUN_STORE=postgres
CHANGE_SET_STORE=postgres
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
| PostgreSQL | 会话/消息、用户默认值、会话配置和滚动摘要、Agent 运行/事件/工具账本/ChangeSet、不可变模型与 Run 上下文快照、工作区/知识库目录、项目记忆事实/证据/任务/Outbox/审计、文档/分块元数据、词法搜索和 LangGraph checkpoint |
| Qdrant | 相互独立的知识库和项目记忆向量集合；项目记忆载荷最小且可重建 |
| Redis | Celery Broker 和结果后端；不是业务记录的事实来源 |
| Chroma | 可选的嵌入式/单节点向量存储，用于替代 Qdrant |

`RAG_VECTOR_STORE` 在 Qdrant 和 Chroma 中二选一。二者实现同一向量存储边界，不会
被同时写入。仓库示例和当前持久化运行时使用 Qdrant；内存 Repository 只作为显式
测试替身。

启动 API 和 Celery Worker 前，先启动依赖；审阅 revision 后由操作者明确授权并应用迁移：

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
消费任务。Agent 启动任务的业务载荷只有持久化 Run ID；Worker 从 Run store 恢复提交时
已验证且配置字段已脱敏的上下文快照；项目文件、指令文件和环境不会在 Worker 中重读。
额外目录只能通过 `additional_workspace_ids` 引用已登记且
当前 actor 有权查看的 Workspace，不能提交任意路径；`cwd`、focus path 和符号链接的
真实路径都必须留在主 Workspace 根内。运行开始时捕获的根目录无法访问时，任务会以结构化
`workspace_unavailable` 消息失败。失败的记忆抽取任务保留尝试次数，Celery 可以重试
同一个来源；已完成的 `source_type + source_id` 保持幂等。

### 运行时装配与生命周期

FastAPI `create_app()` 与 Celery Worker 进程单例都通过
`build_runtime(settings, role=api|worker|cli)` 进入同一个 `ApplicationFactory`。
Repository、LLM、模型注册中心、Workspace、RAG、MCP、Tool Registry、LangGraph
checkpointer、Agent runtime 和业务 Service 因此使用同一依赖图；正式 CLI print、REPL
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
