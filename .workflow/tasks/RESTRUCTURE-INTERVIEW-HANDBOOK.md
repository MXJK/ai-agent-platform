# RESTRUCTURE-INTERVIEW-HANDBOOK：面试手册学习化与架构图重构

## Goal

基于当前源码、测试和中央事实索引，将 `INTERVIEW_NOTES` 从连续说明书重构为适合复习、口述和源码回查的分层学习手册，并为每个顶层 Part 配置可编辑架构/流程图。

## In scope

- 精简根入口，提供按学习目标、时间和源码主线组织的导航。
- 为 Part 00–10 增加“先记结论、学习小点、架构/流程图、源码阅读顺序”。
- 将过长的 Part 10 拆成多个独立知识单元，保留通用知识与项目证据的事实边界。
- 根据当前工作树中的真实模块、调用关系和测试证据校正文档。
- 生成可编辑 `.drawio` 与可直接嵌入 Markdown 的 PNG，并扩展本地校验器覆盖拆分文档和图文件。

## Out of scope

- 修改应用业务逻辑、API、数据库、迁移或运行配置。
- 把未验证工作树、规划能力或本地 profile 包装成提交级、生产级事实。
- 编造线上 QPS、延迟、准确率、成本或业务收益。
- 提交、合并、发布、迁移、部署或外部写入。

## Acceptance criteria

- [x] 根入口可以在 5 分钟内定位项目叙事、技术主线和面试演练路径。
- [x] Part 00–10 均有重点速记、编号学习点、架构/流程图和源码阅读路径。
- [x] Part 10 不再是千行单文件，拆分后的知识单元可以独立复习。
- [x] 每张图同时提供可编辑 `.drawio` 和 Markdown 可显示的 PNG。
- [x] 文档中的实现状态、源码路径与测试证据通过本地校验。
- [x] 手册校验、全量 pytest、compileall 与 diff 检查通过，或明确记录与本任务无关的既有失败。

## Decisions

- 保留 Part 00–09 的文件名和事实标记，避免破坏已有链接与 `facts.json` 映射。
- Part 10 变为总索引，通用知识按知识域拆入子目录；项目实现只引用已核验事实，不冒充个人独立贡献。
- 架构图使用中文标签，默认自上而下表达主链路，并把同层并行职责横向排列；源文件与导出图放在 `INTERVIEW_NOTES/assets/diagrams/`。
- 当前 `.workflow/state.yaml` 仍指向未完成的 `ENABLE-MEMORY-UX`，本任务不切换或关闭该活动任务，避免覆盖并行中的源码工作。

## Verification

- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过；校验 24 个 Markdown 文件、38 项能力、Part 10 的 12 个知识单元，以及 Part 00–10 的图文件契约。校验器按设计报告事实基线后的 evidence drift，因为工作树存在进行中的源码改动。
- `drawio-skill/scripts/validate.py ... --score`：11 个 `.drawio` 全部 `0 error(s), 0 warning(s)`，无穿越节点、连线交叉或形状重叠。每张最终 `.drawio.png` 已嵌入可编辑 XML，并运行 `repair_png.py` 修复/确认 PNG 结尾。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python -m pytest -q`：未全绿；`409 passed, 49 subtests passed, 2 failed`。失败为 `test_reads_database_and_qdrant_settings_from_environment`（当前 SQLite 默认状态与 Celery 环境组合冲突）和 `test_workspace_mode_environment_precedence_and_legacy_mapping`（当前实现允许 `worktree`，测试仍期望仅 `patch_only/direct`）。两处都位于本次开始前已修改的 `core/config.py`/配置测试工作树，本任务未修改应用源码或这些测试。
- `git diff --check`：通过。
- `git check-ignore -v INTERVIEW_NOTES.md INTERVIEW_NOTES/validate.py INTERVIEW_NOTES/assets/diagrams/part-01.drawio.png`：通过；个人面试手册、校验器和图继续由现有 `.gitignore` 排除。

## Result

完成手册的学习化重构：根入口从密集状态说明改为五分钟导航；Part 00–09 均新增“先记结论、学习小点、架构图、源码阅读顺序”；Part 10 从 1141 行长文改为 77 行总索引和 12 个 43–46 行的独立知识单元，保留原 Q1–Q57 并补上口述版、项目源码映射和自测清单。新增 11 组可编辑 `.drawio` 与最终 PNG，图内容覆盖事实边界、系统分层、LLM/SSE、三类上下文、Agent 状态图、工具权限、可靠执行、网关、观测、回答流程和通用知识树；本地校验器同步扩展到拆分文档和图资产。

本任务没有改变应用行为、API、配置或能力状态，因此不修改 `README.md` 或 `facts.json`；也没有提交、迁移、部署或外部写入。由于全量 pytest 仍受并行中的配置/Memory 工作树两项失败影响，并且 `.workflow/state.yaml` 的活动任务是 `ENABLE-MEMORY-UX`，closeout 未调用 controller 覆盖该活动状态，也未把项目状态标记为 done。
