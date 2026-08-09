"""Tool-registry assembly and local high-level coding tools."""

from __future__ import annotations

from typing import Any, Optional

from ai_agent_platform.integrations.mcp import (
    MCPToolProvider,
    register_mcp_tools,
)
from ai_agent_platform.integrations.tools import ToolRegistry
from ai_agent_platform.tools import register_repository_tools, register_sandbox_tools


def create_coding_tool_registry(
    mcp_providers: Optional[list[MCPToolProvider]] = None,
    sandbox_mode: str = "local",
    sandbox_docker_image: str = "python:3.11-slim",
    sandbox_command_timeout_seconds: float = 30.0,
    sandbox_command_output_max_chars: int = 12000,
    sandbox_workspace_parent: str | None = None,
    sandbox_workspace_ttl_seconds: float = 86400.0,
    sandbox_allowed_commands: tuple[str, ...] | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    register_repository_tools(registry)
    register_sandbox_tools(
        registry,
        mode=sandbox_mode,
        docker_image=sandbox_docker_image,
        command_timeout_seconds=sandbox_command_timeout_seconds,
        command_output_max_chars=sandbox_command_output_max_chars,
        workspace_parent=sandbox_workspace_parent,
        workspace_ttl_seconds=sandbox_workspace_ttl_seconds,
        allowed_commands=sandbox_allowed_commands,
    )
    if mcp_providers:
        register_mcp_tools(registry, mcp_providers)
    registry.register(
        "agent.request_user_input",
        request_user_input_tool,
        description=(
            "Pause the active run and ask the user one concise question when a "
            "material choice cannot be inferred safely."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "minLength": 1, "maxLength": 1000},
                "context": {"type": "string", "maxLength": 2000},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )
    registry.register(
        "file_symbol_locator",
        file_symbol_locator_tool,
        description="Suggest file and symbol location commands from retrieved context.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "focus_files": {"type": "array", "items": {"type": "string"}},
                "symbols": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query", "focus_files", "symbols"],
            "additionalProperties": False,
        },
    )
    registry.register(
        "code_explainer",
        code_explainer_tool,
        description="Build a structured explanation plan from retrieved snippets.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "files": {"type": "array", "items": {"type": "string"}},
                "context_snippets": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["query", "files", "context_snippets"],
            "additionalProperties": False,
        },
    )
    registry.register(
        "change_planner",
        change_planner_tool,
        description="Plan a safe code change across candidate files.",
        input_schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "minLength": 1},
                "candidate_files": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["goal", "candidate_files"],
            "additionalProperties": False,
        },
        permission_level="write_safe",
        requires_approval=True,
        risk_summary=(
            "Plans implementation work that may lead to code edits; human review "
            "is required before execution."
        ),
    )
    registry.register(
        "bug_investigator",
        bug_investigator_tool,
        description="Plan a focused debugging path for a reported symptom.",
        input_schema={
            "type": "object",
            "properties": {
                "symptom": {"type": "string", "minLength": 1},
                "candidate_files": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["symptom", "candidate_files"],
            "additionalProperties": False,
        },
    )
    registry.register(
        "test_designer",
        test_designer_tool,
        description="Suggest focused tests for a requested behavior or fix.",
        input_schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "minLength": 1},
                "candidate_files": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["goal", "candidate_files"],
            "additionalProperties": False,
        },
    )
    return registry


def request_user_input_tool(*, question: str, context: str = "") -> dict[str, Any]:
    del question, context
    raise RuntimeError(
        "agent.request_user_input must be handled by the Agent checkpoint runtime"
    )


def file_symbol_locator_tool(
    *, query: str, focus_files: list[str], symbols: list[str]
) -> dict[str, Any]:
    return {
        "query": query,
        "focus_files": focus_files,
        "symbols": symbols,
        "suggested_commands": [
            "rg -n '<symbol-or-keyword>' .",
            "rg --files | rg '<path-fragment>'",
        ],
    }


def code_explainer_tool(
    *, query: str, files: list[str], context_snippets: list[str]
) -> dict[str, Any]:
    return {
        "query": query,
        "files": files,
        "summary_style": "explain responsibility, call flow, inputs, outputs, and extension points",
        "context_snippet_count": len(context_snippets),
    }


def change_planner_tool(*, goal: str, candidate_files: list[str]) -> dict[str, Any]:
    return {
        "goal": goal,
        "candidate_files": candidate_files,
        "plan": [
            "confirm current API/data contracts around the candidate files",
            "make the smallest behavior change behind the existing boundary",
            "add focused tests for the changed agent route",
            "run unit tests and a compile check before handing off",
        ],
    }


def bug_investigator_tool(
    *, symptom: str, candidate_files: list[str]
) -> dict[str, Any]:
    return {
        "symptom": symptom,
        "candidate_files": candidate_files,
        "debug_order": [
            "reproduce the failing request or test",
            "trace from route to service/runtime to integration boundary",
            "check whether history, retrieved context, or tool results are missing",
            "patch the narrowest failing branch and add a regression test",
        ],
    }


def test_designer_tool(*, goal: str, candidate_files: list[str]) -> dict[str, Any]:
    return {
        "goal": goal,
        "candidate_files": candidate_files,
        "recommended_tests": [
            "API contract test for /api/v1/agent/runs",
            "runtime unit test for intent routing and planned tools",
            "workspace isolation test that proves tools cannot cross registered roots",
        ],
    }
