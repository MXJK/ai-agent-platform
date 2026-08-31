"""Deterministic task shapes, evidence contracts, and progress accounting."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Literal, Sequence

from ai_agent_platform.agents.coding.models import ContextSource
from ai_agent_platform.agents.coding.text import extract_paths
from ai_agent_platform.integrations.tools import ToolSpec


TaskShape = Literal[
    "overview",
    "targeted_read",
    "bounded_change",
    "investigation",
    "broad_review",
]

TASK_SHAPES: tuple[TaskShape, ...] = (
    "overview",
    "targeted_read",
    "bounded_change",
    "investigation",
    "broad_review",
)

_OVERVIEW_EXACT = frozenset(
    {
        "分析下当前项目",
        "分析当前项目",
        "看看当前项目",
        "介绍一下这个项目",
        "说明项目结构",
        "这个仓库有哪些文件",
        "summarize this project",
        "project overview",
    }
)
_OVERVIEW_SUBJECTS = (
    "当前项目",
    "这个项目",
    "项目结构",
    "这个仓库",
    "current project",
    "this project",
    "project overview",
    "repository overview",
)
_OVERVIEW_ACTIONS = (
    "分析",
    "看看",
    "介绍",
    "说明",
    "干什么",
    "做什么",
    "结构",
    "有哪些文件",
    "summarize",
    "overview",
    "introduce",
    "what does",
)
_CHANGE_MARKERS = (
    "修改",
    "新增",
    "实现",
    "修复",
    "重构",
    "删除",
    "改成",
    "加上",
    "接入",
    "创建",
    "change",
    "implement",
    "fix",
    "refactor",
    "delete",
    "add ",
    "create ",
)
_INVESTIGATION_MARKERS = (
    "故障",
    "报错",
    "异常",
    "失败",
    "问题",
    "原因",
    "排查",
    "调查",
    "诊断",
    "bug",
    "error",
    "failure",
    "traceback",
    "investigate",
    "diagnose",
    "root cause",
)
_VALIDATION_MARKERS = (
    "验证",
    "测试",
    "复现",
    "pytest",
    "test",
    "verify",
    "validate",
    "reproduce",
)
_REVIEW_MARKERS = (
    "审查",
    "评审",
    "review",
    "audit",
    "全面分析",
    "整体分析",
)
_TOOL_REQUEST_MARKERS = (
    " tool",
    "工具",
    "using ",
    "use the ",
)
_CONTINUATION_MARKERS = (
    "继续读取",
    "继续读",
    "keep reading",
    "until the hard budget",
    "until hard budget",
)

_CONTRACTS: dict[TaskShape, dict[str, Any]] = {
    "overview": {
        "required_evidence": [
            "project_purpose",
            "major_modules",
            "run_entrypoint",
            "technology_stack",
        ],
        "allowed_tool_families": ["list", "find", "search", "read", "collect_evidence"],
        "max_model_requests": 5,
        "soft_tool_rounds": 2,
        "max_tool_rounds": 3,
        "soft_tool_calls": 8,
        "max_tool_calls": 12,
        "max_evidence_tokens": 12000,
        "max_extension_rounds": 1,
    },
    "targeted_read": {
        "required_evidence": ["target_location", "target_behavior"],
        "allowed_tool_families": ["search", "read", "collect_evidence"],
        "max_model_requests": 8,
        "soft_tool_rounds": 4,
        "max_tool_rounds": 6,
        "soft_tool_calls": 12,
        "max_tool_calls": 20,
        "max_evidence_tokens": 16000,
        "max_extension_rounds": 1,
    },
    "bounded_change": {
        "required_evidence": ["current_behavior", "applied_change"],
        "allowed_tool_families": ["search", "read", "patch", "test"],
        "max_model_requests": 26,
        "soft_tool_rounds": 12,
        "max_tool_rounds": 24,
        "soft_tool_calls": 36,
        "max_tool_calls": 72,
        "max_evidence_tokens": 24000,
        "max_extension_rounds": 1,
    },
    "investigation": {
        "required_evidence": ["symptom_context", "cause_evidence"],
        "allowed_tool_families": ["search", "read", "log", "test"],
        "max_model_requests": 20,
        "soft_tool_rounds": 10,
        "max_tool_rounds": 18,
        "soft_tool_calls": 30,
        "max_tool_calls": 54,
        "max_evidence_tokens": 24000,
        "max_extension_rounds": 1,
    },
    "broad_review": {
        "required_evidence": [
            "scope_inventory",
            "representative_evidence",
            "review_findings",
        ],
        "allowed_tool_families": ["broad_read_only"],
        "max_model_requests": 12,
        "soft_tool_rounds": 6,
        "max_tool_rounds": 10,
        "soft_tool_calls": 20,
        "max_tool_calls": 32,
        "max_evidence_tokens": 20000,
        "max_extension_rounds": 1,
    },
}

_STOP_CONDITIONS = [
    "required_evidence_satisfied",
    "one_round_without_new_evidence",
    "duplicate_equivalent_tool_call",
    "soft_budget_requires_completion",
    "explicit_unresolved_requirement_allows_limited_extension",
    "hard_budget_preserves_partial_or_blocked",
    "final_answer_tools_empty",
]

_OVERVIEW_TOOLS = (
    "repo.list_files",
    "repo.find_files",
    "repo.search_code",
    "repo.read_file",
    "repo.collect_evidence",
)
_TARGETED_REPO_TOOLS = (
    "repo.list_files",
    "repo.find_files",
    "repo.search_code",
    "repo.read_file",
    "repo.collect_evidence",
    "run.read_artifact",
    "file_symbol_locator",
    "code_explainer",
)
_CHANGE_TOOLS = (
    *_TARGETED_REPO_TOOLS,
    "sandbox.workspace_status",
    "sandbox.git_diff",
    "sandbox.apply_patch",
    "sandbox.write_file",
    "sandbox.run_command",
    "change_planner",
    "test_designer",
    "agent.request_user_input",
    "agent.load_skill",
)
_INVESTIGATION_TOOLS = (
    *_TARGETED_REPO_TOOLS,
    "sandbox.workspace_status",
    "sandbox.git_diff",
    "sandbox.run_command",
    "bug_investigator",
    "test_designer",
    "agent.request_user_input",
    "agent.load_skill",
)


def normalize_task_text(text: str) -> str:
    """Normalize width, case, whitespace, and non-semantic punctuation."""

    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    normalized = re.sub(r"[\s\u3000]+", " ", normalized).strip()
    return re.sub(r"[，。！？、；：,.!?;:]+$", "", normalized).strip()


def classify_task_shape(
    user_input: str,
    *,
    paths: Sequence[str] = (),
    symbols: Sequence[str] = (),
    intent: str = "repository_question",
    context_route: str = "repo",
) -> TaskShape:
    """Combine normalized language signals with extracted targets and routing."""

    normalized = normalize_task_text(user_input)
    has_change = intent == "change_planning" or _contains_any(normalized, _CHANGE_MARKERS)
    has_investigation = (
        intent == "bug_investigation"
        or _contains_any(normalized, _INVESTIGATION_MARKERS)
    )
    has_validation = intent == "test_strategy" or _contains_any(
        normalized, _VALIDATION_MARKERS
    )
    explicit_targets = bool(paths or symbols)
    overview_cue = normalized in _OVERVIEW_EXACT or (
        _contains_any(normalized, _OVERVIEW_SUBJECTS)
        and _contains_any(normalized, _OVERVIEW_ACTIONS)
    )

    if has_change:
        return "bounded_change"
    if has_investigation or has_validation:
        return "investigation"
    if overview_cue and not explicit_targets:
        return "overview"
    if (
        explicit_targets
        or intent == "repo_navigation"
        or _contains_any(normalized, _TOOL_REQUEST_MARKERS)
    ):
        return "targeted_read"
    if _contains_any(normalized, _REVIEW_MARKERS):
        return "broad_review"
    if context_route not in {"repo", "hybrid"}:
        return "targeted_read"
    return "broad_review"


def build_evidence_contract(
    task_shape: TaskShape,
    *,
    user_input: str = "",
) -> dict[str, Any]:
    contract = {
        key: list(value) if isinstance(value, list) else value
        for key, value in _CONTRACTS[task_shape].items()
    }
    normalized = normalize_task_text(user_input)
    if _contains_any(normalized, _TOOL_REQUEST_MARKERS):
        required = list(contract["required_evidence"])
        if "requested_tool_evidence" not in required:
            required.append("requested_tool_evidence")
        contract["required_evidence"] = required
    if _contains_any(normalized, _CONTINUATION_MARKERS):
        required = list(contract["required_evidence"])
        if "continuation_requested" not in required:
            required.append("continuation_requested")
        contract["required_evidence"] = required
    if len(extract_paths(user_input)) >= 2:
        required = list(contract["required_evidence"])
        if "cross_target_evidence" not in required:
            required.append("cross_target_evidence")
        contract["required_evidence"] = required
    if task_shape in {"bounded_change", "investigation"} and _contains_any(
        normalized, _VALIDATION_MARKERS
    ):
        required = list(contract["required_evidence"])
        if "validation_result" not in required:
            required.append("validation_result")
        contract["required_evidence"] = required
    contract["stop_conditions"] = list(_STOP_CONDITIONS)
    return contract


def freeze_tool_profile(
    task_shape: TaskShape,
    specs: Sequence[ToolSpec],
    *,
    user_input: str = "",
    explicit_tool_names: Sequence[str] = (),
    skill_requested: bool = False,
) -> list[str]:
    """Freeze a stable ordered intersection with the Run's effective pool."""

    by_name = {spec.name: spec for spec in specs}
    explicit = {str(name) for name in explicit_tool_names if str(name)}
    normalized = normalize_task_text(user_input)

    def dynamic_tool(spec: ToolSpec) -> bool:
        provider = str(getattr(spec, "provider", "") or "")
        return spec.name.startswith("mcp.") or provider.startswith("mcp")

    def named_in_request(spec: ToolSpec) -> bool:
        leaf = re.sub(r"[._-]+", " ", spec.name.split(".")[-1]).strip()
        return bool(leaf and len(leaf) >= 4 and leaf in normalized)

    external_requested = bool(explicit) or _contains_any(
        normalized,
        (
            "mcp",
            "external",
            "plugin",
            " tool",
            "tool ",
            "外部",
            "联网",
            "插件",
            "工具",
            "http://",
            "https://",
        ),
    ) or any(named_in_request(spec) for spec in specs if dynamic_tool(spec))
    skill_requested = skill_requested or _contains_any(
        normalized,
        ("skill", "技能"),
    )

    def lazily_visible(spec: ToolSpec) -> bool:
        if dynamic_tool(spec):
            return external_requested and (
                not explicit or spec.name in explicit
            )
        if spec.name == "agent.load_skill":
            return skill_requested
        return True

    if task_shape == "overview":
        preferred = _OVERVIEW_TOOLS
        return [name for name in preferred if name in by_name]
    if task_shape == "bounded_change":
        preferred = _CHANGE_TOOLS
        allowed = lambda spec: (
            spec.permission_level == "read_only"
            or not spec.name.startswith("sandbox.")
        )
    elif task_shape == "investigation":
        preferred = _INVESTIGATION_TOOLS
        allowed = lambda spec: spec.permission_level == "read_only"
    elif task_shape == "targeted_read":
        preferred = _TARGETED_REPO_TOOLS + (
            "agent.request_user_input",
        )
        allowed = lambda spec: spec.permission_level == "read_only"
    else:
        preferred = _OVERVIEW_TOOLS + (
            "run.read_artifact",
            "agent.request_user_input",
            "agent.load_skill",
        )
        allowed = lambda spec: spec.permission_level == "read_only"

    ordered = [
        name
        for name in preferred
        if name in by_name and lazily_visible(by_name[name])
    ]
    for spec in sorted(specs, key=lambda item: item.name):
        if spec.name not in ordered and allowed(spec) and lazily_visible(spec):
            ordered.append(spec.name)
    return ordered


def model_visible_tool_specs(specs: Sequence[ToolSpec]) -> list[ToolSpec]:
    """Project the frozen profile to the exact schema list sent to the model."""

    ordered = sorted(specs, key=lambda item: item.name)
    if any(spec.name == "repo.collect_evidence" for spec in ordered):
        hidden_children = {
            "repo.find_files",
            "repo.list_files",
            "repo.read_file",
            "repo.search_code",
        }
        ordered = [spec for spec in ordered if spec.name not in hidden_children]
    return ordered


def task_budget(
    state: dict[str, Any],
    name: str,
    fallback: int,
) -> int:
    contract = state.get("evidence_contract")
    if not isinstance(contract, dict):
        return fallback
    value = contract.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        return fallback
    if name in {
        "max_model_requests",
        "soft_tool_rounds",
        "max_tool_rounds",
        "soft_tool_calls",
        "max_tool_calls",
    }:
        return min(int(value), int(fallback))
    return int(value)


def evidence_contract_satisfied(state: dict[str, Any]) -> bool:
    contract = state.get("evidence_contract")
    if not isinstance(contract, dict):
        return False
    required = {str(item) for item in contract.get("required_evidence", [])}
    coverage = {str(item) for item in state.get("evidence_coverage", [])}
    return bool(required) and required.issubset(coverage)


def update_evidence_progress(
    state: dict[str, Any],
    *,
    context_sources: Sequence[ContextSource] = (),
    results: Sequence[dict[str, Any]] = (),
    bundles: Sequence[dict[str, Any]] = (),
    completed_round: bool,
) -> dict[str, Any]:
    """Return checkpoint-safe evidence progress after one observation boundary."""

    prior_keys = {str(item) for item in state.get("evidence_keys", [])}
    keys = set(prior_keys)
    for source in context_sources:
        keys.add(
            "source:"
            + str(getattr(source, "path", ""))
            + ":"
            + str(getattr(source, "content_hash", ""))
        )
    for result in results:
        key = _result_evidence_key(result)
        if key:
            keys.add(key)
    new_evidence_count = max(0, len(keys) - len(prior_keys))

    contract = state.get("evidence_contract") or {}
    required = [str(item) for item in contract.get("required_evidence", [])]
    prior_coverage = [str(item) for item in state.get("evidence_coverage", [])]
    coverage = list(prior_coverage)
    observed = _semantic_coverage(
        state,
        context_sources=context_sources,
        results=results,
        bundles=bundles,
    )
    for item in required:
        if item in observed and item not in coverage:
            coverage.append(item)
    coverage_delta = len(coverage) - len(prior_coverage)
    unresolved = [item for item in required if item not in coverage]
    return {
        "evidence_keys": sorted(keys),
        "evidence_coverage": coverage,
        "new_evidence_count": new_evidence_count,
        "coverage_delta": coverage_delta,
        "unresolved_requirements": unresolved,
        "evidence_rounds_completed": int(state.get("evidence_rounds_completed", 0))
        + (1 if completed_round else 0),
        "evidence_contract_satisfied": bool(required) and not unresolved,
    }


def clamp_evidence_call(
    call: Any,
    *,
    max_evidence_tokens: int,
    max_child_calls: int | None = None,
) -> Any:
    if getattr(call, "name", "") != "repo.collect_evidence":
        return call
    from ai_agent_platform.integrations.tools import ToolCall

    arguments = dict(getattr(call, "arguments", {}) or {})
    requested = arguments.get("max_evidence_tokens")
    if not isinstance(requested, int) or isinstance(requested, bool):
        requested = max_evidence_tokens
    arguments["max_evidence_tokens"] = min(requested, max_evidence_tokens)
    if max_child_calls is not None:
        child_budget = max(2, int(max_child_calls))
        queries = arguments.get("queries")
        if not isinstance(queries, list):
            queries = []
        max_queries = max(0, min(4, (child_budget - 2) // 2))
        queries = queries[:max_queries]
        arguments["queries"] = queries
        requested_files = arguments.get("max_files", 8)
        if not isinstance(requested_files, int) or isinstance(requested_files, bool):
            requested_files = 8
        # Executor cost is one inventory + two calls per query + bounded reads.
        remaining_reads = max(1, child_budget - 1 - (2 * len(queries)))
        arguments["max_files"] = min(requested_files, remaining_reads)
    return ToolCall(
        name=call.name,
        arguments=arguments,
        call_id=call.call_id,
        source=call.source,
    )


def _semantic_coverage(
    state: dict[str, Any],
    *,
    context_sources: Sequence[ContextSource],
    results: Sequence[dict[str, Any]],
    bundles: Sequence[dict[str, Any]],
) -> set[str]:
    paths = [str(getattr(source, "path", "")) for source in context_sources]
    text = "\n".join(
        str(getattr(source, "text", ""))[:12000] for source in context_sources
    ).casefold()
    successful = [item for item in results if item.get("ok")]
    result_names = {str(item.get("name") or "") for item in successful}
    observed: set[str] = set()

    for bundle in bundles:
        observed.update(str(item) for item in bundle.get("coverage", []))

    has_read = any(
        name in {"repo.read_file", "repo.search_code", "repo.collect_evidence"}
        for name in result_names
    ) or bool(context_sources)
    has_inventory = any(
        name in {"repo.list_files", "repo.find_files"} for name in result_names
    )
    full_file_sources = [
        source
        for source in context_sources
        if getattr(source, "kind", "") in {"file", "project_instruction"}
    ]

    if any(path.casefold().endswith(("readme.md", "readme.rst", "readme")) for path in paths):
        observed.add("project_purpose")
    if has_inventory or len({path.split("/", 1)[0] for path in paths if path}) >= 2:
        observed.update({"major_modules", "scope_inventory"})
    entry_names = ("main.py", "app.py", "__main__.py", "cli.py", "manage.py", "index.js")
    if any(path.casefold().endswith(entry_names) for path in paths) or any(
        marker in text
        for marker in ("uvicorn", "python -m", "npm run", "fastapi(", "if __name__")
    ):
        observed.add("run_entrypoint")
    stack_names = (
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "go.mod",
        "cargo.toml",
        "dockerfile",
        "docker-compose.yml",
        "compose.yaml",
    )
    if any(path.casefold().endswith(stack_names) for path in paths) or any(
        marker in text
        for marker in ("fastapi", "langgraph", "pytest", "typescript", "react", "postgres")
    ):
        observed.add("technology_stack")

    if has_read:
        observed.update({"target_location", "current_behavior", "symptom_context"})
    if full_file_sources or "repo.read_file" in result_names:
        observed.update({"target_behavior", "representative_evidence"})
    if any(name in {"repo.search_code", "repo.read_file"} for name in result_names) and len(successful) >= 2:
        observed.add("cause_evidence")
    if any(
        name
        not in {
            "repo.list_files",
            "repo.find_files",
            "repo.search_code",
            "repo.read_file",
            "repo.collect_evidence",
            "sandbox.workspace_status",
            "sandbox.git_diff",
        }
        for name in result_names
    ):
        observed.add("requested_tool_evidence")
    if (
        int(state.get("evidence_rounds_completed", 0)) >= 1
        and any(name in {"repo.read_file", "repo.search_code"} for name in result_names)
    ):
        observed.add("cross_target_evidence")
    if result_names.intersection({"sandbox.apply_patch", "sandbox.write_file"}):
        observed.update({"applied_change", "current_behavior"})
    if "sandbox.run_command" in result_names:
        observed.add("validation_result")
    if state.get("change_iteration", 0) > 0:
        observed.add("applied_change")
    return observed


def _result_evidence_key(result: dict[str, Any]) -> str:
    if result.get("durable_replay"):
        return ""
    payload = {
        "name": result.get("name"),
        "ok": result.get("ok"),
        "result": result.get("result"),
        "error_code": result.get("error_code"),
        "error": result.get("error"),
        "artifact_id": result.get("artifact_id"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return "result:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _contains_any(text: str, markers: Sequence[str]) -> bool:
    return any(marker in text for marker in markers)


__all__ = [
    "TASK_SHAPES",
    "TaskShape",
    "build_evidence_contract",
    "clamp_evidence_call",
    "classify_task_shape",
    "evidence_contract_satisfied",
    "freeze_tool_profile",
    "normalize_task_text",
    "task_budget",
    "update_evidence_progress",
]
