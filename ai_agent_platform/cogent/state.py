from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any, Mapping


RUNTIME_ENGINE = "cogent-v1"
RUNTIME_STATE_VERSION = 1


@dataclass
class CogentState:
    started_at: float = 0.0
    visible_tool_count: int = 0
    tool_schema_tokens: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""
    prompt_version: str = ""
    permission_mode: str = "default"
    sandbox: dict[str, Any] = field(default_factory=dict)
    pending_calls: list[dict[str, Any]] = field(default_factory=list)
    deferred_user_messages: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, str]] = field(default_factory=list)
    consumed_approvals: list[str] = field(default_factory=list)
    completed_call_ids: list[str] = field(default_factory=list)
    all_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    all_tool_results: list[dict[str, Any]] = field(default_factory=list)
    compact_boundaries: list[dict[str, Any]] = field(default_factory=list)
    active_skill: str = ""
    recalled_memory: str = ""
    recovery_count: int = 0
    context_recovery_count: int = 0
    request_count: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    usage_anchor: dict[str, int] = field(default_factory=dict)
    compact_failures: int = 0
    retry_on_resume: bool = False
    response_ready: bool = False
    last_stop_reason: str = ""
    file_history_cursor: int = 0
    mcp_loading_mode: str = ''
    loaded_mcp_tools: list[str] = field(default_factory=list)
    tool_result_files: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return deepcopy({
            "started_at": self.started_at,
            "visible_tool_count": self.visible_tool_count,
            "tool_schema_tokens": self.tool_schema_tokens,
            "messages": self.messages,
            "system_prompt": self.system_prompt,
            "prompt_version": self.prompt_version,
            "permission_mode": self.permission_mode,
            "sandbox": self.sandbox,
            "pending_calls": self.pending_calls,
            "deferred_user_messages": self.deferred_user_messages,
            "approvals": self.approvals,
            "consumed_approvals": self.consumed_approvals,
            "completed_call_ids": self.completed_call_ids,
            "all_tool_calls": self.all_tool_calls,
            "all_tool_results": self.all_tool_results,
            "compact_boundaries": self.compact_boundaries,
            "active_skill": self.active_skill,
            "recalled_memory": self.recalled_memory,
            "recovery_count": self.recovery_count,
            "context_recovery_count": self.context_recovery_count,
            "request_count": self.request_count,
            "usage": self.usage,
            "usage_anchor": self.usage_anchor,
            "compact_failures": self.compact_failures,
            "retry_on_resume": self.retry_on_resume,
            "response_ready": self.response_ready,
            "last_stop_reason": self.last_stop_reason,
            "file_history_cursor": self.file_history_cursor,
            "mcp_loading_mode": self.mcp_loading_mode,
            "loaded_mcp_tools": self.loaded_mcp_tools,
            "tool_result_files": self.tool_result_files,
        })

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "CogentState":
        raw = deepcopy(dict(value or {}))
        return cls(
            started_at=float(raw.get("started_at") or 0),
            visible_tool_count=int(raw.get("visible_tool_count") or 0),
            tool_schema_tokens=int(raw.get("tool_schema_tokens") or 0),            messages=list(raw.get("messages") or []),
            system_prompt=str(raw.get("system_prompt") or ""),
            prompt_version=str(raw.get("prompt_version") or ""),
            permission_mode=str(raw.get("permission_mode") or "default"),
            sandbox=dict(raw.get("sandbox") or {}),
            pending_calls=list(raw.get("pending_calls") or []),
            deferred_user_messages=list(raw.get("deferred_user_messages") or []),
            approvals=list(raw.get("approvals") or []),
            consumed_approvals=list(raw.get("consumed_approvals") or []),
            completed_call_ids=list(raw.get("completed_call_ids") or []),
            all_tool_calls=list(raw.get("all_tool_calls") or []),
            all_tool_results=list(raw.get("all_tool_results") or []),
            compact_boundaries=list(raw.get("compact_boundaries") or []),
            active_skill=str(raw.get("active_skill") or ""),
            recalled_memory=str(raw.get("recalled_memory") or ""),
            recovery_count=int(raw.get("recovery_count") or 0),
            context_recovery_count=int(raw.get("context_recovery_count") or 0),
            request_count=int(raw.get("request_count") or 0),
            usage=dict(raw.get("usage") or {}),
            usage_anchor=dict(raw.get("usage_anchor") or {}),
            compact_failures=int(raw.get("compact_failures") or 0),
            retry_on_resume=bool(raw.get("retry_on_resume", False)),
            response_ready=bool(raw.get("response_ready", False)),
            last_stop_reason=str(raw.get("last_stop_reason") or ""),
            file_history_cursor=int(raw.get("file_history_cursor") or 0),
            mcp_loading_mode=str(raw.get("mcp_loading_mode") or ""),
            loaded_mcp_tools=list(raw.get("loaded_mcp_tools") or []),
            tool_result_files=dict(raw.get("tool_result_files") or {}),
        )
