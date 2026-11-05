# Agent Teams / Worktree 浏览器人工验收（2026-09-30）

## Scope and method

用户要求模拟真人点击测试新功能。本次用 ego-browser 在现有服务
`http://127.0.0.1:8000` 中点击新建会话、工作区、权限模式、发送、审批、取消、
Trace 筛选和事件展开；通过聊天触发真实工具，没有用直接 API 请求代替业务操作。
只检查源码定位原因，未修复实现，未提交、推送、部署或迁移。

- 代码 HEAD：`c28d7f8029b71917b51c31656504e18c1575be0e`。
- 会话：`sess_faa952297d4a`。
- Worktree 正常路径 Run：`run_59e50fb13987`。
- Teams A / B：`run_6f9828ee16f4` / `run_9b1009e99b5d`。
- 模型：网页已配置的 Deepseek V4 Flash。
- Worktree 测试使用逐次确认；Teams 测试使用接受文件编辑。提示词限制仅操作
  本次测试资源，队友只做只读检查或回复 QA-OK，禁止源码写入和 Git 提交。

## Verification matrix

| 项目 | 实测结果 |
| --- | --- |
| 非 Git 工作区创建 Worktree | 正确拒绝：`worktree isolation requires the registered Git repository root`；测试 Run 已点击取消 |
| Git 项目创建命名 Worktree | 通过，`ui-qa-wt-0930a` 创建成功 |
| 同名再次进入 | 通过，相同 path、branch、created_at，列表仍为 1 个 |
| 退出并删除干净 Worktree | 通过，`kept:false`，变更和新增提交均为 0 |
| 当前 Run 执行根 | 返回 `entered:false`，保持原冻结执行根，符合文档 |
| TeamCreate | 两次通过：`ui-qa-team-0930a`、`ui-qa-team-0930b` |
| 共享 TaskCreate / TaskList / TaskGet | 通过，任务 #1 创建后可列出、读取，状态 pending |
| Agent 启动团队队友 | 两次失败，显式传/不传 isolation 均为同一错误 |
| SendMessage | A 返回 `cannot resolve team recipient: reader`，是队友未注册的后果，不能据此断言邮箱本身有独立缺陷 |
| TeamDelete | 团队记录删除成功，但失败启动产生的孤立 Worktree 未回收 |
| 手动清理本次孤立 Worktree | 两次通过 ExitWorktree(remove, discard_changes=false) 清理，最终 WorktreeList count=0 |
| 审批、取消、刷新恢复、最近会话 | 通过；刷新后验收结果和错误仍可见 |
| Trace 搜索、错误筛选、展开原始事件 | 通过；B 的事件 #736263 直接确认工具错误 |

## Findings

### P1：Compose 的目录配置使团队队友无法启动

复现：选择已注册 ai-agent-platform → TeamCreate → Agent 指定 team_name 和
name=reader（是否指定 isolation=worktree 均可）。

预期：启动隔离队友并返回持久子任务。实际：

```json
{"ok":false,"name":"Agent","error":"collaboration worktree is not server-owned","error_code":"tool_execution_error","attempts":1,"duration_ms":192}
```

根因依据：`runtime.py:338-347` 将 execution runtime 的 worktree_parent 配置为
`change_set_worktree_parent`，却将 CollaborationManager 的 worktree_parent 配置为
execution runtime 的 `runtime_parent`。Compose 的两个值分别为
`/var/lib/ai-agent-platform/worktrees` 和 `/var/lib/ai-agent-platform/sandboxes`。
浏览器工具结果确认团队 Worktree 实际创建在后者。
`integrations/execution_workspace.py:418-419` 的 adopt_worktree 只接受前者的子目录，
因而拒绝接管。不是模型是否传 isolation 参数造成的错误。

现有 collaboration 测试多将两个父目录设为同一临时目录，不能覆盖这一真实装配差异。
建议后续修复统一父目录来源，并增加两个父目录不同的 bootstrap 集成回归。

### P2：启动失败后没有回滚 Worktree，TeamDelete 也漏清理

复现：上述 Agent 报错 → TeamDelete（工具实际传 discard_worktrees=true）→
WorktreeList。两次均剩余一个 `team-ui-qa-team-0930*/reader` Worktree，变更 0/0。

`collaboration.py:613-626` 先创建 Worktree 再接管，接管失败时尚未注册成员，
此处没有失败回滚；`team_delete` 只遍历已注册成员的 worktree_name，因此漏掉孤立资源。
本次已通过 ExitWorktree 且 discard_changes=false 清掉两份干净残留。
建议后续在子任务准备失败路径做资源回滚，并覆盖失败后的资源守恒。

### UI 观察（不作为独立阻断项）

- 没有独立 Teams/Worktree 面板；本轮通过聊天工具和 Trace 完成操作。
- 非 Git 工作区缺少执行前能力提示，要到审批后工具失败才知道不满足前提。
- 审批切换时短暂显示无具体工具参数的通用审批卡；本轮等待参数出现后才批准。
- WorktreeList 的实时摘要会显示 `[object Object]`；详情可从工具记录查看。
- 语义点击发送曾报告子元素拦截；截图中按钮可见，鼠标坐标点击成功，故未判为产品缺陷。

## Limits

由于队友无法启动，未通过真实运行验证消息往返、完成通知、共享任务 completed 更新、
运行中队友停止、多人协作、Plan 审批和子 Run 列表隔离。没有测试有改动/新增提交的
Worktree 保留，也没有执行服务重启恢复；同名复用和浏览器刷新不能替代这些测试。

## Automated checks

- 全量 `.venv/bin/python -m pytest -q`：879 passed、12 skipped、108 subtests passed、
  2 failed。一个是沙箱禁止本地 TCP bind 的 HTTP Hook 测试；另一个是既存
  `test_result_storage_never_follows_symlinked_parent` 异常类型契约不一致。
- HTTP Hook 单项在沙箱外复跑：1 passed。没有把该单项结果包装成完整全量通过。
- `.venv/bin/python -m compileall -q ai_agent_platform tests evals`：通过。
- `git diff --check`：通过。
- 本次只追加验收记录，不改变功能、API、配置或架构；README 和 Interview Notes
  不做装饰性修改，事实索引及其映射源码未改变，无需触发手册校验。

## Result

浏览器验收已执行，整体功能验收不通过：Worktree 基础生命周期通过，Teams 队友
启动在当前 Compose 配置中阻断，且失败时遗留资源。两次测试团队及其 Worktree
已清理，会话和 Trace 留作证据。实现代码没有修改。

优先修复目录装配和失败回滚，再重跑 Teams 消息、完成通知、任务更新、停止及清理链路。
原有全量 symlink 失败独立保留为 blocker。

## Repair follow-up（2026-09-30）

已在后续修复中统一协作 Worktree 与执行运行时的 canonical `worktree_parent`，并在
创建后接管失败时回滚干净 Worktree。新增装配回归和失败资源守恒回归均通过；协作、
启动和 Sandbox 相关测试合计 `42 passed`。全量为 `881 passed, 12 skipped,
108 subtests passed, 2 failed`；HTTP Hook 失败已在沙箱外单项复跑通过，唯一产品合同
失败仍是上述既存 ManagedFiles symlink 异常类型不一致。

Compose 当前未运行，而 `docker compose up` 会执行 Alembic migration；按仓库安全规则，
在用户显式授权迁移前没有启动服务，因此本报告原先列出的真实浏览器未覆盖项仍待复验。
