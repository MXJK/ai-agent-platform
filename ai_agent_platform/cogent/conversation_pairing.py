from __future__ import annotations
from dataclasses import replace
from ai_agent_platform.cogent.conversation import Message, ToolResultBlock
INTERRUPTED_TOOL_RESULT = 'Tool execution was interrupted. The tool may or may not have completed; verify before relying on its effects.'
REJECTED_TOOL_RESULT = 'The user rejected this tool use. Nothing was changed (for file edits, the new content was NOT written).'

def ensure_tool_pairing(messages: list[Message]) -> list[Message]:
    resolved: set[str] = set()
    issued: set[str] = set()
    for m in messages:
        for tr in m.tool_results or []:
            resolved.add(tr.tool_use_id)
        for tu in m.tool_uses or []:
            issued.add(tu.tool_use_id)
    out: list[Message] = []
    for m in messages:
        current = m
        if m.tool_results:
            kept = [tr for tr in m.tool_results if tr.tool_use_id in issued]
            if not kept and (not m.content) and (not m.tool_uses):
                continue
            current = replace(m, tool_results=kept)
        out.append(current)
        missing = []
        for tu in m.tool_uses or []:
            if tu.tool_use_id in resolved:
                continue
            missing.append(ToolResultBlock(tool_use_id=tu.tool_use_id, content=INTERRUPTED_TOOL_RESULT, is_error=True))
            resolved.add(tu.tool_use_id)
        if missing:
            out.append(Message(role='user', content='', tool_results=missing))
    return out
