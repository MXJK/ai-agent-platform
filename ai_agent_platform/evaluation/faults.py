"""Deterministic tool-fault injection for the failure-recovery metric.

The metric asks what the loop does after a tool genuinely fails: does it change
target, or keep re-issuing the call that already failed? Answering that needs a
real failed ``ToolResult`` travelling the real execution path, so the fault is
produced by the registry itself rather than simulated afterwards.

This is a test and evaluation affordance. In the running app it is installed
only when ``EVAL_FAULT_INJECTION_ENABLED`` is on; otherwise the plain registry
is used and the metric reports that it was never triggered.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

from ai_agent_platform.integrations.tools import (
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)


INJECTED_FAULT_CODE = "injected_fault"
INJECTED_FAULT_MESSAGE = "injected fault: the tool is unavailable for this call"


class ToolFaultController:
    """Arms one deterministic tool failure for the case currently running.

    A fault is always scoped to the workspace the eval created. The controller
    is process-wide, so without that scope a user's own run could be hit by an
    injected failure while an eval happened to be in flight.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._tool_name = ""
        self._workspace_id = ""
        self._remaining = 0
        self._triggered = 0

    def arm(
        self,
        tool_name: str,
        *,
        workspace_id: str,
        occurrences: int = 1,
    ) -> None:
        if not workspace_id:
            raise ValueError("a tool fault must be scoped to a workspace")
        with self._lock:
            self._tool_name = tool_name
            self._workspace_id = workspace_id
            self._remaining = max(0, occurrences)
            self._triggered = 0

    def disarm(self) -> None:
        with self._lock:
            self._tool_name = ""
            self._workspace_id = ""
            self._remaining = 0
            self._triggered = 0

    @property
    def armed(self) -> bool:
        with self._lock:
            return bool(self._tool_name) and self._remaining > 0

    @property
    def triggered(self) -> int:
        with self._lock:
            return self._triggered

    def consume(self, tool_name: str, workspace_id: str) -> bool:
        with self._lock:
            if (
                tool_name != self._tool_name
                or workspace_id != self._workspace_id
                or self._remaining <= 0
            ):
                return False
            self._remaining -= 1
            self._triggered += 1
            return True


class FaultInjectingToolRegistry(ToolRegistry):
    """A registry that fails armed calls and delegates everything else.

    This subclasses rather than wraps on purpose. ``ToolRegistryView`` re-runs
    ``execute`` against its source registry, so a delegating proxy would be
    bypassed the moment the runtime froze a per-run tool selection. Adopting the
    built registry's state keeps one set of tools, locks and cleanup callbacks.
    """

    def __init__(self, source: ToolRegistry, controller: ToolFaultController) -> None:
        self.__dict__.update(source.__dict__)
        self._fault_controller = controller

    @property
    def fault_controller(self) -> ToolFaultController:
        return self._fault_controller

    def execute(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        workspace_id = str(getattr(context, "workspace_id", "") or "")
        if not self._fault_controller.consume(tool_call.name, workspace_id):
            return super().execute(tool_call, context=context)
        spec = self.get_spec(tool_call.name)
        return ToolResult(
            call_id=tool_call.call_id,
            name=tool_call.name,
            ok=False,
            error=INJECTED_FAULT_MESSAGE,
            error_code=INJECTED_FAULT_CODE,
            provider=spec.provider if spec is not None else "local",
            permission_level=(
                spec.permission_level if spec is not None else "read_only"
            ),
            arguments_summary=_summarized(tool_call.arguments),
        )


def _summarized(arguments: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in arguments.items()}
