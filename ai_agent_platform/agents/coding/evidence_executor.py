"""Bounded, read-only repository evidence orchestration for native Agent runs."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Callable, Iterable

from ai_agent_platform.agents.coding.models import (
    EvidenceBundle,
    EvidenceItem,
    EvidencePlan,
)
from ai_agent_platform.agents.coding.run_artifacts import (
    build_evidence_result_artifact,
)
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry, ToolSpec
from ai_agent_platform.token_counting import estimate_text_tokens
from ai_agent_platform.tools.repository import (
    IGNORED_DIRECTORIES,
    IGNORED_DIRECTORY_PREFIXES,
)


EVIDENCE_TOOL_NAME = "repo.collect_evidence"
EVIDENCE_CHILD_TOOLS = frozenset(
    {
        "repo.list_files",
        "repo.find_files",
        "repo.search_code",
        "repo.read_file",
    }
)
EVIDENCE_STOP_CONDITIONS = (
    "required_evidence_satisfied",
    "first_evidence",
    "max_files_reached",
)
MAX_EVIDENCE_CONCURRENCY = 6
EVIDENCE_PLAN_DEFAULTS = EvidencePlan()
EVIDENCE_PLAN_KEYS = frozenset(asdict(EVIDENCE_PLAN_DEFAULTS))


class EvidencePlanValidationError(ValueError):
    """Raised when an EvidencePlan attempts to expand its capability surface."""


def evidence_plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 500},
                "maxItems": 32,
                "default": [],
            },
            "candidate_paths": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 1000},
                "maxItems": 64,
                "default": [],
            },
            "max_files": {"type": "integer", "minimum": 1, "maximum": 32, "default": 8},
            "max_depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 3},
            "max_results_per_query": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 12,
            },
            "max_chars_per_file": {
                "type": "integer",
                "minimum": 256,
                "maximum": 12000,
                "default": 8000,
            },
            "max_evidence_tokens": {
                "type": "integer",
                "minimum": 256,
                "maximum": 24000,
                "default": 12000,
            },
            "required_evidence": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 300},
                "maxItems": 32,
                "default": [],
            },
            "stop_when": {
                "type": "array",
                "items": {"type": "string", "enum": list(EVIDENCE_STOP_CONDITIONS)},
                "maxItems": 3,
                "default": [],
            },
        },
        "additionalProperties": False,
    }


def evidence_bundle_schema() -> dict[str, Any]:
    evidence_item = {
        "type": "object",
        "properties": {
            name: {"type": "string"}
            for name in ("path", "location", "summary", "snippet", "reason", "artifact_id")
        },
        "required": ["path", "location", "summary", "snippet", "reason", "artifact_id"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "coverage": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": evidence_item},
            "unresolved": {"type": "array", "items": {"type": "string"}},
            "errors": {"type": "array", "items": {"type": "object"}},
            "raw_result_count": {"type": "integer", "minimum": 0},
            "deduplicated_count": {"type": "integer", "minimum": 0},
            "truncated": {"type": "boolean"},
        },
        "required": [
            "coverage",
            "evidence",
            "unresolved",
            "errors",
            "raw_result_count",
            "deduplicated_count",
            "truncated",
        ],
        "additionalProperties": False,
    }


def evidence_tool_spec() -> ToolSpec:
    return ToolSpec(
        name=EVIDENCE_TOOL_NAME,
        description=(
            "Collect bounded repository evidence in one read-only batch. Supply search "
            "queries and candidate paths; raw list/search/read results stay in Run "
            "Artifacts and this tool returns only a compact EvidenceBundle."
        ),
        input_schema=evidence_plan_schema(),
        output_schema=evidence_bundle_schema(),
        provider="runtime",
        permission_level="read_only",
        requires_approval=False,
        accepts_context=False,
        max_output_chars=100_000,
        idempotent=True,
        permission_source="runtime_state",
    )


def register_evidence_tool(registry: ToolRegistry) -> None:
    spec = evidence_tool_spec()
    registry.register(
        spec.name,
        _runtime_evidence_tool_boundary,
        description=spec.description,
        input_schema=spec.input_schema,
        output_schema=spec.output_schema,
        provider=spec.provider,
        permission_level=spec.permission_level,
        requires_approval=spec.requires_approval,
        accepts_context=spec.accepts_context,
        risk_summary=spec.risk_summary,
        max_output_chars=spec.max_output_chars,
        idempotent=spec.idempotent,
        permission_source=spec.permission_source,
    )


def _runtime_evidence_tool_boundary(**arguments: Any) -> dict[str, Any]:
    del arguments
    raise RuntimeError(
        "repo.collect_evidence must be handled by the active Agent runtime"
    )


def normalize_evidence_plan(value: Any) -> tuple[EvidencePlan, int]:
    raw = value if isinstance(value, dict) else {}
    unknown = set(raw).difference(EVIDENCE_PLAN_KEYS)
    if unknown:
        raise EvidencePlanValidationError(
            "unsupported EvidencePlan field(s): " + ", ".join(sorted(unknown))
        )
    deduplicated = 0
    queries, removed = _normalized_strings(raw.get("queries"), limit=32, casefold=True)
    deduplicated += removed
    paths, removed = _normalized_paths(raw.get("candidate_paths"), max_depth=8, limit=64)
    deduplicated += removed
    required, removed = _normalized_strings(
        raw.get("required_evidence"), limit=32, casefold=True
    )
    deduplicated += removed
    stop_when, removed = _normalized_strings(raw.get("stop_when"), limit=3, casefold=False)
    deduplicated += removed
    stop_when = [item for item in stop_when if item in EVIDENCE_STOP_CONDITIONS]
    return EvidencePlan(
        queries=queries,
        candidate_paths=paths,
        max_files=_safe_int(raw.get("max_files"), default=8, minimum=1, maximum=32),
        max_depth=_safe_int(raw.get("max_depth"), default=3, minimum=0, maximum=8),
        max_results_per_query=_safe_int(
            raw.get("max_results_per_query"), default=12, minimum=1, maximum=50
        ),
        max_chars_per_file=_safe_int(
            raw.get("max_chars_per_file"), default=8000, minimum=256, maximum=12000
        ),
        max_evidence_tokens=_safe_int(
            raw.get("max_evidence_tokens"), default=12000, minimum=256, maximum=24000
        ),
        required_evidence=required,
        stop_when=stop_when,
    ), deduplicated


class EvidenceExecutor:
    """Execute a closed set of read-only repository calls and compact the result."""

    def __init__(
        self,
        execute_calls: Callable[[list[ToolCall], bool], list[dict[str, Any]]],
    ) -> None:
        self._execute_calls = execute_calls

    def collect(
        self,
        *,
        outer_call: ToolCall,
    ) -> tuple[EvidenceBundle, list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            plan, deduplicated = normalize_evidence_plan(outer_call.arguments)
        except EvidencePlanValidationError as exc:
            bundle: EvidenceBundle = {
                "coverage": [],
                "evidence": [],
                "unresolved": [],
                "errors": [{"code": "invalid_evidence_plan", "message": str(exc)}],
                "raw_result_count": 0,
                "deduplicated_count": 0,
                "truncated": False,
            }
            return bundle, [], []

        discovery_calls = self._discovery_calls(plan, outer_call.call_id)
        discovery_results = self._execute_bounded(discovery_calls)
        candidate_paths = list(plan.candidate_paths)
        path_reasons = {path: "candidate_path" for path in candidate_paths}
        for result in discovery_results:
            for path, reason in _paths_from_result(result):
                normalized = _normalize_path(path, max_depth=plan.max_depth)
                if normalized is None:
                    deduplicated += 1
                    continue
                if normalized in path_reasons:
                    deduplicated += 1
                    continue
                candidate_paths.append(normalized)
                path_reasons[normalized] = reason
        read_limit = 1 if "first_evidence" in plan.stop_when else plan.max_files
        bounded_paths: list[str] = []
        for path in candidate_paths:
            normalized = _normalize_path(path, max_depth=plan.max_depth)
            if normalized is None:
                deduplicated += 1
                continue
            if normalized in bounded_paths:
                deduplicated += 1
                continue
            bounded_paths.append(normalized)
            if len(bounded_paths) >= read_limit:
                break
        if len(candidate_paths) > len(bounded_paths):
            deduplicated += len(candidate_paths) - len(bounded_paths)

        read_calls = [
            ToolCall(
                call_id=_child_call_id(
                    outer_call.call_id,
                    "repo.read_file",
                    {"path": path, "max_chars": plan.max_chars_per_file},
                ),
                name="repo.read_file",
                arguments={"path": path, "max_chars": plan.max_chars_per_file},
                source="evidence_executor",
            )
            for path in bounded_paths
        ]
        read_results = self._execute_bounded(read_calls)
        raw_results = discovery_results + read_results
        artifacts: list[dict[str, Any]] = []
        artifact_by_call: dict[str, str] = {}
        enriched_results: list[dict[str, Any]] = []
        arguments_by_call = {
            call.call_id: call.arguments for call in discovery_calls + read_calls
        }
        for result in raw_results:
            arguments = dict(arguments_by_call.get(str(result.get("call_id")), {}))
            artifact = build_evidence_result_artifact(result, arguments=arguments)
            artifact_id = str(artifact["id"])
            artifacts.append(artifact)
            artifact_by_call[str(result.get("call_id"))] = artifact_id
            enriched_results.append(
                {**result, "arguments": arguments, "artifact_id": artifact_id}
            )

        evidence: list[EvidenceItem] = []
        seen_content: set[str] = set()
        coverage: list[str] = []
        errors: list[dict[str, Any]] = []
        for result in enriched_results:
            if not result.get("ok"):
                errors.append(_bundle_error(result))
                continue
            if result.get("name") != "repo.read_file":
                continue
            payload = result.get("result")
            if not isinstance(payload, dict):
                errors.append(_bundle_error(result, code="invalid_child_result"))
                continue
            content = str(payload.get("content") or "")
            content_hash = str(payload.get("content_hash") or "") or hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()
            if content_hash in seen_content:
                deduplicated += 1
                continue
            seen_content.add(content_hash)
            path = str(payload.get("path") or result.get("arguments", {}).get("path") or "")
            start = max(1, int(payload.get("start_line") or 1))
            end = max(start, int(payload.get("end_line") or start))
            matched = _matched_requirements(plan.required_evidence, path + "\n" + content)
            for item in matched:
                if item not in coverage:
                    coverage.append(item)
            evidence.append(
                {
                    "path": path,
                    "location": f"lines {start}-{end}",
                    "summary": _summary(content),
                    "snippet": content[: min(len(content), 1600)],
                    "reason": path_reasons.get(path, "discovered repository evidence"),
                    "artifact_id": str(result.get("artifact_id") or ""),
                }
            )
        unresolved = [item for item in plan.required_evidence if item not in coverage]
        truncated = any(
            _result_truncated(result) for result in enriched_results
        ) or len(candidate_paths) > read_limit
        bundle = _fit_bundle(
            {
                "coverage": coverage,
                "evidence": evidence,
                "unresolved": unresolved,
                "errors": errors,
                "raw_result_count": len(enriched_results),
                "deduplicated_count": deduplicated,
                "truncated": truncated,
            },
            max_tokens=plan.max_evidence_tokens,
        )
        return bundle, enriched_results, artifacts

    def _execute_bounded(
        self,
        calls: list[ToolCall],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for offset in range(0, len(calls), MAX_EVIDENCE_CONCURRENCY):
            batch = calls[offset : offset + MAX_EVIDENCE_CONCURRENCY]
            results.extend(self._execute_calls(batch, len(batch) > 1))
        return results

    @staticmethod
    def _discovery_calls(plan: EvidencePlan, outer_call_id: str) -> list[ToolCall]:
        specs: list[tuple[str, dict[str, Any]]] = [
            (
                "repo.list_files",
                {
                    "path": "",
                    "max_results": max(plan.max_files * 4, plan.max_results_per_query),
                    "max_depth": plan.max_depth,
                },
            )
        ]
        for query in plan.queries:
            common = {
                "query": query,
                "path": "",
                "max_results": plan.max_results_per_query,
                "max_depth": plan.max_depth,
            }
            specs.append(("repo.find_files", dict(common)))
            specs.append(("repo.search_code", {**common, "context_lines": 1}))
        calls: list[ToolCall] = []
        seen: set[str] = set()
        for name, arguments in specs:
            if name not in EVIDENCE_CHILD_TOOLS:
                raise RuntimeError(f"Evidence Executor denied tool: {name}")
            key = _canonical_call(name, arguments)
            if key in seen:
                continue
            seen.add(key)
            calls.append(
                ToolCall(
                    call_id=_child_call_id(outer_call_id, name, arguments),
                    name=name,
                    arguments=arguments,
                    source="evidence_executor",
                )
            )
        return calls


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value if minimum <= value <= maximum else default


def _normalized_strings(
    value: Any, *, limit: int, casefold: bool
) -> tuple[list[str], int]:
    if not isinstance(value, list):
        return [], 0
    output: list[str] = []
    seen: set[str] = set()
    removed = 0
    for raw in value[:limit]:
        if not isinstance(raw, str):
            removed += 1
            continue
        item = " ".join(raw.split()).strip()
        key = item.casefold() if casefold else item
        if not item or key in seen:
            removed += 1
            continue
        seen.add(key)
        output.append(item)
    removed += max(0, len(value) - limit)
    return output, removed


def _normalized_paths(value: Any, *, max_depth: int, limit: int) -> tuple[list[str], int]:
    if not isinstance(value, list):
        return [], 0
    output: list[str] = []
    removed = 0
    for raw in value[:limit]:
        path = _normalize_path(raw, max_depth=max_depth)
        if path is None or path in output:
            removed += 1
            continue
        output.append(path)
    removed += max(0, len(value) - limit)
    return output, removed


def _normalize_path(value: Any, *, max_depth: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().replace("\\", "/")
    if not text or text.startswith("/"):
        return None
    path = PurePosixPath(text)
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts or ".." in parts:
        return None
    if any(
        part in IGNORED_DIRECTORIES
        or part.startswith(IGNORED_DIRECTORY_PREFIXES)
        for part in parts
    ):
        return None
    if max(0, len(parts) - 1) > max_depth:
        return None
    return PurePosixPath(*parts).as_posix()


def _canonical_call(name: str, arguments: dict[str, Any]) -> str:
    return name + ":" + json.dumps(
        arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _child_call_id(outer_call_id: str, name: str, arguments: dict[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_call(name, arguments).encode("utf-8")).hexdigest()[:16]
    return f"{outer_call_id}:evidence:{digest}"


def _paths_from_result(result: dict[str, Any]) -> Iterable[tuple[str, str]]:
    if not result.get("ok"):
        return []
    payload = result.get("result")
    if not isinstance(payload, dict):
        return []
    name = str(result.get("name") or "")
    if name == "repo.list_files":
        return [(str(item), "repository inventory") for item in payload.get("files", [])]
    if name == "repo.find_files":
        return [(str(item), f"filename match for {payload.get('query', '')}") for item in payload.get("matches", [])]
    if name == "repo.search_code":
        return [
            (str(item.get("path") or ""), f"code match for {payload.get('query', '')}")
            for item in payload.get("matches", [])
            if isinstance(item, dict)
        ]
    return []


def _summary(content: str) -> str:
    line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    return re.sub(r"\s+", " ", line)[:240]


def _matched_requirements(requirements: list[str], haystack: str) -> list[str]:
    lowered = haystack.casefold()
    return [item for item in requirements if item.casefold() in lowered]


def _result_truncated(result: dict[str, Any]) -> bool:
    payload = result.get("result")
    return bool(
        result.get("output_truncated")
        or (isinstance(payload, dict) and payload.get("truncated"))
    )


def _bundle_error(result: dict[str, Any], *, code: str | None = None) -> dict[str, Any]:
    return {
        "call_id": str(result.get("call_id") or ""),
        "tool": str(result.get("name") or ""),
        "code": code or str(result.get("error_code") or "tool_execution_error"),
        "message": str(result.get("error") or "child call returned no usable result")[:300],
        "artifact_id": str(result.get("artifact_id") or ""),
    }


def _bundle_tokens(bundle: EvidenceBundle) -> int:
    return estimate_text_tokens(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _fit_bundle(bundle: EvidenceBundle, *, max_tokens: int) -> EvidenceBundle:
    fitted: EvidenceBundle = {
        "coverage": list(bundle["coverage"]),
        "evidence": [dict(item) for item in bundle["evidence"]],  # type: ignore[list-item]
        "unresolved": list(bundle["unresolved"]),
        "errors": [dict(item) for item in bundle["errors"]],
        "raw_result_count": int(bundle["raw_result_count"]),
        "deduplicated_count": int(bundle["deduplicated_count"]),
        "truncated": bool(bundle["truncated"]),
    }
    while _bundle_tokens(fitted) > max_tokens and fitted["evidence"]:
        longest = max(fitted["evidence"], key=lambda item: len(item["snippet"]))
        if len(longest["snippet"]) > 160:
            longest["snippet"] = longest["snippet"][: max(160, len(longest["snippet"]) // 2)]
        else:
            fitted["evidence"].pop()
        fitted["truncated"] = True
    while _bundle_tokens(fitted) > max_tokens and fitted["errors"]:
        fitted["errors"].pop()
        fitted["truncated"] = True
    while _bundle_tokens(fitted) > max_tokens and fitted["unresolved"]:
        fitted["unresolved"].pop()
        fitted["truncated"] = True
    while _bundle_tokens(fitted) > max_tokens and fitted["coverage"]:
        fitted["coverage"].pop()
        fitted["truncated"] = True
    return fitted


__all__ = [
    "EVIDENCE_CHILD_TOOLS",
    "EVIDENCE_TOOL_NAME",
    "MAX_EVIDENCE_CONCURRENCY",
    "EvidenceExecutor",
    "EvidencePlanValidationError",
    "evidence_bundle_schema",
    "evidence_plan_schema",
    "evidence_tool_spec",
    "normalize_evidence_plan",
    "register_evidence_tool",
]
