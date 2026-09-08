# Cogent

**简体中文** | [English](README.en.md)

Cogent 是一个本地优先、自托管的 Coding Agent 平台。Web、终端和 Python SDK
共享同一套持久化 Run、工作区工具、权限控制与模型管理；知识库/RAG 与 Agent
上下文保持独立。

当前版本为 `0.2.0`，面向受信任代码仓库的单用户、单实例部署。

## 核心能力

- **持久化 Agent Run**：支持流式事件、审批、追问、暂停/恢复、取消、压缩与重启恢复；
  带工具调用的中间文本会在展示层重置，只有最终模型轮次保留为回答正文。
- **受控代码执行**：工作区限定的读写、搜索、补丁和命令工具，提供
  `default`、`acceptEdits`、`plan`、`bypassPermissions` 权限模式。
- **统一模型管理**：集中管理 OpenAI、DeepSeek、Anthropic、Google、智谱 GLM、
  MiniMax 与豆包连接和模型。
- **MCP 与 Skill**：共享工具目录、中央权限裁决、项目/用户级 Skill 发现和 slash command。
- **分层上下文**：项目指令、Markdown 用户/项目记忆、会话与附件统一装配，并控制 Token 预算。
- **独立知识库**：文档摄取、混合检索、引用与评测独立于 Coding Agent 运行时。

## 快速开始

需要 Docker Desktop。先将 `WORKSPACE_HOST_PATH` 配置为你信任的宿主机代码目录：

```bash
cp -n .env.example .env
mkdir -p workspaces
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml ps
```

Compose 将宿主机 `~/.cogent` 绑定到容器 `/home/app/.cogent`；用户级记忆、Skills 与
Cogent 配置可被宿主机 CLI 共用，并在 App 重建后保留。

打开 <http://127.0.0.1:8000>，然后依次完成：

1. 在“模型管理”中添加 Provider 连接并注册模型。
2. 登记 `/workspaces` 下的项目目录。
3. 新建会话、选择工作区并发送任务。
4. 在出现写入或命令审批时，确认后再继续。

已有安装的备份、迁移和恢复步骤见[完整技术参考](docs/reference.zh-CN.md#存储与数据库迁移)。

## 终端使用

CLI 与网页连接同一个 HTTP/SSE 服务，不会在终端启动第二套 Agent Runtime：

```bash
uv sync
uv run cogent
uv run cogent --workspace-id project
uv run cogent --workspace-id project --permission-mode plan
uv run cogent --workspace-id project --print "解释这个项目的入口结构"
```

默认 API 地址是 `http://127.0.0.1:8000/api/v1`；可通过 `COGENT_API_URL`
或 `--api-url` 修改。

进入 TUI 或 `uv run cogent repl` 后可直接管理下一次 Run：

```text
/permissions [default|acceptEdits|plan|bypassPermissions]
/models
/models register <provider> <model>
/models use <model-id|provider/model>
/models auto [smart|quality|cost|latency]
```

权限模式在终端即时切换；TUI 状态栏按 mewcode 的显示方式标记当前模式为
`default`、`accept-edits`、`plan` 或 `YOLO`，Shift+Tab 按此顺序循环。模型注册和当前会话选模写入网页共用的服务端注册中心。
CLI 不接收 Provider API Key，尚未配置的连接仍需通过本地模型管理页安全录入。

## 当前部署边界

官方 Compose 运行 FastAPI/Web UI、PostgreSQL、Qdrant 和一次性迁移服务。
应用只发布到宿主机 loopback；`single_user` 模式不具备公网认证能力，请勿直接暴露到
局域网或公网。代码工具默认面向用户自己信任的仓库，执行写入前请审阅审批内容。

需要项目调试能力时，可安装经过收紧的推荐 MCP profile：

```bash
docker compose --profile mcp up -d --build --wait \
  mcp-playwright mcp-postgres mcp-qdrant
python3 scripts/install_recommended_mcp.py
```

该命令注册 GitHub、Context7、Playwright、PostgreSQL MCP Pro 和 Qdrant MCP。
Context7、隔离的 Playwright 与 restricted PostgreSQL 默认启用；GitHub 在“能力管理 →
MCP 连接”中通过 Secret Header 录入细粒度 PAT 后再启用。Qdrant MCP 使用 FastEmbed，
与平台 RAG 的 BGE-M3 collection 不兼容，因此只读安装但默认禁用。三个 sidecar 只在
Compose 私有网络监听，不发布宿主机端口；MCP 调用仍经过平台权限和审批。

另保留可选的单进程 SQLite 本地 profile。旧 Go gateway、Celery/Redis 多 Worker、
数据库记忆、Chroma 与操作系统 keyring 兼容实现已经移除。

执行工作区复制、基线和文件历史快照会跳过 `.venv-*` 虚拟环境目录。FileHistory
以 SHA-256 校验内容寻址 blob，读取这些回滚数据时不受普通受管文件的 8 MB 默认上限限制。

## 文档

- [完整技术参考](docs/reference.zh-CN.md)：配置、协议、权限、存储、迁移和评测说明。
- [English README](README.en.md) / [English reference](docs/reference.en.md)
- [架构与事实边界](INTERVIEW_NOTES.md)
- [查询完整链路](docs/architecture/query-full-chain.html)
- [独立 RAG 架构](docs/architecture/rag-architecture.html)
- [Agent 评测说明](evals/README.md)

## 开发与验证

项目需要 Python 3.10+。常用检查：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall ai_agent_platform tests evals
.venv/bin/python INTERVIEW_NOTES/validate.py
node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs
docker compose --env-file .env.example config --quiet
git diff --check
```

更细的本地配置、API、Provider 协议与评测命令请查阅[完整技术参考](docs/reference.zh-CN.md)。
