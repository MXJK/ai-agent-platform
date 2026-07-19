"""Checkpoint, routing, retry, and trace helpers for the LangGraph runtime."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Callable, Optional

from ai_agent_platform.agents.coding.models import (
    MAX_NODE_RETRIES,
    AgentRoute,
    AgentRunMetrics,
    AnswerRoute,
    CodingAgentState,
    PlanRoute,
    RetrievalRoute,
    ReviewRoute,
)
from ai_agent_platform.integrations import (
    RAGConfigurationError,
    RAGProviderError,
    RAGValidationError,
)


def checkpoint_id(snapshot: Any) -> Optional[str]:
    if snapshot is None:
        return None
    configurable = snapshot.config.get("configurable", {})
    value = configurable.get("checkpoint_id")
    return str(value) if value else None


def next_nodes(snapshot: Any) -> list[str]:
    if snapshot is None:
        return []
    return [str(node) for node in snapshot.next]


def pending_approval(
    snapshot: Any,
    state: dict[str, Any],
) -> Optional[dict[str, Any]]:
    if "__interrupt__" in state:
        interrupts = state["__interrupt__"]
        if interrupts:
            return approval_payload_from_interrupt(interrupts[0])
    if snapshot is None:
        return None
    for task in getattr(snapshot, "tasks", ()):
        for task_interrupt in getattr(task, "interrupts", ()):
            return approval_payload_from_interrupt(task_interrupt)
    return None


def approval_payload_from_interrupt(task_interrupt: Any) -> dict[str, Any]:
    value = getattr(task_interrupt, "value", {})
    payload = dict(value) if isinstance(value, dict) else {"message": str(value)}
    interrupt_id = getattr(task_interrupt, "id", None)
    if interrupt_id:
        payload["interrupt_id"] = str(interrupt_id)
    return payload


def snapshot_trace(snapshot: Any) -> list[dict[str, Any]]:
    if snapshot is None or not isinstance(snapshot.values, dict):
        return []
    trace = snapshot.values.get("trace", [])
    return list(trace) if isinstance(trace, list) else []


def snapshot_errors(snapshot: Any) -> list[dict[str, Any]]:
    if snapshot is None or not isinstance(snapshot.values, dict):
        return []
    errors = snapshot.values.get("errors", [])
    return list(errors) if isinstance(errors, list) else []


def latest_trace_node(snapshot: Any) -> Optional[str]:
    trace = snapshot_trace(snapshot)
    if not trace:
        return None
    latest = trace[-1]
    node = latest.get("node") if isinstance(latest, dict) else None
    return str(node) if node else None


def run_with_retries(
    *,
    node: str,
    operation: Callable[[], Any],
    classify_error: Callable[[Exception], tuple[str, bool]],
    max_retries: int = MAX_NODE_RETRIES,
) -> tuple[Any, list[dict[str, Any]], int]:
    errors: list[dict[str, Any]] = []
    max_attempts = max_retries + 1
    for attempt in range(1, max_attempts + 1):
        try:
            result = operation()
        except Exception as exc:
            code, retryable = classify_error(exc)
            should_retry = retryable and attempt < max_attempts
            errors.append(
                structured_error(
                    node=node,
                    code=code,
                    message=str(exc),
                    retryable=retryable,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    recovered=False,
                )
            )
            if not should_retry:
                return None, errors, attempt
            continue
        if errors:
            errors = [dict(error, recovered=True) for error in errors]
        return result, errors, attempt
    return None, errors, max_attempts


def classify_rag_error(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, RAGValidationError):
        return "rag_validation_error", False
    if isinstance(exc, RAGConfigurationError):
        return "rag_configuration_error", False
    if isinstance(exc, RAGProviderError):
        return "rag_provider_error", True
    return "rag_unhandled_error", False


def classify_answer_error(exc: Exception) -> tuple[str, bool]:
    return "answer_generation_error", True


def structured_error(
    *,
    node: str,
    code: str,
    message: str,
    retryable: bool,
    attempt: int,
    max_attempts: int,
    recovered: bool,
) -> dict[str, Any]:
    return {
        "node": node,
        "code": code,
        "message": message,
        "retryable": retryable,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "recovered": recovered,
    }


def error_from_exception(
    node: str,
    exc: Exception,
    *,
    attempt: int,
    max_attempts: int,
) -> dict[str, Any]:
    return structured_error(
        node=node,
        code="runtime_error",
        message=str(exc),
        retryable=False,
        attempt=attempt,
        max_attempts=max_attempts,
        recovered=False,
    )


def append_errors(
    state: CodingAgentState,
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not errors:
        return list(state.get("errors", []))
    return list(state.get("errors", [])) + errors


def unresolved_errors(state: CodingAgentState) -> list[dict[str, Any]]:
    return [
        error
        for error in state.get("errors", [])
        if not error.get("recovered", False)
    ]


def route_after_classification(state: CodingAgentState) -> AgentRoute:
    return next_node_for_intent(state.get("intent", "repository_question"))


def next_node_for_intent(intent: str) -> AgentRoute:
    return "compose_answer" if intent == "small_talk" else "retrieve_repository_context"


def route_after_retrieval(state: CodingAgentState) -> RetrievalRoute:
    return "handle_error" if unresolved_errors(state) else "plan_tools"


def route_after_tool_planning(state: CodingAgentState) -> PlanRoute:
    return "review_tool_plan" if state.get("approval_required_tools") else "inspect_repository"


def route_after_tool_plan_review(state: CodingAgentState) -> ReviewRoute:
    decision = state.get("review_decision", {})
    return "inspect_repository" if decision.get("approved") else "compose_answer"


def route_after_answer_composition(state: CodingAgentState) -> AnswerRoute:
    return "handle_error" if unresolved_errors(state) else "end"


def build_repository_query(state: CodingAgentState) -> str:
    parts = [state["user_input"]]
    focus_files = state.get("focus_files", [])
    if focus_files:
        parts.append("重点文件: " + " ".join(focus_files))
    return "\n".join(parts)


def build_tool_plan_approval_request(state: CodingAgentState) -> dict[str, Any]:
    return {
        "type": "tool_plan_review",
        "approval_required": True,
        "reason": "one or more planned tools require human approval before execution",
        "intent": state.get("intent", "change_planning"),
        "repository_id": state["repository_id"],
        "message": state["user_input"],
        "planned_tools": [call.name for call in state.get("tool_calls", [])],
        "approval_required_tools": state.get("approval_required_tools", []),
        "tool_calls": [
            {"name": call.name, "arguments": call.arguments}
            for call in state.get("tool_calls", [])
        ],
    }


def build_run_metrics(state: CodingAgentState) -> AgentRunMetrics:
    started_at = state.get("started_at")
    elapsed_ms = (
        int((perf_counter() - started_at) * 1000)
        if isinstance(started_at, (int, float))
        else 0
    )
    tool_results = state.get("tool_results", [])
    errors = state.get("errors", [])
    return AgentRunMetrics(
        elapsed_ms=max(0, elapsed_ms),
        node_count=len(state.get("trace", [])),
        tool_call_count=len(state.get("tool_calls", [])),
        successful_tool_call_count=sum(
            1 for result in tool_results if result.get("ok")
        ),
        retry_count=sum(
            1
            for error in errors
            if int(error.get("attempt", 1)) < int(error.get("max_attempts", 1))
        ),
        error_count=len(errors),
        recovered_error_count=sum(
            1 for error in errors if error.get("recovered", False)
        ),
    )


def append_trace(
    state: CodingAgentState,
    *,
    node: str,
    summary: str,
    output: dict[str, Any],
) -> list[dict[str, Any]]:
    trace = list(state.get("trace", []))
    trace.append(
        {
            "step": len(trace) + 1,
            "node": node,
            "summary": summary,
            "output": output,
        }
    )
    return trace
