"""Strict, process-owned Hook configuration; no automatic repository discovery."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from .conditions import ConditionParseError, parse_condition
from .models import Action, Hook

EVENTS = frozenset({"session_start", "session_end", "turn_start", "turn_end",
                    "pre_send", "post_receive", "pre_tool_use", "post_tool_use",
                    "error", "compact", "permission_request", "command_execute"})


class HookConfigError(ValueError):
    pass


def load_hooks(raw: list | None) -> list[Hook]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HookConfigError("hooks must be a list")
    hooks, ids = [], set()
    for index, entry in enumerate(raw):
        label = f"hook #{index + 1}"
        if not isinstance(entry, dict):
            raise HookConfigError(f"{label}: must be a mapping")
        if set(entry) - {"id", "event", "action", "if", "reject", "once", "async"}:
            raise HookConfigError(f"{label}: unknown fields")
        event = entry.get("event")
        if not isinstance(event, str) or event not in EVENTS:
            raise HookConfigError(f"{label}: unsupported event")
        hook_id = entry.get("id", f"{event}_{index}")
        if not isinstance(hook_id, str) or not hook_id.strip() or hook_id in ids:
            raise HookConfigError(f"{label}: id must be nonempty and unique")
        ids.add(hook_id)
        for flag in ("reject", "once", "async"):
            if type(entry.get(flag, False)) is not bool:
                raise HookConfigError(f"{label}: {flag} must be boolean")
        if entry.get("async"):
            raise HookConfigError(f"{label}: async hooks are unsupported by the durable runtime")
        if entry.get("reject") and event != "pre_tool_use":
            raise HookConfigError(f"{label}: reject requires pre_tool_use")
        action = entry.get("action")
        if not isinstance(action, dict):
            raise HookConfigError(f"{label}: action must be a mapping")
        required = {"command": "command", "prompt": "message", "http": "url"}
        kind = action.get("type")
        if not isinstance(kind, str) or kind not in required:
            raise HookConfigError(f"{label}: unsupported action type")
        allowed = {"type", "timeout", required[kind]}
        if kind == "http":
            allowed |= {"method", "body", "headers"}
        if set(action) - allowed:
            raise HookConfigError(f"{label}: unknown action fields")
        for key, value in action.items():
            if key not in {"timeout", "headers"} and not isinstance(value, str):
                raise HookConfigError(f"{label}: {key} must be a string")
        if not action.get(required[kind], "").strip():
            raise HookConfigError(f"{label}: missing {required[kind]}")
        timeout = action.get("timeout", 30)
        if type(timeout) is not int or not 1 <= timeout <= 300:
            raise HookConfigError(f"{label}: timeout must be an integer from 1 to 300")
        headers = action.get("headers", {})
        if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()):
            raise HookConfigError(f"{label}: headers must map strings to strings")
        if kind == "http":
            url = urlsplit(action["url"])
            if url.scheme not in {"http", "https"} or not url.hostname or "$" in action["url"] or url.username or url.password:
                raise HookConfigError(f"{label}: http requires a fixed http(s) URL without credentials")
            if action.get("method", "POST") not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                raise HookConfigError(f"{label}: unsupported HTTP method")
        condition = entry.get("if", "")
        if not isinstance(condition, str):
            raise HookConfigError(f"{label}: if must be a string")
        try:
            parsed = parse_condition(condition)
        except ConditionParseError as exc:
            raise HookConfigError(f"{label}: {exc}") from exc
        hooks.append(Hook(hook_id, event, Action(**action), parsed,
                          entry.get("reject", False), entry.get("once", False)))
    return hooks


def load_hooks_file(path: str) -> list[Hook]:
    if not path:
        return []
    source = Path(path).expanduser()
    if not source.is_absolute():
        raise HookConfigError("cogent_hooks_path must be absolute")
    try:
        with source.open(encoding="utf-8") as stream:
            raw = json.loads(stream.read(1_000_001))
        if isinstance(raw, dict) and set(raw) == {"hooks"}:
            raw = raw["hooks"]
        return load_hooks(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HookConfigError("Cannot read Cogent hooks configuration") from exc
