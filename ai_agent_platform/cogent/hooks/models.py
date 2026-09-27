"""Hook contracts adapted from mewcode-python (see SOURCE.md)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json
import re
from typing import Any

from .conditions import ConditionGroup

VARIABLE = re.compile(r"\$(TOOL_ARGS\.[\w.-]+|TOOL_NAME|FILE_PATH|WORKSPACE_ROOT|RUN_ID|EVENT|MESSAGE|ERROR)(?!\w)")


@dataclass(frozen=True)
class Action:
    type: str
    command: str = ""
    message: str = ""
    url: str = ""
    method: str = "POST"
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: int = 30


@dataclass(frozen=True)
class Hook:
    id: str
    event: str
    action: Action
    condition: ConditionGroup | None = None
    reject: bool = False
    once: bool = False


@dataclass
class ActionResult:
    output: str = ""
    success: bool = True


@dataclass
class HookContext:
    event_name: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    file_path: str = ""
    message: str = ""
    error: str = ""
    run_id: str = ""
    workspace_root: str = ""
    tool_result: dict[str, Any] = field(default_factory=dict)

    def get_field(self, name: str) -> str:
        if name == "tool":
            return self.tool_name
        if name == "event":
            return self.event_name
        value = self.tool_args.get(name[5:], "") if name.startswith("args.") else ""
        return "" if value is None else str(value)

    def variable(self, name: str) -> str:
        if name.startswith("TOOL_ARGS."):
            return self.get_field("args." + name[10:])
        return str({"EVENT": self.event_name, "TOOL_NAME": self.tool_name,
                    "FILE_PATH": self.file_path, "MESSAGE": self.message,
                    "ERROR": self.error, "RUN_ID": self.run_id,
                    "WORKSPACE_ROOT": self.workspace_root}.get(name, ""))

    def expand(self, template: str) -> str:
        # A single pass prevents data containing $VARIABLE from expanding again.
        return VARIABLE.sub(lambda match: self.variable(match[1]), template)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)
