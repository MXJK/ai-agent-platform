# MODEL-LATENCY-PROBES：模型延迟刷新与主动探测

## Goal

让模型管理页及时呈现真实请求延迟，并支持针对单个已注册模型手动测速；在用户显式启用时，
由 API 进程周期性探测长时间无真实流量的模型，同时保证固定短提示的探测样本不污染业务
请求 P50 和自动路由。

## In scope

- 模型管理页在可见时定期只读刷新，并在模型请求结束后刷新注册表状态。
- 为每个已注册模型提供独立的手动测速 API 和按钮。
- 持久化独立的探测样本、成功/失败、最近探测时间和错误。
- 增加默认关闭的周期探测配置；只在 API 进程运行，跳过近期已有真实请求的模型。
- 补充配置、服务、API、生命周期、前端契约和迁移测试。
- 同步 README、Interview Notes 与 facts 证据。

## Out of scope

- 修改现有业务请求延迟 P50 的路由算法或质量/价格画像。
- 在未配置时自动产生付费 Provider 调用。
- 多节点分布式调度、部署、发布或执行生产数据库迁移。
- 提供复杂的测速次数、提示词或并发度 UI 配置。

## Acceptance criteria

- [x] 每个启用且凭证可用的已注册模型都能单独手动测速，响应包含精确模型、耗时和时间。
- [x] 手动及周期探测只更新独立探测指标，不增加业务请求样本数或改变路由 P50。
- [x] 同一模型的并发探测被拒绝；停用、缺少凭证和上游失败返回可理解错误并记录失败。
- [x] `MODEL_PROBE_INTERVAL_SECONDS=0` 时不启动周期任务；启用值至少为 60 秒且只在 API 角色启动。
- [x] 周期任务跳过近期存在真实请求的模型，并能在运行时关闭时停止。
- [x] 模型管理页显示业务 P50、探测延迟、样本数和最近更新时间，测速期间有禁用/忙碌状态。
- [x] 模型管理页可见时每 60 秒只读刷新，请求完成后也刷新，不产生主动模型调用。
- [x] 数据库迁移、配置样例、README、Interview Notes、facts 和自动化测试保持一致。

## Decisions

- 探测使用固定最短回复提示，以便模型间结果可比较，但探测指标不参与现有路由。
- 周期探测默认关闭；启用后以配置间隔为“真实流量新鲜度”阈值，近期有真实成功或失败即跳过。
- 周期任务首轮等待完整间隔，避免进程启动立即产生一轮付费请求。
- 复用 Provider 连接测试作为兼容入口，但将其委托给该 Provider 的第一个启用模型的独立探测。

## Verification

- `.venv/bin/python -m pytest -q`：最终完整复跑通过，`674 passed, 107 subtests passed`。
  首轮出现一次既有异步本地记忆重启竞态；该用例单独复跑通过，随后全量复跑无失败。
- 聚焦回归：`tests/test_model_probes.py`、`tests/test_model_registry.py`、
  `tests/test_self_hosted_compose.py`、`tests/test_config.py`、
  `tests/test_runtime_bootstrap.py` 共 `76 passed, 12 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`、`docker compose config --quiet`、
  `git diff --check`：通过。
- `.venv/bin/alembic heads`：单一 head `20260825_0025`。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 文件和 43 项能力；
  仅输出相对既有 verified commit 的 evidence review warnings。
- Impeccable detector 以降级正则模式运行；本机缺少 HTML/CSS parser，报告项均位于本任务
  未修改的既有样式行。按仓库约定未在 worktree 启动第二套前端服务；合并后再用根 checkout
  的运行栈做真实浏览器确认。

## Result

- 新增精确模型 `POST /model-registry/models/{model_id}/test`，Provider 兼容测试也委托给
  具体启用模型；响应返回 Provider、模型、总耗时和探测时间。
- 新增 `model_probe_stats` 与 `20260825_0025` 迁移，最近 20 个成功固定探测样本、失败、
  最近耗时和时间独立持久化。`LLMClient` 在探测上下文中抑制业务遥测，因此现有最近 100
  个真实请求样本和路由 P50 不受影响。
- 新增默认值为 0 的 `MODEL_PROBE_INTERVAL_SECONDS`。启用值至少为 60 秒，仅 API 角色
  启动后台线程，首轮等待完整间隔，并跳过间隔内有真实成功或失败流量的模型。
- 模型页可见时每 60 秒只读刷新，Chat/Agent 调用结束后也刷新；模型卡显示业务/探测
  样本、延迟和更新时间，按钮有忙碌、禁用、并发拒绝和明确失败反馈。
- README、英文 README、dotenv/Compose 样例和忽略跟踪但按项目约定维护的 Interview
  Notes/facts 已同步。没有执行真实 Provider 请求、数据库迁移、提交、合并或部署。
