# MODULARIZE-INTERVIEW-NOTES: 面试文档模块化重构

## Goal

将本地 `INTERVIEW_NOTES.md` 重构为可按项目能力独立维护的入口与十个 Part，并用中央事实索引和轻量校验降低项目演进后的文档失真风险。

## In scope

- 将单文件内容迁移为根入口和 `INTERVIEW_NOTES/` 下十个主题 Part。
- 保留项目速用话术、事实边界、源码证据和面试追问，补充已完成的 Go 网关。
- 将尚未验收的前端产品化能力标记为“进行中”。
- 增加中央事实索引、维护约定和本地校验脚本。
- 更新 `.gitignore`，继续将个人面试资料排除在 Git 版本管理之外。

## Out of scope

- 修改应用 API、Schema、业务实现或运行时配置。
- 完成或改写当前 `PRODUCTIZE-FRONTEND` 任务中的前端代码。
- 编造线上流量、效果、延迟、成本或业务收益。
- 部署、发布、迁移、提交或外部写入。

## Acceptance criteria

- [x] 根入口可快速定位十个 Part，并包含复习路线和分时长项目介绍。
- [x] 十个 Part 可独立阅读，技术 Part 使用统一章节模板。
- [x] 中央事实索引只使用约定的四种状态，已实现能力均有源码与测试证据。
- [x] Go 网关进入项目主线，前端产品化保持“进行中”。
- [x] 校验脚本可检查链接、证据路径、章节、状态、重复标题和证据变更。
- [x] 文档、Python 与 Go 验证命令通过。

## Decisions

- 文档以项目主线为核心，只保留能解释本项目取舍的通用知识。
- 根文件作为速用入口，详细内容拆到编号 Part；中央事实以 JSON 为单一索引。
- 文档和校验脚本继续本地维护并由 Git 忽略。
- 不切换 `.workflow/state.yaml` 的当前活动任务，避免错误关闭仍在进行的 `PRODUCTIZE-FRONTEND`。

## Verification

- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过；校验 11 个 Markdown 和 15 项能力。正确提示当前 `tests/test_api.py` 与前端 HTML/CSS/JS 相对事实基线有变化，需要在前端任务完成后复查对应能力。
- `.venv/bin/python -m pytest -q`：通过，83 passed；保留 1 个第三方 LangGraph pending deprecation warning，另有本机 LibreSSL/urllib3 提示。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `GOCACHE=/private/tmp/ai-agent-platform-go-cache /private/tmp/ai-agent-platform-go-toolchain/bin/go test ./gateway/...`：通过；使用已存在的 Go 1.26.5 临时工具链。沙箱内首次因 `httptest` 无权监听回环端口失败，允许本地测试端口后通过。
- `.venv/bin/python evals/run_evals.py`：4/4 cases passed；Recall@5=1.000，MRR=0.833，3 个带检索标注的 case。
- `git diff --check`：通过。
- `git check-ignore -v INTERVIEW_NOTES.md INTERVIEW_NOTES/facts.json INTERVIEW_NOTES/validate.py`：通过，根入口与拆分目录均保持本地忽略。
- JSON 语法、Markdown fence、内部链接、重复标题、证据路径和敏感信息模式检查：通过。

## Result

将原 1700 行单文件重构为 72 行根入口和十个独立 Part，按项目叙事、AI 核心、工程化与面试演练分组。保留简历话术、分时长介绍、三个难点和表达红线；补充 Go 流量网关、Go/Python 职责边界、网关追问与测试证据；前端产品化明确标记为“进行中”。新增 `facts.json` 作为四态能力与证据索引，并新增纯标准库校验器检查文档结构和项目漂移。仅修改 `.gitignore` 以忽略拆分目录，未修改应用 API、Schema 或运行时行为，也未触碰当前前端实现改动。
