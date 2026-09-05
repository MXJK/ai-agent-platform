from dataclasses import dataclass


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    description: str
    usage: str
    aliases: tuple[str, ...] = ()


COMMANDS = (
    CommandDefinition('help', '列出 Cogent 共享命令', '/help'),
    CommandDefinition('status', '查看当前会话与执行状态', '/status'),
    CommandDefinition('clear', '建立空白的后续对话，不删除历史', '/clear'),
    CommandDefinition('compact', '压缩对话并保留近期工具配对', '/compact [保留重点]'),
    CommandDefinition('mcp', '查看当前工作区的 MCP 工具', '/mcp'),
    CommandDefinition('session', '查看当前会话信息', '/session'),
    CommandDefinition('skill', '列出或执行 inline Skill', '/skill [name] [arguments]', ('skills',)),
    CommandDefinition('tools', '查看当前运行的工具能力', '/tools'),
    CommandDefinition('permissions', '查看或设置 Cogent 权限模式', '/permissions [mode]'),
    CommandDefinition('resume', '继续暂停的执行或明确处理审批', '/resume [approve|reject]'),
    CommandDefinition('plan', '只读规划并仅写入当前计划文件', '/plan [任务]'),
    CommandDefinition('review', '只读审查当前 Git diff', '/review [重点]'),
    CommandDefinition('sandbox', '查看当前 OS sandbox 状态', '/sandbox'),
    CommandDefinition('memory', '查看独立的 Cogent 文件记忆', '/memory'),
    CommandDefinition('rewind', '预览并审批对话或文件回退', '/rewind [snapshot-id|run-id] [all|conversation|files]'),
)
LOCAL_COMMANDS = frozenset({'help', 'status', 'clear', 'compact', 'mcp', 'session', 'skill', 'tools', 'permissions', 'sandbox', 'memory', 'rewind'})


def resolve_command(name: str):
    return next((item for item in COMMANDS if name == item.name or name in item.aliases), None)


def command_capabilities():
    return [dict(name=item.name, description=item.description, usage=item.usage, aliases=list(item.aliases)) for item in COMMANDS]
