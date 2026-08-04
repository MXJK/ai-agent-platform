# CHINESE-README: 中文优先的双语 README

## Goal

把仓库默认 README 调整为中文，在文档顶部提供清晰的中英文切换入口，并保留适合
公开发布的英文说明。

## In scope

- 将 `README.md` 改为默认展示的中文项目说明。
- 将现有英文 README 保留为 `README.en.md`。
- 在中英文文档顶部添加互相跳转的语言入口。
- 清理不会提交到 GitHub 的面试手册链接和相关命令。
- 检查本地链接、Markdown 格式和仓库文档验证。

## Out of scope

- 修改应用行为、API、配置或架构。
- 重写 Interview Notes 的项目知识内容。
- 发布、合并或创建外部资源。

## Acceptance criteria

- [x] GitHub 等默认入口优先展示中文 `README.md`。
- [x] 中文 README 覆盖现有英文文档的主要功能、启动、配置、架构与验证信息。
- [x] 适合公开发布的英文内容保存在 `README.en.md`。
- [x] 两种语言可以在文档顶部互相跳转。
- [x] 公开 README 不引用不会提交到 GitHub 的面试手册文件。
- [x] 文档链接和仓库规定的相关验证通过。

## Decisions

- 采用 `README.md`（中文）与 `README.en.md`（英文）的惯例，而不是让默认页先经过
  一个语言选择中间页。
- 中文版按功能主题重写并保留命令、配置名、API 路径和关键边界，不逐句机械直译。
- 面试手册属于本地资料，不进入 GitHub，因此中英文 README 都不展示相关介绍、链接
  或只对本地资料有效的验证命令。

## Verification

- `.venv/bin/python -m pytest -q`：208 passed，1 个既有 Starlette/httpx 弃用
  警告，另有 4 个 subtests 通过。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：12 个 Markdown、28 项能力通过；
  evidence review warning 来自当前 HEAD 相对 `last_verified_commit` 的既有源码差异，
  本任务没有修改这些证据文件。
- 本地 Markdown 链接存在性检查：通过。
- 中英文 Markdown 围栏配对检查：通过。
- 中英文 README 面试手册残留引用检查：通过，未发现相关链接或命令。
- `git diff --check`：通过。

## Result

- 默认 `README.md` 已改为中文，增加目录并覆盖项目概览、本地启动、产品能力、模型
  路由与预算、代码 Agent、工作区 API、项目记忆、知识库、持久化、网关和验证说明。
- 适合公开发布的原英文内容保留为 `README.en.md`；面试手册介绍、链接与本地验证
  命令已从中英文版同步移除。两份文档顶部可以互相切换，中文版末尾也提供英文入口。
- 本任务只调整文档语言与导航，没有改变用户可见行为、API、配置、架构、数据流、
  运维命令或能力边界，因此不需要修改 Interview Notes 与 facts 索引。
- 未修改工作区中既有的未跟踪文件 `.workflow/tasks/AGENT-INTERVIEW-KNOWLEDGE.md`。
