# Cogent 生命周期 Hook

Hook 是模型/工具循环中的扩展点。配置沿用 mewcode 的 `event`、`if`、`action`、`once`、`reject` 风格；引擎适配本项目的同步运行时、审批、Run 快照和工具结果配对。

## 启用

默认没有 Hook。把下面 JSON 保存到服务账号可读取的绝对路径，并设置服务端环境变量：

```bash
COGENT_HOOKS_PATH=/absolute/path/hooks.json
```

也支持 `AI_AGENT_PLATFORM_COGENT_HOOKS_PATH`，或服务端用户配置的 `process_security.cogent_hooks_path`。
Workspace 项目配置不能设置该字段。配置在 Runtime 创建时读取，更新后重启服务。
Docker 内填写容器可读的绝对路径，例如通过现有 `~/.cogent` 挂载使用 `/home/app/.cogent/hooks.json`。
Web 与 CLI 共用服务端配置；不会自动执行仓库中的 Hook 文件。

```json
{
  "hooks": [
    {
      "id": "verification-reminder",
      "event": "session_start",
      "once": true,
      "action": {"type": "prompt", "message": "修改代码后运行相关测试，并如实报告验证结果。"}
    },
    {
      "id": "protect-env",
      "event": "pre_tool_use",
      "if": "tool == WriteFile && args.file_path ~= \"*.env\"",
      "reject": true,
      "action": {"type": "prompt", "message": "此 Hook 不允许 WriteFile 写入 .env 文件。"}
    },
    {
      "id": "after-write",
      "event": "post_tool_use",
      "if": "tool == WriteFile",
      "action": {"type": "command", "command": "printf 'WriteFile finished: %s\\n' \"$FILE_PATH\"", "timeout": 5}
    }
  ]
}
```

顶层也可以直接使用数组。示例的 `.env` 匹配只演示单个工具规则，不是完整 Secret 防护；实际权限仍由平台与工具层执行。

## 事件

| 事件 | 实际触发位置 |
| --- | --- |
| `session_start` / `session_end` | 一次 Run 开始/终态；暂停和等待审批不触发结束 |
| `turn_start` / `turn_end` | 一次前台模型轮次开始/完整响应及工具批次处理完毕；出错中断不保证 turn_end |
| `pre_send` / `post_receive` | 前台模型请求前/完整响应持久化后；不逐 token 触发，也不包装内部记忆维护调用 |
| `pre_tool_use` | 工具批次的前置检查，可拒绝调用，先于审批及执行 |
| `post_tool_use` | 工具结果接收后，包括拒绝/错误结果；不意味着文件一定发生变化 |
| `permission_request` | 批次等待工具审批或 AskUserQuestion 等待输入前 |
| `compact` | 压缩尝试结束，是否改变上下文见消息中的 `changed` |
| `command_execute` | 用户 slash command 分派前；Bash 属于工具事件 |
| `error` | 模型 Provider 异常或 Run 标记失败；工具自身失败通过 post_tool_use 接收 |

session 事件对应 Run，不是数据库 Conversation 的创建/删除。工具后的 Hook 按结果接收顺序执行；并行读取的结果到达顺序不固定。恢复使用相同事件边界去重，不重新触发已完成动作。

## 条件与变量

条件字段支持 `tool`、`event`、`args.<参数名>`。工具名使用模型看到的名称，如 `ReadFile`、`WriteFile`、`Bash`。

- `==`、`!=`：字符串相等/不等。
- `~=`：glob，如 `args.file_path ~= "src/*.py"`。
- `=~`：正则，如 `tool =~ /^Write/`。
- 用 `&&` 或 `||` 连接；不支持混用、括号、嵌套表达式，表达式内的逻辑分隔符也不能作为字符串内容。

提示词和 HTTP body/headers 可以使用 `$EVENT`、`$TOOL_NAME`、`$FILE_PATH`、`$MESSAGE`、`$ERROR`、`$RUN_ID`、`$WORKSPACE_ROOT`、`$TOOL_ARGS.<参数名>`。
变量只替换一次，不把变量内容作为模板再次展开。`args` 只读取顶层工具参数。

`session_start` 的 MESSAGE 为用户输入；`post_receive` 为模型正文；`post_tool_use` 为结果 JSON，ERROR 为工具错误。其他事件按其语义传入通知或空字符串。

## 动作

### prompt

`{"type":"prompt","message":"..."}`。成功输出排队到下一次前台模型请求，附加到 system 内容，然后消费；不会插在 assistant/tool 配对中间，也不会带入下一次 Run。
同一次未完成模型请求的恢复保留已准备的提示词，不重复追加。Run 结束后的 prompt 仅留在执行结果中，没有后续模型请求可消费。

### command

`{"type":"command","command":"...","timeout":30}`。
命令以服务账号在 Run 的 `workspace_root` 执行，是管理员配置的宿主扩展，**不经过 Agent 工具审批、执行沙箱或 ChangeSet**。即使 Agent 使用 plan 模式，管理员 Hook 命令也会执行，因此只配置受信脚本；Hook 不能授予被拒绝的 Agent 工具权限。

上下文通过 JSON stdin 和 `COGENT_HOOK_CONTEXT` 环境变量提供，包含 event_name、run_id、workspace_root、tool_name、tool_args、file_path、message、error、tool_result。
命令模板中的变量转为环境变量引用，不把参数值拼进 shell 源码。示例使用双引号包住引用；若脚本自己使用 `eval` 或再次构造 shell，则自行承担其解释语义。

超时终止整个命令进程组，正常退出也清理背景子进程。不支持长期后台服务。
非零退出、超时和持续超过 1 MB 的输出记作失败；Trace/执行结果最多保留 8,000 字符。

### http

```json
{"id":"notify","event":"session_end","action":{"type":"http","url":"https://hooks.example.test/cogent","method":"POST","timeout":10}}
```

地址由管理员固定配置，只支持 HTTP(S)，不支持 URL 模板、URL 中凭据和重定向。
方法支持 GET/POST/PUT/PATCH/DELETE。默认 JSON body 为完整上下文；可用 `body` 字符串和 `headers` 自定义。自定义 JSON body 的变量替换不自动做 JSON 转义，复杂数据优先使用默认上下文。
`timeout` 用于网络连接/读取超时；GET 不发送 body。响应最多读取 8,000 字节。不内置重试。

## 拒绝、故障与恢复

- 按配置顺序执行。`once:true` 是每 Run 一次尝试，包括失败；不同 Run 状态独立。
- `reject:true` 仅允许 `pre_tool_use`。只要条件命中就拒绝，即使动作失败；不是根据命令退出码决定是否拒绝。第一个拒绝 Hook 截止前置链，结果用 `hook_rejected` 回给模型。
- 普通观察 Hook 失败记录为 failed，继续主流程。执行记录通过 `hook_executed` 进入 Run 事件 API/SSE 和 Trace，包含 Hook ID、生命周期事件、边界、动作类型、状态和截断输出。
- 执行前保存 started、执行后保存结果。恢复遇到 started 但无结果时记录 uncertain，并且不自动重做外部动作；拒绝型 Hook 仍拒绝。此设计避免自动重复副作用，不提供外部 exactly-once 保证。
- Hook 状态存入既有 v1 runtime_state 的可选 `hook_state` 字段，无需数据库迁移。配置仅保存指纹，不复制命令、HTTP headers 等完整配置到 Run。
- 配置在 Runtime 实例内固定。若重启后配置指纹变了，未完成旧 Run 不可继续执行；恢复原配置，或创建新 Run。修改或删除 Hook 都不能绕过该检查。
- 当前动作均同步；`async:true`、`agent`、`startup`、`shutdown`、`file_change` 明确拒绝配置。没有自动安装 Hook、动态 Agent 脚本生成、分布式队列或 UI 配置管理。

源码来源和改造记录见 [SOURCE.md](../ai_agent_platform/cogent/hooks/SOURCE.md)。测试见 [test_cogent_hooks.py](../tests/test_cogent_hooks.py)。
