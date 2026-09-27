"""Run-local durable Hook dispatch adapted from mewcode's matching semantics."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from typing import Callable

from .executors import OUTPUT_LIMIT, execute_action
from .models import ActionResult, Hook, HookContext


class HookEngine:
    def __init__(self, hooks: list[Hook] | None = None):
        self.hooks = tuple(deepcopy(hooks or []))
        self.events = frozenset(hook.event for hook in self.hooks)
        self.fingerprint = hashlib.sha256(json.dumps(
            [asdict(hook) for hook in self.hooks], sort_keys=True).encode()).hexdigest()

    def initial_state(self) -> dict:
        return {"config_hash": self.fingerprint, "executions": {}, "prompts": []}

    def validate_state(self, state: dict) -> None:
        if state.get("config_hash", self.fingerprint) != self.fingerprint:
            raise ValueError("Hook configuration changed; restore the original configuration or start a new Run")

    def run(self, ctx: HookContext, boundary: str, state: dict,
            checkpoint: Callable[[], None], notify: Callable[[dict], None]) -> str | None:
        self.validate_state(state)
        if not self.hooks:
            return None
        state.setdefault("config_hash", self.fingerprint)
        entries = state.setdefault("executions", {})
        for hook in self.hooks:
            if hook.event != ctx.event_name or (hook.condition and not hook.condition.evaluate(ctx)):
                continue
            key = json.dumps([ctx.event_name, boundary, hook.id])
            prior = entries.get(key)
            if prior is None and hook.once and any(item["hook_id"] == hook.id for item in entries.values()):
                continue
            if prior is not None and prior["status"] != "started":
                notify({"hook_id": hook.id, "event": ctx.event_name, "boundary": boundary,
                        "action": hook.action.type, "status": prior["status"],
                        "success": prior["success"], "output": prior["output"], "rejected": hook.reject})
                if hook.reject:
                    return prior["output"] or f"Rejected by hook {hook.id}"
                continue
            if prior is None:
                entries[key] = {"hook_id": hook.id, "status": "started"}
                checkpoint()  # Never start an external action without a durable claim.
                try:
                    result = execute_action(hook.action, ctx)
                except Exception as exc:
                    # Exception messages may contain HTTP credentials or command data.
                    result = ActionResult(f"Hook action failed ({type(exc).__name__})", False)
                status = "completed" if result.success else "failed"
            else:
                result = ActionResult("Previous hook execution has no durable result; not replayed", False)
                status = "uncertain"
            output = result.output[:OUTPUT_LIMIT]
            entries[key] = {"hook_id": hook.id, "status": status,
                            "output": output, "success": result.success}
            if hook.action.type == "prompt" and result.success and not hook.reject:
                state.setdefault("prompts", []).append(output)
            checkpoint()
            notify({"hook_id": hook.id, "event": ctx.event_name, "boundary": boundary,
                    "action": hook.action.type, "status": status,
                    "success": result.success, "output": output, "rejected": hook.reject})
            if hook.reject:
                return output or f"Rejected by hook {hook.id}"
        return None
