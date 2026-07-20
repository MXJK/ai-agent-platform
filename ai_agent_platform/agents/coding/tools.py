"""Tool-registry assembly and local high-level coding tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ai_agent_platform.integrations.mcp import (
    MCPToolProvider,
    register_mcp_tools,
)
from ai_agent_platform.integrations.tools import ToolRegistry
from ai_agent_platform.tools import register_repository_tools, register_sandbox_tools


def create_coding_tool_registry(
    root_path: Path | str | None = None,
    mcp_providers: Optional[list[MCPToolProvider]] = None,
    sandbox_mode: str = "local",
    sandbox_docker_image: str = "python:3.11-slim",
    sandbox_command_timeout_seconds: float = 30.0,
    sandbox_workspace_parent: Path | str | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    resolved_root_path = root_path or Path.cwd()
    register_repository_tools(registry, resolved_root_path)
    register_sandbox_tools(
        registry,
        root_path=resolved_root_path,
        mode=sandbox_mode,
        docker_image=sandbox_docker_image,
        command_timeout_seconds=sandbox_command_timeout_seconds,
        workspace_parent=sandbox_workspace_parent,
    )
    if mcp_providers:
        register_mcp_tools(registry, mcp_providers)
    registry.register(
        "repository_context_search",
        repository_context_search_tool,
        description="Summarize repository RAG retrieval state for answer grounding.",
    )
    registry.register(
        "file_symbol_locator",
        file_symbol_locator_tool,
        description="Suggest file and symbol location commands from retrieved context.",
    )
    registry.register(
        "code_explainer",
        code_explainer_tool,
        description="Build a structured explanation plan from retrieved snippets.",
    )
    registry.register(
        "change_planner",
        change_planner_tool,
        description="Plan a safe code change across candidate files.",
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
    )
    registry.register(
        "test_designer",
        test_designer_tool,
        description="Suggest focused tests for a requested behavior or fix.",
    )
    return registry


def repository_context_search_tool(
    *,
    query: str,
    repository_id: str,
    citation_count: int,
    candidate_files: list[str],
) -> dict[str, Any]:
    return {
        "query": query,
        "repository_id": repository_id,
        "citation_count": citation_count,
        "candidate_files": candidate_files,
        "next_step": "ground the answer in retrieved file chunks and ask for indexing if citations are empty",
    }


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
            "RAG-scoped test that proves repository_id isolates code indexes",
        ],
    }
