from __future__ import annotations
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal
import yaml
Effect = Literal['allow', 'deny', 'ask']
_RULE_RE = re.compile('^(\\w+)\\((.+)\\)$')
_CONTENT_FIELDS: dict[str, str] = {'Bash': 'command', 'ReadFile': 'file_path', 'WriteFile': 'file_path', 'EditFile': 'file_path', 'Glob': 'pattern', 'Grep': 'pattern'}

@dataclass(frozen=True)
class Rule:
    tool_name: str
    pattern: str
    effect: Effect

    def matches(self, tool_name: str, content: str) -> bool:
        if self.tool_name != tool_name:
            return False
        return fnmatch(content, self.pattern)

def parse_rule(raw: str, effect: Effect) -> Rule:
    m = _RULE_RE.match(raw.strip())
    if not m:
        raise ValueError(f'无效的规则语法: {raw}')
    return Rule(tool_name=m.group(1), pattern=m.group(2), effect=effect)

def extract_content(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == 'mcp_call':
        return f"{str(arguments.get('server', '')).strip()}__{str(arguments.get('tool', '')).strip()}"
    field = _CONTENT_FIELDS.get(tool_name)
    if field is None:
        return ''
    return str(arguments.get(field, ''))

def _load_rules_file(path: Path) -> list[Rule]:
    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return []
    except yaml.YAMLError as exc:
        raise ValueError(f'Invalid permission rules: {path.name}') from exc
    if not isinstance(raw, list):
        raise ValueError(f'Permission rules must be a list: {path.name}')
    rules: list[Rule] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(f'Invalid permission entry: {path.name}')
        rule_str = entry.get('rule', '')
        effect = entry.get('effect', '')
        if effect not in ('allow', 'deny', 'ask'):
            raise ValueError(f'Invalid permission effect: {path.name}')
        try:
            rules.append(parse_rule(rule_str, effect))
        except ValueError as exc:
            raise ValueError(f'Invalid permission rule: {path.name}') from exc
    return rules

class RuleEngine:

    def __init__(self, user_rules_path: Path | None=None, project_rules_path: Path | None=None, local_rules_path: Path | None=None) -> None:
        self._user_path = user_rules_path
        self._project_path = project_rules_path
        self._local_path = local_rules_path
        self._cache: dict[Path, tuple[tuple[int, int], list[Rule]]] = {}

    def _rules_for(self, path: Path | None) -> list[Rule]:
        if path is None:
            return []
        try:
            st = path.stat()
        except FileNotFoundError:
            self._cache.pop(path, None)
            return []
        stamp = (st.st_mtime_ns, st.st_size)
        cached = self._cache.get(path)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        rules = _load_rules_file(path)
        self._cache[path] = (stamp, rules)
        return rules

    def snapshot(self) -> list[Rule]:
        rules: list[Rule] = []
        for p in (self._user_path, self._project_path, self._local_path):
            rules.extend(self._rules_for(p))
        return rules

    def evaluate(self, tool_name: str, content: str) -> Effect | None:
        return evaluate_rules(self.snapshot(), tool_name, content)

    def append_local_rule(self, rule: Rule) -> None:
        if self._local_path is None:
            return
        self._local_path.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_rules_file(self._local_path)
        existing.append(rule)
        entries = [{'rule': f'{r.tool_name}({r.pattern})', 'effect': r.effect} for r in existing]
        self._local_path.write_text(yaml.dump(entries, allow_unicode=True), encoding='utf-8')

def evaluate_rules(rules: list[Rule], tool_name: str, content: str) -> Effect | None:
    hit: Effect | None = None
    for rule in rules:
        if not rule.matches(tool_name, content):
            continue
        if rule.effect == 'deny':
            return 'deny'
        if rule.effect == 'ask':
            hit = 'ask'
        elif hit is None:
            hit = 'allow'
    return hit
