from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentDefinition:
    agent_type: str
    when_to_use: str
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    model: str = "inherit"
    max_turns: int = 200
    permission_mode: str = "default"
    background: bool = False
    isolation: str = ""
    file_path: Path | None = None
    source: str = "builtin"
