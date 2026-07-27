"""Intent classification and tool planning for the coding agent."""

from __future__ import annotations

import json
import re
from typing import Any

from ai_agent_platform.agents.coding.models import (
    VALID_AGENT_INTENTS,
    AgentPlanner,
    CodingAgentState,
    LLMCompletionClient,
)
from ai_agent_platform.agents.coding.change_loop import SANDBOX_MUTATION_TOOLS
from ai_agent_platform.agents.coding.runtime_support import (
    recent_conversation_context,
)
from ai_agent_platform.agents.coding.text import (
    extract_paths,
    extract_symbols,
    snippet,
    unique,
)
from ai_agent_platform.integrations.tools import (
    ToolCall,
    ToolSpec,
    summarize_tool_arguments,
)


class RuleBasedAgentPlanner:
    source = "rules"

    def classify_intent(self, user_input: str) -> dict[str, Any]:
        intent, reason = classify_intent(user_input)
        return {
            "intent": intent,
            "reason": reason,
            "confidence": 0.72,
            "source": self.source,
        }

    def classify_request(
        self,
        user_input: str,
        knowledge_bases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        decision = self.classify_intent(user_input)
        route, route_reason, selected = classify_context_source(
            user_input,
            intent=str(decision["intent"]),
            knowledge_bases=knowledge_bases,
        )
        return {
            **decision,
            "context_route": route,
            "route_reason": route_reason,
            "selected_knowledge_base_ids": selected,
        }

    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        return plan_rule_based_tool_calls(state, tool_specs)

    def plan_repair_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        return []

    def compose_answer(self, state: CodingAgentState) -> str:
        return grounded_answer_fallback(state)


class LLMStructuredAgentPlanner:
    """Uses structured LLM JSON for agent decisions with rule-based fallback."""

    source = "llm_structured"

    def __init__(
        self,
        llm_client: LLMCompletionClient,
        *,
        fallback: AgentPlanner | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._fallback = fallback or RuleBasedAgentPlanner()

    def classify_intent(self, user_input: str) -> dict[str, Any]:
        return self.classify_request(user_input, [])

    def classify_request(
        self,
        user_input: str,
        knowledge_bases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            body = json_object_from_llm(
                self._llm_client.complete(
                    request_classification_prompt(user_input, knowledge_bases)
                ).text
            )
            intent = str(body.get("intent", ""))
            if intent not in VALID_AGENT_INTENTS:
                raise ValueError(f"unsupported intent: {intent}")
            context_route = str(body.get("context_route") or "")
            if context_route not in {"none", "repo", "rag", "hybrid"}:
                raise ValueError(f"unsupported context route: {context_route}")
            selected = body.get("selected_knowledge_base_ids", [])
            if not isinstance(selected, list):
                selected = []
            return {
                "intent": intent,
                "reason": str(body.get("reason") or "LLM structured decision"),
                "confidence": bounded_confidence(body.get("confidence")),
                "source": self.source,
                "context_route": context_route,
                "route_reason": str(
                    body.get("route_reason") or "LLM structured context decision"
                ),
                "selected_knowledge_base_ids": [
                    str(item) for item in selected[:3]
                ],
            }
        except Exception:
            classify_request = getattr(self._fallback, "classify_request", None)
            if callable(classify_request):
                return classify_request(user_input, knowledge_bases)
            return self._fallback.classify_intent(user_input)

    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        try:
            body = json_object_from_llm(
                self._llm_client.complete(tool_planning_prompt(state, tool_specs)).text
            )
            calls = tool_calls_from_structured_plan(body, state, tool_specs)
            if not calls:
                raise ValueError("LLM returned no executable tool calls")
            return calls
        except Exception:
            return self._fallback.plan_tool_calls(state, tool_specs)

    def plan_repair_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        try:
            body = json_object_from_llm(
                self._llm_client.complete(repair_planning_prompt(state, tool_specs)).text
            )
            calls = tool_calls_from_structured_plan(body, state, tool_specs)
            return [call for call in calls if call.name in SANDBOX_MUTATION_TOOLS]
        except Exception:
            fallback = getattr(self._fallback, "plan_repair_tool_calls", None)
            return fallback(state, tool_specs) if callable(fallback) else []

    def compose_answer(self, state: CodingAgentState) -> str:
        try:
            response = self._llm_client.complete(answer_prompt(state))
            text = str(response.text).strip()
            if not text:
                raise ValueError("LLM returned an empty answer")
            return text
        except Exception:
            return self._fallback.compose_answer(state)


def classify_intent(text: str) -> tuple[str, str]:
    normalized = text.lower()
    if normalized.strip() in {
        "hi",
        "hello",
        "hey",
        "你好",
        "您好",
        "早上好",
        "下午好",
        "晚上好",
    }:
        return "small_talk", "greeting matched"
    if re.search(r"(帮我|需要|请|新增|修改|改成|支持|接入).{0,12}实现", normalized):
        return "change_planning", "implementation planning phrase matched"
    rules: list[tuple[str, tuple[str, ...], str]] = [
        (
            "bug_investigation",
            ("报错", "异常", "bug", "失败", "fail", "traceback", "exception", "修复"),
            "failure or debugging keyword matched",
        ),
        (
            "test_strategy",
            ("测试", "单测", "覆盖率", "pytest", "unittest", "test"),
            "test keyword matched",
        ),
        (
            "repo_navigation",
            ("在哪", "哪里", "哪个文件", "哪个函数", "入口", "调用链", "symbol", "class "),
            "repository navigation keyword matched",
        ),
        (
            "code_explanation",
            ("解释", "讲解", "怎么启动", "流程", "架构", "模块", "接口", "函数", "class"),
            "code explanation keyword matched",
        ),
        (
            "change_planning",
            ("新增", "修改", "重构", "接入", "支持", "改成", "帮我做", "加上"),
            "implementation planning keyword matched",
        ),
    ]
    for intent, keywords, reason in rules:
        if any(keyword in normalized for keyword in keywords):
            return intent, reason
    return "repository_question", "default repository QA route"


def intent_classification_prompt(user_input: str) -> str:
    return request_classification_prompt(user_input, [])


def request_classification_prompt(
    user_input: str,
    knowledge_bases: list[dict[str, Any]],
) -> str:
    intents = ", ".join(sorted(VALID_AGENT_INTENTS))
    payload = {
        "user_request": user_input,
        "knowledge_bases": knowledge_bases,
    }
    return (
        "You are classifying a coding-agent user request. "
        "Return only one JSON object with keys intent, reason, confidence, "
        "context_route, route_reason, selected_knowledge_base_ids. "
        f"Allowed intent values: {intents}.\n"
        "Allowed context_route values: none, repo, rag, hybrid. Use repo for "
        "live source code, rag for managed business/reference documentation, "
        "hybrid when both are needed, and none for small talk. Select at most "
        "three IDs and only from the supplied knowledge_bases. A code change, "
        "bug investigation, or test task must include repo.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def classify_context_source(
    user_input: str,
    *,
    intent: str,
    knowledge_bases: list[dict[str, Any]],
) -> tuple[str, str, list[str]]:
    if intent == "small_talk":
        return "none", "small talk needs no external context", []

    normalized = user_input.casefold()
    repo_keywords = (
        "代码",
        "源码",
        "实现",
        "函数",
        "类",
        "接口",
        "文件",
        "调用链",
        "报错",
        "bug",
        "test",
        "pytest",
        ".py",
        ".js",
        ".ts",
        ".go",
        ".java",
    )
    rag_keywords = (
        "文档",
        "知识库",
        "规范",
        "手册",
        "政策",
        "流程",
        "制度",
        "指南",
        "需求",
        "说明书",
        "policy",
        "manual",
        "guide",
        "spec",
    )
    needs_repo = intent in {
        "repository_question",
        "repo_navigation",
        "code_explanation",
        "change_planning",
        "bug_investigation",
        "test_strategy",
    } and (
        intent != "repository_question"
        or any(keyword in normalized for keyword in repo_keywords)
    )
    if intent in {"change_planning", "bug_investigation", "test_strategy"}:
        needs_repo = True
    needs_rag = any(keyword in normalized for keyword in rag_keywords)
    selected = select_knowledge_bases(user_input, knowledge_bases) if needs_rag else []
    if needs_repo and needs_rag:
        return "hybrid", "request needs source code and managed documentation", selected
    if needs_rag:
        return "rag", "request targets managed documentation", selected
    return "repo", "default to live workspace evidence", []


def select_knowledge_bases(
    user_input: str,
    knowledge_bases: list[dict[str, Any]],
) -> list[str]:
    normalized = user_input.casefold()
    scored: list[tuple[int, str]] = []
    for item in knowledge_bases:
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        name = str(item.get("name") or "")
        description = str(item.get("description") or "")
        tags = [str(tag) for tag in item.get("tags", [])]
        score = 0
        for value, weight in ((item_id, 4), (name, 4)):
            if value and value.casefold() in normalized:
                score += weight
        for tag in tags:
            if tag and tag.casefold() in normalized:
                score += 3
        metadata = " ".join([item_id, name, description, *tags]).casefold()
        for token in re.findall(r"[A-Za-z0-9_.-]{2,}", normalized):
            if token in metadata:
                score += 1
        scored.append((score, item_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    positive = [item_id for score, item_id in scored if score > 0]
    return (positive or [item_id for _, item_id in scored[:1]])[:3]


def tool_planning_prompt(
    state: CodingAgentState,
    tool_specs: list[ToolSpec],
) -> str:
    tool_payload = [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
            "provider": spec.provider,
            "permission_level": spec.permission_level,
            "requires_approval": spec.requires_approval,
        }
        for spec in tool_specs
    ]
    sources = [
        {
            "kind": source.kind,
            "path": source.path,
            "start_line": source.start_line,
            "end_line": source.end_line,
            "snippet": snippet(source.text, limit=180),
        }
        for source in state.get("context_sources", [])
    ]
    payload = {
        "user_input": state["user_input"],
        "conversation_context": recent_conversation_context(state),
        "intent": state.get("intent", "repository_question"),
        "workspace_id": state["workspace_id"],
        "focus_files": state.get("focus_files", []),
        "context_sources": sources,
        "available_tools": tool_payload,
    }
    return (
        "You are planning tool calls for a coding-agent backend. "
        "Return only one JSON object: {\"tool_calls\": [{\"name\": string, "
        "\"arguments\": object}]}. Use only available tool names. Prefer "
        "read-only tools unless the intent needs change planning.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def repair_planning_prompt(
    state: CodingAgentState,
    tool_specs: list[ToolSpec],
) -> str:
    mutation_tools = [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
        }
        for spec in tool_specs
        if spec.name in SANDBOX_MUTATION_TOOLS
    ]
    failed_validations = [
        {
            "name": result.get("name"),
            "error": result.get("error"),
            "output": result.get("result"),
        }
        for result in state.get("validation_results", [])
    ]
    payload = {
        "user_input": state["user_input"],
        "focus_files": state.get("focus_files", []),
        "iteration": state.get("change_iteration", 0),
        "failed_validations": failed_validations,
        "available_mutation_tools": mutation_tools,
    }
    return (
        "You are repairing a sandboxed code change after validation failed. "
        "Return only one JSON object: {\"tool_calls\": [{\"name\": string, "
        "\"arguments\": object}]}. Use only the available mutation tools, make "
        "the smallest repair, and do not include test commands or external tools.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def json_object_from_llm(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("structured LLM response must be a JSON object")
    return parsed


def bounded_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(confidence, 1.0))


def tool_calls_from_structured_plan(
    body: dict[str, Any],
    state: CodingAgentState,
    tool_specs: list[ToolSpec],
) -> list[ToolCall]:
    raw_calls = body.get("tool_calls", [])
    if not isinstance(raw_calls, list):
        raise ValueError("tool_calls must be a list")
    specs_by_name = {spec.name: spec for spec in tool_specs}
    planned: list[ToolCall] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            continue
        name = str(raw_call.get("name") or "")
        spec = specs_by_name.get(name)
        if spec is None:
            continue
        raw_arguments = raw_call.get("arguments", {})
        arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        arguments = complete_tool_arguments(spec, arguments, state=state)
        planned.append(
            ToolCall(name=name, arguments=arguments, source="llm_structured")
        )
    return unique_tool_calls(planned)


def complete_tool_arguments(
    spec: ToolSpec,
    arguments: dict[str, Any],
    *,
    state: CodingAgentState,
) -> dict[str, Any]:
    required = spec.input_schema.get("required", [])
    if not isinstance(required, list):
        return arguments
    mentioned_paths = extract_paths(state["user_input"])
    symbols = extract_symbols(state["user_input"])
    for name in required:
        if name in arguments:
            continue
        arguments[name] = argument_value_for_name(
            str(name),
            user_input=state["user_input"],
            mentioned_paths=mentioned_paths,
            symbols=symbols,
        )
    return arguments


def unique_tool_calls(tool_calls: list[ToolCall]) -> list[ToolCall]:
    seen: set[str] = set()
    result: list[ToolCall] = []
    for tool_call in tool_calls:
        if tool_call.name in seen:
            continue
        seen.add(tool_call.name)
        result.append(tool_call)
    return result


def plan_rule_based_tool_calls(
    state: CodingAgentState,
    tool_specs: list[ToolSpec] | None = None,
) -> list[ToolCall]:
    intent = state.get("intent", "repository_question")
    user_input = state["user_input"]
    focus_files = state.get("focus_files", [])
    sources = state.get("context_sources", [])
    cited_files = unique([source.path for source in sources])
    mentioned_paths = extract_paths(user_input)
    symbols = extract_symbols(user_input)
    calls: list[ToolCall] = []
    if intent in {
        "repository_question",
        "repo_navigation",
        "code_explanation",
        "bug_investigation",
        "test_strategy",
        "change_planning",
    }:
        calls.append(
            ToolCall(
                name="repo.search_code",
                arguments={
                    "query": build_repo_tool_search_query(
                        user_input, symbols, mentioned_paths, cited_files
                    ),
                    "max_results": 8,
                    "context_lines": 0,
                },
            )
        )
    files_to_read = unique(focus_files + mentioned_paths + cited_files)[:3]
    for file_path in files_to_read:
        calls.append(
            ToolCall(
                name="repo.read_file",
                arguments={"path": file_path, "max_chars": 6000},
            )
        )
    if intent in {"repo_navigation", "code_explanation", "bug_investigation"}:
        calls.append(
            ToolCall(
                name="file_symbol_locator",
                arguments={
                    "query": user_input,
                    "focus_files": unique(focus_files + mentioned_paths + cited_files),
                    "symbols": symbols,
                },
            )
        )
    if intent in {"repository_question", "code_explanation", "repo_navigation"}:
        calls.append(
            ToolCall(
                name="code_explainer",
                arguments={
                    "query": user_input,
                    "files": unique(focus_files + cited_files),
                    "context_snippets": [snippet(item.text) for item in sources],
                },
            )
        )
    if intent == "change_planning":
        calls.append(
            ToolCall(
                name="change_planner",
                arguments={
                    "goal": user_input,
                    "candidate_files": unique(
                        focus_files + mentioned_paths + cited_files
                    ),
                },
            )
        )
    if intent == "bug_investigation":
        calls.append(
            ToolCall(
                name="bug_investigator",
                arguments={
                    "symptom": user_input,
                    "candidate_files": unique(
                        focus_files + mentioned_paths + cited_files
                    ),
                },
            )
        )
    if intent in {"test_strategy", "change_planning", "bug_investigation"}:
        calls.append(
            ToolCall(
                name="test_designer",
                arguments={
                    "goal": user_input,
                    "candidate_files": unique(focus_files + cited_files),
                },
            )
        )
    calls.extend(
        plan_dynamic_mcp_tool_calls(
            user_input=user_input,
            mentioned_paths=mentioned_paths,
            symbols=symbols,
            tool_specs=tool_specs or [],
            already_planned={call.name for call in calls},
        )
    )
    return calls


def answer_prompt(state: CodingAgentState) -> str:
    sources = [
        {
            "kind": source.kind,
            "path": source.path,
            "lines": [source.start_line, source.end_line],
            "text": source.text,
            "reason": source.reason,
            "truncated": source.truncated,
            "knowledge_base_id": source.knowledge_base_id,
            "document_id": source.document_id,
            "score": source.score,
        }
        for source in (
            list(state.get("project_instructions", []))
            + list(state.get("context_sources", []))
        )
    ]
    payload = {
        "task": state["user_input"],
        "intent": state.get("intent"),
        "context_route": state.get("context_route", "repo"),
        "selected_knowledge_base_ids": state.get(
            "selected_knowledge_base_ids", []
        ),
        "history": state.get("history", [])[-8:],
        "sources": sources,
        "context_warnings": state.get("context_warnings", []),
        "tool_summaries": _tool_summaries(state.get("tool_results", [])),
        "artifacts": state.get("artifacts", []),
        "budget_exhausted": state.get("context_budget_exhausted", False),
    }
    return (
        "Answer using only the supplied evidence. Cite live code as path:start-end "
        "and managed documentation with its knowledge:// path. Distinguish code "
        "from documentation, explain context warnings or insufficient evidence, "
        "and include validation and diff outcomes when present. Do not claim that "
        "the live repository uses an index or embedding.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _tool_summaries(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item in results:
        summary: dict[str, Any] = {
            "name": item.get("name"),
            "ok": item.get("ok"),
            "error": item.get("error"),
            "duration_ms": item.get("duration_ms"),
        }
        output = item.get("result")
        if isinstance(output, dict):
            summary["result"] = {
                key: output[key]
                for key in (
                    "path",
                    "start_line",
                    "end_line",
                    "count",
                    "truncated",
                    "engine",
                    "changed_files",
                    "exit_code",
                    "command",
                )
                if key in output
            }
        summaries.append(summary)
    return summaries


def grounded_answer_fallback(state: CodingAgentState) -> str:
    sources = state.get("context_sources", [])
    lines = [
        f"上下文路由：`{state.get('context_route', 'repo')}`；"
        f"工作区：`{state['workspace_id']}`。"
    ]
    if not sources:
        lines.append("当前没有收集到足够的代码或知识库证据，无法可靠回答。")
    else:
        for source in sources:
            location = source.path
            if source.start_line is not None:
                location += f":{source.start_line}-{source.end_line or source.start_line}"
            lines.append(f"- {location}：{snippet(source.text, limit=240)}")
    for warning in state.get("context_warnings", []):
        lines.append(f"- 上下文提示：{warning}")
    if state.get("context_budget_exhausted"):
        lines.append("探索预算已耗尽；未覆盖到的部分需要下一次任务继续定向读取。")
    for decision_name in ("review_decision", "repair_review_decision"):
        decision = state.get(decision_name, {})
        if decision and not decision.get("approved"):
            lines.append(
                "审批未通过："
                + str(decision.get("feedback") or "未提供补充说明")
            )
    artifacts = state.get("artifacts", [])
    if artifacts:
        lines.append(f"已生成 {len(artifacts)} 个验证/Diff 产物，可从 artifacts 审阅。")
    return "\n".join(lines)


def approval_required_tools(
    tool_calls: list[ToolCall],
    tool_specs: list[ToolSpec],
) -> list[dict[str, Any]]:
    specs_by_name = {spec.name: spec for spec in tool_specs}
    calls_by_name = {tool_call.name: tool_call for tool_call in tool_calls}
    approval_tools: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        spec = specs_by_name.get(tool_call.name)
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
                        calls_by_name[tool_call.name].arguments
                    ),
                }
            )
    return approval_tools


def plan_dynamic_mcp_tool_calls(
    *,
    user_input: str,
    mentioned_paths: list[str],
    symbols: list[str],
    tool_specs: list[ToolSpec],
    already_planned: set[str],
) -> list[ToolCall]:
    scored_specs: list[tuple[int, ToolSpec]] = []
    for spec in tool_specs:
        if not spec.provider.startswith("mcp:") or spec.name in already_planned:
            continue
        score = score_tool_for_request(spec, user_input)
        if score > 0:
            scored_specs.append((score, spec))
    planned: list[ToolCall] = []
    for _, spec in sorted(scored_specs, key=lambda item: item[0], reverse=True)[:3]:
        planned.append(
            ToolCall(
                name=spec.name,
                arguments=arguments_for_tool_spec(
                    spec,
                    user_input=user_input,
                    mentioned_paths=mentioned_paths,
                    symbols=symbols,
                ),
                source="dynamic_tool_spec",
            )
        )
    return planned


def score_tool_for_request(spec: ToolSpec, user_input: str) -> int:
    normalized_input = user_input.lower()
    tool_name = spec.name.split(".")[-1]
    searchable_text = " ".join(
        [tool_name, spec.description, " ".join(schema_property_names(spec.input_schema))]
    ).lower().replace("_", " ")
    score = 0
    for token in tool_match_tokens(searchable_text):
        if token in normalized_input:
            score += 3 if token in tool_name.lower().replace("_", " ") else 1
    if tool_name.lower() in normalized_input:
        score += 6
    return score


def arguments_for_tool_spec(
    spec: ToolSpec,
    *,
    user_input: str,
    mentioned_paths: list[str],
    symbols: list[str],
) -> dict[str, Any]:
    properties = schema_properties(spec.input_schema)
    required = spec.input_schema.get("required", [])
    if not isinstance(required, list):
        required = []
    return {
        name: argument_value_for_name(
            name,
            user_input=user_input,
            mentioned_paths=mentioned_paths,
            symbols=symbols,
        )
        for name in properties
        if name in required
    }


def argument_value_for_name(
    name: str,
    *,
    user_input: str,
    mentioned_paths: list[str],
    symbols: list[str],
) -> Any:
    normalized = name.lower()
    if normalized in {"query", "question", "prompt", "input", "message", "text"}:
        return user_input
    if normalized in {"title", "summary", "name"}:
        return snippet(user_input, limit=80)
    if normalized in {"path", "file", "filename"}:
        return mentioned_paths[0] if mentioned_paths else ""
    if normalized in {"symbol", "function", "class_name"}:
        return symbols[0] if symbols else ""
    return user_input


def schema_properties(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties", {})
    return properties if isinstance(properties, dict) else {}


def schema_property_names(schema: dict[str, Any]) -> list[str]:
    return list(schema_properties(schema).keys())


def tool_match_tokens(text: str) -> list[str]:
    ignored = {
        "tool",
        "tools",
        "the",
        "and",
        "for",
        "with",
        "object",
        "string",
        "payload",
    }
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", text)
    return unique([token.lower() for token in tokens if token.lower() not in ignored])


def build_repo_tool_search_query(
    user_input: str,
    symbols: list[str],
    mentioned_paths: list[str],
    cited_files: list[str],
) -> str:
    focused_terms = unique(symbols + mentioned_paths + cited_files)
    return " ".join(focused_terms) if focused_terms else user_input
