"""Completion, execution-budget, and user-control policies for the Agent Loop."""

from __future__ import annotations

import inspect
from dataclasses import replace
from time import perf_counter
from typing import Any, Callable

from ai_agent_platform.agents.coding.formatting import format_error_answer
from ai_agent_platform.agents.coding.completion_contract import (
    completion_contract_state,
)
from ai_agent_platform.agents.coding.models import AgentRunStatus, CodingAgentState
from ai_agent_platform.agents.coding.planner import RuleBasedAgentPlanner
from ai_agent_platform.agents.coding.runtime_support import (
    append_errors as _append_errors,
    append_trace as _append_trace,
    error_from_exception as _error_from_exception,
)
from ai_agent_platform.agents.coding.task_shaping import (
    change_validation_state,
    evidence_contract_satisfied,
    task_budget,
)
from ai_agent_platform.integrations.llm import contains_tool_protocol_text


ANSWER_EVENT_CHUNK_CHARS = 512

def _looks_like_tool_call(text: str) -> bool:
    """Detect a model answer that is actually an unexecuted tool-call block.

    Some models emit their native tool-call markup as message content instead
    of a provider ``tool_calls`` array. Treating such text as a terminal answer
    leaks the raw block to the user, so answers shaped this way are replaced by
    the deterministic grounded renderer.
    """
    return contains_tool_protocol_text(str(text or ""))


class AnswerEventStream:
    """One tentative answer; reset boundaries survive reconnects and node retries."""

    def __init__(
        self,
        run_id: str,
        sink: Callable[..., Any] | None,
        *,
        stream_id: str,
    ) -> None:
        self.run_id = run_id
        self.sink = sink
        self.stream_id = stream_id
        self.parts: list[str] = []
        self.pending = ""
        self.delta_count = 0
        self.last_flush = perf_counter()

    def _event(self, kind: str, output: dict[str, Any], key: str) -> None:
        if self.run_id and self.sink is not None:
            self.sink(
                run_id=self.run_id,
                event_type=kind,
                node="compose_answer",
                summary="Agent answer text updated.",
                output=output,
                event_key=f"answer:{self.stream_id}:{key}",
            )

    def emit(self, text: str) -> None:
        if not text:
            return
        self.parts.append(text)
        self.pending += text
        if (
            not self.delta_count
            or len(self.pending) >= 128
            or perf_counter() - self.last_flush >= 0.1
        ):
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        if not self.delta_count:
            self._event("answer_reset", {}, "start")
        for offset in range(0, len(self.pending), ANSWER_EVENT_CHUNK_CHARS):
            self.delta_count += 1
            self._event(
                "answer_delta",
                {
                    "text": self.pending[
                        offset : offset + ANSWER_EVENT_CHUNK_CHARS
                    ],
                    "index": self.delta_count,
                },
                str(self.delta_count),
            )
        self.pending = ""
        self.last_flush = perf_counter()

    def discard(self) -> None:
        self.pending = ""
        if self.delta_count:
            self._event("answer_reset", {}, "discard")


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

    def consume_compaction(self, state: CodingAgentState) -> dict[str, Any] | None:
        run_id = state.get("run_id")
        if not run_id:
            return None
        try:
            record = self._run_store.get(run_id)
        except KeyError:
            return None
        request = record.pending_compaction
        if request is not None:
            self._run_store.save(replace(record, pending_compaction=None))
        return dict(request) if isinstance(request, dict) else None


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
        contract_state = completion_contract_state(state)
        if contract_state == "invalid":
            return "completion_contract_unavailable", "partial"
        if (
            contract_state == "unresolved"
            and state.get("completion_unresolved_rounds", 0)
            >= self.no_progress_rounds
            and change_validation_state(state) not in {"missing", "failed"}
        ):
            return "completion_requirements_unresolved", "partial"
        validation_state = change_validation_state(state)
        validation_incomplete = validation_state in {"missing", "failed"}
        if (
            validation_incomplete
            and state.get("validation_missing_rounds", 0) >= self.no_progress_rounds
        ):
            return f"validation_{validation_state}", "partial"
        started_at = state.get("started_at")
        elapsed_budget_reached = isinstance(started_at, (int, float)) and (
            perf_counter() - started_at >= self.max_elapsed_seconds
        )
        max_model_requests = task_budget(
            state, "max_model_requests", self.max_tool_rounds + 2
        )
        hard_budget_reached = (
            elapsed_budget_reached
            or state.get("task_model_request_count", 0)
            >= max(0, max_model_requests - 1)
            or state.get("native_tool_round", 0)
            >= task_budget(state, "max_tool_rounds", self.max_tool_rounds)
            or state.get("native_tool_call_count", 0)
            >= task_budget(state, "max_tool_calls", self.max_tool_calls)
        )
        if validation_incomplete and hard_budget_reached:
            return f"validation_{validation_state}", "partial"
        if (
            contract_state == "unresolved"
            and hard_budget_reached
        ):
            return "completion_requirements_unresolved", "partial"
        if (
            contract_state == "satisfied"
            and validation_state == "passed"
            and evidence_contract_satisfied(state)
        ):
            return "completion_contract_satisfied", "completed"
        if contract_state == "unresolved":
            return "", "completed"
        if validation_state == "passed" and evidence_contract_satisfied(state):
            return "evidence_contract_satisfied", "completed"
        if validation_incomplete:
            return "", "completed"
        if evidence_contract_satisfied(state):
            return "evidence_contract_satisfied", "completed"
        if (
            state.get("evidence_contract")
            and state.get("evidence_rounds_completed", 0) >= 1
            and state.get("new_evidence_count", 0) <= 0
        ):
            return "no_new_evidence", "partial"
        if (
            not state.get("evidence_contract")
            and state.get("native_no_progress_rounds", 0) >= self.no_progress_rounds
        ):
            return "no_progress", "partial"
        if (
            state.get("native_unfulfilled_change_rounds", 0)
            >= self.no_progress_rounds
        ):
            return "change_not_applied", "blocked"
        if elapsed_budget_reached:
            return "max_elapsed_time", "partial"
        if state.get("task_model_request_count", 0) >= max(0, max_model_requests - 1):
            return "hard_model_request_budget", "partial"
        if state.get("native_tool_round", 0) >= task_budget(
            state, "max_tool_rounds", self.max_tool_rounds
        ):
            return "hard_tool_round_budget", "partial"
        if state.get("native_tool_call_count", 0) >= task_budget(
            state, "max_tool_calls", self.max_tool_calls
        ):
            return "hard_tool_call_budget", "partial"
        return "", "completed"

    def soft_limit_reached(self, state: CodingAgentState) -> bool:
        return (
            state.get("native_tool_round", 0)
            >= task_budget(state, "soft_tool_rounds", self.soft_tool_rounds)
            or state.get("native_tool_call_count", 0)
            >= task_budget(state, "soft_tool_calls", self.soft_tool_calls)
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

    def stream_decision(
        self,
        state: CodingAgentState,
        decide: Callable[..., Any],
        *args: Any,
        allow_text: bool = True,
        stream_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        if (
            not allow_text or state.get("evaluation_isolated", False)
            or "on_delta" not in inspect.signature(decide).parameters
        ):
            decision = decide(*args, **kwargs)
            if not decision.tool_calls and _looks_like_tool_call(decision.text):
                raise ValueError("model returned tool protocol as public answer text")
            return decision
        stream = AnswerEventStream(
            str(state.get("run_id") or ""),
            self._event_sink,
            stream_id=stream_id or f"turn:{state.get('native_tool_round', 0) + 1}",
        )
        try:
            decision = decide(*args, on_delta=stream.emit, **kwargs)
            if not decision.tool_calls and _looks_like_tool_call(decision.text):
                stream.discard()
                raise ValueError("model returned tool protocol as public answer text")
            if (
                decision.tool_calls
                or "".join(stream.parts).strip() != decision.text.strip()
            ):
                stream.discard()
                return decision
            stream.flush()
            return replace(decision, answer_delta_count=stream.delta_count)
        except Exception as exc:
            stream.discard()
            if stream.delta_count and hasattr(exc, "retryable"):
                exc.retryable = False
            raise

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
                        # Final-answer requests are a separate, text-only phase.
                        "tool_specs": [],
                    }
                    finalize_parameters = inspect.signature(finalize).parameters
                    if "use_model_max_output_tokens" in finalize_parameters:
                        kwargs["use_model_max_output_tokens"] = True
                    elif "max_output_tokens" in finalize_parameters:
                        kwargs["max_output_tokens"] = self._final_max_output_tokens
                    decision = self.stream_decision(
                        state,
                        finalize,
                        native_messages,
                        stream_id=(
                            f"finalize:{reason}:{state.get('native_tool_round', 0)}"
                        ),
                        **kwargs,
                    )
                else:
                    decision = self.stream_decision(
                        state,
                        finalize,
                        native_messages,
                        reason=reason,
                        stream_id=(
                            f"finalize:{reason}:{state.get('native_tool_round', 0)}"
                        ),
                    )
            else:
                decide = getattr(self._planner, "decide_tool_calls")
                decision = self.stream_decision(
                    state, decide,
                    native_messages,
                    [],
                    stream_id=(
                        f"finalize:{reason}:{state.get('native_tool_round', 0)}"
                    ),
                )
            if decision.tool_calls:
                raise ValueError("finalization returned tool calls")
            answer = str(decision.text or "").strip()
            if _looks_like_tool_call(answer):
                raise ValueError("finalization returned tool-call-shaped text")
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
        stream = AnswerEventStream(
            run_id,
            self._event_sink,
            stream_id=f"compose:{state.get('native_tool_round', 0)}",
        )
        prior_delta_count = 0

        try:
            compose = getattr(self._planner, "compose_answer", None)
            native_answer = str(state.get("native_tool_answer") or "").strip()
            if native_answer:
                answer = native_answer
                last_assistant = next((
                    message for message in reversed(state.get("native_tool_messages", []))
                    if message.get("role") == "assistant"
                ), {})
                if str(last_assistant.get("content") or "").strip() == answer:
                    prior_delta_count = int(last_assistant.get("answer_delta_count") or 0)
                if not prior_delta_count:
                    stream.emit(answer)
            else:
                if callable(compose) and "on_delta" in inspect.signature(compose).parameters:
                    answer = compose(state, on_delta=stream.emit)
                else:
                    answer = (
                        compose(state)
                        if callable(compose)
                        else RuleBasedAgentPlanner().compose_answer(state)
                    )
                if answer and not stream.parts:
                    stream.emit(str(answer))
            if _looks_like_tool_call(answer):
                stream.discard()
                answer = str(RuleBasedAgentPlanner().compose_answer(state)).strip()
                if answer:
                    stream.emit(answer)
            stream.flush()
            errors: list[dict[str, Any]] = []
        except Exception as exc:
            stream.discard()
            answer = ""
            errors = [
                _error_from_exception(
                    "compose_answer",
                    exc,
                    attempt=1,
                    max_attempts=1,
                )
            ]
        composed_answer = str(answer or "")
        validation_state = change_validation_state(state)
        terminal_status = (
            "failed"
            if errors and not answer
            else state.get("terminal_status") or "completed"
        )
        terminal_reason = (
            "answer_composition_failed"
            if errors and not answer
            else state.get("terminal_reason") or "model_completed"
        )
        change_status = state.get("change_status", "not_requested")
        contract_state = completion_contract_state(state)
        if contract_state == "invalid":
            terminal_status = "partial"
            terminal_reason = "completion_contract_unavailable"
            contract = state.get("change_completion_contract", {})
            answer = (
                "The bounded change did not run because a reliable "
                "ChangeCompletionContract could not be frozen. Required input: "
                + str(contract.get("generation_error") or "an explicit workspace target")
            )
        elif contract_state == "unresolved" and (
            state.get("change_completion_contract", {}).get("unresolved_changes")
            or validation_state not in {"missing", "failed"}
        ):
            terminal_status = "partial"
            terminal_reason = "completion_requirements_unresolved"
            contract = state.get("change_completion_contract", {})
            unresolved_changes = [
                f"{item.get('operation')} {item.get('target')}"
                for item in contract.get("required_changes", [])
                if item.get("status") != "satisfied"
            ]
            unresolved_validations = [
                f"{item.get('category')} ({item.get('target')})"
                for item in contract.get("required_validations", [])
                if item.get("status") != "satisfied"
            ]
            answer = (
                "This Run is partial because the frozen completion contract is "
                "not satisfied. Unresolved changes: "
                + (", ".join(unresolved_changes) or "none")
                + ". Unresolved validations: "
                + (", ".join(unresolved_validations) or "none")
                + ". The completed items, failed validation evidence, workspace "
                "status, and Diff were preserved."
            )
        elif validation_state in {"missing", "failed"}:
            if terminal_status == "completed":
                terminal_status = "partial"
                terminal_reason = f"validation_{validation_state}"
            if change_status not in {"repair_rejected", "execution_failed"}:
                change_status = (
                    "changes_ready"
                    if validation_state == "missing"
                    else "validation_failed"
                )
            changed_files = list(state.get("changed_files", []))
            file_summary = ", ".join(changed_files) if changed_files else "the workspace"
            if validation_state == "missing":
                answer = (
                    f"Workspace changes were produced for {file_summary}, but no "
                    "post-change validation command completed. This Run is not "
                    "completed; review the diff and run an appropriate validation."
                )
            else:
                answer = (
                    f"Workspace changes were produced for {file_summary}, but "
                    "post-change validation failed. This Run is not completed; the "
                    "failed validation evidence and diff were preserved."
                )
                feedback = str(
                    (state.get("repair_review_decision") or {}).get("feedback") or ""
                ).strip()
                if feedback:
                    answer += f" Repair stopped after reviewer feedback: {feedback}"
        if str(answer or "") != composed_answer:
            stream.discard()
            prior_delta_count = 0
            stream.emit(str(answer or ""))
            stream.flush()
        if answer and run_id and self._event_sink is not None:
            self._event_sink(
                run_id=run_id,
                event_type="answer_completed",
                node="compose_answer",
                summary="Agent answer generation completed.",
                output={
                    "answer_chars": len(answer),
                    "delta_count": prior_delta_count or stream.delta_count,
                },
                event_key="answer-completed",
            )
        return {
            "answer": answer,
            "errors": _append_errors(state, errors),
            "terminal_status": terminal_status,
            "terminal_reason": terminal_reason,
            "change_status": change_status,
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
        **(
            {"answer_delta_count": decision.answer_delta_count}
            if getattr(decision, "answer_delta_count", 0)
            else {}
        ),
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
