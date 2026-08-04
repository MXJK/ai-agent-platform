# AUTO-DISCOVER-MODELS: Provider 模型发现与简化注册

## Goal

把模型注册从手工填写路由器内部参数改为 Provider 驱动的选择流程：保存凭据后由后端
发现当前账号可用的文本生成模型，用户只需选择模型并确认，质量、价格、能力和冷启动
延迟由后端元数据策略维护，真实延迟由被动观测自动反馈给路由器。

## In scope

- 为 OpenAI、DeepSeek、Anthropic 和 Google 增加实时模型发现适配与统一 API。
- 过滤明显不适用于 Chat/Agent 的嵌入、音频、图像等模型，并标记已注册项。
- 用 Provider 返回信息与后端模型画像生成显示名称、上下文、能力、质量、价格和冷启动
  延迟，不接受前端直接提供这些内部路由参数。
- 路由器在存在成功请求样本时使用实测总延迟 P50，冷启动时才使用后端先验。
- 将前端注册表单改为 Provider、发现模型选择、启用和自动选择；保留模型 ID 手动兜底。
- 同步 API/用户行为文档、Interview Notes 和 facts 证据。

## Out of scope

- 定时联网同步模型或价格、定时付费质量评测、部署、发布或真实生成请求。
- 自定义 Provider Base URL、多账号 Provider 连接或第三方兼容 API。
- 为所有未来模型承诺精确价格或质量；未知模型使用明确的保守后端先验。

## Acceptance criteria

- [x] 保存有效 Provider 凭据后，可通过后端发现该账号当前可用的文本生成模型。
- [x] 发现失败返回可理解且不泄露凭据的错误，未配置/停用连接不会发起外部请求。
- [x] 注册请求只需要 Provider、模型 ID 和启用偏好，模型显示名及路由元数据由后端生成。
- [x] 前端不再要求填写显示名称、上下文、质量分、预估延迟或输入/输出价格。
- [x] 已注册模型不会重复注册，仍可通过手动模型 ID 兜底。
- [x] 自动路由在有成功观测后使用总延迟 P50，状态页面说明元数据来源。
- [x] 四种 Provider 的发现解析、过滤、注册默认值、API 边界和前端关键流程有测试覆盖。
- [x] README、Interview Notes、facts 与实现保持一致，仓库规定验证全部通过。

## Decisions

- 只访问代码中固定的 OpenAI、DeepSeek、Anthropic 和 Gemini 官方模型列表端点；
  不接受前端 Base URL，API Key 只进入对应鉴权 Header，错误不返回响应正文或凭据。
- OpenAI-compatible 目录按 Chat/Reasoning 模型家族纳入，Gemini 要求支持
  `generateContent`；统一排除 embedding、音频、图像、实时流和 TTS 等当前运行时
  无法调用的模型。
- Provider 能提供的显示名、上下文和能力优先；缺失字段由确定性的 Provider/模型
  家族画像补齐。质量和成本是路由先验，不声称是在线评测或官方实时报价。
- 新模型在没有样本时使用后端冷启动延迟；每次成功调用更新被动样本后立即刷新本
  进程运行时目录，后续 `latency`/`smart` 排序使用总延迟 P50。
- 沿用现有 `registered_models` 数值字段，不增加迁移；前端创建 API 缩减为 Provider、
  模型 ID、启用和自动候选四项，更新 API 只维护两个状态开关。

## Verification

- `.venv/bin/python -m pytest -q`：217 passed，另有 4 个 subtests；仅有既有
  Starlette/httpx 弃用警告。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：12 个 Markdown、28 项能力通过；
  evidence review warnings 来自工作区相对既有 `last_verified_commit` 的未提交证据
  差异，不是校验失败。
- `node --check ai_agent_platform/static/app.js`：通过。
- `git diff --check`：通过。
- 本地 fake/memory 服务的浏览器 QA：桌面模型管理页、手动模型 ID 展开入口通过；
  600×800 视口注册字段为单列，`scrollWidth=viewportWidth=600`，控制台无错误。
- 未向四家 Provider 发起真实发现或生成调用；协议解析使用确定性模拟响应测试。

## Result

- 新增统一 Provider 模型发现层，按官方目录协议规范化四家返回、过滤非文本生成模型、
  标记已注册项，并用安全错误边界处理鉴权、限流、网络和无效响应。
- 模型管理页改成“保存 Provider → 自动发现 → 选择模型 → 注册”；移除显示名、上下文、
  工具能力、质量、价格和延迟输入，保留列表缺项时的模型 ID 手动兜底。
- 后端注册服务独占路由元数据生成：Provider 元数据与模型家族画像组成冷启动配置；
  成功请求产生后，实测总延迟 P50 自动进入运行时路由和前端状态来源标签。
- 已注册模型卡改为直接启用/停用、加入/退出自动路由和删除，不再复用底层参数编辑
  表单。README、Interview Notes、facts 与测试已同步。
- 没有新增数据库迁移、部署、提交、推送或真实付费 Provider 调用。
