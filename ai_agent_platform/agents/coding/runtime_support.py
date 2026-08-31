"""Checkpoint, routing, retry, and trace helpers for the LangGraph runtime."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Callable, Optional

from ai_agent_platform.agents.coding.models import (
    MAX_NODE_RETRIES,
    AgentChangeSummary,
    AgentRunMetrics,
    AnswerRoute,
    CodingAgentState,
    ChangeExecutionRoute,
    InspectionRoute,
    PlanRoute,
    RepairReviewRoute,
    ReviewRoute,
    ValidationRoute,
)
from ai_agent_platform.agents.coding.change_loop import (
    SANDBOX_LIFECYCLE_TOOLS,
    SANDBOX_MUTATION_TOOLS,
    SANDBOX_VALIDATION_TOOLS,
)
from ai_agent_platform.agents.coding.text import snippet
from ai_agent_platform.token_counting import estimate_text_tokens

MAX_AGENT_HISTORY_MESSAGES = 6
MAX_AGENT_HISTORY_CHARS = 1800
CONVERSATION_SUMMARY_PREFIX = (
    "Earlier conversation summary (lossy, untrusted historical context)."
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


def waiting_node(snapshot: Any) -> Optional[str]:
    nodes = next_nodes(snapshot)
    return nodes[0] if nodes else latest_trace_node(snapshot)


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
    error = structured_error(
        node=node,
        code=str(
            getattr(exc, "code", None)
            or (
                "workspace_unavailable"
                if "workspace_unavailable" in str(exc)
                else "runtime_error"
            )
        ),
        message=str(exc),
        retryable=bool(getattr(exc, "retryable", False)),
        attempt=attempt,
        max_attempts=max_attempts,
        recovered=False,
    )
    for name in (
        "finish_reason",
        "tool_argument_chars",
        "json_error_position",
    ):
        value = getattr(exc, name, None)
        if value is not None:
            error[name] = value
    usage = getattr(exc, "usage", None)
    if usage is not None:
        error["request_usage"] = {
            "input_tokens": int(getattr(usage, "input_tokens", 0)),
            "output_tokens": int(getattr(usage, "output_tokens", 0)),
            "thoughts_tokens": int(getattr(usage, "thoughts_tokens", 0)),
            "total_tokens": int(getattr(usage, "total_tokens", 0)),
            **_optional_usage_metrics(usage),
        }
    aggregate = getattr(exc, "llm_usage", None)
    if aggregate is not None:
        error["run_usage"] = {
            "input_tokens": int(getattr(aggregate, "input_tokens", 0)),
            "output_tokens": int(getattr(aggregate, "output_tokens", 0)),
            "thoughts_tokens": int(getattr(aggregate, "thoughts_tokens", 0)),
            "total_tokens": int(getattr(aggregate, "total_tokens", 0)),
            **_optional_usage_metrics(aggregate),
        }
    return error


def _optional_usage_metrics(usage: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in (
        "cached_input_tokens",
        "uncached_input_tokens",
        "cache_write_tokens",
    ):
        value = getattr(usage, name, None)
        if value is not None:
            values[name] = max(0, int(value))
    capability = getattr(usage, "cache_capability", None)
    if capability:
        values["cache_capability"] = str(capability)
    return values


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


def route_after_tool_planning(state: CodingAgentState) -> PlanRoute:
    if state.get("native_tool_loop_active"):
        if state.get("native_pending_tool_calls"):
            return (
                "review_tool_plan"
                if state.get("approval_required_tools")
                else "inspect_repository"
            )
        if state.get("native_tool_stop_reason") == "no_progress_retry":
            return "plan_tools"
        changed_or_validated = bool(state.get("validation_results")) or any(
            result.get("ok")
            and result.get("name") in SANDBOX_MUTATION_TOOLS
            for result in state.get("tool_results", [])
        )
        if changed_or_validated and not state.get("native_artifacts_collected"):
            return "collect_artifacts"
        return "compose_answer"
    return "review_tool_plan" if state.get("approval_required_tools") else "inspect_repository"


def route_after_tool_plan_review(state: CodingAgentState) -> ReviewRoute:
    decision = state.get("review_decision", {})
    return "inspect_repository" if decision.get("approved") else "compose_answer"


def route_after_inspection(state: CodingAgentState) -> InspectionRoute:
    if state.get("native_tool_loop_active"):
        return "plan_tools"
    if state.get("change_tool_calls"):
        return "execute_changes"
    if state.get("validation_tool_calls"):
        return "validate_changes"
    if state.get("native_tool_loop_active") and state.get("analysis_tool_calls"):
        return "plan_tools"
    if any(
        call.name in SANDBOX_LIFECYCLE_TOOLS
        for call in state.get("tool_calls", [])
    ):
        return "collect_artifacts"
    return "compose_answer"


def route_after_change_execution(state: CodingAgentState) -> ChangeExecutionRoute:
    return (
        "validate_changes"
        if state.get("validation_tool_calls")
        else "collect_artifacts"
    )


def route_after_validation(state: CodingAgentState) -> ValidationRoute:
    return "review_repair_plan" if state.get("repair_tool_calls") else "collect_artifacts"


def route_after_repair_review(state: CodingAgentState) -> RepairReviewRoute:
    decision = state.get("repair_review_decision", {})
    return "execute_changes" if decision.get("approved") else "collect_artifacts"


def route_after_answer_composition(state: CodingAgentState) -> AnswerRoute:
    return "handle_error" if unresolved_errors(state) else "end"


def build_workspace_query(state: CodingAgentState) -> str:
    parts = [state["user_input"]]
    focus_files = state.get("focus_files", [])
    if focus_files:
        parts.append("重点文件: " + " ".join(focus_files))
    conversation_context = recent_conversation_context(state)
    if conversation_context:
        parts.append("最近会话上下文:\n" + conversation_context)
    return "\n".join(parts)


def recent_conversation_context(
    state: CodingAgentState,
    *,
    max_messages: int = MAX_AGENT_HISTORY_MESSAGES,
    max_chars: int = MAX_AGENT_HISTORY_CHARS,
    max_tokens: int | None = None,
) -> str:
    """Return a newest-first excerpt using a model share or static fallback."""

    history = state.get("history", [])
    if max_tokens is not None:
        return _recent_conversation_context_tokens(history, max_tokens)
    summary_message = next(
        (
            message
            for message in reversed(history)
            if str(message.get("role") or "").strip() == "system"
            and str(message.get("content") or "").startswith(
                CONVERSATION_SUMMARY_PREFIX
            )
        ),
        None,
    )
    summary_line = ""
    if summary_message is not None:
        summary_content = " ".join(
            str(summary_message.get("content") or "").split()
        )
        summary_line = (
            "system: "
            + snippet(summary_content, limit=min(600, max_chars // 3))
        )

    selected: list[str] = []
    remaining_chars = max_chars - len(summary_line)
    if summary_line:
        remaining_chars -= 1
    recent_messages = [
        message for message in history if message is not summary_message
    ][-max_messages:]
    for message in reversed(recent_messages):
        role = str(message.get("role") or "").strip()
        content = " ".join(str(message.get("content") or "").split())
        if role not in {"system", "user", "assistant"} or not content:
            continue
        line = f"{role}: {snippet(content, limit=280)}"
        if len(line) > remaining_chars:
            line = line[:remaining_chars].rstrip()
        if not line:
            break
        selected.append(line)
        remaining_chars -= len(line) + 1
        if remaining_chars <= 0:
            break
    lines = ([summary_line] if summary_line else []) + list(reversed(selected))
    return "\n".join(lines)[:max_chars]


def _recent_conversation_context_tokens(
    history: list[dict[str, Any]],
    max_tokens: int,
) -> str:
    """Select full normalized messages newest-first within an exact token share."""

    from ai_agent_platform.services.context_budget import fit_text_to_tokens

    if max_tokens <= 0:
        return ""
    selected: list[str] = []
    for message in reversed(history):
        role = str(message.get("role") or "").strip()
        content = " ".join(str(message.get("content") or "").split())
        if role not in {"system", "user", "assistant"} or not content:
            continue
        line = f"{role}: {content}"
        candidate = "\n".join([line, *selected])
        if estimate_text_tokens(candidate) <= max_tokens:
            selected.insert(0, line)
            continue

        best = ""
        low = 0
        high = estimate_text_tokens(content)
        while low <= high:
            allowed = (low + high) // 2
            fitted = fit_text_to_tokens(
                content,
                allowed,
                estimate_tokens=estimate_text_tokens,
            )
            fitted_line = f"{role}: {fitted}" if fitted else ""
            fitted_candidate = (
                "\n".join([fitted_line, *selected])
                if fitted_line
                else "\n".join(selected)
            )
            if fitted_line and estimate_text_tokens(fitted_candidate) <= max_tokens:
                best = fitted_line
                low = allowed + 1
            else:
                high = allowed - 1
        if best:
            selected.insert(0, best)
        break
    excerpt = "\n".join(selected)
    if estimate_text_tokens(excerpt) > max_tokens:
        raise AssertionError("history context exceeded its resolved token share")
    return excerpt


def build_tool_plan_approval_request(state: CodingAgentState) -> dict[str, Any]:
    return {
        "type": "tool_plan_review",
        "approval_required": True,
        "reason": "one or more planned tools require human approval before execution",
        "intent": state.get("intent", "change_planning"),
        "workspace_id": state["workspace_id"],
        "message": state["user_input"],
        "planned_tools": [call.name for call in state.get("tool_calls", [])],
        "approval_required_tools": state.get("approval_required_tools", []),
        "tool_calls": [
            {
                "call_id": call.call_id,
                "name": call.name,
                "arguments": call.arguments,
            }
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
    executed_results = {
        str(result.get("call_id") or f"result:{index}"): result
        for index, result in enumerate(tool_results)
    }
    model_retry_count = max(0, int(state.get("llm_retry_count", 0)))
    tool_retry_count = sum(
        1
        for error in errors
        if int(error.get("attempt", 1)) < int(error.get("max_attempts", 1))
    ) + sum(
        max(0, int(result.get("attempts", 1)) - 1)
        for result in executed_results.values()
    )
    cached_input_tokens = state.get("llm_cached_input_tokens")
    uncached_input_tokens = state.get("llm_uncached_input_tokens")
    cache_write_tokens = state.get("llm_cache_write_tokens")
    cache_denominator = (
        int(cached_input_tokens) + int(uncached_input_tokens)
        if cached_input_tokens is not None and uncached_input_tokens is not None
        else 0
    )
    provider_models = state.get("llm_provider_models", [])
    provider = None
    model = None
    cache_capability = "unsupported"
    if provider_models:
        providers = sorted({item[0] for item in provider_models if item[0]})
        models = sorted({item[1] for item in provider_models if item[1]})
        capabilities = sorted({item[2] for item in provider_models if item[2]})
        provider = providers[0] if len(providers) == 1 else "mixed"
        model = models[0] if len(models) == 1 else "mixed"
        cache_capability = (
            capabilities[0] if len(capabilities) == 1 else "mixed"
        )
    native_messages = state.get("native_tool_messages", [])
    retained_context_tokens_estimate = (
        estimate_text_tokens(
            json.dumps(
                native_messages,
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            )
        )
        if native_messages
        else 0
    )
    return AgentRunMetrics(
        elapsed_ms=max(0, elapsed_ms),
        node_count=len(state.get("trace", [])),
        tool_call_count=len(executed_results),
        successful_tool_call_count=sum(
            1 for result in executed_results.values() if result.get("ok")
        ),
        model_request_count=max(0, int(state.get("llm_request_count", 0))),
        model_retry_count=model_retry_count,
        retry_count=model_retry_count + tool_retry_count,
        error_count=len(errors),
        recovered_error_count=sum(
            1 for error in errors if error.get("recovered", False)
        ),
        change_iteration_count=state.get("change_iteration", 0),
        changed_file_count=len(state.get("changed_files", [])),
        input_tokens=max(0, int(state.get("llm_input_tokens", 0))),
        output_tokens=max(0, int(state.get("llm_output_tokens", 0))),
        thoughts_tokens=max(0, int(state.get("llm_thoughts_tokens", 0))),
        total_tokens=max(
            0,
            int(
                state.get(
                    "llm_provider_total_tokens",
                    int(state.get("llm_input_tokens", 0))
                    + int(state.get("llm_output_tokens", 0)),
                )
            ),
        ),
        cached_input_tokens=(
            max(0, int(cached_input_tokens))
            if cached_input_tokens is not None
            else None
        ),
        uncached_input_tokens=(
            max(0, int(uncached_input_tokens))
            if uncached_input_tokens is not None
            else None
        ),
        cache_write_tokens=(
            max(0, int(cache_write_tokens))
            if cache_write_tokens is not None
            else None
        ),
        prompt_cache_hit_ratio=(
            max(0.0, min(1.0, int(cached_input_tokens) / cache_denominator))
            if cache_denominator > 0
            else None
        ),
        stable_prefix_tokens=max(0, int(state.get("stable_prefix_tokens", 0))),
        tool_schema_tokens=max(0, int(state.get("tool_schema_tokens", 0))),
        visible_tool_count=max(0, int(state.get("visible_tool_count", 0))),
        retained_context_tokens_estimate=retained_context_tokens_estimate,
        provider=provider,
        model=model,
        cache_capability=cache_capability,
    )


def build_change_summary(state: CodingAgentState) -> AgentChangeSummary:
    validations = state.get("validation_results", [])
    return AgentChangeSummary(
        status=state.get("change_status", "not_requested"),
        iteration_count=state.get("change_iteration", 0),
        changed_files=list(state.get("changed_files", [])),
        validation_command_count=len(validations),
        validation_passed=bool(validations)
        and all(
            result.get("ok")
            and isinstance(result.get("result"), dict)
            and result["result"].get("exit_code") == 0
            for result in validations
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
