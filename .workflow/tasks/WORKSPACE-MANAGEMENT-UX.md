# WORKSPACE-MANAGEMENT-UX: 本地工作区管理收敛

## Goal

把混合用户、会话、模型和路径技术字段的设置弹窗收敛成单一的本地工作区管理入口。

## In scope

- 工作区文件夹添加、当前会话选择和显式的新会话默认值。
- 隐藏用户 ID、会话 ID、工作区 ID、根路径编辑和 Gemini thinking level。
- 失效路径状态、重新关联和保留历史数据的软移除。
- 内存/PostgreSQL 对等实现、API、迁移、测试和文档同步。

## Out of scope

- 删除本地文件、历史会话、用量、项目记忆或审计数据。
- 修改模型路由和 Gemini thinking level 的后端能力。
- 多租户工作区所有权模型重构。

## Acceptance criteria

- [x] 设置弹窗只显示用户可理解的文件夹工作区操作。
- [x] 选择文件夹会自动生成内部 ID、注册并切换当前工作区。
- [x] 当前工作区与新会话默认值含义分离且由用户显式选择。
- [x] 失效路径可见并可重新关联。
- [x] 移除工作区不删除本地文件或关联历史数据，同路径恢复保持 revision。
- [x] 内存与 PostgreSQL 仓储、服务和 API 测试覆盖新增契约。

## Decisions

- 使用 `removed_at` 软移除工作区，活动查询默认过滤已移除记录。
- 相同路径恢复不增加 revision；根路径变化仍按原有语义增加 revision。
- 用户和会话标识仍作为内部身份与持久化键存在，但不再由个人用户编辑。
- 现有知识库文档管理任务正在并行修改共享前端文件，本任务只做可合并的局部增量。

## Verification

- `.venv/bin/python -m pytest -q`：234 passed，4 subtests passed，1 条既有 Starlette 弃用警告。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，12 个 Markdown 文件和 29 项能力有效；证据变更复核提示均为信息性输出。
- `node --check ai_agent_platform/static/app.js`：通过。
- `.venv/bin/alembic heads`：单一 head `20260807_0017`。
- `git diff --check`：通过。
- 本地浏览器视觉 QA：空状态、文件夹选择、自动注册/切换、当前与默认徽标、隐藏技术字段和控制台错误检查均通过。

## Result

已完成。工作区设置现只呈现文件夹添加、选择、默认值、失效路径重新关联和软移除；用户 ID、会话 ID、内部工作区字段与 thinking level 不再由个人用户编辑。移除不会删除本地文件、历史会话或项目记忆，同路径恢复保持工作区 revision。

README、中英文说明、面试手册及能力证据映射已同步。提交时与共享前端、仓储、测试和连续迁移的 `KNOWLEDGE-BASE-DOCUMENT-MANAGEMENT` 作为同一个已验证快照记录。
