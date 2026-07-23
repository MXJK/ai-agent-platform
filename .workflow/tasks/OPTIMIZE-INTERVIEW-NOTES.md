# OPTIMIZE-INTERVIEW-NOTES: 优化秋招面试文档

## Goal

基于项目真实实现重构 INTERVIEW_NOTES.md，使其适合秋招复习、项目陈述与技术追问，并提供可验证的项目证据和表达边界。

## In scope

- 核对 `INTERVIEW_NOTES.md` 与当前源码、测试、README 的一致性。
- 重构文档的信息层级，使其支持 30 秒、1 分钟和 3 分钟项目陈述。
- 补充简历项目描述、核心亮点、技术选型、追问回答和复习路线。
- 明确已实现、可本地切换、规划中与生产化建议的边界。
- 修正过时文件路径、接口说明和容易被面试官追问击穿的表述。

## Out of scope

- 修改应用源码、依赖、数据库或部署配置。
- 编造线上流量、准确率、延迟、成本或业务收益数据。
- 将尚未落地的生产能力描述为已经实现。

## Acceptance criteria

- [x] 文档开头提供可直接使用的项目定位、简历表述和分时长自我陈述。
- [x] 核心模块均关联当前仓库中的实现证据或测试证据。
- [x] 明确区分当前实现与生产化演进方向。
- [x] 覆盖 FastAPI/SSE、LLM、RAG、LangGraph、工具/MCP、持久化、可靠性与可观测性追问。
- [x] 提供面试前复习清单与无法量化时的稳妥表达方式。
- [x] Markdown 结构、内部目录和代码路径通过人工与命令检查。

## Decisions

- 面向 Python/AI 应用后端/Agent 工程方向的秋招岗位。
- 主打“可运行的工程平台与可靠性边界”，避免包装成大规模线上系统。
- 保留原文中仍准确的技术原理，但将项目陈述与通用知识分层。
- 默认配置与可选生产化后端分开表述，不把 Docker Compose 或配置开关描述为生产经验。
- `INTERVIEW_NOTES.md` 受 `.gitignore` 忽略，本次按用户要求更新本地文档，不纳入版本控制。

## Verification

- `rg` 检查文档标题、关键实现路径和过时表述：通过，旧 `api/router.py`、`integrations/rag.py`、默认 Gemini 等表述已清除。
- Markdown fenced code block 检查：126 个 fence，数量成对。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python -m pytest -q`：82 passed，1 个第三方 LangGraph pending deprecation warning。
- `.venv/bin/python evals/run_evals.py`：4/4 cases passed；Recall@5=1.000，MRR=0.833，3 个有检索标注的 case。

## Result

完成 `INTERVIEW_NOTES.md` 秋招导向重构：新增一句话定位、项目事实边界、简历 bullets、30 秒/1 分钟/3 分钟陈述、三个主讲难点、表达红线、源码证据地图和面试前检查清单；修正默认 embedding、RAG 路径、向量库数量和 Agent events 等事实；补充代码感知混合检索、沙箱改动闭环、Celery/Redis 可靠性、离线评测及 15 个综合追问。未修改应用源码、数据库、部署配置或依赖清单。
