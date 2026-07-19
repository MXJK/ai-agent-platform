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
        try:
            body = json_object_from_llm(
                self._llm_client.complete(intent_classification_prompt(user_input)).text
            )
            intent = str(body.get("intent", ""))
            if intent not in VALID_AGENT_INTENTS:
                raise ValueError(f"unsupported intent: {intent}")
            return {
                "intent": intent,
                "reason": str(body.get("reason") or "LLM structured decision"),
                "confidence": bounded_confidence(body.get("confidence")),
                "source": self.source,
            }
        except Exception:
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


def classify_intent(text: str) -> tuple[str, str]:
    normalized = text.lower()
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
    intents = ", ".join(sorted(VALID_AGENT_INTENTS))
    return (
        "You are classifying a coding-agent user request. "
        "Return only one JSON object with keys intent, reason, confidence. "
        f"Allowed intent values: {intents}.\n"
        f"User request:\n{user_input}"
    )


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
    citations = [
        {
            "filename": citation.filename,
            "chunk_index": citation.chunk_index,
            "score": citation.score,
            "snippet": snippet(citation.text, limit=180),
        }
        for citation in state.get("rag_context", [])
    ]
    payload = {
        "user_input": state["user_input"],
        "intent": state.get("intent", "repository_question"),
        "repository_id": state["repository_id"],
        "focus_files": state.get("focus_files", []),
        "retrieved_context": citations,
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
    if spec.name == "repository_context_search":
        arguments.setdefault("query", state["user_input"])
        arguments.setdefault("repository_id", state["repository_id"])
        citations = state.get("rag_context", [])
        arguments.setdefault("citation_count", len(citations))
        arguments.setdefault(
            "candidate_files", unique([citation.filename for citation in citations])
        )
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
    repository_id = state["repository_id"]
    focus_files = state.get("focus_files", [])
    citations = state.get("rag_context", [])
    cited_files = unique([citation.filename for citation in citations])
    mentioned_paths = extract_paths(user_input)
    symbols = extract_symbols(user_input)
    calls = [
        ToolCall(
            name="repository_context_search",
            arguments={
                "query": user_input,
                "repository_id": repository_id,
                "citation_count": len(citations),
                "candidate_files": cited_files,
            },
        )
    ]
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
                    "context_snippets": [snippet(item.text) for item in citations],
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
