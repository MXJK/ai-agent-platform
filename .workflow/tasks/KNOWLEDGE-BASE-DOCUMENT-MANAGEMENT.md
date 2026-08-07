# KNOWLEDGE-BASE-DOCUMENT-MANAGEMENT: 知识库与文档管理 v1

## Goal

将现有粗粒度知识库页面升级为以当前知识库为上下文的文档管理工作台，
支持文档元数据可见、分页查询、编辑、显式替换、单个/批量删除，
并保留现有检索问答和知识库目录管理能力。

## In scope

- 稳定的知识文档元数据模型、PostgreSQL 迁移和内存存储对等实现。
- 文档列表、详情、元数据修改、文件替换、单个和批量删除 API。
- 同名上传冲突、替换失败保留旧内容和跨存储补偿。
- 左侧知识库选择、右侧文档/检索问答/设置标签页的响应式工作台。
- 后端、API、前端状态和迁移回归测试，以及用户/架构文档同步。

## Out of scope

- Workspace 或用户级知识库所有权与权限改造。
- 原始上传文件保存和下载。
- 在线正文编辑、用户可见版本历史或知识库间移动。
- 更换前端技术栈，修改 RAG 排名算法，部署、发布、提交或外部写入。

## Acceptance criteria

- [x] 选中知识库后，文档列表可分页、搜索、筛选和排序，并显示约定元数据。
- [x] 文档详情可修改标题、描述和标签，且不触发重新嵌入。
- [x] 同名新建显式返回冲突；用户确认后可保留文档 ID 替换文件并重建索引。
- [x] 替换失败时旧文档仍可检索；删除后词法和向量检索无残留。
- [x] 单个和最多 100 个文档的批量删除返回可审计的成功/失败结果。
- [x] 知识库工作台在桌面和移动端可用，支持键盘、焦点恢复和非颜色状态表达。
- [x] 现有知识库检索、问答、精排、Agent 路由和目录删改能力无回归。
- [x] README、英文 README、访谈手册及 facts 证据与新 API、数据流和能力保持一致。
- [x] pytest、compileall、访谈手册校验、JavaScript 语法和 diff 检查通过。

## Decisions

- 知识库保持全局共享；文档修改分为元数据 PATCH 和文件 PUT。
- 不保存原文件；文件大小和 MIME 只作为上传元数据保留。
- 同名文件不静默覆盖，必须走显式替换接口。
- 保留原生 HTML/CSS/JavaScript，以文档索引健康轨作为页面标志性信息编码。
- PostgreSQL 删除依赖单事务回滚，向量侧使用文档快照补偿；内存实现保持同一边界。
- 900 px 以下用原生顶部选择器替代目录长列表，680 px 以下将表格转成文档卡片，
  详情抽屉占满视口。

## Verification

- `.venv/bin/python -m pytest -q`：234 passed，4 subtests passed，1 个第三方弃用 warning。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/alembic heads`：单一 head `20260807_0017`。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：12 份 Markdown、29 个 capability 通过；
  evidence review 仅报告当前未提交工作树中的已知变更路径。
- `git diff --check`：通过。
- 浏览器冒烟：创建知识库、加载文档列表、搜索、详情抽屉、元数据保存、索引健康状态；
  1440/1024/390 px 无横向溢出，390 px 使用顶部选择器、文档卡片和全屏详情。
- 2026-08-07 UI 修复复查：为批量操作增加独立 `document-actions` 布局，并重置上传
  `<label>` 继承的表单 margin/grid。1440/1024 px 下“删除已选 (1)”与“批量上传”
  均为 40 px 高且顶部坐标一致；390 px 下各占半行，无相互或底部导航叠加。
- 修复后的全量验证已尝试，但在测试收集前被工作树中同期出现的未解决 Git 冲突阻塞：
  `workspace.py`、`workspace_service.py`、`workspaces.py`、`app.js`、`index.html`、
  `styles.css`、README 和 workspace tests 含 conflict markers。未擅自选择冲突版本。

## Result

已完成知识库上下文工作台、稳定 `KnowledgeDocument` 模型、文档 CRUD/显式替换/
批量删除 API、同名冲突、PostgreSQL 回填迁移、Memory/PostgreSQL 对等仓储，以及
文档/chunk/vector 失败补偿。未增加原文件保存、下载、正文编辑、版本历史或租户隔离。
README、英文 README 和被 `.gitignore` 排除但由工作流要求维护的访谈手册/facts 已同步。

后续 UI 修复已完成并通过浏览器尺寸检查；该修复不改变 API、数据模型、配置或用户
文档语义，因此无额外文档影响。仓库需先由用户完成/授权冲突解决，再重新运行必需的
pytest、compileall、JavaScript 和 diff 验证，任务才能恢复为 done。
