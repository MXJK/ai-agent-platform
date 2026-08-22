# AI Agent Platform

**简体中文** | [English](README.en.md)

基于 FastAPI 的单用户自托管 AI Agent 平台，提供流式对话、任务驱动的代码 Agent、
托管文档知识库、工作区级项目记忆，以及带审批机制的受控执行能力。当前产品通过
Docker Compose 运行 App、PostgreSQL 和 Qdrant，并以进程内队列保持单实例边界。

代码 Agent 不会预先索引代码仓库，也不依赖向量嵌入。每次运行都会捕获已注册的
工作区根目录，围绕当前任务搜索实时文件系统，只读取必要的源码区间，并把原始
片段直接放入当前模型上下文。

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
Chroma、OS keyring 和本地 SentenceTransformer/Torch 依赖；这些 Adapter 仍保留在完整
开发依赖中。

```bash
cp -n .env.example .env
mkdir -p workspaces
docker compose up -d --build
docker compose ps
```

浏览器入口是 <http://127.0.0.1:8000>。Compose 只把 App 发布到宿主机 loopback；
PostgreSQL 和 Qdrant 只在私有 Compose 网络可达。`WORKSPACE_HOST_PATH` 指向用户信任的
宿主机目录，并统一挂载到容器 `/workspaces`；页面复用现有网页目录浏览器登记项目，
不会尝试从容器打开 macOS Finder。

默认 `RUNTIME_PROFILE=custom` 组合现有 PostgreSQL Repository、Qdrant Vector Store 和
`in_process` TaskQueue。项目记忆开启，用户记忆关闭；Provider API Key 只从模型管理页
录入，Compose 将其加密保存到私有 `app_state` 持久卷，PostgreSQL 只保存不透明引用。
默认模型仍是 Fake LLM；接入真实 Provider 时在页面保存连接并注册模型即可。

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

## CLI、REPL、SDK 与进程入口

可安装入口全部是 `RuntimeContainer` 和 `QueryService` 之上的薄适配器：

```bash
# 一个 Query；stdout 每行都是 AgentEvent 的 JSON，适合脚本消费。
.venv/bin/ai-agent --workspace /absolute/path/to/project print "解释入口结构"

# 同一 conversation 的多轮交互。
.venv/bin/ai-agent --workspace /absolute/path/to/project repl

# 兼容的非 Docker 直启入口；未认证模式仍强制 loopback。
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
会在 `close()` 时释放自己的容器。官方 App 镜像从
`ai_agent_platform.api.entrypoint` 启动 FastAPI，并由 lifespan 关闭同一个 RuntimeContainer。
Celery Worker 生命周期适配器仍保留为兼容实现，但当前 Compose 不启动它。

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

启用 `skills_enabled` 后，运行时只发现以下用户全局目录中的 `SKILL.md`：

- bundled：`ai_agent_platform/bundled_skills/<skill>/SKILL.md`；
- user：`SKILLS_DIRECTORY_PATH`，默认
  `~/.ai-agent-platform/skills/<skill>/SKILL.md`。

Workspace 下的 `.agents/skills` 不再参与运行时发现；注册一次后，每个 Workspace 的
composer、CLI 和 Run 都看到同一份 catalog。来源优先级固定为 `user > bundled`，限定名
分别是 `user:<name>` 和 `bundled:<name>`。同一来源的重复名称按相对路径字典序选择
第一项并产生错误诊断；跨来源覆盖、slash command/alias 冲突也产生稳定诊断。最终
Skill 与 command 都按规范化名称排序。

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

`command` 可以省略；此时平台自动用 Skill 的 `name` 和 `description` 注册同名 `/`
入口。第一版只接受上述字段。每个文件最多 64 KiB，每次发现最多 64 个候选、最多加载
128 KiB 字符；单个 Skill 的上下文预算上限为 16,000 字符，最终还受 Run 的项目
指令总预算约束。错误 UTF-8、损坏/重复 YAML、未知字段、超限文件和单个坏 Skill
只产生诊断，不会终止其余发现。来源根、子目录或 `SKILL.md` 中的 symlink 都不会被
跟随，真实路径必须留在对应来源根内。

Skill 使用渐进加载：composer 和普通 Run 先只接收名称、描述、路径等元数据；用户通过
`/` 显式选择时，选中 Skill 的正文在入队前冻结；没有显式选择时，正文不会批量进入
上下文，模型只有在描述与任务强匹配时才可调用只读 `agent.load_skill` 加载一份正文。
Skill 是纯声明数据：系统不会执行同目录 Python/Shell，也不会从 Markdown 注册函数。
`tools` 只是所需工具名称；缺少本次 Run 已筛选工具时 Skill 不进入上下文，即使工具
存在，调用仍受既有 `ToolUseContext`、Sandbox 和 allow/ask/deny 规则约束。Skill
不能注册工具、降低审批、扩大 allowlist 或授予权限。Skill 指令的快照优先级低于
Workspace 指令文件和项目配置指令。REPL 对非内置 slash command 使用用户全局
Skill catalog 解析覆盖、进程启用列表、Agent/模式和 `required_tools`；成功后提交普通
`QueryParams(skill_name, skill_arguments)`，并在入队前把选中 Skill 指令与 invocation
元数据冻结。未知、禁用或缺依赖命令返回稳定诊断；注册表只保存元数据，不执行 Skill
目录代码，也不扩大工具池或绕过 `PermissionResolver`。

浏览器统一输入框也复用这条调用链。输入 `/` 会按内置命令、全局有效 Skill 和当前
`EffectiveToolPool` 中的 MCP 工具分组展示并过滤，支持方向键、Enter/Tab、Escape 和
鼠标选择。选择 Skill 会把限定名与引号感知的参数提交给 Agent；选择 MCP 工具只冻结
“优先使用此工具”的用户意图，仍由模型原生 tool calling、中央权限解析、审批和 Sandbox
决定是否调用。`GET /api/v1/agent/composer-capabilities` 按已鉴权会话、Workspace、模型与
配置生成只读目录，不会把进程中已注册但本次 Run 不可用的能力暴露为可选项。

工作台「工具」（`/#tools`）统一管理 Skill 与 MCP。Skill 管理 API 为
`GET /api/v1/skills`、`PUT /api/v1/skills/{name}`、
`PATCH /api/v1/skills/{name}/enabled` 和 `DELETE /api/v1/skills/{name}`；用户 Skill
支持创建、编辑、启停和删除，bundled Skill 只读。写入使用本机管理能力校验、严格
Frontmatter 校验、拒绝 symlink，并以 `0600` 原子替换 `SKILL.md`；停用状态保存在同目录
`.disabled` 标记中，保存后无需重启。

## 主要能力

统一输入框提供两种模式：

- `快速对话`：直接返回模型的 SSE 流式响应；
- `代码 Agent`：围绕任务探索工作区，并在同一条助手消息内展示进度、审批、文件变更、
  Diff 和 ChangeSet 操作；
- 键入 `/` 可调用内置 `/chat`、`/agent`、`/new`、`/tools` 命令，或选择当前会话真正
  可用的 Skill/MCP 工具；Skill/MCP 选择会自动切换到代码 Agent；
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
  当前工作区通过左侧工作上下文或设置管理，不再提供独立代码 Agent 页面；当前 Compose
  禁用容器原生目录选择器，网页目录浏览器只展示挂载到 `/workspaces` 的内容，所有选中
  路径仍经过允许根校验；macOS Finder 选择器仅作为非默认本机开发兼容实现保留；
- Agent 审批、追问和暂停检查点直接显示在对应的助手消息中，可就地确认、拒绝或补充
  要求，运行中也可就地暂停、取消或发送转向；终态消息内显示修改文件、逐文件增删行、
  可展开完整 Diff、ChangeSet 校验状态和安全回滚。当前产品的 `direct` Run 会显示已经
  写入的源码位置，回滚需二次确认摘要；历史 `patch_only` / `worktree` 记录仍按原语义显示。
  刷新或重新进入会话时会恢复该会话最近一次 Run 及其检查点/ChangeSet，避免把
  `waiting_approval` 误认为卡死，也避免误以为 Sandbox 文件已经进入真实工作区；
- 对话输入框随内容自动增高，按会话保存未发送草稿；发送可用性会即时反映空输入、
  流式忙碌、归档状态和 Agent 工作区前置条件。长对话只在用户停留于底部附近时自动
  跟随，否则显示显式“回到底部”，避免用户阅读历史时被新内容强制拉走；
- 对话页将空会话引导与活动会话工作台分开：开始对话后切换为紧凑会话标题和独立消息
  滚动区，输入框固定显示当前 Workspace、模型与上下文预算；用户消息可复制或重新编辑，
  助手消息可复制，失败响应提供重试、更换模型和运行详情入口。窄屏详情面板使用带遮罩的
  抽屉，移动端导航保留四个主要目的地并通过“更多”承载低频工作台；
- 安全 Markdown 渲染、响应取消、响应式导航和无障碍文字状态。

### 持久化会话与重启恢复

PostgreSQL 是可跨重启恢复会话的事实来源。会话记录保存自动生成或手工修改的标题、
归档状态、最后更新时间、工作区和模型配置；`user_preferences` 保存未来会话的默认值
和最后活跃会话。Provider API Key 位于服务端 Secret Store；数据库 URL 和允许访问的
文件系统根目录仍是服务端配置，三者都不会进入会话或偏好记录。

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

浏览器 `localStorage` 只保存设备级 UI 状态和最多 20 个非空会话草稿，不保存用户 ID，
也不重复保存会话配置。草稿仅保留在当前设备，消息成功提交前不会进入服务端会话记录。
本地单用户模式使用内部身份，认证模式由可信网关注入身份。健康检查接口会暴露
`session_storage` 和 `persistent_sessions`；如果使用内存
模式，界面会明确标记为临时存储。

### 全局模型注册中心

这是一个本地单用户应用，所有工作区共享同一个全局模型注册中心。模型管理页可以
统一配置 OpenAI、DeepSeek、Anthropic 和 Google，再为每个 Provider 注册多个模型。
API Key 只能写入：PostgreSQL 仅保存 secret reference，密钥值进入选定的 Secret Store。
原生本机运行使用操作系统钥匙串；官方 Compose 使用私有持久卷中的 Fernet 密文与
`0600` 随机本机密钥。Provider 环境变量和 `.env` 不再作为启动凭据，密钥绝不会由
API 返回。升级后若连接仍是遗留 `env:*` 引用，页面会要求重新录入，而不会读取环境变量。

保存 Provider 后，模型管理页会调用该 Provider 的官方模型列表接口，过滤出当前
API Key 可用的文本生成模型供用户选择，并标记已经注册的条目。注册只需要选择
Provider/模型、模型最大输出 token 以及是否启用、是否参与自动路由；显示名称、上下文、
能力和冷启动路由画像由 Provider 元数据与后端先验合成。发现接口暂时没有目标模型时仍可手动
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
→ 调用 Provider；首个 delta 前失败时可尝试下一个跨 Provider 候选
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

模型注册记录分别保存上下文窗口和最大输出 token；后者可在模型管理页调整。普通 Chat
默认请求 `LLM_MAX_OUTPUT_TOKENS`，代码 Agent 则按规划、变更、最终回答三个阶段分别请求
`AGENT_PLAN_MAX_OUTPUT_TOKENS=4096`、`AGENT_MUTATION_MAX_OUTPUT_TOKENS=16384` 和
`AGENT_FINAL_MAX_OUTPUT_TOKENS=4096`。真正发给 Provider 的额度取阶段请求、模型能力上限、
上下文剩余空间和 Usage Ledger 授权结果的最小值。因此 16K 是变更阶段预算，不是要求每个
模型都生成 16K，也不会越过注册的模型能力。

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

`merge_evidence` 会在工具或变更规划、答案生成之前保留所有证据来源。当前产品的变更任务
在 Run 开始前由服务端冻结登记源码根作为 `direct` 执行根；UI 和 `POST /agent/runs` 都
不接受逐 Run 的执行位置选择。精确工具审批、验证、有界修复以及 Diff/测试产物继续生效。
终态把完整补丁、写前/写后哈希和源码位置持久化为已应用 ChangeSet，并继续使用基线冲突
检查、mutation journal、单写者锁和摘要绑定的安全回滚。底层仍可读取历史
`patch_only` / `worktree` Run 和 ChangeSet。

运行中的 Agent 状态以产品运行存储为事实源，并把最近的 LangGraph checkpoint 作为
只读进度叠加，因此 API 可以在任务仍执行时暴露已经完成的 Trace 节点，GET 查询本身
不会反写 Run。产品记录一旦进入终态便不可被迟到的 running 快照覆盖；恢复异常也会
保留原始错误并完成沙箱清理。最终指标包括耗时、节点和工具数量、修改文件、已恢复
错误，以及 Provider 上报的输入、输出、思考和总 Token。

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

OpenAI、DeepSeek、Anthropic 和 Google 适配器会通过各 Provider 原生的 Function/Tool Calling
API 发送 `ToolSpec`。生产模型不再通过 Prompt 文本生成 JSON 工具计划。Provider
特有的函数调用会被标准化为 `LLMToolDecision`，其中包含稳定的 tool-call ID；带点号
的注册名会转换为 Provider 安全别名，并在执行前映射回原名。Fake Provider 保留
确定性规则规划，用于离线测试。

原生模型采用统一、有界的“观察—决策—执行”循环。读文件、写 Sandbox、运行验证、
获取状态与 Diff 都由同一次模型会话按原顺序选择，不再把读、写、验证拆成彼此看不到
结果的固定阶段：

创建/修改任务在空工作区也必须继续调用 `sandbox.write_file` 或
`sandbox.apply_patch`；目录盘点使用 `repo.list_files`。`sandbox.run_command` 的模型
可见契约列出允许的 executable basename，并定位为变更后的验证工具。变更前失败的
诊断命令只作为可观察错误回灌，不会触发 artifact 收尾或产生空 ChangeSet。
`change_planning` 还有运行时完成门：没有成功的 Sandbox mutation 时，模型的文本终答
会被退回并附带明确重规划要求；连续三次仍不执行变更则进入 `blocked`，不会把零文件
结果标成 `completed`。
Google Developer API 的工具调用 Token 预检把 system instruction 与工具 Schema 作为
额外 content 计数，因为它不支持 `CountTokensConfig.system_instruction/tools`；真实
生成请求仍使用原生 system instruction 和 tools，预算预检不会因 fallback 到 Google
而失败。
跨 Provider fallback 时，仅 Google 自己返回的原始 provider items 会作为带
`thought_signature` 的原生 functionCall 历史重放；其他 Provider 的调用与结果转成
明确文本观察，避免伪造 Gemini 签名或丢失已执行证据。
运行时收集 Workspace 状态与 Diff 时会合成一轮 assistant 工具历史：DeepSeek
思考模式要求每个工具轮次都回传 `reasoning_content`，因此本地合成轮次使用空字符串
作为无私有推理的 Provider 合法占位；模型真实返回的 reasoning/provider items 仍原样
重放。OpenAI 和 Anthropic 继续使用各自合法的合成 function/tool-use 历史，Google
继续降级为文本观察，不伪造 `thought_signature`。Provider JSON 错误的 message 会先做
凭据脱敏与长度裁剪再进入 Run 错误，便于定位协议问题而不返回请求正文。

生产规划器每轮最多接受一个工具调用；OpenAI 请求同时设置
`parallel_tool_calls=false`，其他 Provider 即使返回多个调用也只执行第一个并为其余调用
回灌 `single_tool_turn`。变更 Prompt 要求一次只修改一个文件并优先使用小
`sandbox.apply_patch`，避免把多个完整文件塞进一个 JSON 参数。当前仍使用各 Provider 的
结构化工具参数协议，没有假装 DeepSeek/Anthropic/Google 已支持 OpenAI 的 freeform
`apply_patch` custom tool。

如果函数参数 JSON 非法，错误会被标为可恢复；若 Provider 的 finish reason 是
`length`/`max_tokens`/`MAX_TOKENS`/`max_output_tokens`，则进一步标记为截断。`LLMClient`
在 `LLM_MAX_RETRIES` 范围内追加“单工具、单文件、小 patch”的纠错提示并重新预检、授权、
调用。失败尝试的真实 usage 仍写统一账本；Run 错误只保存 finish reason、参数字符数和
JSON 解析位置，不保存可能含源码的原始参数。

```text
原生工具调用
→ EffectiveToolPool 只暴露本 Run 冻结且校验通过的 ToolSpec
→ PermissionResolver 以 ToolUseContext 判定 allow/ask/deny
→ ToolRegistry 在执行点复判并校验/执行
→ 通过 call ID 关联结果或错误
→ Harness 对内置/MCP 结果统一执行 Token 上限，超限原文写入 Run Artifact
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
另有 900 秒、连续三轮无进展和连续三次失败保护。硬停止会保留一次文本最终总结：请求仍
下发同一批工具定义，并由 Provider 的 tool choice 禁止调用，因为 transcript 中已有的
tool_use/tool_result 在缺少工具定义时会被 Provider 拒绝。这样返回 `partial`/`blocked`，
不会把预算耗尽误报为 `completed`。相关配置为
`AGENT_SOFT_TOOL_ROUNDS`、`AGENT_MAX_TOOL_ROUNDS`、`AGENT_SOFT_TOOL_CALLS`、
`AGENT_MAX_TOOL_CALLS`、`AGENT_MAX_ELAPSED_SECONDS`、`AGENT_NO_PROGRESS_ROUNDS` 和
`AGENT_MAX_CONSECUTIVE_FAILURES`。阶段输出预算由 `AGENT_PLAN_MAX_OUTPUT_TOKENS`、
`AGENT_MUTATION_MAX_OUTPUT_TOKENS` 和 `AGENT_FINAL_MAX_OUTPUT_TOKENS` 控制。工具会话超过
`AGENT_TOOL_RESULT_MAX_TOKENS`（默认 2000，最小 64）的单次结果会在进入模型转录前，无条件替换为
包含原文首尾、原始 Token 估算与 `artifact_id` 的占位符；完整结果仍保存为同一 Run 的
`tool_result` Artifact。该规则位于统一 Harness 边界，因此内置工具和 MCP 使用同一路径，
并通过 `agent_tool_results_truncated_total` 计数。现有每工具字符上限仍作为第二道保护。
工具会话超过
`AGENT_NATIVE_CONTEXT_MAX_CHARS`，或超过由当前模型上下文窗口乘以
`AGENT_NATIVE_CONTEXT_TOKEN_RATIO` 得到的 Token 预算时，按完整
assistant/tool 组压缩旧观察，避免拆断 call/result 对。被折叠的转录交给与会话压缩
同一个压缩器做语义摘要，保留已读文件、已执行命令、已应用修改和失败原因；模型不可用
时回退到确定性的规则式摘要。图的独立保险由
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
和输出。校验失败只回报路径、约束名、schema 侧期望值以及被拒值的类型和长度，凭据或
文件内容不会经由错误文本回灌模型。工具规格还声明超时、重试和幂等行为。只有幂等工具
遇到可重试失败时才会重试；相同 `run_id + call_id` 会重放缓存结果，参数变化则会被拒绝。
超时不等于取消：工具在独立 daemon 线程中执行，超时后线程被放弃，结果如实说明调用可能
仍在运行；同一 Run 内还有被放弃的写工具时，后续副作用调用返回 `tool_timeout_in_flight`
而不是与之竞争。MCP 工具使用同一注册契约：优先读取 `structuredContent`，文本块会被
标准化，`isError=true` 会变成稳定的工具失败，而不是成功载荷；执行上下文使用保留参数名
`__tool_context__`，因此服务端自带的 `context` 参数原样透传。

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

启用 MCP 后可直接打开工作台的「工具」（`/#tools`，旧 `/#mcp` 自动兼容跳转）注册、编辑、测试/刷新、启停
或删除 Server。前端支持当前 stdio、当前 Streamable HTTP 以及两条显式兼容路径，展示
独立连接状态、协议版本、重试错误和发现/注册工具数。`MCP_CONFIG_PATH` 默认是
`~/.ai-agent-platform/mcp.json`；文件可以尚不存在，首次保存会以 `0600` 原子创建。
同时设置 `MCP_ENABLED=true` 时，保存会立即替换该 Server 的连接并同步 ToolRegistry；
否则配置会保存并标记为等待重启。普通环境变量/Header 与 Secret 输入分开，Secret 值只
写共享 `SecretStore`，配置文件和后续 GET 响应只保留引用或键名。当前
`AUTH_MODE=single_user` 下管理写接口统一归属固定 `owner`，并依靠 Compose 只发布
loopback 端口；关闭认证的直连 loopback 和带本机证明的可信网关是保留兼容模式，
不是当前产品链路。

管理 API 为 `GET /api/v1/mcp/servers`、
`PUT /api/v1/mcp/servers/{name}`、
`PATCH /api/v1/mcp/servers/{name}/enabled`、
`POST /api/v1/mcp/servers/{name}/test` 和
`DELETE /api/v1/mcp/servers/{name}`。这些变更只影响目标 Server；停用或删除会关闭其连接
并原子移除对应的动态工具，不会重建其他 Server 的生命周期。

自托管 Compose 默认同时启用 Skill 与 MCP，把宿主机 `~/.ai-agent-platform` 挂载到
容器同名目录，并在镜像内安装 `nodejs`/`npm`/`npx`，因此工具页保存的注册表可跨重建
保留，常见的 Node stdio MCP Server 也具备启动运行时。

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
配置解析要求 Session 与 Run store 使用同一受支持后端；无法建立原子 UoW 的组合在运行时
资源构造前就会失败。
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
当前请求和实时证据始终优先。摘要按 `FACTS`/`PREFERENCES`/`DECISIONS`/`OPEN` 分区
重写，`PREFERENCES` 只增不删，避免反复递归压缩逐轮丢失用户偏好。

滚动摘要的边界按最后已摘要消息 ID 对齐，消息被删除后不会错位；ID 不存在时才退回
消息计数，并计入 `conversation_summary_realigned_total`。

上下文装配同时受消息条数和 Token 预算约束。Token 预算来自本次将要服务的模型的
上下文窗口（`LLM_CONTEXT_INPUT_TOKEN_RATIO` 乘以窗口再扣除输出预留），因此小窗口
模型会更早收敛，大窗口模型可以在 `LLM_MAX_CONTEXT_MESSAGES` 与
`LLM_MAX_CONTEXT_MESSAGES_CEILING` 之间自动放宽历史条数。超预算按代价从低到高恢复：
先同步触发一次会话压缩，再丢弃最旧轮次，最后才对消息正文做保留首尾的截断，而不是
把请求直接抛给 Provider 触发 `context_window_too_small`。装配结果通过 Chat SSE 的
`context` 事件和 `/sessions/{id}/token-usage` 的 `context` 字段暴露：估算 Token、
预算、是否含摘要、丢弃与截断条数、同步压缩次数。

Prompt 前缀按稳定性排序：用户画像等长期稳定内容在最前，滚动摘要与历史其次，
每轮随查询变化的项目记忆紧贴当前用户消息，使 Provider 的前缀缓存可以命中。

项目记忆是独立的工作区长期子系统，不等同于会话历史或 LangGraph checkpoint，也
不会自动吸收知识库文档。记忆只作为历史线索；系统/项目指令、当前请求和实时源码
始终优先。

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
`score` 来源字段。已经移除的 `repository_id`、`rag_context` 和逐 Run
`workspace_mode` Agent 字段不会被接受；Run 状态仍返回服务端冻结的最终 mode 与
execution root 作为审计信息。

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
拒绝或跳过并记录。写文件接受可选 `expected_sha256`，所有已存在目标都校验 Run 基线和
当前哈希；补丁还校验路径、上下文和写前哈希。写入使用同目录临时文件、`fsync` 和原子
替换，原内容先持久化到服务端 mutation journal。`direct` 对同一 Workspace 实施单写者
锁，外部编辑和并发 Agent 冲突都会停止而不覆盖内容。

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
天。候选会先全局排序，再应用六条结果和 3,000 字符预算；Chat/Agent 来源信息会暴露
最终分数及三个组成项。向量失败时降级为词法检索，记忆失败不会导致主 Chat 或 Agent
回答失败。

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
不调用 LLM。Chat 与 Agent
始终把已确认小画像标记为不可信历史偏好；它不能覆盖当前请求、系统/项目指令、权限或
实时源码。Agent 的助手结果只可提炼 L1，不能推导 L3。凭据、完整环境变量、权限提升
要求和 Prompt Injection 会在 L1/L3 写入前被拒绝。

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

前端“记忆工作台”采用与后端层级一致的 L1/L2/L3/L0 信息架构：项目记忆和个人事实
均使用左侧资产列表、右侧详情治理的双栏布局，显示 active/candidate 统计、状态/类型
筛选、证据、版本和可用操作；个人画像单独预览模型实际会看到的确定性快照；对话搜索
展示用户隔离的最近消息、搜索命中与原文详情；画像页同时展示 L2 场景及其 L1 来源数。
L1 固定自动提炼，前端不再暴露工作区模式、手动重建索引或刷新控件；进入页面即自动加载。

当前 Docker MVP 默认启用完整流水线：PostgreSQL 保存 L0 会话和 L1 事实、证据、任务与
Outbox，Qdrant 保存可重建 L1 向量，SQLite v2 保存 L2 场景与 L3 用户画像：

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

会话压缩单独配置：

```dotenv
CONVERSATION_SUMMARY_ENABLED=true
CONVERSATION_SUMMARY_TRIGGER_MESSAGES=12
CONVERSATION_SUMMARY_KEEP_RECENT_MESSAGES=6
CONVERSATION_SUMMARY_MAX_CHARS=4000
CONVERSATION_SUMMARY_MAX_SOURCE_CHARS=12000
CONVERSATION_SUMMARY_SYNC_ON_OVERFLOW=true
```

上下文预算单独配置：

```dotenv
LLM_MAX_CONTEXT_MESSAGES=12
LLM_MAX_CONTEXT_MESSAGES_CEILING=48
LLM_CONTEXT_INPUT_TOKEN_RATIO=0.6
AGENT_NATIVE_CONTEXT_TOKEN_RATIO=0.5
AGENT_TOOL_RESULT_MAX_TOKENS=2000
```

`LLM_MAX_CONTEXT_MESSAGES` 是下界，`LLM_MAX_CONTEXT_MESSAGES_CEILING` 是 Token 预算
允许时的上界；两者相等即固定为原有的定长窗口。

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
- `20260820_0022`：为每个已注册模型添加 `max_output_tokens` 能力上限；现有 DeepSeek
  记录回填为 8192、fake 为 4096，其余为 16384。该迁移随代码交付但未在当前数据库执行。

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
LANGGRAPH_CHECKPOINTER=postgres
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
| PostgreSQL | 会话/消息、用户默认值、会话配置和滚动摘要、Agent 运行/事件/工具账本/ChangeSet、不可变模型与 Run 上下文快照、工作区/知识库目录、项目记忆事实/证据/任务/Outbox/审计、文档/分块元数据、词法搜索和 LangGraph checkpoint |
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
docker compose --env-file .env.example config --quiet
bash -n scripts/start.sh scripts/start-local.sh
node --check ai_agent_platform/static/app.js
git diff --check
```

修改保留的 Go gateway 兼容实现后，再额外运行 `go test ./gateway/...`。

运行离线 Agent 评估：

```bash
.venv/bin/python evals/run_evals.py
.venv/bin/python evals/run_memory_evals.py
```

---

[查看英文版 README](README.en.md)
