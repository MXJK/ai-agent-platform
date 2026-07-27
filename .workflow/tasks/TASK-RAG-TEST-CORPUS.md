# TASK-RAG-TEST-CORPUS: RAG 手工测试语料

## Goal

为知识库文件上传、检索、重排和问答生成一套可重复使用的多格式测试语料，
并提供明确的测试问题、预期答案、命中文档和参数调试方法。

## In scope

- 在独立目录中生成 Markdown、TXT、JSON、YAML、HTML、PDF 和 DOCX 文档。
- 设计精确查找、语义检索、跨文档综合、版本冲突、负例和参数对比测试。
- 提供导入顺序、测试步骤、预期结果和故障排查说明。
- 将整个测试目录加入 `.gitignore`。
- 对 PDF 和 DOCX 做文本提取与渲染 QA。

## Out of scope

- 将语料自动上传到运行中的服务。
- 修改 RAG 检索或问答实现。
- OCR、旧版 `.doc` 或图片文档测试。
- 提交、部署或发布。

## Acceptance criteria

- [x] 独立目录包含至少 12 份可上传语料，并覆盖所有已支持的主要文档类别。
- [x] 测试指南与可上传语料分离，避免预期答案污染知识库。
- [x] 指南包含可执行问题、预期答案/来源、参数对照和负例。
- [x] PDF 与 DOCX 可被当前解析器提取正文，并通过视觉渲染检查。
- [x] 测试目录已被 Git 忽略。
- [x] 项目规定验证和 diff 检查通过。

## Decisions

- 使用完全虚构的 AuroraDesk 产品资料，避免与真实业务或项目代码混淆。
- `corpus/` 只放应上传的文档；根目录的 `TEST_GUIDE.md` 不应上传。
- 语料包含一份明确标记为过期的政策，用于验证版本冲突和重排能力。
- DOCX 采用 `compact_reference_guide` 预设和 `customer_story` 首屏结构。
- PDF 采用简洁的英文合规简报版式，避免字体兼容性影响解析测试。

## Verification

- 当前 `TextDocumentParser.parse_bytes` 成功解析 `corpus/` 下全部 18 份文档；
  分块结果共 21 个片段。
- DOCX 正文提取确认包含 `LANTERN-8`、`42 percent` 和 `17 minutes`；
  使用 `render_docx.py` 渲染为 1 页并目检，无裁切、重叠、孤页或字体问题。
- PDF 正文提取确认跨页包含 `STARLING-22`、`02:10 UTC`、`400 days`
  和 `35 days`；使用 Poppler 渲染 2 页并逐页目检，无裁切、重叠或页眉冲突。
- 使用当前内存 RAG、Hashing Embedding、混合排序和 Noop Reranker 运行 7 个
  冒烟问题，7/7 的预期文件均进入 Top 5。
- `git check-ignore`：确认测试指南、DOCX 和 PDF 均由
  `rag_test_documents/` 规则忽略。
- `.venv/bin/python -m pytest -q`：通过，98 passed（1 个 TestClient
  第三方弃用 warning）。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `git diff --check`：通过。

## Result

完成。`rag_test_documents/corpus/` 包含 18 份可上传的虚构 AuroraDesk 资料，
覆盖 Markdown、TXT、JSON、YAML、HTML、DOCX 和 PDF。根目录
`TEST_GUIDE.md` 提供 16 个有预期答案的问题、3 个版本冲突场景、4 个负例、
7 类格式检查、召回/重排参数对照和结果记录模板。测试指南与语料已分离，整个
目录已加入 Git 忽略，不会污染仓库历史。
