# UPDATE-PROJECT-DOCUMENTATION: 同步 README、面试手册与维护规则

## Goal

以当前工作树中的真实实现为事实源，更新 README 与模块化面试手册，并在项目
`AGENTS.md` 中建立后续功能变更的文档同步要求。

## In scope

- 更新 README 的当前能力、Agent 上下文链路、RAG、工作台、运行轨迹与 Token
  说明。
- 更新 `INTERVIEW_NOTES.md`、受影响的模块文档与 `facts.json`。
- 移除已经下线的代码仓库向量索引叙事，改为实时工作区探索与独立知识库 RAG。
- 更新前端、RAG 状态机、离线评测、Agent 轨迹与 Token 的面试口径。
- 在 `AGENTS.md` 中增加按行为变更同步 README 和面试手册的规则。
- 运行项目规定验证、面试文档校验、JavaScript 语法与 diff 检查。

## Out of scope

- 修改应用运行时行为、API 或数据库结构。
- 提交、推送、合并、部署或执行数据库迁移。
- 虚构生产流量、业务收益或未验证的模型质量。

## Acceptance criteria

- [x] README 能准确说明当前启动方式、核心能力和系统边界。
- [x] 面试手册不再把代码仓库向量索引描述为当前能力。
- [x] 面试手册覆盖实时源码探索、自动上下文路由、生产化 RAG、统一工作台、
      运行中轨迹与真实 Token 指标。
- [x] `facts.json` 中每项已实现能力均有存在的源码与测试证据。
- [x] `AGENTS.md` 明确功能、接口、配置、架构或用户流程变化时必须同步检查并
      更新 README 与面试手册。
- [x] 所有规定验证通过。

## Decisions

- 保持现有面试手册文件名和十 Part 结构，避免破坏既有链接；只更新标题和内容
  语义。
- 文档同步规则以“行为或事实变化”为触发条件；纯格式、内部重构且无外部事实
  变化时允许记录“无需更新”，避免无意义改写。

## Verification

- `.venv/bin/python -m pytest -q`：108 passed；保留 1 个第三方 Starlette
  弃用 warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：11 份 Markdown、18 项能力
  通过；changed-evidence warnings 符合当前未提交功能差异。
- `.venv/bin/python evals/run_evals.py`：8/8；RAG-only Recall@5=1.000、
  Precision@5=0.250、MRR=1.000、NDCG@5=1.000、Hit Rate@5=1.000。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- `go test ./gateway/...`：未执行，当前环境没有 `go` 命令；本任务未修改 Go
  源码，面试手册已明确该复验边界。
- 离线评测保留现有 LangGraph 未注册 msgpack 类型的未来兼容 warning，未将其
  误写成已解决问题。

## Result

README 已补齐统一工作台、知识库目录与索引任务、Dense/Lexical + weighted RRF、
运行中 checkpoint 轨迹和真实 Token 指标。模块化面试手册已从旧的“代码仓库
向量索引”叙事切换为“实时工作区探索 + 独立文档 RAG + 自动上下文路由”，并
更新前端、持久化、可靠性、评测和项目边界；中央事实索引现包含 18 项有效能力。

`AGENTS.md` 现在要求行为、API、配置、架构、数据流、运维、验证或能力边界变化
时，在同一任务同步 README、总面试入口、受影响 Part 与 `facts.json`；无文档
影响时必须在任务 Result 记录原因。面试手册继续遵循现有 `.gitignore`，保持为
本地个人资料；本任务没有擅自改变其版本控制/发布边界。
