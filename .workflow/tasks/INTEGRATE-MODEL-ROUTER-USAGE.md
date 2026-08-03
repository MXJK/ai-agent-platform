# INTEGRATE-MODEL-ROUTER-USAGE: 整合模型路由、统一用量治理与本机安全加固

## Goal

在独立整合分支中组合已验证的本机安全加固、统一模型用量治理和真正模型路由，
解决重叠实现并保持三者的验收语义与测试证据。

## In scope

- 保留安全加固分支的 loopback、凭据、Sandbox 和生命周期边界。
- 合入 `codex/unified-llm-usage-budget` 的 allowlist、精确 Token 预检、统一账本和预算。
- 让 ModelRouter 与预算授权、Provider usage 记录和 Chat SSE 正确组合。
- 解决配置、运行时装配、LLM、Chat、前端、文档和测试冲突。
- 在新 `codex/` 分支提交本地整合结果，但不推送、不建 PR、不部署。

## Out of scope

- 修改既有两个来源分支、发布、迁移数据库或调用真实付费模型。
- 集群共享熔断、在线价格同步或新的预算产品需求。

## Acceptance criteria

- [ ] 新分支同时包含三个来源能力，原来源分支不变。
- [ ] 路由候选必须通过 allowlist，预算降级模型必须重新通过能力与健康校验。
- [ ] 每个实际 Provider 尝试使用对应模型的精确输入计数和授权输出上限。
- [ ] 成功、partial failure 和首 delta 前 fallback 的 usage 不重复、不漏记。
- [ ] Chat meta/route/error 同时保留预算审计和路由 Trace。
- [ ] 安全加固配置和运行时清理没有被旧分支覆盖。
- [ ] 专项测试、全量 pytest、compileall、前端语法和文档校验通过。

## Decisions

- 使用新的整合分支，避免直接改写 `codex/harden-local-boundaries` 或
  `codex/unified-llm-usage-budget`。
- ModelRouter 决定候选顺序；每个候选在调用前进入 UsageLedger 授权。预算 downgrade
  不是无条件跳转，目标模型仍必须在目录中满足能力与健康要求。
- 首个文本 delta 仍是禁止自动重放的边界；usage 以实际 Provider 返回为准。

## Verification

## Result
