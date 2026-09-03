"""LangGraph topology for the coding Agent Loop.

The builder owns node names and edges only. Node implementations stay behind the
``CodingAgentNodes`` protocol so callers outside the Agent runtime never observe
``CodingAgentState``.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from langgraph.graph import END, StateGraph

from ai_agent_platform.agents.coding.models import CodingAgentState
from ai_agent_platform.agents.coding.runtime_support import (
    route_after_artifact_collection,
    route_after_change_execution,
    route_after_inspection,
    route_after_repair_review,
    route_after_tool_plan_review,
    route_after_tool_planning,
    route_after_validation,
)


Node = Callable[[CodingAgentState], CodingAgentState]
Route = Callable[[CodingAgentState], str]


class CodingAgentNodes(Protocol):
    setup_workspace: Node
    load_project_instructions: Node
    classify_request: Node
    decide_context_source: Node
    retrieve_project_memory: Node
    retrieve_knowledge: Node
    plan_exploration: Node
    execute_exploration: Node
    assess_context: Node
    route_after_context: Route
    merge_evidence: Node
    resolve_change_targets: Node
    define_completion_contract: Node
    plan_tools: Node
    review_tool_plan: Node
    inspect_repository: Node
    execute_changes: Node
    validate_changes: Node
    review_repair_plan: Node
    collect_artifacts: Node
    compose_answer: Node
    compose_error_answer: Node


def build_coding_agent_graph(*, nodes: CodingAgentNodes, checkpointer: Any):
    """Compile the established Coding Agent graph without changing topology."""
    workflow = StateGraph(CodingAgentState)
    workflow.add_node("setup_workspace", nodes.setup_workspace)
    workflow.add_node("load_project_instructions", nodes.load_project_instructions)
    workflow.add_node("classify_request", nodes.classify_request)
    workflow.add_node("decide_context_source", nodes.decide_context_source)
    workflow.add_node("retrieve_project_memory", nodes.retrieve_project_memory)
    workflow.add_node("retrieve_knowledge", nodes.retrieve_knowledge)
    workflow.add_node("plan_exploration", nodes.plan_exploration)
    workflow.add_node("execute_exploration", nodes.execute_exploration)
    workflow.add_node("assess_context", nodes.assess_context)
    workflow.add_node("merge_evidence", nodes.merge_evidence)
    workflow.add_node("resolve_change_targets", nodes.resolve_change_targets)
    workflow.add_node("define_completion_contract", nodes.define_completion_contract)
    workflow.add_node("plan_tools", nodes.plan_tools)
    workflow.add_node("review_tool_plan", nodes.review_tool_plan)
    workflow.add_node("inspect_repository", nodes.inspect_repository)
    workflow.add_node("execute_changes", nodes.execute_changes)
    workflow.add_node("validate_changes", nodes.validate_changes)
    workflow.add_node("review_repair_plan", nodes.review_repair_plan)
    workflow.add_node("collect_artifacts", nodes.collect_artifacts)
    workflow.add_node("compose_answer", nodes.compose_answer)
    workflow.add_node("compose_error_answer", nodes.compose_error_answer)
    workflow.set_entry_point("setup_workspace")
    workflow.add_edge("setup_workspace", "load_project_instructions")
    workflow.add_edge("load_project_instructions", "classify_request")
    workflow.add_edge("classify_request", "decide_context_source")
    workflow.add_edge("decide_context_source", "retrieve_project_memory")
    workflow.add_conditional_edges(
        "retrieve_project_memory",
        lambda state: state.get("context_route", "repo"),
        {
            "none": "merge_evidence",
            "repo": "plan_exploration",
            "rag": "retrieve_knowledge",
            "hybrid": "retrieve_knowledge",
        },
    )
    workflow.add_conditional_edges(
        "retrieve_knowledge",
        lambda state: (
            "plan_exploration"
            if state.get("context_route") == "hybrid"
            else "merge_evidence"
        ),
        {
            "plan_exploration": "plan_exploration",
            "merge_evidence": "merge_evidence",
        },
    )
    workflow.add_edge("plan_exploration", "execute_exploration")
    workflow.add_edge("execute_exploration", "assess_context")
    workflow.add_conditional_edges(
        "assess_context",
        nodes.route_after_context,
        {
            "plan_exploration": "plan_exploration",
            "merge_evidence": "merge_evidence",
        },
    )
    workflow.add_conditional_edges(
        "merge_evidence",
        lambda state: (
            "resolve_change_targets"
            if state.get("task_shape") == "bounded_change"
            and state.get("workspace_completion_required", True)
            else "plan_tools"
            if state.get("context_route") in {"repo", "hybrid"}
            else "compose_answer"
        ),
        {
            "resolve_change_targets": "resolve_change_targets",
            "plan_tools": "plan_tools",
            "compose_answer": "compose_answer",
        },
    )
    workflow.add_conditional_edges(
        "resolve_change_targets",
        lambda state: (
            "compose_answer"
            if state.get("terminal_reason") == "target_selection_skipped"
            else "define_completion_contract"
        ),
        {
            "define_completion_contract": "define_completion_contract",
            "compose_answer": "compose_answer",
        },
    )
    workflow.add_conditional_edges(
        "define_completion_contract",
        lambda state: (
            "compose_answer"
            if state.get("terminal_reason") == "completion_contract_unavailable"
            else "plan_tools"
        ),
        {
            "plan_tools": "plan_tools",
            "compose_answer": "compose_answer",
        },
    )
    workflow.add_conditional_edges(
        "plan_tools",
        route_after_tool_planning,
        {
            "plan_tools": "plan_tools",
            "review_tool_plan": "review_tool_plan",
            "inspect_repository": "inspect_repository",
            "collect_artifacts": "collect_artifacts",
            "compose_answer": "compose_answer",
        },
    )
    workflow.add_conditional_edges(
        "review_tool_plan",
        route_after_tool_plan_review,
        {
            "inspect_repository": "inspect_repository",
            "compose_answer": "compose_answer",
        },
    )
    workflow.add_conditional_edges(
        "inspect_repository",
        route_after_inspection,
        {
            "plan_tools": "plan_tools",
            "execute_changes": "execute_changes",
            "validate_changes": "validate_changes",
            "collect_artifacts": "collect_artifacts",
            "compose_answer": "compose_answer",
        },
    )
    workflow.add_conditional_edges(
        "execute_changes",
        route_after_change_execution,
        {
            "validate_changes": "validate_changes",
            "collect_artifacts": "collect_artifacts",
        },
    )
    workflow.add_conditional_edges(
        "validate_changes",
        route_after_validation,
        {
            "review_repair_plan": "review_repair_plan",
            "collect_artifacts": "collect_artifacts",
        },
    )
    workflow.add_conditional_edges(
        "review_repair_plan",
        route_after_repair_review,
        {
            "execute_changes": "execute_changes",
            "collect_artifacts": "collect_artifacts",
        },
    )
    workflow.add_conditional_edges(
        "collect_artifacts",
        route_after_artifact_collection,
        {
            "plan_tools": "plan_tools",
            "compose_answer": "compose_answer",
        },
    )
    workflow.add_edge("compose_answer", END)
    workflow.add_edge("compose_error_answer", END)
    return workflow.compile(checkpointer=checkpointer)
