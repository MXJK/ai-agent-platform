# CLARIFY-MEMORY-WORKBENCH：简化记忆工作台层级

## Goal

让记忆工作台只展示用户可以直接查看和治理的记忆对象，隐藏后台 L2 聚合过程，并用自然语言名称替代 L0/L1/L3 技术层级名称。

## In scope

- 移除项目页概览中的 L2 节点和个人记忆页的 L2 场景列表。
- 将前端的 L0/L1/L3 名称统一改为“对话记录 / 项目记忆 / 个人记忆”。
- 移除个人记忆页对 `/users/me/memory-scenes` 的非必要请求和对应渲染状态。
- 保留后端 L2 聚合、API 与画像生成语义，不修改持久化或 Agent 注入。
- 更新前端契约测试和当前项目文档。

## Acceptance criteria

- [x] 记忆工作台不再出现 L0/L1/L2/L3 技术层级标签或 L2 场景列表。
- [x] 三个记忆范围使用“项目记忆 / 个人记忆 / 对话记录”，说明文案准确描述读取和生效边界。
- [x] 个人记忆仍能加载设置、事实列表和模型可见摘要，且不请求场景列表。
- [x] 后端 L2 流水线与 API 保持不变。
- [x] 前端语法、契约测试、项目规定验证与文档校验通过。

## Decisions

- L2 是画像生成的后台实现细节，不再作为用户需要理解或治理的独立对象展示。
- “项目记忆”替代“项目原子记忆”，“个人记忆”替代“个人画像”，“对话记录”替代“对话原文”。
- 画像来源数量统一称为“记忆来源”，不把内部层级编号暴露给用户。

## Verification

- `.venv/bin/python -m pytest -q`：663 passed，107 subtests passed。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python -m pytest -q tests/test_api.py`：36 passed。
- `node --check ai_agent_platform/static/app.js`、`git diff --check`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 文件和 43 项能力；仅输出既有 evidence review warnings。
- Impeccable detector 以降级正则模式运行；本机缺少 HTML/CSS parser，报告的 11 项均为本任务范围外的既有样式告警。
- 真实浏览器 1440×960：三项概览等宽、无 L2 节点或场景列表、个人摘要正常加载，页面无横向溢出。
- 真实浏览器 390×844：三项概览和标签纵向折叠，底部导航可用，个人记忆页无横向溢出，控制台无 error/warning。

## Result

- 记忆工作台只展示“项目记忆 / 个人记忆 / 对话记录”，不再要求用户理解 L0/L1/L2/L3 编号。
- 删除了独立 L2 场景概览和个人记忆页的场景资产列表；前端也不再请求 `/users/me/memory-scenes`。
- 个人摘要仍展示模型会参考的确定性内容，但将内部英文分组标题在展示层转换为中文自然语言，其中 `Project scenes` 显示为“项目记忆摘要”。
- README 与被忽略但按项目约定维护的面试手册已同步；后端 L2 聚合、API、存储、画像生成和 Agent 注入均未修改，无迁移或部署影响。
- 当前改动未提交、未合并、未部署。
