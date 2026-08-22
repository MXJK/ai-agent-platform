# Agent 评测体系设计

> **状态：阶段一已实现，L2–L3 仍是设计方案。**
> 已落地：L1 轨迹约束、四项过程指标、引用真实性验证，见
> `evals/run_trajectory_evals.py`、`evals/trajectory.py`、`evals/citations.py`、
> `evals/trajectory_cases.json`，实测基线记录在 `evals/README.md`。
> 已落地：L1 还能在 app 内对着**已注册的真实模型**跑，结果入库并在前端"评测"页
> 展示成功率、五项指标、越界与相对基线的回归预警、历史与单例详情，见
> `ai_agent_platform/evaluation/`、`api/routes/evals.py`。
> 未落地：L2 结果质量、L3 稳定性与成本、A/B 实验矩阵、自建 25 条数据集、
> LLM judge、SWE-bench 子集。按 `INTERVIEW_NOTES/00` 的事实状态约定，未落地部分
> 只能以"规划"表述，不得使用完成时态。

## 为什么需要这份设计

当前 `evals/` 只有一层：确定性回归（fake LLM + 固定 fixture + 检索指标门禁）。它能证明
"管道没坏"，但回答不了面试和工程中最关键的三个问题——**过程对不对、多次跑稳不稳、
花了多少钱**。这三个问题正是 Agent 区别于普通 LLM 应用的地方，也是当前评测的空白。

一个关键的现有优势：`run_evals.py` 是通过真实 HTTP API 跑完整栈的
（`TestClient` → `POST /api/v1/agent/runs`，见 `_run_agent_case`），而 `fake` 只是
`LLMClient` 内部的一个 provider 分支（`integrations/llm.py:1016`）。**这意味着同一套
harness 换个模型配置就能从"确定性回归"切换到"真实质量评测"**，不需要另起炉灶。

## 分层模型

Agent 评测不是一件事。混在一起评就什么都说不清，因此拆成四层，各层的模型、频率和
成本都不同：

| 层 | 评什么 | 模型 | 频率 | 成本 | 现状 |
| --- | --- | --- | --- | --- | --- |
| L0 管道回归 | 系统没坏 | fake | 每次提交 | 0 | 已有 |
| L1 轨迹质量 | 过程对不对 | fake/真实 | 每次提交 / 手动 | 极低 / 有 | 已实现 |
| L2 结果质量 | 答案对不对 | 真实 | 手动/每周 | 有 | 缺 |
| L3 稳定性与成本 | 稳不稳、多少钱 | 真实 | 手动/每周 | 有 | 缺 |

L0 保持现状，不要改动。增量全部在 L1–L3。

---

## L0 管道回归（已有，保留）

- 实现：`evals/run_evals.py`、`evals/run_memory_evals.py`
- 数据：`evals/agent_cases.json`（9 个 fixture、9 个 case）、`evals/memory_cases.json`
- 门禁：`retrieval_thresholds` 中的 Recall@k / Precision@k / MRR / NDCG@k / HitRate@k
- 轨迹快照：`tests/golden/agent_loop_trajectories.json`
  （已有 7 个场景：`read_only`、`native_multi_turn`、`change_repair`、
  `waiting_input_resume`、`controls`、`hard_budget`、`change_set`）

**边界**：这层用 fake LLM，通过率**不能**当作答案质量证据。现有 `evals/README.md`
已经诚实标注了这一点，保持该表述。

---

## L1 轨迹评测

**优先级最高，因为免费。** fake 或廉价模型就能跑，可以进 CI 每次提交执行，而且它测的
正是"你的系统"——工具抑制、预算裁剪、重试策略全是本项目的代码，不是模型能力。

### 改造点：从"全等匹配"改成"约束式"（已实现，落点与原方案不同）

原方案打算把 `tests/golden/agent_loop_trajectories.json` 从全等比对改成约束式。

**实现时的修正**：约束层落在**新的 L1 套件**（`evals/trajectory_cases.json`），
金丝轨迹保持全等断言不变。理由是"全等太脆"这个论证在那里不成立——
`tests/test_agent_loop_characterization.py` 用手写确定性 planner 驱动图，
节点序列由本项目代码而不是模型决定，而它正是 `AGENTS.md` 与 `CLAUDE.md` 要求的
重构安全网。真正会随启发式演进而漂移的是规则 planner 的真实轨迹，约束式属于那里。

**问题**：全等太脆。模型稍微变一下就红，最后必然会去改期望值迁就它，评测随即失效。

**改法**：每个 case 声明**约束**而不是**标准答案**：

- `required_tools` —— 必须出现的工具
- `forbidden_tools` —— 必须不出现的工具（只读任务里出现 `sandbox.write_file` 是硬失败）
- `order_constraints` —— 偏序约束，如"读必须在写之前"
- `max_steps` —— 步数上限

L1 套件报告里观察到的节点序列以 `trace (diagnostic)` 打印，不作为通过条件。

### 四个自动采集的指标

1. **无效动作率** = (重复调用 + 失败后未换策略 + 被抑制的调用) / 总调用数
   - 采集点现成：`agents/coding/tool_loop_nodes.py:263` 已有 `suppressed_calls`
     和 `repeated_tool_call` 标记
2. **步数效率** = 实际步数 / 参考最优步数
3. **预算触顶率** —— 触发 `_max_exploration_rounds` 或 hard_budget 的 case 比例
   - golden 中已有 `hard_budget` 场景，扩成指标即可
4. **失败恢复能力** —— 注入一个必然失败的工具调用，观察是换策略还是死磕重试
   - 区分度最高的一个指标，大多数评测方案里没有

### 需要区分的两种失败

- **过程对但结果错** —— 通常是模型能力或证据不足问题
- **结果对但过程瞎撞** —— 更危险，因为不可复现；pass@1 会把它记成成功

L1 存在的意义就是把第二种揪出来。

---

## L2 结果质量

打分器**按可判定性排序**，能程序化判定的绝不用 judge。

### 优先级 1：程序化 ground truth

能跑测试就跑测试。本项目已具备全部条件：

- `patch_only` 仍是默认模式（`core/config.py:253`）
- ChangeSet + `sandbox.run_command` 可以完成"应用 patch → 跑测试 → 判定"

**这条路能覆盖的 case 一律不要用 judge。**

### 优先级 2：引用真实性验证（本项目最独特的指标，已在阶段一实现）

实现落在 `evals/citations.py`，由 L1 套件的 `verify_citations` case 调用。它不依赖真实模型，所以提前到了阶段一，而不是等 L2。

`result.context_sources` 携带 `path` / `start_line` / `end_line` / `text`，可以纯程序化
验证三件事：

1. 引用的 `path` 在工作区真实存在
2. `start_line..end_line` 范围内的**实际文件内容**等于 `text`
   —— 抓模型幻觉出来的代码片段
3. 答案中引用的路径 ⊆ 实际读过的 `context_sources` 路径
   —— **幻觉引用检测**，抓"引用了从没读过的文件"

零成本、零主观、结论极硬。本项目的核心卖点是"可信上下文"，这组指标正是它的量化证明。

### 优先级 3：LLM judge（最后手段）

只用于前两条覆盖不了的开放式回答。三条纪律缺一不可：

1. **带 rubric 打分**。不要问"好不好"，要问可判定的子项：
   "是否只使用了给定证据""是否回答了实际问题""拒答是否恰当"
2. **judge 模型必须不同于被测模型**（同模型自评有系统性偏高）
3. **必须做人工对齐校准**：抽 30 条自己标注，算 judge 与人工的一致率；
   低于阈值则该 judge 不可信，先修 rubric 再用

第 3 条没做的 judge 分数，在面试里一问就塌。

### 负样本必须占 20% 以上

专门构造**证据不足 / 越权请求 / 需要澄清**的 case，测系统在该拒绝时会不会硬答。

`agents/coding/planner.py` 的 `answer_prompt` 已有"当前没有收集到足够的代码或知识库
证据，无法可靠回答"这条路径，正好可以针对性验证。这类 case 比正样本更能体现工程水平。

---

## L3 稳定性与成本

- **pass^k**：同一 case 跑 k 次（k≥5），**全部通过才算过**
  - Agent 最大的问题是不稳定；pass@1 掩盖它，pass^k 暴露它
  - 不需要新数据集，改打分方式即可
- **方差**：报分布不报单值。"78%" 和 "78% ± 12%" 是两个完全不同的结论
- **成本与延迟**：每个 case 记录 token、USD、p50/p95 延迟
  - 用量账本（`usage_ledger.py`）已在记 token；USD 目前只有 `model_router.py` 的
    `estimated_cost_usd` 预估，账本侧缺实际成本字段

---

## 实验设计

前面几层是**打分方法**，这一步才是产出工程结论的地方。

**固定模型，只动系统配置，跑单变量 A/B：**

| 变量 | 对照条件 | 想验证的结论 |
| --- | --- | --- |
| rerank on/off | 同模型同 case | cross-encoder 值不值那点延迟 |
| 项目记忆 on/off | 同上 | 记忆注入是帮忙还是污染 |
| 探索预算 5 / 10 / 15 轮 | 同上 | 边际收益在哪一轮衰减 |
| 压缩阈值 | 同上 | 压缩省的 token vs 丢失的信息 |
| 模型 A vs B | 同配置 | 便宜模型够不够用 |

每格跑 k 次取分布，输出相对 baseline 的 diff 报告。

**每一行都应产出一句可量化的结论**，例如："关掉 rerank 引用准确率下降 N%，
p95 延迟降低 M ms，因此在 X 场景默认关闭"。这类结论与"我用了 cross-encoder 重排"
是两个层级的表述。

---

## 数据集

- **自建 25–30 条为主**，来源是真实使用本平台时遇到的任务。分层：
  简单检索 / 多文件理解 / 单文件修改 / 多文件修改 / 该拒绝的 / 需澄清的多轮
- **必须分 dev / test 两份**。调 prompt 只看 dev，test 只在阶段末尾跑一次
- **SWE-bench Verified 抽 20–50 条**作为外部锚点

### 关于公开 benchmark 的立场

AgentBench / SWE-bench / τ-bench 本质是**模型 benchmark，不是系统 benchmark**——
分数绝大部分由底层 LLM 决定，换个更强的模型分数就涨，与本项目的路由、rerank、压缩、
审批机制无关。

正确用法是把它们当**固定任务集 + 打分器**，跑"同一模型 × 不同系统配置"的 A/B，
此时分数差才反映本项目的贡献。

- **SWE-bench** —— 唯一真正适配的。本项目的代码 Agent 就是改文件，`patch_only`
  输出与 SWE-bench 的判定接口天然吻合。取 20–50 条子集即可；报绝对分时必须写清
  模型和子集，不得表述为"本平台 SWE-bench 得分 X"
- **τ-bench** —— **借方法，不跑 benchmark**。它是 airline/retail 客服域，与本项目
  场景不匹配，适配成本高。但两个设计值得抄：① user simulator（另一个 LLM 扮演用户，
  测多轮追问/改主意，正好覆盖当前无人测试的 `waiting_input` 与 steering 机制）；
  ② pass^k 指标
- **AgentBench** —— 跳过。2023 年方案，八个环境与本项目场景基本不重叠，信号弱

---

## CI 门禁分层

- **每次提交**：L0 + L1（fake 模型，秒级，零 API 成本）
- **手动 / 每周**：L2 + L3（真实模型，有成本）
- baseline 结果存盘，PR 中展示相对 baseline 的涨跌，而非绝对分数

---

## 落地顺序

| 阶段 | 内容 | 预估 | API 成本 | 状态 |
| --- | --- | --- | --- | --- |
| 一 | 轨迹约束改造 + 四个轨迹指标 + 引用真实性验证，进 CI | 1–2 天 | 0 | 已实现 |
| 二 | 自建 25 条数据集（dev/test 分离，负样本 20%）+ 接真实模型跑通 L2 程序化判定 | 2–3 天 | 低 | 规划 |
| 三 | A/B 矩阵 + pass^k + 成本延迟采集 + baseline diff 报告 | 2–3 天 | 中 | 规划 |
| 四（可选） | LLM judge（含人工校准）+ SWE-bench 子集 | 3–4 天 | 中高 | 规划 |

---

## 必须避的坑

1. **别拿 fake LLM 的通过率当质量证据** —— `evals/README.md` 标注得很诚实，保持
2. **别用被测模型当 judge** —— 系统性偏高
3. **别用 pass@1 当稳定性证据** —— 它恰好掩盖 Agent 最大的问题
4. **别让 test 集参与调 prompt** —— 会不知不觉过拟合到评测集，最常见也最致命
5. **别把全等轨迹匹配当质量门禁** —— 脆，最终会倒逼你修改期望值来迁就模型。
   例外是确定性 planner 驱动的 characterization 测试：那里没有模型可迁就，全等断言正是它的价值

---

## 相关源码入口

| 主题 | 位置 |
| --- | --- |
| L0 eval runner | `evals/run_evals.py`（`_run_agent_case`、`_run_search_case`） |
| L0 case 数据 | `evals/agent_cases.json`、`evals/memory_cases.json` |
| L1 轨迹 runner | `evals/run_trajectory_evals.py` |
| L1 约束与指标 | `ai_agent_platform/evaluation/trajectory.py`、`evaluation/trajectory_cases.json` |
| L1 引用验证 | `ai_agent_platform/evaluation/citations.py` |
| L1 应用内服务与 API | `ai_agent_platform/evaluation/service.py`、`api/routes/evals.py` |
| L1 前端评测页 | `ai_agent_platform/static/app.js`（`loadEvalDashboard`） |
| 轨迹快照 | `tests/golden/agent_loop_trajectories.json` |
| 工具循环与抑制标记 | `agents/coding/tool_loop_nodes.py` |
| 探索预算上限 | `agents/coding/context_nodes.py` |
| 拒答路径与 prompt | `agents/coding/planner.py`（`answer_prompt`） |
| 执行模式配置 | `core/config.py:253`（`patch_only` 默认） |
| 用量账本 | `usage_ledger.py` |
| 成本预估 | `integrations/model_router.py`（`estimated_cost_usd`） |
