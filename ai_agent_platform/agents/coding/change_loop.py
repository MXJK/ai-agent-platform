"""Tool partitioning and artifact helpers for sandboxed code-change runs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from langgraph.types import interrupt

from ai_agent_platform.agents.coding.models import (
    AgentRunStore,
    AgentToolExecution,
    CodingAgentState,
)
from ai_agent_platform.integrations.tools import (
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolSpec,
    summarize_tool_arguments,
)


MAX_CHANGE_ITERATIONS = 2
SANDBOX_MUTATION_TOOLS = {"sandbox.apply_patch", "sandbox.write_file"}
SANDBOX_VALIDATION_TOOLS = {"sandbox.run_command"}
SANDBOX_ARTIFACT_TOOLS = {"sandbox.git_diff", "sandbox.workspace_status"}
SANDBOX_LIFECYCLE_TOOLS = (
    SANDBOX_MUTATION_TOOLS | SANDBOX_VALIDATION_TOOLS | SANDBOX_ARTIFACT_TOOLS
)


class ChangeLoopExecutor:
    """Executes approved Sandbox changes, validation, repair, and artifacts."""

    def __init__(
        self,
        *,
        tools: ToolRegistry,
        planner: object,
        run_store: AgentRunStore | None = None,
    ) -> None:
        self._tools = tools
        self._planner = planner
        self._run_store = run_store

    def execute_changes(self, state: CodingAgentState) -> CodingAgentState:
        iteration = state.get("change_iteration", 0)
        repair_calls = state.get("repair_tool_calls", [])
        change_calls = repair_calls or (
            state.get("change_tool_calls", []) if iteration == 0 else []
        )
        results = self.execute_tool_calls(state, change_calls)
        next_iteration = iteration + 1 if change_calls else iteration
        return {
            "tool_results": list(state.get("tool_results", [])) + results,
            "repair_tool_calls": [],
            "change_iteration": next_iteration,
            "trace": _append_trace(
                state,
                node="execute_changes",
                summary=(
                    "在每个 run 独立的 Sandbox 工作区中执行已批准代码修改。"
                ),
                output={
                    "iteration": next_iteration,
                    "called_tools": [item["name"] for item in results],
                    "success_count": sum(1 for item in results if item["ok"]),
                    "failure_count": sum(1 for item in results if not item["ok"]),
                },
            ),
        }

    def validate_changes(self, state: CodingAgentState) -> CodingAgentState:
        validation_calls = [
            ToolCall(
                name=call.name,
                arguments=call.arguments,
                source=f"{call.source}:iteration-{state.get('change_iteration', 0)}",
            )
            for call in state.get("validation_tool_calls", [])
        ]
        validation_results = self.execute_tool_calls(
            state,
            validation_calls,
        )
        validation_history = list(state.get("validation_history", []))
        validation_history.append(
            {
                "iteration": state.get("change_iteration", 0),
                "results": validation_results,
            }
        )
        passed = bool(validation_results) and all(
            is_validation_success(result) for result in validation_results
        )
        repair_calls: list[ToolCall] = []
        if not passed and state.get("change_iteration", 0) < MAX_CHANGE_ITERATIONS:
            repair_state = dict(state)
            repair_state["validation_results"] = validation_results
            repair_state["validation_history"] = validation_history
            plan_repair = getattr(self._planner, "plan_repair_tool_calls", None)
            if callable(plan_repair):
                repair_calls = [
                    call
                    for call in plan_repair(repair_state, self._tools.list_specs())
                    if call.name in SANDBOX_MUTATION_TOOLS
                ]

        approval_required_tools = _approval_required_tools(
            repair_calls,
            self._tools.list_specs(),
        )
        return {
            "tool_calls": list(state.get("tool_calls", [])) + repair_calls,
            "tool_results": list(state.get("tool_results", []))
            + validation_results,
            "validation_results": validation_results,
            "validation_history": validation_history,
            "repair_tool_calls": repair_calls,
            "approval_required_tools": approval_required_tools,
            "trace": _append_trace(
                state,
                node="validate_changes",
                summary="执行 Sandbox 验证命令，并在失败时规划一次受限修复。",
                output={
                    "iteration": state.get("change_iteration", 0),
                    "passed": passed,
                    "command_count": len(validation_results),
                    "failed_commands": [
                        item["name"]
                        for item in validation_results
                        if not is_validation_success(item)
                    ],
                    "repair_planned_tools": [call.name for call in repair_calls],
                    "max_iterations": MAX_CHANGE_ITERATIONS,
                },
            ),
        }

    def review_repair_plan(self, state: CodingAgentState) -> CodingAgentState:
        decision = interrupt(_build_repair_approval_request(state))
        if isinstance(decision, dict):
            approved = bool(decision.get("approved"))
            feedback = str(decision.get("feedback") or "")
        else:
            approved = bool(decision)
            feedback = ""
        review_decision = {"approved": approved, "feedback": feedback}
        return {
            "repair_review_decision": review_decision,
            "trace": _append_trace(
                state,
                node="review_repair_plan",
                summary=(
                    "测试失败后再次请求人工批准修复补丁，避免静默连续写代码。"
                ),
                output=review_decision,
            ),
        }

    def collect_artifacts(self, state: CodingAgentState) -> CodingAgentState:
        artifact_results = self.execute_tool_calls(
            state,
            [
                ToolCall(name="sandbox.workspace_status", arguments={}),
                ToolCall(name="sandbox.git_diff", arguments={"max_chars": 20000}),
            ],
        )
        status_result, diff_result = artifact_results
        status_output = status_result.get("result")
        status_output = status_output if isinstance(status_output, dict) else {}
        diff_output = diff_result.get("result")
        diff_output = diff_output if isinstance(diff_output, dict) else {}
        changed_files = list(
            diff_output.get("changed_files")
            or status_output.get("changed_files")
            or []
        )
        repair_decision = state.get("repair_review_decision", {})
        execution_failed = any(
            result.get("name") in SANDBOX_MUTATION_TOOLS
            and not result.get("ok")
            for result in state.get("tool_results", [])
        )
        final_status = change_status(
            changed_files=changed_files,
            validation_results=state.get("validation_results", []),
            repair_rejected=bool(repair_decision)
            and not repair_decision.get("approved"),
            execution_failed=execution_failed,
        )
        artifacts = build_change_artifacts(
            validation_results=state.get("validation_results", []),
            diff_result=diff_result,
        )
        return {
            "tool_results": list(state.get("tool_results", []))
            + artifact_results,
            "artifacts": artifacts,
            "changed_files": changed_files,
            "change_status": final_status,
            "trace": _append_trace(
                state,
                node="collect_artifacts",
                summary=(
                    "汇总变更文件、验证报告和最终 Diff，供人工审查与后续应用。"
                ),
                output={
                    "status": final_status,
                    "changed_files": changed_files,
                    "artifact_types": [item["type"] for item in artifacts],
                    "diff_truncated": bool(diff_output.get("truncated", False)),
                },
            ),
        }

    def execute_tool_calls(
        self,
        state: CodingAgentState,
        tool_calls: list[ToolCall],
    ) -> list[dict[str, Any]]:
        context = ToolExecutionContext(
            conversation_id=state["conversation_id"],
            workspace_id=state["workspace_id"],
            workspace_root=state["workspace_root"],
            run_id=state.get("run_id"),
        )
        return [self._execute_tool_call(tool_call, context) for tool_call in tool_calls]

    def _execute_tool_call(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        run_id = context.run_id
        arguments_hash = hashlib.sha256(
            json.dumps(
                tool_call.arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        get_execution = getattr(self._run_store, "get_tool_execution", None)
        save_execution = getattr(self._run_store, "save_tool_execution", None)
        if run_id and callable(get_execution):
            previous = get_execution(run_id, tool_call.call_id)
            if previous is not None:
                if (
                    previous.name != tool_call.name
                    or previous.arguments_hash != arguments_hash
                ):
                    return {
                        "call_id": tool_call.call_id,
                        "name": tool_call.name,
                        "ok": False,
                        "error": "call_id was reused with different arguments",
                        "error_code": "tool_call_identity_conflict",
                        "cached": True,
                    }
                if previous.status == "completed" and previous.response is not None:
                    cached = dict(previous.response)
                    cached["cached"] = True
                    cached["durable_replay"] = True
                    return cached
                return {
                    "call_id": tool_call.call_id,
                    "name": tool_call.name,
                    "ok": False,
                    "error": "tool call has an unfinished durable execution record",
                    "error_code": "tool_execution_in_progress",
                    "cached": True,
                }
        if run_id and callable(save_execution):
            save_execution(
                AgentToolExecution(
                    run_id=run_id,
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    arguments_hash=arguments_hash,
                    status="started",
                )
            )
        response = self._tools.execute(tool_call, context=context).to_response()
        if run_id and callable(save_execution):
            save_execution(
                AgentToolExecution(
                    run_id=run_id,
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    arguments_hash=arguments_hash,
                    status="completed",
                    response=response,
                )
            )
        return response


def partition_tool_calls(
    tool_calls: list[ToolCall],
) -> tuple[list[ToolCall], list[ToolCall], list[ToolCall]]:
    analysis_calls: list[ToolCall] = []
    change_calls: list[ToolCall] = []
    validation_calls: list[ToolCall] = []
    for tool_call in tool_calls:
        if tool_call.name in SANDBOX_MUTATION_TOOLS:
            change_calls.append(tool_call)
        elif tool_call.name in SANDBOX_VALIDATION_TOOLS:
            validation_calls.append(tool_call)
        elif tool_call.name not in SANDBOX_ARTIFACT_TOOLS:
            analysis_calls.append(tool_call)
    return analysis_calls, change_calls, validation_calls


def is_validation_success(result: dict[str, Any]) -> bool:
    if not result.get("ok"):
        return False
    output = result.get("result")
    return isinstance(output, dict) and output.get("exit_code") == 0


def build_change_artifacts(
    *,
    validation_results: list[dict[str, Any]],
    diff_result: dict[str, Any],
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for result in validation_results:
        output = result.get("result") if result.get("ok") else None
        output = output if isinstance(output, dict) else {}
        artifacts.append(
            {
                "type": "test_report",
                "name": result.get("name", "sandbox.run_command"),
                "status": "passed" if is_validation_success(result) else "failed",
                "command": output.get("command", []),
                "exit_code": output.get("exit_code"),
                "stdout": output.get(
                    "stdout",
                    output.get("truncated_output_preview", ""),
                ),
                "stderr": output.get("stderr", result.get("error", "")),
                "duration_ms": result.get("duration_ms", 0),
                "output_truncated": bool(result.get("output_truncated", False)),
            }
        )

    diff_output = diff_result.get("result") if diff_result.get("ok") else None
    diff_output = diff_output if isinstance(diff_output, dict) else {}
    artifacts.append(
        {
            "type": "code_diff",
            "status": "ready" if diff_result.get("ok") else "failed",
            "changed_files": diff_output.get("changed_files", []),
            "diff": diff_output.get("diff", ""),
            "truncated": bool(diff_output.get("truncated", False)),
            "error": diff_result.get("error"),
        }
    )
    return artifacts


def change_status(
    *,
    changed_files: list[str],
    validation_results: list[dict[str, Any]],
    repair_rejected: bool,
    execution_failed: bool,
) -> str:
    if execution_failed:
        return "execution_failed"
    if repair_rejected:
        return "repair_rejected"
    if validation_results and not all(
        is_validation_success(result) for result in validation_results
    ):
        return "validation_failed"
    if validation_results:
        return "validated"
    if changed_files:
        return "changes_ready"
    return "no_changes"


def _approval_required_tools(
    tool_calls: list[ToolCall],
    tool_specs: list[ToolSpec],
) -> list[dict[str, Any]]:
    specs = {spec.name: spec for spec in tool_specs}
    approval_tools: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        spec = specs.get(tool_call.name)
        if spec is None:
            continue
        if spec.requires_approval or spec.permission_level != "read_only":
            approval_tools.append(
                {
                    "name": tool_call.name,
                    "provider": spec.provider,
                    "permission_level": spec.permission_level,
                    "requires_approval": spec.requires_approval,
                    "risk_summary": spec.risk_summary,
                    "arguments_summary": summarize_tool_arguments(
                        tool_call.arguments
                    ),
                }
            )
    return approval_tools


def _build_repair_approval_request(state: CodingAgentState) -> dict[str, Any]:
    repair_calls = state.get("repair_tool_calls", [])
    return {
        "type": "repair_plan_review",
        "approval_required": True,
        "reason": "validation failed and the agent proposed another sandbox mutation",
        "intent": state.get("intent", "bug_investigation"),
        "workspace_id": state["workspace_id"],
        "message": state["user_input"],
        "iteration": state.get("change_iteration", 0) + 1,
        "planned_tools": [call.name for call in repair_calls],
        "approval_required_tools": state.get("approval_required_tools", []),
        "tool_calls": [
            {"name": call.name, "arguments": call.arguments}
            for call in repair_calls
        ],
    }


def _append_trace(
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
