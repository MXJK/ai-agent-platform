# HARDEN-ADAPTIVE-REPO-EXPLORATION: 自适应仓库探索与失败恢复

## Goal

让代码 Agent 在仓库证据不足、搜索零命中或只读工具失败时持续执行
“观察 → 判断 → 换策略”，不再把一次搜索结果或空计划误判为上下文充分，
并确保项目概览类问题优先以当前工作区的 README、清单和源码为依据。

## In scope

- 为仓库探索增加可解释的阶段化策略与失败恢复。
- 区分“证据充分”“探索预算耗尽”“当前策略无新调用”。
- 为自然语言项目概览提供根目录发现与入口文件读取兜底。
- 修复文件列举遇到忽略目录或越界符号链接时整体失败的问题。
- 防止 sandbox 产物工具提前终止仍需继续的只读 native tool loop。
- 补充单元测试、离线 Agent 用例和相关架构文档。

## Out of scope

- 修改知识库向量检索、Embedding 或 reranker 算法。
- 新增 workspace 与知识库的持久化绑定模型。
- 修改前端交互、数据库 schema、认证、部署或发布流程。
- 提交、推送、合并、迁移或外部写入。

## Acceptance criteria

- [x] 零命中搜索不会直接结束探索；Agent 会切换到文件发现或入口读取策略。
- [x] `no_new_plan` 不再等价于 `context_sufficient`，结束原因可在 trace 中审计。
- [x] “这个项目是干什么的？”能够从 README/项目清单获得实时仓库证据。
- [x] `repo.list_files` 会跳过忽略目录、符号链接和越界目标，不因单个条目失败。
- [x] 只读工具失败后 native loop 仍可观察结果并继续规划，不被 artifact 工具抢先终止。
- [x] 新回归测试覆盖中文自然语言、零结果恢复、符号链接和 native replan。
- [x] README、访谈手册和 facts 映射与新行为保持一致并通过校验。
- [x] 仓库要求的 pytest、compileall、手册校验和 diff 检查通过。

## Decisions

- 仓库探索只在取得实际仓库证据且没有待读候选，或明确耗尽探索预算时停止；
  `no_new_plan`、零命中和单次工具失败都只代表需要重新规划。
- 使用可审计的阶段策略：入口发现、候选读取、定向搜索、失败后的广泛文件枚举；
  trace 同时记录 `exploration_strategy` 与 `context_stop_reason`。
- 通用项目概览问题优先路由到实时工作区，并按 README、项目清单和常见入口文件排序，
  不让未显式请求的托管知识库替代仓库事实。
- 对探索调用按语义去重，避免只因参数表示差异重复调用；预算仍作为确定性的最终止损条件。
- 文件枚举跳过忽略目录、符号链接、损坏链接和越界目标，单个不安全条目不会使整个调用失败。
- native tool loop 先观察并重新规划已返回的分析/产物调用，再进入生命周期产物收集，
  从而允许只读工具失败后的恢复调用继续执行。

## Verification

- `.venv/bin/python -m pytest -q`：223 passed，4 subtests passed；仅有既有的
  `StarletteDeprecationWarning`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python evals/run_evals.py --cases evals/agent_cases.json`：9/9 通过；
  Recall@5=1.000，MRR=1.000，NDCG@5=1.000，HitRate@5=1.000。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：校验 12 个 Markdown 文件和
  28 个 capability，通过；输出为证据变更复核提醒，无校验错误。
- `git diff --check`：通过。
- 对真实工作区执行项目概览回放：先发现入口文件，再读取 README 与项目清单，
  trace 依次出现 `discover_project_entries`、`read_discovered_entries`，最终以
  `evidence_sufficient` 停止。
- 对真实工作区执行 `repo.list_files`：返回 200 个文件并正确标记截断，包含
  `README.md`，且不包含 `.venv-*` 路径，也未被越界符号链接中断。

## Result

已完成自适应仓库探索、零结果与工具失败恢复、项目概览实时仓库优先、文件枚举
安全性和 native tool loop 顺序修复。新增单元/集成回归与离线评测，并同步
README、模块化访谈手册和 facts 证据映射。本任务有文档影响，因为它改变了
用户可见的上下文来源优先级、探索停止条件、trace 字段与工具失败恢复行为。

用户已另行授权提交与推送；本任务变更将独立提交，不包含并行进行的知识库
文档管理任务及其共享工作流状态变更。
