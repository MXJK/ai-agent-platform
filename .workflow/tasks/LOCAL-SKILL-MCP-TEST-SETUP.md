# LOCAL-SKILL-MCP-TEST-SETUP：本地 Skill 与 MCP 前端测试配置

## Goal

为当前仓库准备不入库的 project Skill 和 MCP 测试配置，并验证 MCP 页面可以新增、
编辑、测试/刷新、启停和删除单个 Server。

## Acceptance criteria

- [x] `.agents/skills` 下存在三个可被项目发现器加载的测试 Skill。
- [x] Skill 目录和 `mcp.json` 被 Git 忽略。
- [x] 本机 `.env` 允许并启用 Skill/MCP，MCP 管理写接口在 loopback 本机模式可用。
- [x] Everything MCP 默认启用；Filesystem MCP 默认停用且只指向当前仓库。
- [x] MCP 页面新增、编辑、测试/刷新、启停和删除均通过 TestClient 生命周期与前端控件/路由绑定验证；真实浏览器点击受运行环境 localhost URL 策略阻断，未声明浏览器 smoke 通过。
- [x] 项目要求的测试与 compileall 通过。

## Permitted scope

- 本地忽略配置、测试 Skill、MCP Server 注册表、相关验证与最小文档记录。
- 若既有 MCP 页面验收失败，先系统调试，再只修复经证实的缺口。
- 不部署、不迁移、不推送、不放宽中央工具权限、审批或 Sandbox。

## Result

完成。

## Decisions

- 三个测试 Skill 放在被忽略的 `.agents/skills/` 中，覆盖只读代码审查、缺陷分诊和测试设计三种权限映射；不把个人测试 fixture 提交到仓库。
- `.env` 使用 `AUTH_MODE=disabled` 提供 loopback 本地管理写接口，并将与该模式不兼容的 `LIVE_WORKSPACE_WRITES_ENABLED` 关闭，避免无鉴权 live workspace 写入。
- `mcp.json` 默认启用官方 Everything 参考 Server；Filesystem 作为推荐示例保留但默认停用，唯一根目录参数限定为当前仓库绝对路径。
- 内置 Browser 的 localhost URL 安全策略阻止了真实 DOM 点击、刷新和 console smoke，且明确禁止换浏览器或间接绕行；以确定性 TestClient 完整生命周期和已提供的前端控件/路由绑定作为替代验收，并保留该验证限制。

## Verification

- 项目 Skill 有效目录断言：`code-review`、`bug-triage`、`test-design` 均被发现，且工具集合匹配预期。
- Settings 与 MCP 配置断言：Skill/MCP 开关生效；活动 Server 仅 `everything`，包含禁用项时为 `everything=true`、`filesystem=false`。
- 真实 Everything 握手：网络授权环境下状态 `READY`，发现 13 个工具。
- MCP 聚焦测试：4 passed。
- 替代 UI 生命周期：`create -> edit(timeout=20) -> test -> disable -> enable -> delete: PASS`。
- 前端控件与 PUT/POST/PATCH/DELETE 路由绑定：PASS。
- `.venv/bin/python -m pytest -q`：388 passed，49 subtests passed。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：exit 0。
- `node --check ai_agent_platform/static/app.js`：exit 0。
- `git diff --check`：exit 0。

## Documentation impact

本任务没有改变产品 API、架构或用户可见行为，只增加被忽略的本机测试配置并验证既有 MCP 页面；因此无需修改 `README.md`、`INTERVIEW_NOTES.md` 或事实索引。设计与执行计划已记录在 `docs/superpowers/`。

## Remaining risk

受 Browser Use 的 localhost URL 安全策略与网络隔离限制，本轮没有完成真实浏览器 DOM 点击、删除后刷新和 console 检查；后端生命周期与前端契约已有自动化证据，但不能等同于浏览器 smoke 通过。
