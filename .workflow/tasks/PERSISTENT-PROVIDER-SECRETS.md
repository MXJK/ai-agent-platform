# PERSISTENT-PROVIDER-SECRETS: 持久化前端 Provider 密钥并移除环境变量读取

## Goal

让前端录入的 Provider API Key 安全持久化并跨容器重启可用，彻底移除 Provider API Key 的 env/dotenv 配置与读取路径。

## In scope

- 让模型管理页提交的 OpenAI、DeepSeek、Anthropic、Google API Key 写入可跨 App
  容器重启的加密 Secret Store，PostgreSQL 仍只保存不透明引用。
- 从 `Settings`、dotenv/环境变量解析、模型注册中心 bootstrap 和 LLM/RAG Provider
  调用中移除模型 Provider API Key 的配置文件读取与兜底路径。
- 让文本生成、工具调用、知识库 Embedding 和项目记忆 Embedding 统一从模型注册中心
  动态解析当前凭据。
- 将遗留 `env:*` Provider 引用视为需要用户重新录入的悬空凭据，不读取对应环境变量。
- 更新 Compose、示例配置、中英文 README、面试手册与证据映射。
- 增加加密落盘、重启读回、无明文、遗留引用拒绝和运行时凭据解析回归测试。

## Out of scope

- 在本任务中实现外部 Vault/KMS、云 Secret Manager 或多节点密钥同步。
- 把 Qdrant、数据库、网关或 MCP 自身的基础设施凭据迁移到模型 Provider Secret Store。
- 自动读取或迁移现有 `.env` 中的 Provider API Key；用户需要在模型管理页重新录入。
- 部署、发布、数据库 migration 或自动重启当前服务。

## Acceptance criteria

- [x] Compose 中从前端保存的 Provider API Key 跨 App 容器重启仍可解析和调用。
- [x] Provider API Key 不出现在 `.env.example`、`Settings`、dotenv/环境变量解析、API
  响应、日志、PostgreSQL 明文字段或持久化 Secret 文件明文中。
- [x] LLM 与 OpenAI/Gemini Embedding 只通过模型注册中心 Secret resolver 获取凭据。
- [x] 遗留 `env:*` Provider 引用不再读取环境变量，并在模型管理页明确要求重新录入。
- [x] 原生本机 keyring、测试 memory 和单机 Compose 加密文件三种后端具有明确边界；
  Compose 默认使用持久化加密文件后端。
- [x] 聚焦测试、项目规定全量验证、前端语法检查、手册校验、Compose 配置检查和
  diff/Secret 审计全部通过。

## Decisions

- 不把 Provider API Key 明文放入 PostgreSQL；Compose 使用权限受限的持久卷文件保存
  Fernet 密文，并使用同卷中权限为 `0600` 的随机本机密钥保护。该方案面向当前单机、
  单用户 Compose 边界；多节点或生产级密钥托管仍需外部 KMS/Vault。
- Secret reference 使用与后端无关的 `model-provider:<provider>`，不再用误导性的
  `keyring:` 前缀描述 memory/file 后端。
- 不自动迁移 `env:*` 引用，因为迁移本身会继续读取被禁止的 Provider 环境变量；升级后
  由用户在模型管理页重新输入一次。

## Verification

- 聚焦 Provider Secret/配置/路由测试：`78 passed, 35 subtests passed in 2.34s`。
- `.venv/bin/python -m pytest -q`：通过，`442 passed, 52 subtests passed in 42.29s`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，验证 24 个 Markdown 文件和
  39 项能力；相对既有 facts 基线的 evidence review 警告为信息性提示。
- `docker compose --env-file .env.example config --quiet`：通过。
- `git diff --check`：通过。
- Secret 审计：`.env.example`、`Settings`、ConfigResolver、模型注册 bootstrap 与 LLM/RAG
  运行时不再包含 Provider API Key 配置项；新增测试只使用显式假值验证忽略与脱敏行为。
- 未重建或重启当前 App 容器，避免在遗留 `env:*` 引用尚未由用户通过页面重新录入前
  改变正在运行的实例。

## Result

已完成。模型管理页提交的 Provider API Key 现在写入后端无关的
`model-provider:<provider>` 引用；原生运行继续使用 OS keyring，官方单机 Compose 改用
私有 `app_state` 持久卷中的 Fernet 密文与 owner-only 随机本机密钥。Secret Store 新实例
可以读回旧密文，模拟 App 容器重建的回归测试通过；密文存在但密钥文件丢失时 fail closed，
不会生成新密钥混写数据。

OpenAI、DeepSeek、Anthropic、Google API Key 已从 `Settings`、dotenv/环境解析、模型注册
bootstrap 和 LLM fallback 移除；文本生成、工具调用、知识库 Embedding 与项目记忆
Embedding 均动态使用模型注册中心 resolver。遗留 `env:*` Provider 引用被标记为不可用，
前端明确提示重新录入。升级后需要在模型管理页为受影响 Provider 重新输入一次 API Key；
本任务未读取旧环境值、未执行部署、migration、容器重启或提交。

README 中英文、`INTERVIEW_NOTES.md`、受影响 Parts 与 `facts.json` 已同步。单机加密文件
后端不冒充多节点生产密钥管理；后者仍需外部 KMS/Vault。
