"""Fallback formatting for failed coding-agent runs."""

from __future__ import annotations

from ai_agent_platform.agents.coding.models import CODING_AGENT_ROLE, CodingAgentState


def format_error_answer(state: CodingAgentState) -> str:
    errors = [
        error
        for error in state.get("errors", [])
        if not error.get("recovered", False)
    ] or state.get("errors", [])
    lines = [
        f"我是{CODING_AGENT_ROLE}。",
        "本轮代码 Agent 运行失败。",
        f"工作区：`{state['workspace_id']}`。",
    ]
    for index, error in enumerate(errors, start=1):
        lines.append(
            f"[{index}] node={error.get('node')} code={error.get('code')}: "
            f"{error.get('message')}"
        )
    return "\n".join(lines)
