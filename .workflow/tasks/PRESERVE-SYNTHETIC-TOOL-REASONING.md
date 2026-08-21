# PRESERVE-SYNTHETIC-TOOL-REASONING: 修复合成工具历史的 Provider 兼容性

## Goal

修复代码 Agent 在收集变更产物后继续调用思考模型时的 Provider 协议错误，并确认其他
已支持 Provider 不存在同类的合成工具历史缺口。

## In scope

- 审计 OpenAI、DeepSeek、Anthropic、Google 的原生工具消息转换。
- 在 Provider 适配边界补齐运行时合成 assistant/tool-call 历史所需字段。
- 保留并安全呈现 Provider 返回的非敏感错误原因，避免只显示 HTTP 状态码。
- 添加小型、确定性的协议与 Agent 恢复回归测试。
- 同步受影响的 README、面试手册与证据映射。

## Out of scope

- 修改模型注册、凭据、路由策略或模型能力配置。
- 修改测试工作区中的五子棋产物。
- 发布、部署、迁移或提交代码。

## Acceptance criteria

- [x] DeepSeek 思考模式能够接受运行时合成的 artifact tool-call 历史。
- [x] OpenAI、Anthropic、Google 的同类转换边界有明确测试，且不引入 Provider 专属字段污染。
- [x] HTTP Provider 错误保留可诊断的安全错误信息，不泄露凭据或请求正文。
- [x] 聚焦测试、全量测试、compileall、文档校验与 diff 检查通过。

## Decisions

- 修复位于 DeepSeek Provider 消息转换边界：本地合成的 assistant tool-call 轮次补空
  `reasoning_content`，真实 DeepSeek `provider_items` 继续原样重放，避免把 Provider
  协议字段泄漏到通用 Agent 状态机。
- OpenAI 的 Responses function-call input、Anthropic 的 Messages tool-use/result 和
  Google 的非原生历史文本降级都是当前合法边界；不为这些 Provider 添加伪造的推理项
  或签名，只用跨 Provider 回归测试锁定行为。
- Provider JSON error message 只提取单个字符串，经过常见凭据模式脱敏、空白归一化和
  400 字符上限后进入异常；不保留响应其他字段、请求正文或请求头。
- README、README.en 与被 `.gitignore` 排除但由项目工作流维护的面试手册 Parts/facts
  同步记录这条协议边界。

## Verification

- `.venv/bin/python -m pytest -q tests/test_native_tool_calling.py`：通过，`20 passed`。
- DeepSeek、四 Provider 合成历史与安全错误详情的首轮聚焦测试：通过，`3 passed`。
- 安全错误详情脱敏用例修改后的单测：通过，`1 passed`。
- `.venv/bin/python -m pytest -q`：通过，`453 passed, 52 subtests passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`：通过。
- `.venv/bin/python INTERVIEW_NOTES/validate.py`：通过，验证 24 个 Markdown 文件和
  39 项 capability；evidence review warnings 是工作树现有证据变化提示。
- `git diff --check`：通过。

## Result

DeepSeek artifact 工具历史在下一次 `plan_tools` 调用前会携带空
`reasoning_content`，与实测 Provider 200 对照和官方 thinking-mode 约束一致。新增
确定性测试同时锁定 OpenAI、Anthropic、Google 的合法合成/降级行为，并覆盖一次完整的
变更审批、写入、验证、artifact 收集和 DeepSeek 历史重放路径。

Provider HTTP JSON 错误现在能返回有界、脱敏的真实原因，因此同类失败不再只显示状态码。
未修改模型凭据、注册目录、测试工作区或部署状态；未创建 commit。
