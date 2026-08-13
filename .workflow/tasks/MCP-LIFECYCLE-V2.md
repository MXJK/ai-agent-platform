# MCP-LIFECYCLE-V2: MCP 生命周期与新协议兼容

## Goal

以独立 Server 生命周期重构 MCP 集成，使用官方 Python SDK 支持当前协议的 stdio 与 Streamable HTTP，同时把 2025-06-18 stdio 和旧 HTTP+SSE 保留为显式兼容路径。

## In scope

- 定义 `MCPTransport`、`MCPClient`、`MCPConnectionManager`、`MCPServerStatus`。
- 每个 Server 独立连接、超时、重试、熔断、关闭和脱敏诊断。
- required/optional Server readiness 语义。
- `tools/list` 分页、确定性排序、缓存提示与强制刷新。
- 工具调用稳定 call ID、错误码、取消与超时。
- HTTP/stdio Secret 引用和 URL、Header、子进程环境边界。
- MCP 权限注解统一经过 `PermissionResolver`。
- fake stdio/HTTP 生命周期与协议兼容测试。
- 本地管理员可通过前端/API 注册、编辑、测试、启停和删除 MCP Server。
- 注册变更原子持久化 Secret 引用，并在当前进程动态同步连接与 ToolRegistry。
- ADR、README 与访谈手册同步。

## Out of scope

- MCP OAuth/DCR 交互式授权流程。
- MCP resources、prompts、sampling、roots、tasks 或 Apps 扩展接入。
- 部署、迁移、提交、推送、PR 或合并。

## Acceptance criteria

- [x] 四个核心抽象存在，Server 生命周期互不共享连接、超时、重试或熔断状态。
- [x] optional Server 失败不阻止启动；只有 required Server 失败使 readiness 为 false。
- [x] 当前 stdio 和 Streamable HTTP 使用当前协议路径；2025-06-18 stdio 与 HTTP+SSE 仅显式启用。
- [x] `tools/list` 遍历所有游标页、按名称确定性排序、遵守缓存提示并支持 refresh。
- [x] 工具调用携带稳定 call ID，并区分协议、传输、超时、取消与熔断错误码。
- [x] HTTP/stdio 凭据只通过 `SecretStore` 引用解析，不进入配置快照、repr、日志或诊断。
- [x] HTTP 目标、重定向、Header 和 stdio 环境变量均受收窄策略约束。
- [x] 所有协议工具注解均由 `PermissionResolver` 解析后才注册。
- [x] fake stdio/HTTP 测试覆盖兼容、分页、超时、重连、缓存和关闭。
- [x] 前端完成 MCP Server 注册、编辑、测试、启停、删除及响应式/无障碍状态反馈。
- [x] 管理 API 只允许本机管理模式，凭据只写 SecretStore，配置文件只保存引用。
- [x] Server 变更在当前进程动态同步独立连接与 ToolRegistry，并遵守进程 tool allowlist。
- [ ] ADR、README、访谈手册与 facts 同步；全量验证通过。

## Decisions

- ADR `docs/adr/0001-mcp-official-python-sdk-v2.md` 选择并精确锁定官方
  `mcp==2.0.0`；协议 wire model 与当前传输交给 SDK，平台继续拥有生命周期、安全、
  Secret、权限和 ToolRegistry 语义。
- `stdio` 与 `streamable_http` 是当前默认路径；`stdio_2025_06_18` 和
  `legacy_sse + legacy_compatibility=true` 是显式兼容路径。
- 每个 Server 使用独立 `MCPClient` 事件循环和 `MCPConnectionManager` 状态，启动故障
  通过 required 标记进入 readiness，不把可选故障升级为进程启动失败。
- HTTP 采用 host allowlist、DNS 公网检查、默认 HTTPS、禁重定向和代理环境；stdio
  只继承 SDK 的最小安全环境并丢弃 Server stderr。凭据只从共享 SecretStore 引用解析。
- `/api/v1/mcp/servers` 管理 API 只允许 `AUTH_MODE=disabled` 的本机管理模式；配置文件
  以 `0600` 临时文件 fsync 后原子替换。前端只回显普通字段和 Secret 键名，不回显值。
- `MCPRegistryService` 串行化管理变更并只替换目标 Server；ToolRegistry 的动态注册/
  移除受注册表锁保护，且继续受进程 `tool_allowlist` 上限约束。
- 没有数据库模型或迁移影响。用户可见的配置、健康探针和架构行为已同步到 README、
  本地访谈手册与中央 facts。

## Verification

- PASS: `.venv/bin/python -m pytest -q` — 332 passed, 47 subtests passed。
- PASS: `.venv/bin/python -m compileall ai_agent_platform tests evals`。
- PASS: MCP/权限/工具/运行时定向回归 — 45 passed, 8 subtests passed。
- PASS: `.venv/bin/python -m pip check` — No broken requirements found。
- PASS: `node --check ai_agent_platform/static/app.js`。
- PASS: `git diff --check`。
- PASS: 浏览器 QA — 桌面与 800 px 窄屏无横向溢出，stdio/HTTP 字段切换正确，
  禁写状态与空状态正确，控制台无 warning/error。
- BLOCKED: `.venv/bin/python INTERVIEW_NOTES/validate.py`。MCP 新事实条目本身有效，但
  既有 `skill_discovery` 条目引用当前分支不存在的
  `ai_agent_platform/skills/{models,discovery,registry,service}.py` 与
  `tests/test_skill_discovery.py`，共 5 个缺失证据路径。该功能只存在于未合并的
  `codex/skill-discovery` 历史，修复或删除它属于本任务之外的产品/文档范围决定。

## Result

MCP 生命周期、当前协议、本机管理 API、动态 ToolRegistry 同步和响应式前端均已完成；
fake stdio/HTTP 注册覆盖 Secret 引用、热连接、工具刷新、启停和删除，全量代码测试与
编译通过。README 和本地访谈手册已同步新注册方式与能力边界。由于仓库要求的访谈手册
校验被无关的既有 Skill 事实漂移阻断，本任务仍不能按 `codex-close-task` 契约标记 done。
下一步需要决定是恢复 Skill discovery 实现并合入当前基线，还是从本地访谈手册移除/
降级相应事实，再重新运行手册校验。
