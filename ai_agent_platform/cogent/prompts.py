from __future__ import annotations
from datetime import date
import json
from pathlib import Path
from typing import Iterable
from ai_agent_platform.domain import RunContextSnapshot
from ai_agent_platform.integrations.tools import ToolSpec
PROMPT_VERSION = 'cogent-system-v1'

def build_system_prompt(*, snapshot: RunContextSnapshot | None, workspace_root: str, permission_mode: str, sandbox_status: str, tools: Iterable[ToolSpec], memory: str='', active_skill: str='') -> str:
    tool_rows = [{'name': item.name, 'description': item.description, 'permission': item.permission_level} for item in tools]
    instructions = []
    if snapshot is not None:
        instructions = [f'[{item.kind}] {item.path}\n{item.text}' for item in snapshot.instructions.sources if item.text.strip()]
    sections = ['You are Cogent, a coding agent that completes user requests inside an authorized workspace.', 'Work until the requested outcome is complete. Inspect before editing, preserve unrelated changes, use the available tools for repository facts, and validate material changes.', 'Tool calls are capabilities, not suggestions. Follow the permission result exactly. Never claim a write or command succeeded unless its tool result confirms it.', 'Do not reveal hidden reasoning, protocol fields, signatures, encrypted content, secrets, or complete environment-variable values. You may provide concise user-visible reasoning summaries only when the selected provider explicitly supplies them for display.', 'When no tool call is needed, answer directly and clearly. When tools are needed, keep every tool call paired with its result before continuing.', '# Environment\n' + json.dumps({'date': date.today().isoformat(), 'workspace': str(Path(workspace_root)), 'permission_mode': permission_mode, 'sandbox': sandbox_status}, ensure_ascii=False, indent=2), '# Tools\n' + json.dumps(tool_rows, ensure_ascii=False, indent=2)]
    if instructions:
        sections.append('# Workspace instructions\n' + '\n\n'.join(instructions))
    if active_skill.strip():
        sections.append('# Active Skill\n' + active_skill.strip())
    if memory.strip():
        sections.append('# Recalled memory\n' + memory.strip())
    return '\n\n'.join(sections)
COMPACTION_PROMPT = 'Create a structured continuation summary of the earlier conversation.\nReturn plain text with these headings: User goal, Constraints, Completed work, Tool evidence,\nFiles and state, Open work, Next action. Preserve concrete paths, commands, decisions, errors,\nand unresolved questions. Do not include hidden reasoning, private protocol data, secrets, or\nspeculation. Do not call tools.'
