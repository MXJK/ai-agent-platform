# LOCAL-SKILL-MCP-TEST-SETUP：本地 Skill 与 MCP 前端测试配置

## Goal

为当前仓库准备不入库的 project Skill 和 MCP 测试配置，并验证 MCP 页面可以新增、
编辑、测试/刷新、启停和删除单个 Server。

## Acceptance criteria

- [ ] `.agents/skills` 下存在三个可被项目发现器加载的测试 Skill。
- [ ] Skill 目录和 `mcp.json` 被 Git 忽略。
- [ ] 本机 `.env` 允许并启用 Skill/MCP，MCP 管理写接口在 loopback 本机模式可用。
- [ ] Everything MCP 默认启用；Filesystem MCP 默认停用且只指向当前仓库。
- [ ] MCP 页面新增、编辑、测试/刷新、启停和删除均通过验证。
- [ ] 项目要求的测试与 compileall 通过。

## Permitted scope

- 本地忽略配置、测试 Skill、MCP Server 注册表、相关验证与最小文档记录。
- 若既有 MCP 页面验收失败，先系统调试，再只修复经证实的缺口。
- 不部署、不迁移、不推送、不放宽中央工具权限、审批或 Sandbox。

## Result

进行中。
