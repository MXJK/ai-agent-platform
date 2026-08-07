# DYNAMIC-MODEL-POLICY: 前端驱动的动态模型准入

## Goal

让持久化模型注册中心成为聊天与 Agent 模型准入的唯一运行时来源，使前端注册、启用并选择的任意受支持模型无需 `.env` 静态 Provider/Model 白名单即可使用。

## In scope

- 移除 `MODEL_PROVIDER_ALLOWLIST`、`MODEL_ALLOWLIST` 配置及静态校验。
- 统一会话偏好、聊天路由、Agent 与 Token 预算预检的模型准入来源。
- 保留“Provider 连接启用且模型已注册并启用”的安全边界。
- 修复只修改工作区时被旧会话模型静态校验阻断的问题。
- 为动态注册、禁用模型、会话工作区切换和旧会话增加回归测试。
- 同步 `.env.example`、README 与相关任务事实说明。

## Out of scope

- 新增 Provider 类型、在线模型质量/价格同步或调用真实付费模型。
- 修改 API Key 的 keyring 存储边界。
- 部署、迁移数据库、发布或外部写入。

## Acceptance criteria

- [x] 前端注册并启用的模型可直接设为手动首选并用于聊天/Agent，无需静态白名单环境变量。
- [x] 自动路由只考虑连接已启用、模型已注册且已启用的候选。
- [x] 未注册、已禁用模型或已禁用 Provider 仍在外部调用前被拒绝。
- [x] 修改会话工作区或默认工作区不再因为保留的模型字段返回静态 allowlist 400。
- [x] `.env.example` 与 README 不再要求维护 Provider/Model 静态白名单。
- [x] 完整 pytest、compileall、前端语法和文档相关验证通过。

## Decisions

- 模型注册中心是运行时策略来源；`.env` 只保留主模型 bootstrap、Provider 凭据和非模型策略配置。
- 注册/启用状态是管理员在本机前端表达的准入意图，模型执行路径不得再叠加另一份静态名单。
- 会话与用户偏好的部分更新仅在 Provider/Model 字段实际变化时校验动态注册状态；修改工作区不会被保留的旧模型阻断。
- Embedding 模型仍由独立 RAG 配置选择，不接受请求级任意 Provider/Model 覆盖，因此移除重复静态名单后继续使用现有配置边界。

## Verification

- `.venv/bin/python -m pytest -q`：`234 passed, 1 warning, 4 subtests passed`；唯一警告为既有 FastAPI TestClient 的 httpx2 弃用提示。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- `.venv/bin/python -m alembic heads`：返回单一 `20260807_0017 (head)`。
- 专项模型配置、路由、注册中心与流式测试：`54 passed, 1 warning, 4 subtests passed`。
- 文档影响已同步 `.env.example`、`README.md` 与 `README.en.md`；仓库当前不跟踪 `INTERVIEW_NOTES.md`、Parts、`facts.json` 或验证脚本，因此对应校验不适用。

## Result

- 删除 `MODEL_PROVIDER_ALLOWLIST`、`MODEL_ALLOWLIST` Settings 字段、环境读取和启动校验；主模型目录只作为空注册中心 bootstrap。
- `ModelRegistryService` 统一判定 Provider 连接和模型是否启用，手动首选模型保存前与每次真实模型尝试前都会复验。
- LLM 无注册中心的独立运行模式以当前路由目录的启用模型为边界，不允许目录外请求覆盖。
- 会话工作区/默认工作区 PATCH 不再校验未修改的模型字段；直接修改 Provider/Model 仍必须命中动态注册中心。
- 回归测试覆盖前端式 Provider 配置、动态模型注册、手动选择、工作区切换、真实聊天适配器调用、未注册模型拒绝以及禁用模型/Provider 拒绝。
- 未新增数据库迁移，未修改真实 `.env` 或凭据；用户已明确授权提交并推送当前分支，未授权合并、部署或执行数据库迁移。
