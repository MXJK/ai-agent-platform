"""Completion, execution-budget, and user-control policies for the Agent Loop."""

from __future__ import annotations

import inspect
from dataclasses import replace
from time import perf_counter
from typing import Any, Callable

from ai_agent_platform.agents.coding.formatting import format_error_answer
from ai_agent_platform.agents.coding.models import AgentRunStatus, CodingAgentState
from ai_agent_platform.agents.coding.planner import RuleBasedAgentPlanner
from ai_agent_platform.agents.coding.runtime_support import (
    append_errors as _append_errors,
    append_trace as _append_trace,
    error_from_exception as _error_from_exception,
)


ANSWER_EVENT_CHUNK_CHARS = 512


class ControlPolicy:
    """Consume queued steering and control actions at safe graph boundaries."""

    def __init__(self, run_store: Any) -> None:
        self._run_store = run_store

    def consume_steering(
        self,
        state: CodingAgentState,
        native_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        run_id = state.get("run_id")
        if not run_id:
            return native_messages
        try:
            record = self._run_store.get(run_id)
        except KeyError:
            return native_messages
        if not record.steering_messages:
            return native_messages
        updated_messages = list(native_messages)
        for message in record.steering_messages:
            updated_messages.append(
                {
                    "role": "user",
                    "content": "User steering for the active run: " + message,
                }
            )
        self._run_store.save(replace(record, steering_messages=[]))
        return updated_messages

    def consume_action(self, state: CodingAgentState) -> str:
        run_id = state.get("run_id")
        if not run_id:
            return ""
        try:
            record = self._run_store.get(run_id)
        except KeyError:
            return ""
        action = str(record.control_action or "")
        if action:
            self._run_store.save(replace(record, control_action=None))
        return action


class BudgetPolicy:
    """Keep hard and soft native tool-loop limits in one policy boundary."""

    def __init__(
        self,
        *,
        soft_tool_rounds: int,
        max_tool_rounds: int,
        soft_tool_calls: int,
        max_tool_calls: int,
        max_elapsed_seconds: int,
        no_progress_rounds: int,
        max_consecutive_failures: int,
    ) -> None:
        self.soft_tool_rounds = soft_tool_rounds
        self.max_tool_rounds = max_tool_rounds
        self.soft_tool_calls = soft_tool_calls
        self.max_tool_calls = max_tool_calls
        self.max_elapsed_seconds = max_elapsed_seconds
        self.no_progress_rounds = no_progress_rounds
        self.max_consecutive_failures = max_consecutive_failures

    def stop(self, state: CodingAgentState) -> tuple[str, AgentRunStatus]:
        if (
            state.get("native_consecutive_failures", 0)
            >= self.max_consecutive_failures
        ):
            return "max_consecutive_tool_failures", "blocked"
        if state.get("native_no_progress_rounds", 0) >= self.no_progress_rounds:
            return "no_progress", "partial"
        if (
            state.get("native_unfulfilled_change_rounds", 0)
            >= self.no_progress_rounds
        ):
            return "change_not_applied", "blocked"
        started_at = state.get("started_at")
        if isinstance(started_at, (int, float)) and (
            perf_counter() - started_at >= self.max_elapsed_seconds
        ):
            return "max_elapsed_time", "partial"
        if state.get("native_tool_round", 0) >= self.max_tool_rounds:
            return "hard_tool_round_budget", "partial"
        if state.get("native_tool_call_count", 0) >= self.max_tool_calls:
            return "hard_tool_call_budget", "partial"
        return "", "completed"

    def soft_limit_reached(self, state: CodingAgentState) -> bool:
        return (
            state.get("native_tool_round", 0) >= self.soft_tool_rounds
            or state.get("native_tool_call_count", 0) >= self.soft_tool_calls
        )


class CompletionPolicy:
    """Finalize native sessions and compose terminal graph answers."""

    def __init__(
        self,
        *,
        planner: Any,
        visible_tool_specs: Any,
        final_max_output_tokens: int,
    ) -> None:
        self._planner = planner
        self._visible_tool_specs = visible_tool_specs
        self._final_max_output_tokens = final_max_output_tokens
        self._event_sink: Callable[..., Any] | None = None

    def set_event_sink(self, event_sink: Callable[..., Any]) -> None:
        self._event_sink = event_sink

    def finalize_native_session(
        self,
        state: CodingAgentState,
        native_messages: list[dict[str, Any]],
        *,
        reason: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        try:
            finalize = getattr(self._planner, "finalize_tool_session", None)
            if callable(finalize):
                if "tool_specs" in inspect.signature(finalize).parameters:
                    kwargs: dict[str, Any] = {
                        "reason": reason,
                        "tool_specs": self._visible_tool_specs(state),
                    }
                    if "max_output_tokens" in inspect.signature(finalize).parameters:
                        kwargs["max_output_tokens"] = self._final_max_output_tokens
                    decision = finalize(native_messages, **kwargs)
                else:
                    decision = finalize(native_messages, reason=reason)
            else:
                decide = getattr(self._planner, "decide_tool_calls")
                decision = decide(
                    native_messages
                    + [
                        {
                            "role": "system",
                            "content": (
                                "Tools are disabled. Return the best final answer, "
                                "including incomplete work and the stopping reason: "
                                + reason
                            ),
                        }
                    ],
                    [],
                )
            if decision.tool_calls:
                raise ValueError("finalization returned tool calls")
            answer = str(decision.text or "").strip()
            if not answer:
                raise ValueError("finalization returned an empty answer")
            return answer, native_assistant_message(decision), []
        except Exception as exc:
            compose = getattr(self._planner, "compose_answer", None)
            answer = (
                str(compose(state)).strip()
                if callable(compose)
                else RuleBasedAgentPlanner().compose_answer(state)
            )
            if not answer:
                answer = (
                    "The Agent stopped before it could produce a complete answer. "
                    f"Stopping reason: {reason}."
                )
            return (
                answer,
                {"role": "assistant", "content": answer, "tool_calls": []},
                [
                    _error_from_exception(
                        "finalize_tool_session",
                        exc,
                        attempt=1,
                        max_attempts=1,
                    )
                ],
            )

    def compose_answer(self, state: CodingAgentState) -> CodingAgentState:
        run_id = str(state.get("run_id") or "")
        delta_index = 0

        def emit_delta(text: str) -> None:
            nonlocal delta_index
            if not text or not run_id or self._event_sink is None:
                return
            for offset in range(0, len(text), ANSWER_EVENT_CHUNK_CHARS):
                chunk = text[offset : offset + ANSWER_EVENT_CHUNK_CHARS]
                delta_index += 1
                self._event_sink(
                    run_id=run_id,
                    event_type="answer_delta",
                    node="compose_answer",
                    summary="Agent generated answer text.",
                    output={"text": chunk, "index": delta_index},
                    event_key=f"answer-delta:{delta_index}",
                )

        try:
            compose = getattr(self._planner, "compose_answer", None)
            native_answer = str(state.get("native_tool_answer") or "").strip()
            if native_answer:
                answer = native_answer
                emit_delta(answer)
            else:
                if callable(compose) and "on_delta" in inspect.signature(compose).parameters:
                    answer = compose(state, on_delta=emit_delta)
                else:
                    answer = (
                        compose(state)
                        if callable(compose)
                        else RuleBasedAgentPlanner().compose_answer(state)
                    )
                if answer and delta_index == 0:
                    emit_delta(str(answer))
            errors: list[dict[str, Any]] = []
        except Exception as exc:
            answer = ""
            errors = [
                _error_from_exception(
                    "compose_answer",
                    exc,
                    attempt=1,
                    max_attempts=1,
                )
            ]
        if answer and run_id and self._event_sink is not None:
            self._event_sink(
                run_id=run_id,
                event_type="answer_completed",
                node="compose_answer",
                summary="Agent answer generation completed.",
                output={"answer_chars": len(answer), "delta_count": delta_index},
                event_key="answer-completed",
            )
        return {
            "answer": answer,
            "errors": _append_errors(state, errors),
            "terminal_status": (
                "failed"
                if errors and not answer
                else state.get("terminal_status") or "completed"
            ),
            "terminal_reason": (
                "answer_composition_failed"
                if errors and not answer
                else state.get("terminal_reason") or "model_completed"
            ),
            "trace": _append_trace(
                state,
                node="compose_answer",
                summary="根据会话、项目指令、合并证据、测试和 Diff 生成回答。",
                output={
                    "answer_chars": len(answer),
                    "source_count": len(state.get("context_sources", [])),
                    "context_route": state.get("context_route", "repo"),
                },
            ),
        }

    @staticmethod
    def compose_error_answer(state: CodingAgentState) -> CodingAgentState:
        return {
            "answer": format_error_answer(state),
            "trace": _append_trace(
                state,
                node="compose_error_answer",
                summary="生成结构化错误回答。",
                output={},
            ),
        }


def native_assistant_message(decision: Any) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": str(decision.text or ""),
        "provider": str(decision.provider or ""),
        "provider_items": decision.provider_items or [],
        "tool_calls": [
            {
                "call_id": call.call_id,
                "name": call.name,
                "arguments": call.arguments,
            }
            for call in decision.tool_calls
        ],
    }


class AgentLoopPolicies:
    """Dependency bundle used by the graph-internal node collaborators."""

    def __init__(self, runtime: Any) -> None:
        self.control = ControlPolicy(runtime._run_store)
        self.budget = BudgetPolicy(
            soft_tool_rounds=runtime._soft_tool_rounds,
            max_tool_rounds=runtime._max_tool_rounds,
            soft_tool_calls=runtime._soft_tool_calls,
            max_tool_calls=runtime._max_tool_calls,
            max_elapsed_seconds=runtime._max_elapsed_seconds,
            no_progress_rounds=runtime._no_progress_rounds,
            max_consecutive_failures=runtime._max_consecutive_failures,
        )
        self.completion = CompletionPolicy(
            planner=runtime._planner,
            visible_tool_specs=runtime._visible_tool_specs,
            final_max_output_tokens=runtime._final_max_output_tokens,
        )
