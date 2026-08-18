# LOCAL-NATIVE-DIRECTORY-PICKER: 本机可信网关原生目录选择器

## Goal

让仅发布到 loopback 的本机可信网关能够安全调用同机原生目录选择器，并在能力不可用时可靠回退网页目录浏览器。

## In scope

- 增加进程级原生目录选择器模式，区分直连 loopback、本机可信网关和禁用部署。
- 本机可信网关请求通过现有共享密钥身份边界后允许打开同机 Finder。
- 原生选择器因部署策略不可用时，前端可靠回退受限网页目录浏览器。
- 补充配置/API/前端契约测试，并同步中英文 README 与面试手册事实。

## Out of scope

- 不把私网或 Docker 网桥地址泛化为 loopback。
- 不允许普通 OIDC/远端网关无配置地触发服务器桌面窗口。
- 不改变 `WORKSPACE_ALLOWED_ROOTS`、Workspace 角色或 direct/worktree 写入边界。
- 不提交、推送、合并、部署或迁移。

## Acceptance criteria

- [x] 默认直连 loopback 模式保持现有行为，远端地址仍返回 403。
- [x] `trusted_local_gateway` 模式仅接受通过现有 gateway 信任密钥验证的请求，并允许 Docker 网桥来源调用原生选择器。
- [x] `disabled` 与策略性 403 会回退网页选择器，且其他 403 不被误判为能力不可用。
- [x] 所有选中/浏览路径继续受 `WORKSPACE_ALLOWED_ROOTS` 规范化校验。
- [x] 本地/production 配置示例、README 和面试手册准确描述部署边界。
- [x] 聚焦测试、全量 pytest、compileall、文档校验和 diff 检查通过。

## Decisions

- 以显式进程配置表达本机交互能力，不依赖代理后的客户端 IP 推断物理位置。
- 保留 `loopback` 为默认值以兼容现有直连使用；可信本地网关必须显式启用。
- 网页回退只识别当前原生选择器策略错误，避免吞掉未来其他权限型 403。
- `trusted_local_gateway` 复用现有 `X-Gateway-Auth` / `X-Authenticated-User` 信任边界；
  production 示例显式使用 `disabled`，不把 OIDC 身份等同于本机桌面能力。

## Verification

- `.venv/bin/python -m pytest -q tests/test_api.py tests/test_config.py tests/test_config_resolver.py`：`81 passed, 32 subtests passed`。
- `.venv/bin/python -m pytest -q`：`422 passed, 49 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`、`git diff --check`：通过。
- `./scripts/start-local.sh --check`：当前 local + trusted gateway + native picker 配置解析通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 文件和 39 项能力；仅输出当前脏工作树的 evidence review 提醒。

## Documentation impact

- 已同步 `README.md`、`README.en.md`、`INTERVIEW_NOTES.md`、Part 07、`facts.json`、
  local/production dotenv 示例和当前本机 `.env`。
- 明确 Workspace 路径属于实际执行 Agent 的文件系统；云端控制面若访问用户本地代码，
  需要本地 Agent/桌面 companion，不使用云端 Finder 选择客户端目录。

## Result

已完成。原生目录选择器现在以 `loopback`、`trusted_local_gateway`、`disabled` 三种
进程级模式表达部署能力。本机可信网关请求先通过现有共享密钥身份校验，再允许来自
Docker 网桥地址的请求打开同机 Finder；默认直连 loopback 与路径 allowlist 行为保持
不变。前端对明确的 picker 策略型 403 回退网页目录浏览器，其他权限型 403 仍直接暴露。

本任务没有修改 Workspace 登记、角色、direct/worktree、文件写入或生产 OIDC 边界，
没有提交、推送、部署或迁移。工作树中原有的其他工作流文档改动未纳入本任务。
