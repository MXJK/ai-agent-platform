"""Tool partitioning and artifact helpers for sandboxed code-change runs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from langgraph.types import interrupt

from ai_agent_platform.agents.coding.models import (
    AgentRunStore,
    AgentToolExecution,
    CodingAgentState,
)
from ai_agent_platform.agents.coding.completion_contract import (
    advance_completion_contract,
    completion_contract_state,
)
from ai_agent_platform.agents.coding.tool_access import ToolAccessCoordinator
from ai_agent_platform.agents.coding.task_shaping import run_is_unbounded
from ai_agent_platform.integrations.tools import (
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolSpec,
    summarize_tool_arguments,
)
from ai_agent_platform.integrations.permissions import (
    PermissionDecision,
    ToolUseContext,
    canonical_arguments_hash,
)


MAX_CHANGE_ITERATIONS = 2
MAX_VALIDATION_MISSING_ROUNDS = 2
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
        pool_provider: Callable[[CodingAgentState], Any] | None = None,
        context_provider: Callable[[CodingAgentState], ToolUseContext] | None = None,
    ) -> None:
        self._tools = tools
        self._planner = planner
        self._run_store = run_store
        fallback_access = ToolAccessCoordinator(
            tools=tools,
            default_approval_policy="on_request",
        )
        self._pool_provider = pool_provider or fallback_access.tools_for_state
        self._context_provider = (
            context_provider or fallback_access.tool_use_context
        )
        self._event_sink: Callable[..., Any] | None = None

    def set_event_sink(self, event_sink: Callable[..., Any]) -> None:
        self._event_sink = event_sink

    def _emit_tool_event(
        self,
        *,
        run_id: str,
        event_type: str,
        tool_call: ToolCall,
        output: dict[str, Any],
        event_key: str,
    ) -> None:
        if not run_id or self._event_sink is None:
            return
        self._event_sink(
            run_id=run_id,
            event_type=event_type,
            node=None,
            summary=(
                f"Tool started: {tool_call.name}."
                if event_type == "tool_started"
                else f"Tool completed: {tool_call.name}."
                if event_type == "tool_result"
                else "A workspace mutation was applied."
                if event_type == "mutation_applied"
                else "A post-change validation passed."
                if event_type == "validation_passed"
                else "A post-change validation failed."
                if event_type == "validation_failed"
                else f"Tool failed: {tool_call.name}."
            ),
            output=output,
            event_key=event_key,
        )

    def execute_changes(self, state: CodingAgentState) -> CodingAgentState:
        iteration = state.get("change_iteration", 0)
        repair_calls = state.get("repair_tool_calls", [])
        change_calls = repair_calls or (
            state.get("change_tool_calls", []) if iteration == 0 else []
        )
        results = self.execute_tool_calls(state, change_calls)
        contract = advance_completion_contract(state, results=results)
        next_iteration = iteration + 1 if change_calls else iteration
        return {
            "tool_results": list(state.get("tool_results", [])) + results,
            "repair_tool_calls": [],
            "repair_approval_tool_calls": [],
            "change_iteration": next_iteration,
            "change_completion_contract": contract,
            "completion_contract_satisfied": bool(
                contract.get("completion_contract_satisfied")
            ),
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
        tools = self._tools_for_state(state)
        validation_calls = [
            ToolCall(
                name=call.name,
                arguments=call.arguments,
                call_id=call.call_id,
                source=f"{call.source}:iteration-{state.get('change_iteration', 0)}",
            )
            for call in state.get("validation_tool_calls", [])
        ]
        validation_results = self.execute_tool_calls(
            state,
            validation_calls,
        )
        contract = advance_completion_contract(state, results=validation_results)
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
        next_validation_calls: list[ToolCall] = []
        if not passed and (
            run_is_unbounded(state)
            or state.get("change_iteration", 0) < MAX_CHANGE_ITERATIONS
        ):
            repair_state = dict(state)
            repair_state["validation_results"] = validation_results
            repair_state["validation_history"] = validation_history
            plan_repair = getattr(self._planner, "plan_repair_tool_calls", None)
            if callable(plan_repair):
                repair_calls = [
                    call
                    for call in plan_repair(repair_state, tools.list_specs())
                    if call.name in SANDBOX_MUTATION_TOOLS
                ]

            next_validation_calls = [
                ToolCall(
                    name=call.name,
                    arguments=call.arguments,
                    call_id=(
                        f"{call.call_id}:iteration-"
                        f"{state.get('change_iteration', 0) + 1}"
                    ),
                    source=(
                        f"{call.source}:iteration-"
                        f"{state.get('change_iteration', 0) + 1}"
                    ),
                )
                for call in state.get("validation_tool_calls", [])
            ]

        permission_context = self._tool_use_context(state)
        repair_approval_calls = repair_calls + next_validation_calls
        repair_decisions = [
            (
                call,
                tools.resolve_permission(call, permission_context, phase="plan"),
            )
            for call in repair_approval_calls
        ]
        allowed_repair_call_ids = {
            call.call_id
            for call, decision in repair_decisions
            if decision.effect != "deny"
        }
        repair_calls = [
            call for call in repair_calls if call.call_id in allowed_repair_call_ids
        ]
        next_validation_calls = [
            call
            for call in next_validation_calls
            if call.call_id in allowed_repair_call_ids
        ]
        approval_required_tools = [
            _permission_approval_item(
                call,
                decision,
                tools.list_specs(),
                run_id=state.get("run_id", ""),
            )
            for call, decision in repair_decisions
            if decision.effect == "ask"
        ]
        return {
            "tool_calls": list(state.get("tool_calls", [])) + repair_calls,
            "tool_results": list(state.get("tool_results", []))
            + validation_results,
            "validation_results": validation_results,
            "validation_history": validation_history,
            "repair_tool_calls": repair_calls,
            "repair_approval_tool_calls": repair_calls + next_validation_calls,
            "validation_tool_calls": (
                next_validation_calls
                if next_validation_calls
                else state.get("validation_tool_calls", [])
            ),
            "approval_required_tools": approval_required_tools,
            "change_completion_contract": contract,
            "completion_contract_satisfied": bool(
                contract.get("completion_contract_satisfied")
            ),
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
                    "max_iterations": (
                        None if run_is_unbounded(state) else MAX_CHANGE_ITERATIONS
                    ),
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
        approved_by = (
            str(decision.get("approved_by") or "")
            if isinstance(decision, dict)
            else ""
        ) or state.get("actor_user_id", "")
        approvals = list(state.get("tool_approvals", []))
        if approved:
            required_call_ids = {
                str(item.get("call_id") or "")
                for item in state.get("approval_required_tools", [])
                if isinstance(item, dict)
            }
            try:
                tools = self._tools_for_state(state)
                permission_context = self._tool_use_context(state)
                for call in state.get(
                    "repair_approval_tool_calls",
                    state.get("repair_tool_calls", []),
                ):
                    if call.call_id not in required_call_ids:
                        continue
                    approvals.append(
                        tools.issue_approval(
                            call,
                            permission_context,
                            approved_by=approved_by,
                        ).to_dict()
                    )
            except PermissionError as exc:
                approved = False
                feedback = str(exc)
        review_decision = {"approved": approved, "feedback": feedback}
        return {
            "repair_review_decision": review_decision,
            "tool_approvals": approvals,
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
        contract = advance_completion_contract(
            state,
            results=artifact_results,
            changed_files=changed_files,
            final_workspace_status_collected=bool(status_result.get("ok")),
            final_diff_collected=bool(diff_result.get("ok")),
            diff_text=str(diff_output.get("diff") or ""),
        )
        if (
            contract.get("completion_contract_satisfied")
            and state.get("run_id")
            and self._event_sink is not None
        ):
            self._event_sink(
                run_id=state["run_id"],
                event_type="completion_contract_satisfied",
                node="collect_artifacts",
                summary="The ChangeCompletionContract is satisfied.",
                output={
                    "revision": contract.get("revision", 0),
                    "completion_contract_satisfied": True,
                },
                event_key="completion-contract-satisfied",
            )
        native_loop = bool(state.get("native_tool_loop_active"))
        validation_missing_rounds = state.get("validation_missing_rounds", 0)
        terminal_status = state.get("terminal_status", "")
        terminal_reason = state.get("terminal_reason", "")
        if final_status == "changes_ready":
            validation_missing_rounds += 1
            if (
                not native_loop
                and validation_missing_rounds >= MAX_VALIDATION_MISSING_ROUNDS
            ):
                terminal_status = "partial"
                terminal_reason = "validation_missing"
        elif final_status == "validation_failed" and not native_loop:
            terminal_status = "partial"
            terminal_reason = "validation_failed"
        elif final_status in {"repair_rejected", "execution_failed"}:
            terminal_status = "blocked"
            terminal_reason = final_status
        elif (
            final_status == "validated"
            and completion_contract_state(
                {**state, "change_completion_contract": contract}
            ) in {"satisfied", "legacy_phase1"}
            and not native_loop
        ):
            terminal_status = terminal_status or "completed"
            terminal_reason = terminal_reason or (
                "completion_contract_satisfied"
                if contract.get("completion_contract_satisfied")
                else "validation_passed"
            )
        return {
            "tool_results": list(state.get("tool_results", []))
            + artifact_results,
            "artifacts": artifacts,
            "changed_files": changed_files,
            "change_status": final_status,
            "validation_missing_rounds": validation_missing_rounds,
            "terminal_status": terminal_status,
            "terminal_reason": terminal_reason,
            "change_completion_contract": contract,
            "completion_contract_satisfied": bool(
                contract.get("completion_contract_satisfied")
            ),
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
                    "completion_contract_satisfied": bool(
                        contract.get("completion_contract_satisfied")
                    ),
                    "unresolved_changes": contract.get("unresolved_changes", []),
                    "unresolved_validations": contract.get(
                        "unresolved_validations", []
                    ),
                },
            ),
        }

    def execute_tool_calls(
        self,
        state: CodingAgentState,
        tool_calls: list[ToolCall],
        *,
        parallel_read_only: bool = False,
    ) -> list[dict[str, Any]]:
        context = self._tool_use_context(state)
        tools = self._tools_for_state(state)
        if parallel_read_only and self._is_parallel_read_batch(tool_calls, tools):
            with ThreadPoolExecutor(
                max_workers=min(10, len(tool_calls)),
                thread_name_prefix="agent-read",
            ) as executor:
                futures = [
                    executor.submit(
                        self._execute_tool_call, state, call, context, tools
                    )
                    for call in tool_calls
                ]
                # Preserve model-proposed order even when later reads finish first.
                return [future.result() for future in futures]
        return [
            self._execute_tool_call(state, tool_call, context, tools)
            for tool_call in tool_calls
        ]

    @staticmethod
    def _is_parallel_read_batch(tool_calls: list[ToolCall], tools: Any) -> bool:
        if len(tool_calls) <= 1:
            return False
        call_ids = [call.call_id for call in tool_calls]
        if len(set(call_ids)) != len(call_ids):
            return False
        for call in tool_calls:
            spec = tools.get_spec(call.name)
            if (
                spec is None
                or spec.permission_level != "read_only"
                or spec.requires_approval
                or not spec.idempotent
                or call.name == "agent.request_user_input"
            ):
                return False
        return True

    def _tools_for_state(self, state: CodingAgentState):
        return self._pool_provider(state)

    def _tool_use_context(self, state: CodingAgentState) -> ToolUseContext:
        return self._context_provider(state)

    def _execute_tool_call(
        self,
        state: CodingAgentState,
        tool_call: ToolCall,
        context: ToolExecutionContext,
        tools: Any,
    ) -> dict[str, Any]:
        run_id = context.run_id
        self._emit_tool_event(
            run_id=run_id,
            event_type="tool_started",
            tool_call=tool_call,
            output={
                "call_id": tool_call.call_id,
                "name": tool_call.name,
                "arguments": tool_call.arguments,
                "source": tool_call.source,
            },
            event_key=f"tool-started:{tool_call.call_id}",
        )

        def finish(response: dict[str, Any]) -> dict[str, Any]:
            self._emit_tool_event(
                run_id=run_id,
                event_type="tool_result" if response.get("ok") else "tool_error",
                tool_call=tool_call,
                output=response,
                event_key=f"tool-result:{tool_call.call_id}",
            )
            if response.get("ok") and tool_call.name in SANDBOX_MUTATION_TOOLS:
                self._emit_tool_event(
                    run_id=run_id,
                    event_type="mutation_applied",
                    tool_call=tool_call,
                    output={"call_id": tool_call.call_id, "name": tool_call.name},
                    event_key=f"mutation-applied:{tool_call.call_id}",
                )
            if tool_call.name in SANDBOX_VALIDATION_TOOLS:
                passed = is_validation_success(response)
                self._emit_tool_event(
                    run_id=run_id,
                    event_type="validation_passed" if passed else "validation_failed",
                    tool_call=tool_call,
                    output={"call_id": tool_call.call_id, "passed": passed},
                    event_key=f"validation:{tool_call.call_id}",
                )
            return response

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
                    return finish({
                        "call_id": tool_call.call_id,
                        "name": tool_call.name,
                        "ok": False,
                        "error": "call_id was reused with different arguments",
                        "error_code": "tool_call_identity_conflict",
                        "cached": True,
                    })
                if previous.status == "completed" and previous.response is not None:
                    cached = dict(previous.response)
                    cached["cached"] = True
                    cached["durable_replay"] = True
                    return finish(cached)
                return finish({
                    "call_id": tool_call.call_id,
                    "name": tool_call.name,
                    "ok": False,
                    "error": "tool call has an unfinished durable execution record",
                    "error_code": "tool_execution_in_progress",
                    "cached": True,
                })
        permission = tools.resolve_permission(
            tool_call,
            context,
            phase="execute",
        )
        if permission.effect != "allow":
            response = tools.execute(tool_call, context=context).to_response()
            return finish(response)
        safety_error = _mutation_safety_error(state, tool_call)
        if safety_error is not None:
            return finish(
                {
                    "call_id": tool_call.call_id,
                    "name": tool_call.name,
                    "ok": False,
                    "result": None,
                    "error": safety_error[1],
                    "error_code": safety_error[0],
                    "provider": "runtime",
                    "permission_level": "write_safe",
                    "requires_approval": True,
                    "cached": False,
                }
            )
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
        response = tools.execute(tool_call, context=context).to_response()
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
        return finish(response)


def _mutation_safety_error(
    state: CodingAgentState,
    tool_call: ToolCall,
) -> tuple[str, str] | None:
    """Combine the frozen target contract with read-before-edit/CAS evidence."""

    if tool_call.name not in SANDBOX_MUTATION_TOOLS:
        return None
    try:
        mutations = _mutation_paths(tool_call)
    except ValueError as exc:
        return "mutation_paths_invalid", str(exc)
    if not mutations:
        return "mutation_paths_invalid", "mutation call contains no file target"

    contract = state.get("change_completion_contract", {})
    if isinstance(contract, dict) and contract.get("applicable"):
        allowed = {
            str(item.get("target") or "")
            for item in contract.get("required_changes", [])
            if isinstance(item, dict) and item.get("target_kind", "path") == "path"
        }
        outside = sorted(path for path in mutations if path not in allowed)
        if outside:
            return (
                "mutation_target_outside_contract",
                "mutation target is outside the frozen ChangeCompletionContract: "
                + ", ".join(outside),
            )

    root = Path(
        str(state.get("execution_root") or state.get("workspace_root") or ".")
    ).resolve()
    observations = _observed_file_hashes(state)
    for relative, operation in mutations.items():
        target = (root / relative).resolve()
        if target != root and root not in target.parents:
            return "mutation_path_escape", f"mutation path escapes workspace: {relative}"
        exists = target.is_file()
        if operation == "create" and exists:
            return "mutation_create_conflict", f"create target already exists: {relative}"
        if not exists:
            continue
        if relative not in observations:
            return (
                "mutation_target_not_observed",
                f'edit requires reading "{relative}" first',
            )
        observed_hash = observations[relative]
        current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        version_is_authoritative = bool(
            len(observed_hash) == 64
            and all(character in "0123456789abcdef" for character in observed_hash)
        )
        if version_is_authoritative and current_hash != observed_hash:
            return (
                "mutation_target_stale",
                f'file changed after the Agent observed it; read "{relative}" again',
            )
        expected = (
            str(tool_call.arguments.get("expected_sha256") or "")
            if tool_call.name == "sandbox.write_file"
            else ""
        )
        if expected and expected != current_hash:
            return (
                "mutation_expected_hash_mismatch",
                f'expected_sha256 does not match the latest read of "{relative}"',
            )
    return None


def _observed_file_hashes(state: CodingAgentState) -> dict[str, str]:
    observed: dict[str, str] = {}
    for source in state.get("context_sources", []):
        if getattr(source, "kind", "") != "file":
            continue
        path = _safe_agent_relative_path(str(getattr(source, "path", "") or ""))
        content_hash = str(getattr(source, "content_hash", "") or "")
        if path and content_hash:
            observed[path] = content_hash
    for result in state.get("tool_results", []):
        if (
            not isinstance(result, dict)
            or not result.get("ok")
            or result.get("name") not in {"repo.read_file", "sandbox.write_file"}
        ):
            continue
        output = result.get("result")
        if not isinstance(output, dict):
            continue
        path = _safe_agent_relative_path(str(output.get("path") or ""))
        content_hash = str(output.get("content_hash") or output.get("sha256") or "")
        if path and content_hash:
            observed[path] = content_hash
    return observed


def _mutation_paths(tool_call: ToolCall) -> dict[str, str]:
    if tool_call.name == "sandbox.write_file":
        path = _safe_agent_relative_path(str(tool_call.arguments.get("path") or ""))
        if not path:
            raise ValueError("sandbox.write_file requires a safe relative path")
        return {path: "write"}

    patch = str(tool_call.arguments.get("patch") or "")
    old_path: str | None = None
    paths: dict[str, str] = {}
    for line in patch.splitlines():
        if line.startswith("--- "):
            old_path = _diff_path(line[4:])
        elif line.startswith("+++ "):
            new_path = _diff_path(line[4:])
            if old_path is None and new_path is None:
                raise ValueError("patch has invalid file headers")
            selected = new_path or old_path
            assert selected is not None
            paths[selected] = (
                "create" if old_path is None else "delete" if new_path is None else "write"
            )
            old_path = None
    return paths


def _diff_path(raw: str) -> str | None:
    value = raw.strip().split("\t", 1)[0]
    if value == "/dev/null":
        return None
    if not value.startswith(("a/", "b/")):
        raise ValueError("patch paths must use a/ and b/ prefixes")
    path = _safe_agent_relative_path(value[2:])
    if not path:
        raise ValueError("patch contains an unsafe relative path")
    return path


def _safe_agent_relative_path(raw: str) -> str:
    normalized = raw.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


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


def _permission_approval_item(
    tool_call: ToolCall,
    decision: PermissionDecision,
    tool_specs: list[ToolSpec],
    *,
    run_id: str,
) -> dict[str, Any]:
    spec = next((item for item in tool_specs if item.name == tool_call.name), None)
    return {
        "name": tool_call.name,
        "run_id": run_id,
        "call_id": tool_call.call_id,
        "arguments_hash": canonical_arguments_hash(tool_call.arguments),
        "provider": spec.provider if spec is not None else "unknown",
        "permission_level": (
            spec.permission_level if spec is not None else "unknown"
        ),
        "requires_approval": True,
        "matched_rule": decision.matched_rule,
        "reason": decision.reason,
        "risk_summary": decision.risk_summary,
        "arguments_summary": summarize_tool_arguments(tool_call.arguments),
    }


def _build_repair_approval_request(state: CodingAgentState) -> dict[str, Any]:
    repair_calls = state.get("repair_tool_calls", [])
    approval_calls = state.get("repair_approval_tool_calls", repair_calls)
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
            {
                "call_id": call.call_id,
                "name": call.name,
                "arguments": call.arguments,
            }
            for call in approval_calls
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
