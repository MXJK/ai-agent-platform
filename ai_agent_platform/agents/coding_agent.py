from __future__ import annotations

from dataclasses import dataclass
import re
from time import perf_counter
from typing import Any, Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph

from ai_agent_platform.domain import Message
from ai_agent_platform.integrations import (
    RAGConfigurationError,
    RAGProviderError,
    RAGService,
    RAGValidationError,
)
from ai_agent_platform.integrations.rag import RetrievedDocument
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry


CODING_AGENT_ROLE = "研发助手 / 代码仓库问答 Agent"
CODING_AGENT_OBJECTIVE = (
    "围绕代码仓库检索上下文、解释实现、定位文件/符号、规划安全改动，"
    "并输出可复盘的执行轨迹。"
)


class CodingAgentState(TypedDict, total=False):
    conversation_id: str
    user_input: str
    repository_id: str
    history: list[dict[str, str]]
    focus_files: list[str]
    intent: str
    intent_reason: str
    rag_context: list[RetrievedDocument]
    tool_calls: list[ToolCall]
    tool_results: list[dict[str, Any]]
    answer: str
    trace: list[dict[str, Any]]
    started_at: float


AgentRoute = Literal["retrieve_repository_context", "compose_answer"]


@dataclass(frozen=True)
class AgentRunResult:
    conversation_id: str
    repository_id: str
    role: str
    objective: str
    intent: str
    answer: str
    graph_engine: str
    rag_context: list[RetrievedDocument]
    tool_calls: list[ToolCall]
    tool_results: list[dict[str, Any]]
    trace: list[dict[str, Any]]


class CodingAgentRuntime:
    """LangGraph runtime for a repository-aware development assistant."""

    def __init__(
        self,
        *,
        rag_service: RAGService,
        tool_registry: Optional[ToolRegistry] = None,
    ) -> None:
        self._rag_service = rag_service
        self._tools = tool_registry or create_coding_tool_registry()
        self._graph = self._build_graph()
        self.graph_engine = "langgraph"

    def run(
        self,
        *,
        conversation_id: str,
        user_input: str,
        history: list[Message],
        repository_id: str = "repo_main",
        focus_files: Optional[list[str]] = None,
    ) -> AgentRunResult:
        state = self._graph.invoke(
            {
                "conversation_id": conversation_id,
                "user_input": user_input,
                "repository_id": repository_id,
                "focus_files": focus_files or [],
                "history": [
                    {"role": message.role, "content": message.content}
                    for message in history
                ],
                "trace": [],
                "started_at": perf_counter(),
            }
        )
        return AgentRunResult(
            conversation_id=conversation_id,
            repository_id=repository_id,
            role=CODING_AGENT_ROLE,
            objective=CODING_AGENT_OBJECTIVE,
            intent=state.get("intent", "repository_question"),
            answer=state.get("answer", ""),
            graph_engine=self.graph_engine,
            rag_context=state.get("rag_context", []),
            tool_calls=state.get("tool_calls", []),
            tool_results=state.get("tool_results", []),
            trace=state.get("trace", []),
        )

    def _build_graph(self):
        workflow = StateGraph(CodingAgentState)
        workflow.add_node("setup", self._setup)
        workflow.add_node("classify_request", self._classify_request)
        workflow.add_node("retrieve_repository_context", self._retrieve_repository_context)
        workflow.add_node("plan_tools", self._plan_tools)
        workflow.add_node("inspect_repository", self._inspect_repository)
        workflow.add_node("compose_answer", self._compose_answer)
        workflow.set_entry_point("setup")
        workflow.add_edge("setup", "classify_request")
        workflow.add_conditional_edges(
            "classify_request",
            _route_after_classification,
            {
                "retrieve_repository_context": "retrieve_repository_context",
                "compose_answer": "compose_answer",
            },
        )
        workflow.add_edge("retrieve_repository_context", "plan_tools")
        workflow.add_edge("plan_tools", "inspect_repository")
        workflow.add_edge("inspect_repository", "compose_answer")
        workflow.add_edge("compose_answer", END)
        return workflow.compile()

    def _setup(self, state: CodingAgentState) -> CodingAgentState:
        history = state.get("history", [])
        return {
            "trace": _append_trace(
                state,
                node="setup",
                summary="加载研发助手角色、仓库范围和多轮上下文。",
                output={
                    "role": CODING_AGENT_ROLE,
                    "objective": CODING_AGENT_OBJECTIVE,
                    "repository_id": state["repository_id"],
                    "focus_files": state.get("focus_files", []),
                    "history_messages": len(history),
                },
            )
        }

    def _classify_request(self, state: CodingAgentState) -> CodingAgentState:
        intent, reason = _classify_intent(state["user_input"])
        return {
            "intent": intent,
            "intent_reason": reason,
            "trace": _append_trace(
                state,
                node="classify_request",
                summary="判断用户是在问实现、定位代码、排查问题、规划改动还是设计测试。",
                output={
                    "intent": intent,
                    "reason": reason,
                    "next_node": _next_node_for_intent(intent),
                },
            ),
        }

    def _retrieve_repository_context(
        self, state: CodingAgentState
    ) -> CodingAgentState:
        try:
            citations = self._rag_service.search(
                knowledge_base_id=state["repository_id"],
                query=_build_repository_query(state),
                limit=4,
                recall_limit=12,
            )
            trace_output: dict[str, Any] = {
                "repository_id": state["repository_id"],
                "citation_count": len(citations),
                "filenames": [citation.filename for citation in citations],
            }
        except (RAGValidationError, RAGConfigurationError, RAGProviderError) as exc:
            citations = []
            trace_output = {
                "repository_id": state["repository_id"],
                "citation_count": 0,
                "error": str(exc),
            }

        return {
            "rag_context": citations,
            "trace": _append_trace(
                state,
                node="retrieve_repository_context",
                summary="从仓库索引中检索最可能相关的文件片段。",
                output=trace_output,
            ),
        }

    def _plan_tools(self, state: CodingAgentState) -> CodingAgentState:
        tool_calls = _plan_tool_calls(state)
        return {
            "tool_calls": tool_calls,
            "trace": _append_trace(
                state,
                node="plan_tools",
                summary="根据意图和检索结果规划研发助手工具调用。",
                output={"planned_tools": [tool_call.name for tool_call in tool_calls]},
            ),
        }

    def _inspect_repository(self, state: CodingAgentState) -> CodingAgentState:
        tool_results: list[dict[str, Any]] = []
        for tool_call in state.get("tool_calls", []):
            try:
                result = self._tools.call(tool_call)
                tool_results.append(
                    {
                        "name": tool_call.name,
                        "ok": True,
                        "result": result,
                    }
                )
            except Exception as exc:
                tool_results.append(
                    {
                        "name": tool_call.name,
                        "ok": False,
                        "error": str(exc),
                    }
                )

        return {
            "tool_results": tool_results,
            "trace": _append_trace(
                state,
                node="inspect_repository",
                summary="执行仓库检索、符号定位、方案规划或测试建议工具。",
                output={
                    "called_tools": [item["name"] for item in tool_results],
                    "success_count": sum(1 for item in tool_results if item["ok"]),
                },
            ),
        }

    def _compose_answer(self, state: CodingAgentState) -> CodingAgentState:
        answer = _format_answer(state)
        elapsed_ms = int((perf_counter() - state["started_at"]) * 1000)
        return {
            "answer": answer,
            "trace": _append_trace(
                state,
                node="compose_answer",
                summary="汇总代码上下文、工具结果和下一步研发建议。",
                output={"elapsed_ms": elapsed_ms, "answer_chars": len(answer)},
            ),
        }


def create_coding_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("repository_context_search", _repository_context_search_tool)
    registry.register("file_symbol_locator", _file_symbol_locator_tool)
    registry.register("code_explainer", _code_explainer_tool)
    registry.register("change_planner", _change_planner_tool)
    registry.register("bug_investigator", _bug_investigator_tool)
    registry.register("test_designer", _test_designer_tool)
    return registry


def _classify_intent(text: str) -> tuple[str, str]:
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


def _route_after_classification(state: CodingAgentState) -> AgentRoute:
    return _next_node_for_intent(state.get("intent", "repository_question"))


def _next_node_for_intent(intent: str) -> AgentRoute:
    if intent == "small_talk":
        return "compose_answer"
    return "retrieve_repository_context"


def _build_repository_query(state: CodingAgentState) -> str:
    parts = [state["user_input"]]
    focus_files = state.get("focus_files", [])
    if focus_files:
        parts.append("重点文件: " + " ".join(focus_files))
    return "\n".join(parts)


def _plan_tool_calls(state: CodingAgentState) -> list[ToolCall]:
    intent = state.get("intent", "repository_question")
    user_input = state["user_input"]
    repository_id = state["repository_id"]
    focus_files = state.get("focus_files", [])
    citations = state.get("rag_context", [])
    cited_files = _unique([citation.filename for citation in citations])
    mentioned_paths = _extract_paths(user_input)
    symbols = _extract_symbols(user_input)

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
    if intent in {"repo_navigation", "code_explanation", "bug_investigation"}:
        calls.append(
            ToolCall(
                name="file_symbol_locator",
                arguments={
                    "query": user_input,
                    "focus_files": _unique(focus_files + mentioned_paths + cited_files),
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
                    "files": _unique(focus_files + cited_files),
                    "context_snippets": [_snippet(citation.text) for citation in citations],
                },
            )
        )
    if intent == "change_planning":
        calls.append(
            ToolCall(
                name="change_planner",
                arguments={
                    "goal": user_input,
                    "candidate_files": _unique(focus_files + mentioned_paths + cited_files),
                },
            )
        )
    if intent == "bug_investigation":
        calls.append(
            ToolCall(
                name="bug_investigator",
                arguments={
                    "symptom": user_input,
                    "candidate_files": _unique(focus_files + mentioned_paths + cited_files),
                },
            )
        )
    if intent in {"test_strategy", "change_planning", "bug_investigation"}:
        calls.append(
            ToolCall(
                name="test_designer",
                arguments={
                    "goal": user_input,
                    "candidate_files": _unique(focus_files + cited_files),
                },
            )
        )
    return calls


def _repository_context_search_tool(
    *, query: str, repository_id: str, citation_count: int, candidate_files: list[str]
) -> dict[str, Any]:
    return {
        "query": query,
        "repository_id": repository_id,
        "citation_count": citation_count,
        "candidate_files": candidate_files,
        "next_step": "ground the answer in retrieved file chunks and ask for indexing if citations are empty",
    }


def _file_symbol_locator_tool(
    *, query: str, focus_files: list[str], symbols: list[str]
) -> dict[str, Any]:
    return {
        "query": query,
        "focus_files": focus_files,
        "symbols": symbols,
        "suggested_commands": [
            "rg -n '<symbol-or-keyword>' .",
            "rg --files | rg '<path-fragment>'",
        ],
    }


def _code_explainer_tool(
    *, query: str, files: list[str], context_snippets: list[str]
) -> dict[str, Any]:
    return {
        "query": query,
        "files": files,
        "summary_style": "explain responsibility, call flow, inputs, outputs, and extension points",
        "context_snippet_count": len(context_snippets),
    }


def _change_planner_tool(*, goal: str, candidate_files: list[str]) -> dict[str, Any]:
    return {
        "goal": goal,
        "candidate_files": candidate_files,
        "plan": [
            "confirm current API/data contracts around the candidate files",
            "make the smallest behavior change behind the existing boundary",
            "add focused tests for the changed agent route",
            "run unit tests and a compile check before handing off",
        ],
    }


def _bug_investigator_tool(*, symptom: str, candidate_files: list[str]) -> dict[str, Any]:
    return {
        "symptom": symptom,
        "candidate_files": candidate_files,
        "debug_order": [
            "reproduce the failing request or test",
            "trace from route to service/runtime to integration boundary",
            "check whether history, retrieved context, or tool results are missing",
            "patch the narrowest failing branch and add a regression test",
        ],
    }


def _test_designer_tool(*, goal: str, candidate_files: list[str]) -> dict[str, Any]:
    return {
        "goal": goal,
        "candidate_files": candidate_files,
        "recommended_tests": [
            "API contract test for /api/v1/agent/runs",
            "runtime unit test for intent routing and planned tools",
            "RAG-scoped test that proves repository_id isolates code indexes",
        ],
    }


def _format_answer(state: CodingAgentState) -> str:
    lines = [
        f"我是{CODING_AGENT_ROLE}。",
        f"目标：{CODING_AGENT_OBJECTIVE}",
        (
            f"我把本轮请求归类为 `{state.get('intent', 'repository_question')}`，"
            f"原因：{state.get('intent_reason', '')}。"
        ),
        f"仓库索引：`{state['repository_id']}`。",
    ]

    history_count = len(state.get("history", []))
    lines.append(f"已读取 {history_count} 条历史消息作为上下文。")

    citations = state.get("rag_context", [])
    if citations:
        lines.append("我检索到的代码上下文：")
        for index, citation in enumerate(citations, start=1):
            snippet = _snippet(citation.text, limit=140)
            lines.append(
                f"[{index}] {citation.filename} chunk={citation.chunk_index} "
                f"score={citation.score:.3f}: {snippet}"
            )
    else:
        lines.append(
            "我还没有检索到代码片段。可以先把 README、关键源码文件或目录索引写入这个 repository_id。"
        )

    tool_results = state.get("tool_results", [])
    if tool_results:
        lines.append("工具复盘：")
        for item in tool_results:
            if item.get("ok"):
                lines.append(f"- {item['name']}: {item['result']}")
            else:
                lines.append(f"- {item['name']}: failed, {item.get('error')}")

    if citations:
        lines.append(
            "回答建议：优先依据上面的文件片段定位实现；如果要改代码，下一步应沿 trace 中的候选文件做最小修改并补测试。"
        )
    else:
        lines.append(
            "下一步建议：先通过知识库 ingest 接口索引仓库文件，再用同一个 repository_id 继续提问。"
        )
    return "\n".join(lines)


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


def _extract_paths(text: str) -> list[str]:
    path_pattern = r"[\w./-]+\.(?:py|ts|tsx|js|jsx|md|toml|yaml|yml|json|go|rs|java)"
    return _unique(re.findall(path_pattern, text))


def _extract_symbols(text: str) -> list[str]:
    candidates = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", text)
    ignored = {
        "the",
        "and",
        "for",
        "with",
        "class",
        "def",
        "api",
        "rag",
        "sse",
    }
    symbols = [
        item
        for item in candidates
        if item.lower() not in ignored and ("_" in item or item[:1].isupper())
    ]
    return _unique(symbols)


def _snippet(text: str, *, limit: int = 120) -> str:
    return text.strip().replace("\n", " ")[:limit]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
