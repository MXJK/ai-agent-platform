"""Context selection, retrieval, and repository exploration nodes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from ai_agent_platform.agents.coding.context import load_project_instructions
from ai_agent_platform.agents.coding.models import CodingAgentState, ContextSource
from ai_agent_platform.agents.coding.planner import (
    bounded_confidence,
    classify_context_source,
)
from ai_agent_platform.agents.coding.runtime_support import (
    append_trace as _append_trace,
    build_workspace_query,
)
from ai_agent_platform.agents.coding.text import extract_paths, unique
from ai_agent_platform.integrations.permissions import ToolApproval
from ai_agent_platform.integrations.tools import ToolCall

from ai_agent_platform.agents.coding.tool_access import (
    permission_approval_item as _permission_approval_item,
)


CHANGE_INTENTS = {"change_planning", "bug_investigation"}
READ_ONLY_REPOSITORY_TOOLS = {
    "repo.find_files",
    "repo.list_files",
    "repo.read_file",
    "repo.search_code",
}
VALID_CONTEXT_ROUTES = {"none", "repo", "rag", "hybrid"}
MAX_ROUTING_CATALOG_ENTRIES = 50
MAX_ROUTING_CATALOG_CHARS = 12000
MAX_SELECTED_KNOWLEDGE_BASES = 3
RAG_RESULTS_PER_KNOWLEDGE_BASE = 5
PROJECT_OVERVIEW_MARKERS = (
    "这个项目是干什么",
    "这个项目做什么",
    "项目是干什么",
    "项目是做什么",
    "介绍一下这个项目",
    "介绍这个项目",
    "what does this project do",
    "what is this project",
    "project overview",
    "summarize this project",
)
MANAGED_DOCUMENT_MARKERS = (
    "文档",
    "知识库",
    "手册",
    "规范",
    "政策",
    "policy",
    "manual",
    "guide",
    "spec",
)
ENTRY_FILE_PRIORITY = {
    "readme.md": 0,
    "readme.rst": 1,
    "readme.txt": 2,
    "pyproject.toml": 3,
    "package.json": 4,
    "go.mod": 5,
    "cargo.toml": 6,
    "pom.xml": 7,
    "build.gradle": 8,
    "build.gradle.kts": 9,
    "composer.json": 10,
    "requirements.txt": 11,
    "docker-compose.yml": 12,
    "docker-compose.yaml": 13,
    "makefile": 14,
}


class ContextRetrievalNodes:
    """Context and retrieval node implementations used only inside the graph."""

    def __init__(self, runtime: Any) -> None:
        self._tools = runtime._tools
        self._planner = runtime._planner
        self._max_instruction_chars = runtime._max_instruction_chars
        self._knowledge_context_provider = runtime._knowledge_context_provider
        self._project_memory_provider = runtime._project_memory_provider
        self._max_rag_context_chars = runtime._max_rag_context_chars
        self._max_exploration_rounds = runtime._max_exploration_rounds
        self._max_read_tools_per_round = runtime._max_read_tools_per_round
        self._max_context_files = runtime._max_context_files
        self._max_context_chars = runtime._max_context_chars
        self._tools_for_state = runtime._tools_for_state
        self._tool_use_context = runtime._tool_use_context
        self._visible_tool_specs = runtime._visible_tool_specs

    def _setup_workspace(self, state: CodingAgentState) -> CodingAgentState:
        root = Path(state["workspace_root"]).resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(
                "workspace_unavailable: captured workspace root is inaccessible"
            )
        for path in state.get("focus_files", []):
            _validate_relative_workspace_path(path, root)
        return {
            "workspace_root": str(root),
            "trace": _append_trace(
                state,
                node="setup_workspace",
                summary="固定本次 run 的工作区根路径快照并校验边界。",
                output={
                    "workspace_id": state["workspace_id"],
                    "focus_files": state.get("focus_files", []),
                },
            ),
        }

    def _load_project_instructions(
        self, state: CodingAgentState
    ) -> CodingAgentState:
        if state.get("instructions_snapshotted"):
            instructions = list(state.get("project_instructions", []))
            source = "RunContextSnapshot"
        else:
            instructions = load_project_instructions(
                workspace_root=state["workspace_root"],
                focus_files=unique(
                    state.get("focus_files", []) + extract_paths(state["user_input"])
                ),
                max_chars=self._max_instruction_chars,
            )
            source = "workspace"
        return {
            "project_instructions": instructions,
            "trace": _append_trace(
                state,
                node="load_project_instructions",
                summary="按既有优先级使用提交时冻结的项目指令链。",
                output={
                    "files": [item.path for item in instructions],
                    "chars": sum(len(item.text) for item in instructions),
                    "limit": self._max_instruction_chars,
                    "source": source,
                },
            ),
        }

    def _classify_request(self, state: CodingAgentState) -> CodingAgentState:
        warnings = list(state.get("context_warnings", []))
        catalog: list[dict[str, Any]] = []
        catalog_truncated = False
        if self._knowledge_context_provider is not None:
            try:
                catalog, catalog_truncated = _routing_catalog(
                    self._knowledge_context_provider.list(),
                    query=state["user_input"],
                )
            except Exception as exc:
                warnings.append(f"knowledge base catalog unavailable: {exc}")
        classify_request = getattr(self._planner, "classify_request", None)
        if callable(classify_request):
            decision = classify_request(state["user_input"], catalog)
        else:
            decision = self._planner.classify_intent(state["user_input"])
            route, route_reason, selected = classify_context_source(
                state["user_input"],
                intent=str(decision.get("intent") or "repository_question"),
                knowledge_bases=catalog,
            )
            decision = {
                **decision,
                "context_route": route,
                "route_reason": route_reason,
                "selected_knowledge_base_ids": selected,
            }
        intent = str(decision.get("intent") or "repository_question")
        return {
            "intent": intent,
            "intent_reason": str(decision.get("reason") or ""),
            "intent_confidence": bounded_confidence(decision.get("confidence")),
            "planner_source": str(decision.get("source") or "unknown"),
            "context_route": str(decision.get("context_route") or "repo"),
            "route_reason": str(decision.get("route_reason") or ""),
            "selected_knowledge_base_ids": list(
                decision.get("selected_knowledge_base_ids") or []
            ),
            "knowledge_base_catalog": catalog,
            "catalog_truncated": catalog_truncated,
            "context_warnings": warnings,
            "trace": _append_trace(
                state,
                node="classify_request",
                summary="分类任务意图并提出上下文来源。",
                output={
                    "intent": intent,
                    "proposed_context_route": decision.get("context_route"),
                    "catalog_size": len(catalog),
                    "catalog_truncated": catalog_truncated,
                    "source": decision.get("source"),
                },
            ),
        }

    def _decide_context_source(
        self,
        state: CodingAgentState,
    ) -> CodingAgentState:
        catalog = state.get("knowledge_base_catalog", [])
        valid_ids = {str(item.get("id")) for item in catalog if item.get("id")}
        route = str(state.get("context_route") or "repo")
        if route not in VALID_CONTEXT_ROUTES:
            route = "repo"
        selected: list[str] = []
        for item in state.get("selected_knowledge_base_ids", []):
            item_id = str(item)
            if item_id in valid_ids and item_id not in selected:
                selected.append(item_id)
            if len(selected) >= MAX_SELECTED_KNOWLEDGE_BASES:
                break

        fallback_route, fallback_reason, fallback_selected = classify_context_source(
            state["user_input"],
            intent=state.get("intent", "repository_question"),
            knowledge_bases=catalog,
        )
        if route in {"rag", "hybrid"} and not selected:
            selected = [
                item_id
                for item_id in fallback_selected
                if item_id in valid_ids
            ][:MAX_SELECTED_KNOWLEDGE_BASES]
        live_repo_intents = CHANGE_INTENTS | {
            "test_strategy",
            "code_explanation",
            "repo_navigation",
            "bug_investigation",
        }
        requires_live_repo = (
            state.get("intent") in live_repo_intents
            or (
                state.get("intent") == "repository_question"
                and fallback_route == "repo"
            )
        )
        if requires_live_repo:
            if route == "rag":
                route = "hybrid" if selected else "repo"
            elif route == "none":
                route = "repo"
        route_reason = str(state.get("route_reason") or fallback_reason)
        if _is_generic_project_overview_request(state["user_input"], catalog):
            route = "repo"
            selected = []
            route_reason = (
                "generic project overview requires live workspace entry files"
            )
        if state.get("intent") == "small_talk":
            route = "none"
            selected = []

        warnings = list(state.get("context_warnings", []))
        if route in {"rag", "hybrid"} and not selected:
            warnings.append("no routable knowledge base was available")
        if route == "repo" and fallback_route == "repo" and not route_reason:
            route_reason = fallback_reason
        return {
            "context_route": route,
            "route_reason": route_reason,
            "selected_knowledge_base_ids": selected,
            "context_warnings": warnings,
            "trace": _append_trace(
                state,
                node="decide_context_source",
                summary="校验上下文路由和知识库选择边界。",
                output={
                    "context_route": route,
                    "selected_knowledge_base_ids": selected,
                    "route_reason": route_reason,
                    "catalog_truncated": state.get("catalog_truncated", False),
                    "warnings": warnings,
                },
            ),
        }

    def _retrieve_knowledge(
        self,
        state: CodingAgentState,
    ) -> CodingAgentState:
        warnings = list(state.get("context_warnings", []))
        selected = state.get("selected_knowledge_base_ids", [])
        retrieved: list[Any] = []
        hit_counts: dict[str, int] = {}
        if self._knowledge_context_provider is None:
            warnings.append("knowledge retrieval is not configured")
        else:
            query = build_workspace_query(state)
            for knowledge_base_id in selected:
                try:
                    results = self._knowledge_context_provider.search(
                        knowledge_base_id=knowledge_base_id,
                        query=query,
                        limit=RAG_RESULTS_PER_KNOWLEDGE_BASE,
                        recall_limit=None,
                    )
                    hit_counts[knowledge_base_id] = len(results)
                    retrieved.extend(results)
                except Exception as exc:
                    hit_counts[knowledge_base_id] = 0
                    warnings.append(
                        f"knowledge retrieval failed for {knowledge_base_id}: {exc}"
                    )

        retrieved.sort(key=lambda item: float(item.score), reverse=True)
        seen: set[tuple[str, str, str]] = set()
        sources: list[ContextSource] = []
        used_chars = 0
        truncated = False
        for item in retrieved:
            key = (item.knowledge_base_id, item.document_id, item.id)
            if key in seen:
                continue
            seen.add(key)
            remaining = self._max_rag_context_chars - used_chars
            if remaining <= 0:
                truncated = True
                break
            text = str(item.text)
            source_truncated = len(text) > remaining
            if source_truncated:
                text = text[:remaining]
                truncated = True
            sources.append(
                ContextSource(
                    kind="knowledge_chunk",
                    path=(
                        f"knowledge://{item.knowledge_base_id}/"
                        f"{item.filename}#chunk-{item.chunk_index}"
                    ),
                    start_line=item.start_line,
                    end_line=item.end_line,
                    text=text,
                    reason=f"RAG retrieval score={float(item.score):.3f}",
                    content_hash=hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                    truncated=source_truncated,
                    knowledge_base_id=item.knowledge_base_id,
                    document_id=item.document_id,
                    score=float(item.score),
                )
            )
            used_chars += len(text)
        if selected and not sources and not any(
            warning.startswith("knowledge retrieval failed") for warning in warnings
        ):
            warnings.append("knowledge retrieval returned no evidence")
        return {
            "rag_context_sources": sources,
            "context_warnings": warnings,
            "trace": _append_trace(
                state,
                node="retrieve_knowledge",
                summary="从选定知识库检索并裁剪文档证据。",
                output={
                    "selected_knowledge_base_ids": selected,
                    "hit_counts": hit_counts,
                    "source_count": len(sources),
                    "chars": used_chars,
                    "limit": self._max_rag_context_chars,
                    "truncated": truncated,
                    "warnings": warnings,
                },
            ),
        }

    def _retrieve_project_memory(
        self,
        state: CodingAgentState,
    ) -> CodingAgentState:
        warnings = list(state.get("context_warnings", []))
        sources: list[ContextSource] = []
        if (
            self._project_memory_provider is not None
            and state.get("intent") != "small_talk"
        ):
            try:
                retrieved = self._project_memory_provider.retrieve(
                    workspace_id=state["workspace_id"],
                    actor_user_id=state.get("actor_user_id", "demo_user"),
                    query=build_workspace_query(state),
                )
                for item in retrieved:
                    memory = item.memory
                    sources.append(
                        ContextSource(
                            kind="project_memory",
                            path=(
                                f"memory://{memory.workspace_id}/{memory.id}"
                            ),
                            start_line=None,
                            end_line=None,
                            text=memory.content,
                            reason=(
                                "Historical project memory; verify mutable "
                                f"claims against live sources (score={item.score:.4f})"
                            ),
                            content_hash=hashlib.sha256(
                                memory.content.encode("utf-8")
                            ).hexdigest(),
                            memory_id=memory.id,
                            memory_kind=memory.kind,
                            confidence=memory.confidence,
                            last_confirmed_at=(
                                memory.last_confirmed_at.isoformat()
                                if memory.last_confirmed_at
                                else None
                            ),
                            relevance_score=item.relevance_score,
                            recency_score=item.recency_score,
                            importance_score=item.importance_score,
                            score=item.score,
                        )
                    )
            except Exception as exc:
                warnings.append(f"project memory retrieval unavailable: {exc}")
        return {
            "memory_context_sources": sources,
            "context_warnings": warnings,
            "trace": _append_trace(
                state,
                node="retrieve_project_memory",
                summary="检索工作区当前 revision 的可用项目记忆。",
                output={
                    "source_count": len(sources),
                    "memory_ids": [
                        item.memory_id for item in sources if item.memory_id
                    ],
                    "warnings": warnings,
                },
            ),
        }

    def _plan_exploration(self, state: CodingAgentState) -> CodingAgentState:
        round_number = state.get("exploration_round", 0) + 1
        tool_specs = [
            spec
            for spec in self._visible_tool_specs(state)
            if spec.name in READ_ONLY_REPOSITORY_TOOLS
        ]
        proposed = self._planner.plan_tool_calls(state, tool_specs)
        proposed = [
            call for call in proposed if call.name in READ_ONLY_REPOSITORY_TOOLS
        ]
        if _is_generic_project_overview_request(
            state["user_input"],
            state.get("knowledge_base_catalog", []),
        ):
            proposed = [
                call for call in proposed if call.name != "repo.search_code"
            ]
        strategy, deterministic = self._fallback_exploration_calls(state)
        calls = _unique_exploration_calls(proposed + deterministic)
        seen = set(state.get("seen_context_keys", []))
        context_files = set(state.get("context_files", []))
        filtered: list[ToolCall] = []
        for call in calls:
            key = _exploration_call_key(call)
            if key in seen:
                continue
            if call.name == "repo.read_file":
                path = str(call.arguments.get("path") or "")
                if (
                    path not in context_files
                    and len(context_files) >= self._max_context_files
                ):
                    continue
                call = ToolCall(
                    name=call.name,
                    arguments={
                        **call.arguments,
                        "max_chars": min(
                            int(call.arguments.get("max_chars", 8000)),
                            max(1, self._max_context_chars - state.get("context_chars", 0)),
                        ),
                    },
                    source=call.source,
                )
            filtered.append(call)
            seen.add(key)
            if len(filtered) >= self._max_read_tools_per_round:
                break
        return {
            "exploration_round": round_number,
            "exploration_strategy": strategy,
            "analysis_tool_calls": filtered,
            "seen_context_keys": list(seen),
            "tool_calls": list(state.get("tool_calls", [])) + filtered,
            "trace": _append_trace(
                state,
                node="plan_exploration",
                summary="规划本轮只读搜索与原始文件读取。",
                output={
                    "round": round_number,
                    "strategy": strategy,
                    "planned_tools": [call.name for call in filtered],
                    "limit": self._max_read_tools_per_round,
                },
            ),
        }

    def _fallback_exploration_calls(
        self, state: CodingAgentState
    ) -> tuple[str, list[ToolCall]]:
        read_files = set(state.get("context_files", []))
        discovered = _rank_discovered_paths(
            _candidate_paths(state.get("exploration_results", [])),
        )
        candidates = unique(
            state.get("focus_files", [])
            + extract_paths(state["user_input"])
            + discovered
        )
        calls = [
            ToolCall(
                name="repo.read_file",
                arguments={"path": path, "max_chars": 8000},
                source="rules",
            )
            for path in candidates
            if path not in read_files
        ]
        if calls:
            strategy = (
                "read_discovered_entries"
                if discovered
                else "read_explicit_candidates"
            )
            return strategy, calls

        previous_results = state.get("exploration_results", [])
        generic_project_overview = _is_generic_project_overview_request(
            state["user_input"],
            state.get("knowledge_base_catalog", []),
        )
        if generic_project_overview and not previous_results:
            return (
                "discover_project_entries",
                [
                    ToolCall(
                        name="repo.list_files",
                        arguments={"path": "", "max_results": 120},
                        source="rules",
                    )
                ],
            )
        if generic_project_overview:
            return (
                "fallback_project_search",
                [
                    ToolCall(
                        name="repo.search_code",
                        arguments={
                            "query": build_workspace_query(state),
                            "max_results": 12,
                            "context_lines": 1,
                        },
                        source="rules",
                    )
                ],
            )
        if previous_results or state.get("exploration_round", 0) > 0:
            return (
                "broaden_file_inventory",
                [
                    ToolCall(
                        name="repo.list_files",
                        arguments={"path": "", "max_results": 120},
                        source="rules",
                    )
                ],
            )
        return (
            "targeted_search",
            [
                ToolCall(
                    name="repo.search_code",
                    arguments={
                        "query": build_workspace_query(state),
                        "max_results": 12,
                        "context_lines": 1,
                    },
                    source="rules",
                )
            ],
        )

    def _execute_exploration(self, state: CodingAgentState) -> CodingAgentState:
        context = self._tool_use_context(state)
        calls = state.get("analysis_tool_calls", [])
        tools = self._tools_for_state(state)
        specs = self._visible_tool_specs(state)
        decisions = [
            (call, tools.resolve_permission(call, context, phase="plan"))
            for call in calls
        ]
        approval_items = [
            _permission_approval_item(
                call,
                decision,
                specs,
                run_id=state.get("run_id", ""),
            )
            for call, decision in decisions
            if decision.effect == "ask"
        ]
        approvals = list(state.get("tool_approvals", []))
        if approval_items:
            response = interrupt(
                {
                    "type": "tool_plan_review",
                    "approval_required": True,
                    "reason": "repository exploration tools require approval",
                    "workspace_id": state["workspace_id"],
                    "planned_tools": [call.name for call in calls],
                    "approval_required_tools": approval_items,
                    "tool_calls": [
                        {
                            "call_id": call.call_id,
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                        for call in calls
                    ],
                }
            )
            approved = (
                bool(response.get("approved"))
                if isinstance(response, dict)
                else bool(response)
            )
            approved_by = (
                str(response.get("approved_by") or "")
                if isinstance(response, dict)
                else ""
            ) or state.get("actor_user_id", "")
            if approved:
                for call, decision in decisions:
                    if decision.effect != "ask":
                        continue
                    approvals.append(
                        tools.issue_approval(
                            call,
                            context,
                            approved_by=approved_by,
                        ).to_dict()
                    )
                context = context.with_approvals(
                    tuple(ToolApproval.from_mapping(item) for item in approvals)
                )
        results = [
            tools.execute(call, context=context).to_response()
            for call in calls
        ]
        return {
            "exploration_results": results,
            "tool_results": list(state.get("tool_results", [])) + results,
            "tool_approvals": approvals,
            "trace": _append_trace(
                state,
                node="execute_exploration",
                summary="在工作区快照边界内执行实时搜索和原始文件读取。",
                output={
                    "round": state.get("exploration_round", 0),
                    "success_count": sum(1 for result in results if result["ok"]),
                    "called_tools": [result["name"] for result in results],
                },
            ),
        }

    def _assess_context(self, state: CodingAgentState) -> CodingAgentState:
        sources = list(state.get("context_sources", []))
        seen = {
            f"{source.path}:{source.start_line}:{source.end_line}:{source.content_hash}"
            for source in sources
        }
        content_hashes = {source.content_hash for source in sources}
        context_files = set(state.get("context_files", []))
        chars = state.get("context_chars", 0)
        for result in state.get("exploration_results", []):
            if not result.get("ok"):
                continue
            output = result.get("result")
            if not isinstance(output, dict):
                continue
            additions = _context_sources_from_result(
                result["name"],
                output,
                focus_files=set(state.get("focus_files", [])),
            )
            for source in additions:
                key = (
                    f"{source.path}:{source.start_line}:"
                    f"{source.end_line}:{source.content_hash}"
                )
                if key in seen or source.content_hash in content_hashes:
                    continue
                if source.kind == "file" and source.path not in context_files:
                    if len(context_files) >= self._max_context_files:
                        continue
                    context_files.add(source.path)
                remaining = self._max_context_chars - chars
                if remaining <= 0:
                    break
                if len(source.text) > remaining:
                    source = ContextSource(
                        **{
                            **source.__dict__,
                            "text": source.text[:remaining],
                            "truncated": True,
                            "content_hash": hashlib.sha256(
                                source.text[:remaining].encode("utf-8")
                            ).hexdigest(),
                        }
                    )
                sources.append(source)
                seen.add(key)
                content_hashes.add(source.content_hash)
                chars += len(source.text)
        round_number = state.get("exploration_round", 0)
        budget_exhausted = (
            round_number >= self._max_exploration_rounds
            or len(context_files) >= self._max_context_files
            or chars >= self._max_context_chars
        )
        unread = [
            path
            for path in _candidate_paths(state.get("exploration_results", []))
            if path not in context_files
        ]
        has_repo_evidence = bool(sources)
        sufficient = has_repo_evidence and (budget_exhausted or not unread)
        failed_count = sum(
            1
            for result in state.get("exploration_results", [])
            if not result.get("ok")
        )
        zero_result_count = sum(
            1
            for result in state.get("exploration_results", [])
            if result.get("ok")
            and isinstance(result.get("result"), dict)
            and result["result"].get("count") == 0
        )
        if budget_exhausted:
            stop_reason = "budget_exhausted"
        elif sufficient:
            stop_reason = "evidence_sufficient"
        elif unread:
            stop_reason = "unread_candidates"
        elif failed_count:
            stop_reason = "tool_failure_retry"
        elif zero_result_count:
            stop_reason = "zero_results_retry"
        elif not state.get("analysis_tool_calls", []):
            stop_reason = "no_new_plan_retry"
        else:
            stop_reason = "evidence_incomplete"
        warnings = list(state.get("context_warnings", []))
        if budget_exhausted and not has_repo_evidence:
            warning = "live repository exploration exhausted without evidence"
            if warning not in warnings:
                warnings.append(warning)
        sources.sort(
            key=lambda source: (
                0
                if source.reason == "user-selected file"
                else 1
                if source.kind == "search_match"
                else 2,
                source.path,
                source.start_line or 0,
            )
        )
        return {
            "context_sources": sources,
            "context_files": sorted(context_files),
            "context_chars": chars,
            "context_budget_exhausted": budget_exhausted,
            "context_sufficient": sufficient,
            "context_stop_reason": stop_reason,
            "context_warnings": warnings,
            "trace": _append_trace(
                state,
                node="assess_context",
                summary="去重并裁剪证据，判断是否继续探索。",
                output={
                    "round": round_number,
                    "source_count": len(sources),
                    "file_count": len(context_files),
                    "chars": chars,
                    "unread_candidates": len(unread),
                    "sufficient": sufficient,
                    "budget_exhausted": budget_exhausted,
                    "stop_reason": stop_reason,
                    "failed_tools": failed_count,
                    "zero_result_tools": zero_result_count,
                },
            ),
        }

    def _route_after_context(self, state: CodingAgentState) -> str:
        if not state.get("context_sufficient") and not state.get(
            "context_budget_exhausted"
        ):
            return "plan_exploration"
        return "merge_evidence"

    def _merge_evidence(self, state: CodingAgentState) -> CodingAgentState:
        merged: list[ContextSource] = []
        seen: set[tuple[Any, ...]] = set()
        repo_count = 0
        knowledge_count = 0
        for source in (
            list(state.get("context_sources", []))
            + list(state.get("rag_context_sources", []))
            + list(state.get("memory_context_sources", []))
        ):
            key = (
                source.kind,
                source.path,
                source.start_line,
                source.end_line,
                source.knowledge_base_id,
                source.document_id,
                source.content_hash,
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(source)
            if source.kind == "knowledge_chunk":
                knowledge_count += 1
            elif source.kind == "project_memory":
                pass
            else:
                repo_count += 1
        return {
            "context_sources": merged,
            "trace": _append_trace(
                state,
                node="merge_evidence",
                summary="合并工作区和知识库证据并保留各自来源。",
                output={
                    "context_route": state.get("context_route", "repo"),
                    "repo_source_count": repo_count,
                    "knowledge_source_count": knowledge_count,
                    "memory_source_count": sum(
                        item.kind == "project_memory" for item in merged
                    ),
                    "source_count": len(merged),
                    "warnings": state.get("context_warnings", []),
                },
            ),
        }


def _validate_relative_workspace_path(path: str, root: Path) -> None:
    candidate = Path(path)
    if candidate.is_absolute():
        raise ValueError("focus_files must contain workspace-relative paths")
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"focus file escapes workspace root: {path}")


def _exploration_call_key(call: ToolCall) -> str:
    arguments = call.arguments
    if call.name in {"repo.search_code", "repo.find_files"}:
        identity = {
            "query": arguments.get("query"),
            "path": arguments.get("path") or "",
        }
    elif call.name == "repo.list_files":
        identity = {"path": arguments.get("path") or ""}
    elif call.name == "repo.read_file":
        identity = {
            "path": arguments.get("path"),
            "start_line": arguments.get("start_line") or 1,
            "end_line": arguments.get("end_line"),
        }
    else:
        identity = arguments
    return (
        f"explore:{call.name}:"
        f"{json.dumps(identity, sort_keys=True, ensure_ascii=False)}"
    )


def _unique_exploration_calls(calls: list[ToolCall]) -> list[ToolCall]:
    seen: set[str] = set()
    result: list[ToolCall] = []
    for call in calls:
        key = _exploration_call_key(call)
        if key in seen:
            continue
        seen.add(key)
        result.append(call)
    return result


def _is_generic_project_overview_request(
    user_input: str,
    knowledge_bases: list[dict[str, Any]],
) -> bool:
    normalized = user_input.casefold()
    if not any(marker in normalized for marker in PROJECT_OVERVIEW_MARKERS):
        return False
    if any(marker in normalized for marker in MANAGED_DOCUMENT_MARKERS):
        return False
    for item in knowledge_bases:
        tags = item.get("tags") or []
        values = [
            item.get("id"),
            item.get("name"),
            *(tags if isinstance(tags, list) else []),
        ]
        if any(
            str(value).strip()
            and str(value).casefold() in normalized
            for value in values
        ):
            return False
    return True


def _rank_discovered_paths(paths: list[str]) -> list[str]:
    def rank(path: str) -> tuple[int, int, int, str]:
        candidate = Path(path)
        name = candidate.name.casefold()
        if name.startswith("readme."):
            priority = 0
        elif name in ENTRY_FILE_PRIORITY:
            priority = ENTRY_FILE_PRIORITY[name]
        elif name in {"main.py", "app.py", "index.js", "index.ts", "main.go"}:
            priority = 20
        elif candidate.suffix.casefold() in {
            ".py",
            ".go",
            ".js",
            ".ts",
            ".tsx",
            ".java",
            ".rs",
        }:
            priority = 30
        elif candidate.suffix.casefold() in {".md", ".rst", ".txt"}:
            priority = 40
        else:
            priority = 50
        hidden = int(any(part.startswith(".") for part in candidate.parts))
        return priority, hidden, len(candidate.parts), path

    return sorted(unique(paths), key=rank)


def _routing_catalog(
    records: list[Any],
    *,
    query: str,
) -> tuple[list[dict[str, Any]], bool]:
    normalized = query.casefold()

    def relevance(record: Any) -> tuple[int, str]:
        score = 0
        for value, weight in (
            (record.id, 4),
            (record.name, 4),
            (record.description, 1),
        ):
            text = str(value or "").casefold()
            if text and text in normalized:
                score += weight
        for tag in record.tags:
            if str(tag).casefold() in normalized:
                score += 3
        return (-score, str(record.id))

    ranked = sorted(records, key=relevance)
    catalog: list[dict[str, Any]] = []
    used_chars = 0
    truncated = len(ranked) > MAX_ROUTING_CATALOG_ENTRIES
    for record in ranked[:MAX_ROUTING_CATALOG_ENTRIES]:
        item = {
            "id": str(record.id),
            "name": str(record.name),
            "description": str(record.description)[:1000],
            "tags": [str(tag) for tag in record.tags[:20]],
        }
        item_chars = len(json.dumps(item, ensure_ascii=False))
        if catalog and used_chars + item_chars > MAX_ROUTING_CATALOG_CHARS:
            truncated = True
            break
        if item_chars > MAX_ROUTING_CATALOG_CHARS:
            item["description"] = item["description"][
                : max(0, MAX_ROUTING_CATALOG_CHARS // 2)
            ]
            item_chars = len(json.dumps(item, ensure_ascii=False))
        catalog.append(item)
        used_chars += item_chars
    return catalog, truncated


def _candidate_paths(results: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for result in results:
        output = result.get("result")
        if not result.get("ok") or not isinstance(output, dict):
            continue
        if result.get("name") == "repo.find_files":
            paths.extend(str(item) for item in output.get("matches", []))
        if result.get("name") == "repo.list_files":
            paths.extend(str(item) for item in output.get("files", []))
        if result.get("name") == "repo.search_code":
            paths.extend(
                str(item.get("path"))
                for item in output.get("matches", [])
                if isinstance(item, dict) and item.get("path")
            )
    return unique(paths)


def _context_sources_from_result(
    name: str,
    output: dict[str, Any],
    *,
    focus_files: set[str],
) -> list[ContextSource]:
    if name == "repo.read_file":
        text = str(output.get("content") or "")
        if not text:
            return []
        path = str(output.get("path") or "")
        return [
            ContextSource(
                kind="file",
                path=path,
                start_line=int(output.get("start_line") or 1),
                end_line=int(output.get("end_line") or 1),
                text=text,
                reason=(
                    "user-selected file"
                    if path in focus_files
                    else "read after search match"
                ),
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                truncated=bool(output.get("truncated")),
            )
        ]
    if name != "repo.search_code":
        return []
    sources: list[ContextSource] = []
    for match in output.get("matches", []):
        if not isinstance(match, dict):
            continue
        text = str(match.get("text") or "")
        if not text:
            continue
        line = int(match.get("line") or 1)
        sources.append(
            ContextSource(
                kind="search_match",
                path=str(match.get("path") or ""),
                start_line=line,
                end_line=line,
                text=text,
                reason="exact symbol or keyword match",
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                truncated=False,
            )
        )
    return sources
