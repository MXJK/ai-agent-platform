# RAG-ARCHITECTURE-DIAGRAM: 修复 RAG 系统架构图

## Goal

基于当前代码重新组织 RAG 子系统架构图，清楚表达入口、索引、混合检索、回答与
Coding Agent 进程内接入，并通过 Archify showcase 与桌面视觉验收。

## In scope

- 修订 `docs/architecture/rag-architecture.architecture.json`。
- 重新交付 `docs/architecture/rag-architecture.html` 及视觉检查证据。
- 仅记录已由代码验证的组件、数据流、存储职责和可配置适配器。

## Out of scope

- 修改 RAG 运行时代码、配置默认值、评测结果或产品文档。
- 部署或迁移。

## Acceptance criteria

- [x] 索引与检索/回答两条主链可一眼区分，Coding Agent 接入不与 HTTP 入口混淆。
- [x] 外部 LLM 与后端进程边界关系正确；向量库和文档/词法存储职责分开。
- [x] Archify showcase 校验为 9/9，0 errors，0 warnings。
- [x] 1440×900、1600×1000、1920×1080、2048×1320 均无桌面溢出。
- [x] 检查亮/暗主题截图，未见节点遮挡、路线歧义、卡片裁切或明显失衡。

## Decisions

- 保持 `architecture` 类型，用一条左到右入口主脊和上下两条短分支表达索引与检索。
- 将实现适配器写成可配置能力，不把某个评测 profile 误写为产品默认部署。
- 外部 `LLM Provider` 不放入后端进程边界；`DocumentStore` 明确区分 PostgreSQL FTS
  与内存 BM25，`VectorStore` 单独承载 Dense 召回。
- 文档影响仅限本任务交付的架构图：运行时行为、API/config 契约与已有 README、
  Interview Notes 事实均未改变，因此不做无关的说明性改写。

## Verification

- 已核对 `factory.py`、`service.py`、Knowledge Base API 与 Coding Agent
  `retrieve_knowledge` 节点，并将候选绑定公开仓库 revision
  `1d63ef5f37b210ab73383bdc92743c5a7094c133`。
- 初始旧 HTML 的 `visual-check` 为失败：1440×900、1600×1000、1920×1080
  均纵向溢出；2048×1320 才通过 containment。
- 新一轮重排回答分支后，`validate` 与 `deliver` 均为 showcase 9/9，0 errors、
  0 warnings；specification SHA-256
  `84827e266c53c90df5c9f22ff69fad78017ce75adcd1a1116ac96904915385aa`，HTML
  SHA-256 `003d1b7e5de6216054de402a196b209b022809d27ce60504352d81dd8d00be6f`。
- `visual-check` 覆盖旧 receipt/contact sheet/四张截图；1440×900、1600×1000、
  1920×1080、2048×1320 的 `scrollWidth/scrollHeight` 均等于 viewport，亮暗主题捕获
  通过。人工检查 1440 与 2048 亮暗截图，未见节点遮挡、路线歧义、标签或卡片裁切、
  主题对比异常和大屏明显空白失衡；`visual_review: passed`，视觉修正轮次 0。
- `.venv/bin/python -m pytest -q`：`805 passed, 135 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals` 与
  `git diff --check`：通过。

## Result

完成。新的代码证据架构图已覆盖旧 JSON、HTML、visual-check receipt、contact sheet 与
四张截图。图中将 HTTP 入口和 Coding Agent 进程内接入分开，索引链路位于上方，
DocumentStore/Reranker 分支位于下方，回答主路单独指向外部 LLM；四档桌面尺寸和亮暗
主题均已验收。未修改 RAG 运行时代码、产品默认值或评测结论。
