from __future__ import annotations

from dataclasses import dataclass, replace
import difflib
import fnmatch
from pathlib import Path
from typing import Any

from ai_agent_platform.cogent.tools.base import Tool, ToolCategory
from ai_agent_platform.integrations.permissions import ToolUseContext
from ai_agent_platform.integrations.tools import ToolCall, ToolResult, ToolSpec


@dataclass(frozen=True)
class PreparedCall:
    visible_call: ToolCall
    execution_call: ToolCall | None
    tool: Tool
    spec: ToolSpec
    internal: str | None = None
    permission_arguments: dict[str, Any] | None = None


class CogentToolAdapter:
    def __init__(self, tool_access: Any, *, mcp_loading_mode: str = "dispatch", result_files=None) -> None:
        self._tools = tool_access
        self.mcp_loading_mode = mcp_loading_mode
        self.loaded_mcp_tools: set[str] = set()
        self.result_files = result_files

    def mcp_specs(self) -> dict[str, ToolSpec]:
        result = {}
        for source in self._tools.list_specs():
            if not source.name.startswith("mcp."):
                continue
            parts = source.name.split(".", 2)
            if len(parts) != 3:
                continue
            name = f"mcp__{parts[1]}__{parts[2]}"
            if name in result:
                raise ValueError("MCP tool names collide after wrapping")
            result[name] = source
        return result

    def list_specs(self) -> list[ToolSpec]:
        specs = {item.name: item for item in self._tools.list_specs()}
        result: list[ToolSpec] = []
        for visible, actual in _ALIASES.items():
            source = specs.get(actual)
            if source is not None:
                result.append(_visible_spec(visible, source))
        if any(item.name.startswith("mcp.") for item in specs.values()):
            if self.mcp_loading_mode == "eager":
                result.extend(replace(spec, name=name) for name, spec in self.mcp_specs().items())
            elif self.mcp_loading_mode == 'native':
                result.append(replace(_tool_search_spec(), native_type='tool_search_tool_regex_20251119'))
                result.extend(replace(spec, name=name, defer_loading=name not in self.loaded_mcp_tools)
                              for name, spec in self.mcp_specs().items())
            else:
                result.append(_tool_search_spec())
                result.extend(replace(spec, name=name) for name, spec in self.mcp_specs().items() if name in self.loaded_mcp_tools)
            result.append(_mcp_call_spec())
        result.append(_exit_plan_spec())
        return result

    def prepare(self, call: ToolCall, *, context: ToolUseContext) -> PreparedCall:
        import jsonschema
        spec = next((spec for spec in self.list_specs() if spec.name == call.name), None)
        if spec is None:
            raise ValueError(f'Tool is not loaded for this Run: {call.name}')
        jsonschema.validate(call.arguments, spec.input_schema)
        if call.name.startswith("mcp__"):
            source = self.mcp_specs().get(call.name)
            if source is None:
                raise ValueError(f"unknown MCP tool: {call.name}")
            _, server, tool = source.name.split(".", 2)
            return PreparedCall(
                call, ToolCall(source.name, dict(call.arguments), call.call_id, call.source),
                Tool("mcp_call", _category(source), source.description), source,
                permission_arguments={"server": server, "tool": tool, "arguments": call.arguments},
            )
        if call.name == "ToolSearch":
            return PreparedCall(
                call,
                None,
                Tool(call.name, "read"),
                _tool_search_spec(),
                "tool_search",
            )
        if call.name == "ExitPlanMode":
            return PreparedCall(
                call,
                None,
                Tool(call.name, "read"),
                _exit_plan_spec(),
                "exit_plan",
            )
        if call.name == "mcp_call":
            server = str(call.arguments.get("server") or "").strip()
            name = str(call.arguments.get("tool") or "").strip()
            actual_name = f"mcp.{server}.{name}"
            source = self._tools.get_spec(actual_name)
            if source is None:
                raise ValueError(f"unknown MCP tool: {server}__{name}")
            raw_arguments = call.arguments.get("arguments") or {}
            if not isinstance(raw_arguments, dict):
                raise ValueError("mcp_call arguments must be an object")
            execution = ToolCall(
                name=actual_name,
                arguments=dict(raw_arguments),
                call_id=call.call_id,
                source=call.source,
            )
            return PreparedCall(
                call,
                execution,
                Tool(call.name, _category(source), source.description),
                source,
            )
        actual_name = _ALIASES.get(call.name)
        if actual_name is None:
            raise ValueError(f"unknown Cogent tool: {call.name}")
        source = self._tools.get_spec(actual_name)
        if source is None:
            raise ValueError(f"tool unavailable for this run: {call.name}")
        if call.name == 'ReadFile' and self.result_files is not None:
            relative = self.result_files.relative(str(call.arguments.get('file_path') or ''))
            if relative is not None:
                return PreparedCall(call, ToolCall(actual_name, {'path': relative}, call.call_id, call.source),
                    Tool(call.name, 'read', source.description), source, 'result_read',
                    permission_arguments={**call.arguments, 'file_path': relative})
        execution = ToolCall(
            name=actual_name,
            arguments=self._translate_arguments(call, context=context),
            call_id=call.call_id,
            source=call.source,
        )
        return PreparedCall(
            call,
            execution,
            Tool(call.name, _category(source), source.description),
            source,
        )

    def execute(self, prepared: PreparedCall, context: ToolUseContext) -> ToolResult:
        if prepared.internal == 'result_read':
            args = prepared.visible_call.arguments
            return _internal_result(prepared.visible_call, self.result_files.read(
                str(args.get('file_path') or ''), offset=args.get('offset', 0), limit=args.get('limit', 2000)))
        if prepared.internal == "tool_search":
            return _internal_result(
                prepared.visible_call,
                self._search(prepared.visible_call.arguments),
            )
        if prepared.internal == "exit_plan":
            return _internal_result(
                prepared.visible_call,
                {
                    "requested": True,
                    "message": "Plan completion requires user confirmation.",
                },
            )
        assert prepared.execution_call is not None
        raw = self._tools.execute(prepared.execution_call, context=context)
        return ToolResult(
            call_id=raw.call_id,
            name=prepared.visible_call.name,
            ok=raw.ok,
            result=self._postprocess(prepared.visible_call, raw.result),
            error=raw.error,
            provider=raw.provider,
            permission_level=raw.permission_level,
            requires_approval=raw.requires_approval,
            duration_ms=raw.duration_ms,
            risk_summary=raw.risk_summary,
            arguments_summary=raw.arguments_summary,
            output_truncated=raw.output_truncated,
            error_code=raw.error_code,
            attempts=raw.attempts,
            cached=raw.cached,
            permission_decision=raw.permission_decision,
        )

    def _search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").casefold()
        limit = max(1, min(int(arguments.get("max_results") or 10), 50))
        matches = []
        for name, spec in self.mcp_specs().items():
            haystack = f"{name} {spec.description}".casefold()
            if query and not all(term in haystack for term in query.split()):
                continue
            matches.append(
                {
                    "name": name,
                    "server": spec.name.split(".", 2)[1],
                    "tool": spec.name.split(".", 2)[2],
                    "description": spec.description,
                    "input_schema": spec.input_schema,
                }
            )
        selected = matches[:limit]
        self.loaded_mcp_tools.update(item["name"] for item in selected)
        return {"tools": selected, "count": len(selected)}

    def _translate_arguments(
        self,
        call: ToolCall,
        *,
        context: ToolUseContext,
    ) -> dict[str, Any]:
        args = dict(call.arguments)
        if call.name == "ReadFile":
            offset = max(0, int(args.get("offset") or 0))
            limit = max(1, int(args.get("limit") or 2000))
            return {
                "path": str(args.get("file_path") or ""),
                "start_line": offset + 1,
                "end_line": offset + limit,
                "max_chars": 50_000,
            }
        if call.name == "WriteFile":
            translated: dict[str, Any] = {
                "path": str(args.get("file_path") or ""),
                "content": str(args.get("content") or ""),
            }
            if args.get("expected_sha256"):
                translated["expected_sha256"] = str(args["expected_sha256"])
            return translated
        if call.name == "EditFile":
            return {"patch": self._edit_patch(args, context=context)}
        if call.name == "Glob":
            return {
                "path": str(args.get("path") or "."),
                "max_results": max(
                    1,
                    min(int(args.get("max_results") or 1000), 5000),
                ),
            }
        if call.name == "Grep":
            return {
                "query": str(args.get("pattern") or ""),
                "path": str(args.get("path") or "."),
                "max_results": max(
                    1,
                    min(int(args.get("max_results") or 100), 1000),
                ),
                "context_lines": max(
                    0,
                    min(int(args.get("context_lines") or 0), 20),
                ),
            }
        if call.name == "Bash":
            return {
                "command": str(args.get("command") or ""),
                "cwd": str(args.get("cwd") or "."),
                **(
                    {"timeout_seconds": float(args["timeout_seconds"])}
                    if args.get("timeout_seconds") is not None
                    else {}
                ),
            }
        if call.name == "Diff":
            return {"max_chars": max(1, int(args.get("max_chars") or 20_000))}
        if call.name == "AskUserQuestion":
            return {"questions": list(args.get("questions") or [])}
        if call.name == "LoadSkill":
            return {
                "name": str(args.get("name") or ""),
                "arguments": str(args.get("arguments") or ""),
            }
        return args

    @staticmethod
    def _edit_patch(arguments: dict[str, Any], *, context: ToolUseContext) -> str:
        relative = Path(str(arguments.get("file_path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("EditFile path must stay inside the execution workspace")
        root = Path(context.execution_root or context.workspace_root).resolve()
        target = (root / relative).resolve()
        if target != root and root not in target.parents:
            raise ValueError("EditFile path escapes the execution workspace")
        from ai_agent_platform.integrations.execution_workspace import _reject_sensitive_path
        from ai_agent_platform.cogent.permissions.sandbox import PathSandbox
        _reject_sensitive_path(relative.as_posix())
        allowed, reason = PathSandbox(str(root)).check_deny_write(relative.as_posix())
        if not allowed:
            raise PermissionError(reason)
        original = target.read_text(encoding="utf-8")
        old = str(arguments.get("old_string") or "")
        new = str(arguments.get("new_string") or "")
        count = original.count(old)
        if not old or count != 1:
            raise ValueError(
                f"EditFile old_string must occur exactly once; found {count}"
            )
        updated = original.replace(old, new, 1)
        name = relative.as_posix()
        return "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
            )
        )

    @staticmethod
    def _postprocess(call: ToolCall, value: Any) -> Any:
        if call.name != "Glob" or not isinstance(value, dict):
            return value
        pattern = str(call.arguments.get("pattern") or "*")
        files = [
            item
            for item in value.get("files", [])
            if fnmatch.fnmatch(str(item), pattern)
        ]
        return {**value, "files": files, "count": len(files)}


_ALIASES = {
    "ReadFile": "repo.read_file",
    "WriteFile": "sandbox.write_file",
    "EditFile": "sandbox.apply_patch",
    "Glob": "repo.list_files",
    "Grep": "repo.search_code",
    "Bash": "sandbox.run_command",
    "Diff": "sandbox.git_diff",
    "AskUserQuestion": "agent.request_user_input",
    "LoadSkill": "agent.load_skill",
}


def _category(spec: ToolSpec) -> ToolCategory:
    if spec.name == "sandbox.run_command":
        return "command"
    return "read" if spec.permission_level == "read_only" else "write"


def _visible_spec(name: str, source: ToolSpec) -> ToolSpec:
    schemas = {
        "ReadFile": {
            "type": "object",
            "required": ["file_path"],
            "properties": {
                "file_path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
        "WriteFile": {
            "type": "object",
            "required": ["file_path", "content"],
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
                "expected_sha256": {
                    "type": "string",
                    "pattern": "^[a-f0-9]{64}$",
                },
            },
            "additionalProperties": False,
        },
        "EditFile": {
            "type": "object",
            "required": ["file_path", "old_string", "new_string"],
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string", "minLength": 1},
                "new_string": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "Glob": {
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "Grep": {
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "include": {"type": "string"},
                "max_results": {"type": "integer"},
                "context_lines": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "Bash": source.input_schema,
        "Diff": source.input_schema,
        "AskUserQuestion": source.input_schema,
        "LoadSkill": source.input_schema,
    }
    descriptions = {
        "ReadFile": "Read a UTF-8 file from the authorized execution workspace.",
        "WriteFile": "Create or replace a UTF-8 file in the execution workspace.",
        "EditFile": "Replace one exact, unique string in an existing UTF-8 file.",
        "Glob": "Find workspace files matching a glob pattern.",
        "Grep": "Search workspace text with a regular expression or phrase.",
        "Bash": "Run an allowed command in the execution workspace.",
        "Diff": "Show the current Run's workspace diff.",
        "AskUserQuestion": source.description,
        "LoadSkill": source.description,
    }
    return ToolSpec(
        name=name,
        description=descriptions[name],
        input_schema=schemas[name],
        output_schema=source.output_schema,
        provider=source.provider,
        permission_level=source.permission_level,
        requires_approval=source.requires_approval,
        risk_summary=source.risk_summary,
        max_output_chars=source.max_output_chars,
        timeout_seconds=source.timeout_seconds,
        max_retries=source.max_retries,
        idempotent=source.idempotent,
        permission_source=source.permission_source,
    )


def _tool_search_spec() -> ToolSpec:
    return ToolSpec(
        name="ToolSearch",
        description="Search the current Run's deferred MCP tool catalog.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        provider="cogent",
    )


def _mcp_call_spec() -> ToolSpec:
    return ToolSpec(
        name="mcp_call",
        description="Call one MCP tool selected by server and tool name.",
        input_schema={
            "type": "object",
            "required": ["server", "tool", "arguments"],
            "properties": {
                "server": {"type": "string"},
                "tool": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        provider="mcp",
        permission_level="external_side_effect",
        requires_approval=True,
        permission_source="mcp_annotation",
    )


def _exit_plan_spec() -> ToolSpec:
    return ToolSpec(
        name="ExitPlanMode",
        description=(
            "Request user confirmation of the completed plan before leaving plan mode."
        ),
        input_schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        provider="cogent",
    )


def _internal_result(call: ToolCall, result: dict[str, Any]) -> ToolResult:
    return ToolResult(
        call_id=call.call_id,
        name=call.name,
        ok=True,
        result=result,
        provider="cogent",
    )
