# TASK-RAG-FILE-UPLOAD: 知识库文件上传与解析

## Goal

用本机文件选择和上传替代知识库页面的手工文件名、正文粘贴录入，并支持从
PDF、Word（`.docx`）和 Markdown/文本文件提取正文后进入现有分块、Embedding
和向量存储链路；同时澄清检索参数中最终重排保留数量的界面文案。

## In scope

- 将知识库文档录入接口改为文件上传。
- 服务端解析 PDF、DOCX 和现有 UTF-8 文本格式。
- 知识库页面支持选择一个或多个本机文件并逐个录入。
- 移除手工文件名和正文输入控件。
- 将“返回数量”改为“重排数量”，保留“召回数量”。
- 增加文件解析、上传校验和页面契约测试。
- 更新依赖与使用说明。

## Out of scope

- 旧版二进制 `.doc`、扫描件 OCR、图片、表格结构化抽取。
- 文件预览、文档级列表和删除。
- 部署、发布或数据库迁移。

## Acceptance criteria

- [x] 用户可从浏览器选择一个或多个本机文件，不再需要粘贴正文或填写文件名。
- [x] `.pdf`、`.docx`、`.md`/`.markdown` 及现有 UTF-8 文本格式可录入。
- [x] 空文件、无可提取文本、非法类型和超限文件返回明确错误。
- [x] 文件仍复用现有知识库存在性检查、分块、Embedding、存储和文档计数逻辑。
- [x] 检索参数显示“重排数量”和“召回数量”，并继续分别绑定 `limit` 与
  `recall_limit`。
- [x] 项目规定验证、前端语法检查和 diff 检查通过。

## Decisions

- Word 首版支持现代 Open XML `.docx`；旧版 `.doc` 返回明确的不支持提示。
- 单文件上传上限为 20 MiB。
- 浏览器通过 multipart 上传原始文件，PDF/DOCX 文本提取发生在服务端。
- 多文件由页面逐个调用单文件接口，单个失败不阻止其余文件继续录入。

## Verification

- `.venv/bin/python -m pytest -q`：通过，98 passed（1 个 TestClient
  第三方弃用 warning）。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- `.venv/bin/python evals/run_evals.py`：通过，4/4；Recall@5=1.000，
  MRR=1.000。
- 针对性上传与解析测试覆盖真实 PDF、DOCX、UTF-8 Markdown、空文件、非法
  UTF-8、旧版 `.doc` 和 20 MiB 限制。

## Result

完成。知识库页面已用多文件选择替代手工文件名和正文粘贴；上传接口改为
multipart 原始文件，服务端提取 PDF、DOCX 和 UTF-8 文本后复用现有录入链路。
界面中的最终保留数量已明确标为“重排数量”，候选数量仍标为“召回数量”。
旧版 `.doc` 与扫描件 OCR 保持在范围外，并会返回明确的不支持或无正文错误。
