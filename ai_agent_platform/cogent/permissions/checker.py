from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ai_agent_platform.cogent.permissions.dangerous import DangerousCommandDetector, is_safe_command
from ai_agent_platform.cogent.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from ai_agent_platform.cogent.permissions.rules import RuleEngine, evaluate_rules, extract_content
from ai_agent_platform.cogent.permissions.sandbox import PathSandbox
from ai_agent_platform.cogent.tools.base import Tool

@dataclass(frozen=True)
class Decision:
    effect: DecisionEffect
    reason: str

class PermissionChecker:

    def __init__(self, detector: DangerousCommandDetector, sandbox: PathSandbox, rule_engine: RuleEngine, mode: PermissionMode=PermissionMode.DEFAULT, sandbox_enabled: bool=False) -> None:
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.mode = mode
        self.plan_file_path = ''
        self.sandbox_enabled = sandbox_enabled

    @staticmethod
    def describe_tool_action(tool_name: str, arguments: dict[str, Any]) -> str:
        return extract_content(tool_name, arguments) or tool_name

    def check(self, tool: Tool, arguments: dict[str, Any]) -> Decision:
        content = extract_content(tool.name, arguments)
        path = self._file_path(tool.name, arguments)
        if path is not None:
            allowed, reason = self.sandbox.check(path)
            if not allowed:
                return Decision('deny', reason)
            if tool.category == 'write':
                allowed, reason = self.sandbox.check_deny_write(path)
                if not allowed:
                    return Decision('deny', reason)
        if tool.category == 'command':
            dangerous, reason = self.detector.detect(content)
            if dangerous:
                return Decision('deny', reason)
        try:
            rule = evaluate_rules(self.rule_engine.snapshot(), tool.name, content)
        except (OSError, ValueError) as exc:
            return Decision('deny', f'Permission rules could not be loaded: {exc}')
        if rule == 'deny':
            return Decision('deny', 'Permission rule denies this operation')
        if self.mode == PermissionMode.PLAN:
            if tool.category == 'command':
                return Decision('deny', 'Plan mode does not permit shell execution')
            if tool.category == 'write' and (not (tool.name in {'WriteFile', 'EditFile'} and path is not None and self._is_plan_file(path))):
                return Decision('deny', 'Plan mode only permits writing the current plan file')
        if rule == 'ask':
            return Decision('ask', 'Permission rule requires confirmation')
        if tool.category == 'command' and self.mode == PermissionMode.BYPASS and (not self.sandbox_enabled):
            return Decision('ask', 'OS sandbox is unavailable; command confirmation is required')
        if rule == 'allow':
            return Decision('allow', 'Permission rule allows this operation')
        if self.mode == PermissionMode.PLAN:
            return Decision('allow', 'Operation is within the current plan boundary')
        if tool.category == 'command' and is_safe_command(content):
            return Decision('allow', 'Known read-only command')
        return Decision(mode_decide(self.mode, tool.category), f'Permission mode: {self.mode.value}')

    @staticmethod
    def _file_path(name: str, arguments: dict[str, Any]) -> str | None:
        if name in {'ReadFile', 'WriteFile', 'EditFile'}:
            return str(arguments.get('file_path') or '')
        if name in {'Glob', 'Grep', 'Bash'}:
            return str(arguments.get('cwd' if name == 'Bash' else 'path') or '.')
        return None

    def _is_plan_file(self, target_path: str) -> bool:
        if not self.plan_file_path:
            return False
        target = Path(target_path)
        if not target.is_absolute():
            target = self.sandbox.project_root / target
        return target.resolve() == Path(self.plan_file_path).resolve()
