# ENABLE-MEMORY-UX：启用本地记忆与改造记忆工作台

## Goal

在当前开发环境中正式启用 SQLite 本地 L0/L1/L3 记忆，并参考 TencentDB Agent MemoryPanel 的交互结构，提升记忆浏览、筛选、审核、详情查看和画像治理体验。

## In scope

- 将本地 SQLite 记忆 profile 安全合并到当前 `.env`，保留既有模型与非记忆配置。
- 审计 TencentDB MemoryPanel 的 Chat Memory 页面，仅借鉴信息架构和交互模式。
- 重构现有“项目记忆 / 我的画像 / 对话搜索”工作台的页头、统计、筛选、列表、详情和状态反馈。
- 保持现有 L0/L1/L3 API、权限、安全过滤和 L2 不实现边界。
- 更新必要文档与前端回归测试。

## Out of scope

- 复制 TencentDB 的品牌、文案、React/Tea 组件或后端协议。
- 引入 L2、团队资产分配、云端 MemoryHub 或新的前端框架。
- 提交、合并、发布、迁移或覆盖此前未提交工作。

## Acceptance criteria

- [x] 当前 `.env` 启用 SQLite Session/Run/Workspace/L1/L3 和 in-process queue。
- [x] 启动配置解析成功，项目记忆和用户记忆均为 review 模式。
- [x] 记忆工作台提供清晰的概览、状态统计、筛选、列表/详情和治理动作。
- [x] 用户能够理解 candidate/active/rejected/superseded 状态及下一步操作。
- [x] 项目记忆、用户画像、对话搜索在桌面与窄屏均可用，并具有键盘焦点和明确空/错/加载状态。
- [x] 不改变既有 API 兼容性和 L0/L1/L3 安全边界。
- [x] 前端语法、相关测试、全量 pytest、compileall、手册校验和 diff 检查通过。

## Decisions

- 仅借鉴 TencentDB MemoryPanel 的顶层作用域切换、资产列表—详情双栏、状态标签和请求防陈旧模式；保留本项目深色工程控制台视觉，不复制品牌、组件或后端协议。
- L1/L3 都以可治理原子事实为中心；L0 维持按需搜索；页面显式展示 L2 不存储及其权威事实源边界。
- 当前机器通过 gitignored `.env` 默认启用本地 SQLite 记忆，不要求进入页面再次开关；仓库仍保留安全的全局 off 默认，并提交可复制的 `.env.local-memory.example`。
- SQLite 会话属于持久化存储，健康接口必须返回 `persistent_sessions=true`，避免前端误报“重启会丢失”。
- 搜索、项目记忆和用户记忆列表用 generation token 丢弃旧响应，加载、空结果和失败状态分别呈现。

## Verification

- `.venv/bin/python -m pytest -q`：`412 passed, 49 subtests passed`。
- `.venv/bin/python -m pytest -q tests/test_config_resolver.py tests/test_api.py tests/test_local_memory.py`：`55 passed, 28 subtests passed`。
- `.venv/bin/python evals/run_memory_evals.py`：PASS，candidate precision `1.000`、Recall@6 `1.000`、cross-workspace leaks `0`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：验证 24 个 Markdown 文件和 38 项能力；仅输出既有 changed-evidence 复核提示。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- 本地启动配置解析：Session/Run/Workspace/L1 store/L1 vector 均为 `sqlite`，L1/L3 enabled 且 mode=`review`，queue=`in_process`。
- 真实浏览器冒烟：L1/L3/L0 切换、空状态、列表—详情布局、L3 手工创建后 active 状态/证据/画像快照、SQLite 持久化健康状态均通过，控制台无 error/warn。
- 响应式规则审计：1120/900/680px 断点依次折叠命令栏、双栏和筛选/统计网格；所有表单保留显式 label、按钮禁用态和键盘焦点。

## Result

已完成。当前开发环境启动后直接使用单文件 SQLite 记忆；前端提供 L1 项目记忆、L3 我的画像和 L0 对话搜索三页工作台，并具备可解释状态、筛选、详情治理、画像预览及错误/空/加载反馈。未引入 L2 或外部记忆服务；实现阶段未覆盖其他未提交工作，随后经用户授权随 local-memory feature branch 提交并推送，未合并或部署。
