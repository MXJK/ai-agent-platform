# INSTALL-RECOMMENDED-MCP-SERVERS: 安装推荐 MCP 服务

## Goal

为官方单实例 Docker Compose 安装并注册 GitHub、Context7、Playwright、PostgreSQL
MCP Pro 与 Qdrant MCP，同时保持 Secret、网络、数据库和向量 collection 边界。

## In scope

- 增加 opt-in `mcp` Compose profile，运行 Playwright、PostgreSQL MCP Pro 和 Qdrant
  MCP sidecar，且不向宿主机发布端口。
- 固定外部 MCP 服务版本；Qdrant 使用项目内的最小镜像定义隔离 Python 依赖。
- 通过现有 MCP Registry 注册两个远程服务与三个 Compose 内部服务。
- GitHub 使用只读/lockdown/toolset 收紧；PostgreSQL 使用 restricted 模式；Qdrant
  使用 read-only 模式；Playwright 使用 headless/isolated 与 origin allowlist。
- 更新自托管文档、事实映射和配置回归测试。
- 对当前本地 Compose 实例执行连接与工具发现验收。

## Out of scope

- 不读取或复用 GitHub CLI 凭据，不创建 PAT；GitHub 在缺少用户提供的细粒度 PAT 时保持禁用。
- 不让 Qdrant MCP 直接查询平台 `knowledge_chunks`：其 FastEmbed 不支持平台当前
  `BAAI/bge-m3` embedding，避免向量维度不兼容。
- 不实现 MCP OAuth、resources 或 prompts。
- 不提交、推送、迁移数据库或向公网发布服务。
- 不处理既有 ManagedFiles symlink 异常类型测试失败。

## Acceptance criteria

- [x] `docker compose --profile mcp config --quiet` 通过，三个 sidecar 不发布端口。
- [x] Playwright 使用 `/mcp` Streamable HTTP；PostgreSQL 使用 restricted + legacy SSE；
  Qdrant 使用 read-only + Streamable HTTP。
- [x] 五个服务均进入 MCP Registry；可安全运行的服务完成真实连接与工具发现。
- [x] 缺少 PAT 的 GitHub和 embedding 不兼容的 Qdrant 保持禁用，并给出明确启用条件。
- [x] 聚焦测试、compileall、文档校验及完整 pytest 通过，或精确记录任务外 blocker。
- [x] 文档准确说明启动、Secret 录入、启用条件和能力边界。

## Decisions

- Sidecar 放在 opt-in `mcp` profile，不增加默认 Compose 的资源占用。
- 远程 MCP 的 Secret 继续由平台 SecretStore 保存，不写入仓库或 Compose。
- PostgreSQL MCP 复用 Compose 数据库凭据，但只在内部网络暴露并强制 restricted 模式。
- Qdrant MCP 使用独立 `mcp_context` collection 与默认受支持 embedding；保持禁用，直到
  该 collection 由相同 embedding 预先构建。

## Verification

- 官方来源与镜像：
  - GitHub、Context7、Playwright、PostgreSQL MCP Pro、Qdrant MCP 当前官方配置已核对。
  - `mcr.microsoft.com/playwright/mcp:v0.0.80` 与
    `crystaldba/postgres-mcp:0.3.0` manifest 同时包含 `linux/amd64` 和
    `linux/arm64`。
- Compose 安装：
  - `docker compose --profile mcp up -d --build --wait mcp-playwright
    mcp-postgres mcp-qdrant` 成功。
  - App、Playwright、PostgreSQL MCP、Qdrant MCP、PostgreSQL、Qdrant 均为 running；
    带健康检查的服务均为 healthy。
  - 三个 MCP sidecar 均无宿主机 published port。
- Registry 与真实 MCP 握手：
  - Context7：ready，协议 `2026-07-28`，2 个 discovered/registered 工具；
    `resolve-library-id` 无副作用调用成功。
  - Playwright：ready，协议 `2025-11-25`，24 个 discovered/registered 工具；
    `browser_navigate` 访问 `http://host.docker.internal:8000/api/v1/health` 成功，随后关闭
    isolated browser。
  - PostgreSQL MCP Pro：ready，协议 `2024-11-05`，9 个 discovered/registered 工具；
    restricted 模式下 `list_schemas` 调用成功。
  - Qdrant：临时启用后 ready，协议 `2025-11-25`，只发现/注册 1 个 `qdrant-find`；
    验证后恢复 disabled。
  - GitHub：已注册官方远程端点和 read-only/lockdown/toolset Header；因未获取用户 PAT
    保持 disabled。
  - OrbStack 把 Context7 解析到合成 `198.18.0.0/15`；确认后仅为两个精确官方域名启用
    `--allow-container-dns-proxy`，其他 Server 的 private-network 边界不变。
- 静态和聚焦验证：
  - `.venv/bin/python -m pytest -q tests/test_self_hosted_compose.py
    tests/test_recommended_mcp_install.py tests/test_mcp_lifecycle.py
    tests/test_mcp_provider.py`：`33 passed`。
  - `docker compose --profile mcp config --quiet`：通过。
  - `.venv/bin/python -m compileall ai_agent_platform tests evals scripts`：通过。
  - `.venv/bin/python INTERVIEW_NOTES/validate.py`：24 个 Markdown、44 个 capability 通过；
    仅有共享工作树既有 evidence review warnings。
  - `git diff --check`：通过。
- 完整验证：
  - `.venv/bin/python -m pytest -q`：
    `825 passed, 12 skipped, 1 failed, 108 subtests passed`。
  - 唯一失败为任务开始前已记录的
    `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`：
    测试期望 `OSError`，`ManagedFiles.read()` 对 ELOOP/ENOTDIR 抛出 `ValueError`；本任务
    未修改对应实现或测试。
- 共享工作树：执行期间 HEAD 由外部更新到 `059e2ea7`；MCP 改动已在该新 HEAD 上重新
  应用并复核，没有回退新进入主线的 Skills/CLI 改动。

## Result

五项推荐 MCP 已安装并写入当前 Cogent Registry。可直接安全运行的 Context7、Playwright、
PostgreSQL MCP Pro 当前 ready；GitHub 等待用户通过 Secret Header 提供细粒度 PAT，Qdrant
已证明可以握手和发现只读工具，但因 FastEmbed 与平台 BGE-M3 collection 不兼容保持禁用。

Compose profile、固定版本、Qdrant 独立镜像、幂等安装器、网络/只读边界、测试及中英文文档
均已落地。没有提交、推送、数据库迁移或公网发布。功能范围完成，但仓库必跑 pytest 仍被
任务外既有 symlink 异常类型契约阻塞，因此工作流状态记录为 blocked。
