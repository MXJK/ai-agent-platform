"""Checkpoint-safe completion requirements for bounded workspace changes."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path, PurePosixPath
import re
import shlex
from typing import Any, Iterable, Literal, TypedDict
from urllib.parse import unquote, urlsplit

from ai_agent_platform.agents.coding.models import CodingAgentState, ContextSource
from ai_agent_platform.agents.coding.text import extract_symbols


ChangeOperation = Literal["create", "update", "delete"]
RequirementStatus = Literal["pending", "satisfied", "failed"]

CONTRACT_SCHEMA_VERSION = 1
MAX_CONTRACT_ITEMS = 32
_FILE_TOKEN_RE = re.compile(
    r"(?<![\w./-])((?:[\w@+.-]+/)*[\w@+.-]+\."
    r"(?:css|html?|js|mjs|cjs|ts|tsx|jsx|py|go|rs|java|kt|json|ya?ml|toml|md))",
    re.IGNORECASE,
)
_HTML_REFERENCE_RE = re.compile(
    r"(?:href|src)\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE
)
_LOCAL_IMPORT_RE = re.compile(
    r"(?:\bfrom\s+|\bimport\s*(?:\(\s*)?|\brequire\s*\(\s*)"
    r"['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_DELETE_RE = re.compile(r"(?:\bdelete\b|\bremove\b|删除|移除)", re.IGNORECASE)


class RequiredChange(TypedDict):
    id: str
    target: str
    target_kind: str
    operation: ChangeOperation
    description: str
    source: dict[str, str]
    status: RequirementStatus
    satisfied_by: str


class RequiredValidation(TypedDict):
    id: str
    target: str
    category: str
    description: str
    status: RequirementStatus
    tool_result_call_id: str


def collect_target_resolution_evidence(state: CodingAgentState) -> dict[str, Any]:
    """Collect server-verifiable target candidates and local entry references."""

    root = _execution_root(state)
    read_files: dict[str, ContextSource] = {}
    candidate_paths: set[str] = set()
    references: list[dict[str, Any]] = []
    for source in state.get("context_sources", []):
        if not isinstance(source, ContextSource) or source.kind not in {
            "file",
            "search_match",
        }:
            continue
        source_path = _safe_relative_path(source.path, root)
        if source_path is None:
            continue
        if (root / source_path).is_file():
            candidate_paths.add(source_path)
        if source.kind != "file":
            continue
        read_files[source_path] = source
        for reference_kind, raw_reference in _source_local_references(
            source_path, source.text
        ):
            target = _local_reference_path(source_path, raw_reference, root)
            if target is None:
                continue
            exists = (root / target).is_file()
            if exists:
                candidate_paths.add(target)
            references.append(
                {
                    "source_path": source_path,
                    "reference": raw_reference,
                    "reference_kind": reference_kind,
                    "target": target,
                    "exists": exists,
                    "source_kind": "live_repository_evidence",
                }
            )
    for raw_path in _request_paths(state) + [
        str(item) for item in state.get("model_target_hints", [])
    ]:
        safe = _safe_relative_path(raw_path, root)
        if safe is not None and (root / safe).is_file():
            candidate_paths.add(safe)
    missing = [item for item in references if not item["exists"]]
    return {
        "candidate_paths": sorted(candidate_paths),
        "read_files": read_files,
        "references": references,
        "missing_local_references": missing,
    }


def resolve_change_targets(
    state: CodingAgentState,
    *,
    selected_paths: Iterable[str] = (),
    selection_source: str = "model",
) -> dict[str, Any]:
    """Resolve bounded-change targets without turning discovery clues into authority."""

    existing_status = str(state.get("target_resolution_status") or "")
    existing_targets = state.get("resolved_change_targets")
    if existing_status == "resolved" and isinstance(existing_targets, list):
        return {
            "target_resolution_status": "resolved",
            "resolved_change_targets": deepcopy(existing_targets),
            "target_resolution_reason": str(
                state.get("target_resolution_reason") or "frozen target resolution"
            ),
            "target_candidate_paths": list(state.get("target_candidate_paths", [])),
            "missing_local_references": deepcopy(
                state.get("missing_local_references", [])
            ),
            "rejected_target_paths": [],
        }

    evidence = collect_target_resolution_evidence(state)
    root = _execution_root(state)
    candidates = set(evidence["candidate_paths"])
    read_files: dict[str, ContextSource] = evidence["read_files"]
    missing = list(evidence["missing_local_references"])
    missing_by_target = {str(item["target"]): item for item in missing}
    explicit_paths = [
        safe
        for path in _request_paths(state)
        if (safe := _safe_relative_path(path, root)) is not None
    ]

    def target_item(
        target: str,
        operation: ChangeOperation,
        *,
        source_path: str,
        detail: str,
        source_kind: str = "live_repository_evidence",
        target_kind: str = "path",
    ) -> dict[str, Any]:
        return {
            "target": target,
            "target_kind": target_kind,
            "operation": operation,
            "source": {
                "kind": source_kind,
                "path": source_path,
                "detail": detail,
            },
        }

    def entry_reference_targets(paths: Iterable[str]) -> list[dict[str, Any]]:
        selected = set(paths)
        return [
            target_item(
                str(item["target"]),
                "create",
                source_path=str(item["source_path"]),
                detail=f"local reference {item['reference']}",
            )
            for item in missing
            if str(item["source_path"]) in selected
        ]

    def resolved(targets: list[dict[str, Any]], reason: str) -> dict[str, Any]:
        deduplicated = list(
            {str(item["target"]): item for item in targets}.values()
        )
        return {
            "target_resolution_status": "resolved",
            "resolved_change_targets": deduplicated,
            "target_resolution_reason": reason,
            "target_candidate_paths": sorted(candidates),
            "missing_local_references": missing,
            "rejected_target_paths": [],
        }

    requested_selection = []
    rejected: list[str] = []
    for raw in selected_paths:
        safe = _safe_relative_path(str(raw), root)
        if safe is None:
            rejected.append(str(raw))
            continue
        if selection_source == "model":
            allowed = safe in candidates and safe in read_files
        else:
            allowed = (safe in candidates and safe in read_files) or safe in missing_by_target
        if not allowed:
            rejected.append(safe)
            continue
        requested_selection.append(safe)
    if requested_selection:
        reference_targets = entry_reference_targets(requested_selection)
        direct_targets = [
            target_item(
                path,
                "update",
                source_path=path,
                detail=f"{selection_source} selected a live-read candidate",
                source_kind=(
                    "user_confirmed_live_repository_evidence"
                    if selection_source == "user"
                    else "live_repository_evidence"
                ),
            )
            for path in requested_selection
            if path not in missing_by_target
            and not any(
                str(item.get("source", {}).get("path") or "") == path
                for item in reference_targets
            )
        ]
        for path in requested_selection:
            if path in missing_by_target:
                item = missing_by_target[path]
                reference_targets.append(
                    target_item(
                        path,
                        "create",
                        source_path=str(item["source_path"]),
                        detail=f"user confirmed local reference {item['reference']}",
                        source_kind="user_confirmed_live_repository_evidence",
                    )
                )
        if reference_targets or direct_targets:
            result = resolved(
                reference_targets + direct_targets,
                f"{selection_source} selection passed live candidate validation",
            )
            result["rejected_target_paths"] = rejected
            return result
    if selected_paths and rejected:
        return {
            "target_resolution_status": "ambiguous",
            "resolved_change_targets": [],
            "target_resolution_reason": "selected target paths were outside the live candidate boundary",
            "target_candidate_paths": sorted(candidates),
            "missing_local_references": missing,
            "rejected_target_paths": rejected,
        }

    if explicit_paths:
        reference_targets = entry_reference_targets(explicit_paths)
        delete_requested = bool(
            _DELETE_RE.search(str(state.get("user_input") or ""))
        )
        direct_targets = [
            target_item(
                path,
                (
                    "delete"
                    if delete_requested
                    else "update"
                    if (root / path).exists()
                    else "create"
                ),
                source_path=path,
                detail="explicit path in current request or frozen focus files",
                source_kind="current_user_request",
            )
            for path in explicit_paths
            if not any(
                str(item.get("source", {}).get("path") or "") == path
                for item in reference_targets
            )
        ]
        if reference_targets or direct_targets:
            return resolved(
                reference_targets + direct_targets,
                "explicit current-request or focus-file targets resolved",
            )

    history_paths = [
        safe
        for path in state.get("model_target_hints", [])
        if (safe := _safe_relative_path(str(path), root)) is not None
        and safe in read_files
    ]
    if history_paths:
        reference_targets = entry_reference_targets(history_paths)
        direct_targets = [
            target_item(
                path,
                "update",
                source_path=path,
                detail="controlled-history target confirmed by live file read",
            )
            for path in history_paths
            if not any(
                str(item.get("source", {}).get("path") or "") == path
                for item in reference_targets
            )
        ]
        return resolved(
            reference_targets + direct_targets,
            "controlled-history paths were confirmed by live reads",
        )

    requested_symbols = extract_symbols(str(state.get("user_input") or ""))
    terms = [
        " ".join(str(item).casefold().split())
        for item in state.get("model_target_terms", [])
        if str(item).strip()
    ]
    terms.extend(
        symbol.casefold()
        for symbol in requested_symbols
        if symbol.casefold() not in terms
    )
    ranked: list[tuple[int, str]] = []
    for path, source in read_files.items():
        searchable = f"{path}\n{source.text}".casefold()
        matched = [term for term in terms if term in searchable]
        if not matched:
            continue
        suffix = PurePosixPath(path).suffix.casefold()
        name = PurePosixPath(path).name.casefold()
        stem = PurePosixPath(path).stem.casefold()
        score = 30 * len(matched)
        score += 40 * sum(term in {name, stem} for term in matched)
        for symbol in requested_symbols:
            if re.search(
                rf"\b(?:class|def|function|interface)\s+{re.escape(symbol)}\b",
                source.text,
                re.IGNORECASE,
            ):
                score += 80
        if "/test" in f"/{path.casefold()}" or PurePosixPath(path).name.casefold().startswith("test_"):
            score -= 10
        if suffix in {".html", ".htm"}:
            score += 20
        if any(str(item["source_path"]) == path for item in missing):
            score += 30
        ranked.append((score, path))
    if ranked:
        best_score = max(score for score, _ in ranked)
        best_paths = sorted(path for score, path in ranked if score == best_score)
        if len(best_paths) == 1:
            reference_targets = entry_reference_targets(best_paths)
            if reference_targets:
                return resolved(
                    reference_targets,
                    "a unique target-term entry exposed missing local references",
                )
            path = best_paths[0]
            return resolved(
                [
                    target_item(
                        path,
                        "update",
                        source_path=path,
                        detail="target term matched a uniquely ranked live-read file",
                    )
                ],
                "a unique live-read candidate matched the target terms",
            )
        return {
            "target_resolution_status": "ambiguous",
            "resolved_change_targets": [],
            "target_resolution_reason": "multiple equally ranked live-read targets matched the discovery terms",
            "target_candidate_paths": sorted(candidates),
            "missing_local_references": missing,
            "rejected_target_paths": [],
        }

    if requested_symbols:
        return resolved(
            [
                target_item(
                    symbol,
                    "update",
                    source_path="",
                    detail="explicit symbol in current user request",
                    source_kind="current_user_request",
                    target_kind="symbol",
                )
                for symbol in requested_symbols[:MAX_CONTRACT_ITEMS]
            ],
            "explicit current-request symbols remain verifiable contract targets",
        )

    return {
        "target_resolution_status": "unresolved",
        "resolved_change_targets": [],
        "target_resolution_reason": (
            "live candidates remain unread or none matched the trusted target terms"
            if candidates
            else "no live repository target candidate was discovered"
        ),
        "target_candidate_paths": sorted(candidates),
        "missing_local_references": missing,
        "rejected_target_paths": [],
    }


def define_completion_contract(state: CodingAgentState) -> dict[str, Any]:
    """Freeze a strict contract once, before any workspace mutation."""

    existing = state.get("change_completion_contract")
    if isinstance(existing, dict) and existing:
        return deepcopy(existing)
    if state.get("task_shape") != "bounded_change" or not state.get(
        "workspace_completion_required", True
    ):
        return _base_contract(applicable=False, generation_status="not_required")

    root = _execution_root(state)
    changes: dict[str, RequiredChange] = {}
    referenced_by: dict[str, list[str]] = {}
    target_evidence = collect_target_resolution_evidence(state)
    evidence_paths = set(target_evidence["read_files"])
    for reference in target_evidence["references"]:
        referenced_by.setdefault(str(reference["source_path"]), []).append(
            str(reference["target"])
        )

    resolution_enabled = "target_resolution_status" in state
    if resolution_enabled:
        if state.get("target_resolution_status") != "resolved":
            return _invalid_contract(
                str(state.get("target_resolution_reason") or "change target unresolved")
            )
        explicit_paths = set(_request_paths(state))
        missing_targets = {
            str(item["target"]): item
            for item in target_evidence["missing_local_references"]
        }
        requested_symbols = set(
            extract_symbols(str(state.get("user_input") or ""))
        )
        for item in state.get("resolved_change_targets", []):
            if not isinstance(item, dict):
                continue
            target_kind = str(item.get("target_kind") or "path")
            target = str(item.get("target") or "")
            operation = str(item.get("operation") or "")
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            if target_kind == "symbol":
                if operation != "update" or target not in requested_symbols:
                    continue
                item_id = _stable_id("change", "update", f"symbol:{target}")
                changes[item_id] = {
                    "id": item_id,
                    "target": target,
                    "target_kind": "symbol",
                    "operation": "update",
                    "description": (
                        f"Update the requested verifiable symbol target {target}."
                    ),
                    "source": {
                        "kind": str(source.get("kind") or "current_user_request"),
                        "path": str(source.get("path") or ""),
                        "detail": str(
                            source.get("detail")
                            or "explicit symbol in current request"
                        ),
                    },
                    "status": "pending",
                    "satisfied_by": "",
                }
                continue
            safe = _safe_relative_path(target, root)
            if safe is None or operation not in {"create", "update", "delete"}:
                continue
            if operation == "create":
                allowed = safe in explicit_paths or safe in missing_targets
            else:
                allowed = safe in explicit_paths or safe in evidence_paths
            if not allowed:
                continue
            _add_change(
                changes,
                target=safe,
                operation=operation,  # type: ignore[arg-type]
                description=(
                    f"{operation.title()} the server-validated target {safe}."
                ),
                source={
                    "kind": str(source.get("kind") or "live_repository_evidence"),
                    "path": str(source.get("path") or safe),
                    "detail": str(source.get("detail") or "resolved change target"),
                },
            )
    else:
        for reference in target_evidence["missing_local_references"]:
            _add_change(
                changes,
                target=str(reference["target"]),
                operation="create",
                description=(
                    f"Create {reference['target']}, which is referenced by "
                    f"{reference['source_path']}."
                ),
                source={
                    "kind": "live_repository_evidence",
                    "path": str(reference["source_path"]),
                    "detail": f"local reference {reference['reference']}",
                },
            )

        explicit_paths = _request_paths(state)
        delete_requested = bool(_DELETE_RE.search(str(state.get("user_input") or "")))
        for target in explicit_paths:
            safe = _safe_relative_path(target, root)
            if safe is None:
                continue
            if safe in referenced_by and any(
                referenced not in evidence_paths for referenced in referenced_by[safe]
            ):
                continue
            exists = (root / safe).exists()
            operation: ChangeOperation = (
                "delete" if delete_requested else "update" if exists else "create"
            )
            _add_change(
                changes,
                target=safe,
                operation=operation,
                description=f"{operation.title()} the requested workspace target {safe}.",
                source={
                    "kind": "current_user_request",
                    "path": safe,
                    "detail": "explicit path in current request or frozen focus files",
                },
            )

        for target in state.get("model_target_hints", []):
            safe = _safe_relative_path(str(target), root)
            if safe is None or safe not in evidence_paths:
                continue
            if safe in referenced_by and any(
                referenced not in evidence_paths for referenced in referenced_by[safe]
            ):
                continue
            _add_change(
                changes,
                target=safe,
                operation="update" if (root / safe).exists() else "create",
                description=(
                    f"Update {safe}, recovered from controlled user history and "
                    "confirmed by live repository evidence."
                ),
                source={
                    "kind": "live_repository_evidence",
                    "path": safe,
                    "detail": "controlled-history target confirmed by live file read",
                },
            )

        if not changes:
            requested_symbols = {
                item.casefold()
                for item in extract_symbols(str(state.get("user_input") or ""))
            }
            for source in state.get("context_sources", []):
                if not isinstance(source, ContextSource) or source.kind != "file":
                    continue
                source_path = _safe_relative_path(source.path, root)
                if source_path is None:
                    continue
                searchable = f"{source_path}\n{source.text}".casefold()
                if not requested_symbols or not any(
                    symbol in searchable for symbol in requested_symbols
                ):
                    continue
                _add_change(
                    changes,
                    target=source_path,
                    operation="update" if (root / source_path).exists() else "create",
                    description=(
                        f"Update {source_path}, matched by a requested symbol in live "
                        "repository evidence."
                    ),
                    source={
                        "kind": "live_repository_evidence",
                        "path": source_path,
                        "detail": "requested symbol matched live file content",
                    },
                )
            if not changes and requested_symbols:
                for symbol in sorted(requested_symbols)[:MAX_CONTRACT_ITEMS]:
                    item_id = _stable_id("change", "update", f"symbol:{symbol}")
                    changes[item_id] = {
                        "id": item_id,
                        "target": symbol,
                        "target_kind": "symbol",
                        "operation": "update",
                        "description": (
                            f"Update the requested verifiable symbol target {symbol}."
                        ),
                        "source": {
                            "kind": "current_user_request",
                            "path": "",
                            "detail": "explicit symbol in current request",
                        },
                        "status": "pending",
                        "satisfied_by": "",
                    }

    try:
        _validate_change_set(changes.values())
    except ValueError as exc:
        return _invalid_contract(str(exc))
    if not changes:
        return _invalid_contract(
            "no stable workspace target could be derived from the current request "
            "or live repository evidence"
        )

    validations: dict[str, RequiredValidation] = {}
    _add_validation(
        validations,
        category="post_change",
        target="workspace",
        description="Run a focused post-change validation beyond Diff formatting.",
    )
    for change in changes.values():
        if PurePosixPath(change["target"]).suffix.casefold() in {".js", ".mjs", ".cjs"}:
            _add_validation(
                validations,
                category="javascript_syntax",
                target=change["target"],
                description=f"Run JavaScript syntax validation for {change['target']}.",
            )
    for html_path, references in referenced_by.items():
        relevant = sorted({path for path in references if path in {c["target"] for c in changes.values()}})
        if relevant:
            _add_validation(
                validations,
                category="local_asset_references",
                target=html_path,
                description=(
                    f"Verify local assets referenced by {html_path} exist: "
                    + ", ".join(relevant)
                ),
            )

    frozen_at = _utc_now()
    contract = _base_contract(applicable=True, generation_status="frozen")
    contract.update(
        {
            "frozen": True,
            "frozen_at": frozen_at,
            "revision": 1,
            "revision_reason": "initial contract from current request and live repository evidence",
            "required_changes": list(changes.values()),
            "required_validations": list(validations.values()),
            "trace": [
                {
                    "revision": 1,
                    "reason": "initial_freeze",
                    "at": frozen_at,
                    "added_change_ids": list(changes),
                    "added_validation_ids": list(validations),
                }
            ],
        }
    )
    return _refresh_summary(contract)


def legacy_phase1_contract(state: CodingAgentState) -> dict[str, Any]:
    """Adapt a pre-contract checkpoint without weakening Phase 1 validation."""

    contract = _base_contract(applicable=True, generation_status="legacy_compatible")
    frozen_at = _utc_now()
    changed_files = sorted({str(path) for path in state.get("changed_files", []) if path})
    contract.update(
        {
            "frozen": True,
            "frozen_at": frozen_at,
            "revision": 1,
            "revision_reason": "legacy checkpoint restored without completion contract channels",
            "compatibility_mode": "legacy_phase1",
            "required_changes": [
                {
                    "id": _stable_id("change", "legacy", path),
                    "target": path,
                    "target_kind": "path",
                    "operation": "update",
                    "description": f"Legacy changed target {path}.",
                    "source": {
                        "kind": "legacy_checkpoint",
                        "path": path,
                        "detail": "preserved Phase 1 completion behavior",
                    },
                    "status": "satisfied",
                    "satisfied_by": "legacy_checkpoint",
                }
                for path in changed_files
            ],
            "required_validations": [],
            "trace": [
                {
                    "revision": 1,
                    "reason": "legacy_phase1_adapter",
                    "at": frozen_at,
                    "added_change_ids": [],
                    "added_validation_ids": [],
                }
            ],
        }
    )
    return _refresh_summary(contract)


def ensure_completion_contract(state: CodingAgentState) -> dict[str, Any]:
    contract = state.get("change_completion_contract")
    if isinstance(contract, dict) and contract:
        return deepcopy(contract)
    if any(
        result.get("ok") and result.get("name") in {"sandbox.write_file", "sandbox.apply_patch"}
        for result in state.get("tool_results", [])
    ):
        return legacy_phase1_contract(state)
    return define_completion_contract(state)


def advance_completion_contract(
    state: CodingAgentState,
    *,
    results: Iterable[dict[str, Any]] = (),
    changed_files: Iterable[str] | None = None,
    final_workspace_status_collected: bool | None = None,
    final_diff_collected: bool | None = None,
    diff_text: str | None = None,
) -> dict[str, Any]:
    """Advance requirement status monotonically using concrete tool evidence."""

    contract = ensure_completion_contract(state)
    if not contract.get("applicable") or contract.get("generation_status") == "invalid":
        return _refresh_summary(contract)
    if contract.get("compatibility_mode") == "legacy_phase1":
        if final_workspace_status_collected is not None:
            contract["final_workspace_status_collected"] = bool(final_workspace_status_collected)
        if final_diff_collected is not None:
            contract["final_diff_collected"] = bool(final_diff_collected)
        return _refresh_summary(contract)

    root = _execution_root(state)
    result_list = [item for item in results if isinstance(item, dict)]
    observed_changed = {
        str(path) for path in (changed_files if changed_files is not None else state.get("changed_files", [])) if path
    }
    for result in result_list:
        if not result.get("ok") or result.get("name") not in {"sandbox.write_file", "sandbox.apply_patch"}:
            continue
        output = result.get("result") if isinstance(result.get("result"), dict) else {}
        result_paths = {str(output.get("path") or "")}
        result_paths.update(str(path) for path in output.get("changed_files", []) if path)
        result_paths.discard("")
        for requirement in contract.get("required_changes", []):
            if requirement.get("status") == "satisfied":
                continue
            target = str(requirement.get("target") or "")
            if target not in result_paths:
                continue
            if _target_state_matches(root, target, str(requirement.get("operation"))):
                requirement["status"] = "satisfied"
                requirement["satisfied_by"] = str(result.get("call_id") or result.get("name"))

    for result in result_list:
        if result.get("name") != "sandbox.run_command":
            continue
        command = _result_command(state, result)
        matches = _matching_validations(contract, command)
        passed = _validation_passed(result)
        for requirement in matches:
            if requirement.get("status") == "satisfied":
                continue
            requirement["status"] = "satisfied" if passed else "failed"
            requirement["tool_result_call_id"] = str(result.get("call_id") or "")

    if final_workspace_status_collected is not None:
        contract["final_workspace_status_collected"] = bool(final_workspace_status_collected)
    if final_diff_collected is not None:
        contract["final_diff_collected"] = bool(final_diff_collected)
    if changed_files is not None:
        contract["final_changed_files"] = sorted(observed_changed)[:MAX_CONTRACT_ITEMS]
        for item in contract.get("required_changes", []):
            if (
                item.get("target_kind") == "symbol"
                and item.get("status") != "satisfied"
                and diff_text is not None
                and str(item.get("target") or "") in diff_text
            ):
                item["status"] = "satisfied"
                item["satisfied_by"] = "final_diff"
        contract["final_target_state_matches"] = all(
            (
                str(item.get("target") or "") in (diff_text or "")
                if item.get("target_kind") == "symbol"
                else str(item.get("target") or "") in observed_changed
                and _target_state_matches(
                    root,
                    str(item.get("target") or ""),
                    str(item.get("operation") or ""),
                )
            )
            for item in contract.get("required_changes", [])
        )
    return _refresh_summary(contract)


def extend_completion_contract(
    contract: dict[str, Any],
    *,
    additions: Iterable[RequiredChange],
    reason: str,
) -> dict[str, Any]:
    """Append evidence-backed requirements; existing entries cannot be removed."""

    updated = deepcopy(contract)
    if not updated.get("frozen") or not reason.strip():
        raise ValueError("a frozen contract and explicit extension reason are required")
    existing = {str(item.get("id")): item for item in updated.get("required_changes", [])}
    added_ids: list[str] = []
    for addition in additions:
        source_kind = str(addition.get("source", {}).get("kind") or "")
        if source_kind not in {"current_user_request", "live_repository_evidence"}:
            raise ValueError("contract extension lacks an authoritative source")
        item = deepcopy(addition)
        item_id = str(item.get("id") or "")
        if item_id in existing:
            continue
        existing[item_id] = item
        added_ids.append(item_id)
    _validate_change_set(existing.values())
    if not added_ids:
        return _refresh_summary(updated)
    updated["required_changes"] = list(existing.values())
    updated["revision"] = int(updated.get("revision", 1)) + 1
    updated["revision_reason"] = reason.strip()[:500]
    updated.setdefault("trace", []).append(
        {
            "revision": updated["revision"],
            "reason": "evidence_backed_extension",
            "detail": reason.strip()[:500],
            "at": _utc_now(),
            "added_change_ids": added_ids,
            "added_validation_ids": [],
        }
    )
    return _refresh_summary(updated)


def extend_completion_contract_from_results(
    state: CodingAgentState,
    *,
    results: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Append requirements proved by newly read live HTML evidence."""

    contract = ensure_completion_contract(state)
    if (
        contract.get("generation_status") != "frozen"
        or contract.get("compatibility_mode") != "strict"
    ):
        return contract
    root = _execution_root(state)
    known_targets = {
        str(item.get("target") or "")
        for item in contract.get("required_changes", [])
    }
    additions: list[RequiredChange] = []
    validation_additions: dict[str, RequiredValidation] = {}
    evidence_call_ids: list[str] = []
    for result in results:
        output = result.get("result")
        if (
            not result.get("ok")
            or result.get("name") != "repo.read_file"
            or not isinstance(output, dict)
        ):
            continue
        source_path = _safe_relative_path(str(output.get("path") or ""), root)
        content = str(output.get("content") or "")
        if (
            source_path is None
            or PurePosixPath(source_path).suffix.casefold() not in {".html", ".htm"}
            or not content
        ):
            continue
        local_targets: list[str] = []
        for raw_reference in _HTML_REFERENCE_RE.findall(content):
            target = _local_reference_path(source_path, raw_reference, root)
            if target is None or target in known_targets or (root / target).exists():
                continue
            operation: ChangeOperation = "create"
            item_id = _stable_id("change", operation, target)
            additions.append(
                {
                    "id": item_id,
                    "target": target,
                    "target_kind": "path",
                    "operation": operation,
                    "description": (
                        f"Create {target}, newly proved by live read of {source_path}."
                    ),
                    "source": {
                        "kind": "live_repository_evidence",
                        "path": source_path,
                        "detail": f"local reference {raw_reference}",
                    },
                    "status": "pending",
                    "satisfied_by": "",
                }
            )
            known_targets.add(target)
            local_targets.append(target)
            if PurePosixPath(target).suffix.casefold() in {".js", ".mjs", ".cjs"}:
                _add_validation(
                    validation_additions,
                    category="javascript_syntax",
                    target=target,
                    description=f"Run JavaScript syntax validation for {target}.",
                )
        if local_targets:
            evidence_call_ids.append(str(result.get("call_id") or "repo.read_file"))
            _add_validation(
                validation_additions,
                category="local_asset_references",
                target=source_path,
                description=(
                    f"Verify newly discovered local assets referenced by {source_path} "
                    "exist: " + ", ".join(sorted(local_targets))
                ),
            )
    if not additions:
        return contract
    reason = (
        "new live repository evidence from " + ", ".join(evidence_call_ids)
    )
    updated = extend_completion_contract(
        contract,
        additions=additions,
        reason=reason,
    )
    known_validation_ids = {
        str(item.get("id")) for item in updated.get("required_validations", [])
    }
    added_validation_ids: list[str] = []
    for item_id, item in validation_additions.items():
        if item_id in known_validation_ids:
            continue
        updated["required_validations"].append(item)
        added_validation_ids.append(item_id)
    if added_validation_ids:
        updated["trace"][-1]["added_validation_ids"] = added_validation_ids
    return _refresh_summary(updated)


def completion_contract_state(state: CodingAgentState) -> str:
    if state.get("task_shape") != "bounded_change":
        return "not_required"
    contract = state.get("change_completion_contract")
    if not isinstance(contract, dict) or not contract:
        return "legacy_phase1"
    if not contract.get("applicable"):
        return "not_required"
    if contract.get("generation_status") == "invalid":
        return "invalid"
    if contract.get("compatibility_mode") == "legacy_phase1":
        return "legacy_phase1"
    return "satisfied" if contract.get("completion_contract_satisfied") else "unresolved"


def completion_contract_summary(contract: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(contract, dict) or not contract:
        return {}
    return {
        key: deepcopy(contract.get(key))
        for key in (
            "schema_version",
            "applicable",
            "compatibility_mode",
            "generation_status",
            "generation_error",
            "frozen",
            "frozen_at",
            "revision",
            "revision_reason",
            "required_changes",
            "required_validations",
            "unresolved_changes",
            "unresolved_validations",
            "completion_contract_satisfied",
            "final_workspace_status_collected",
            "final_diff_collected",
            "final_target_state_matches",
        )
        if key in contract
    }


def completion_contract_prompt(contract: dict[str, Any]) -> str:
    summary = completion_contract_summary(contract)
    unresolved = [
        {
            "id": item.get("id"),
            "target": item.get("target"),
            "operation": item.get("operation"),
            "description": item.get("description"),
        }
        for item in summary.get("required_changes", [])
        if item.get("status") != "satisfied"
    ]
    validations = [
        {
            "id": item.get("id"),
            "target": item.get("target"),
            "category": item.get("category"),
            "description": item.get("description"),
        }
        for item in summary.get("required_validations", [])
        if item.get("status") != "satisfied"
    ]
    return (
        "The frozen ChangeCompletionContract is authoritative and cannot be shrunk. "
        f"Complete these unresolved changes: {unresolved}. "
        f"Then complete these validations: {validations}. "
        "Final workspace status and Diff are collected by the runtime."
    )


def _base_contract(*, applicable: bool, generation_status: str) -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "applicable": applicable,
        "compatibility_mode": "strict",
        "generation_status": generation_status,
        "generation_error": "",
        "frozen": False,
        "frozen_at": "",
        "revision": 0,
        "revision_reason": "",
        "required_changes": [],
        "required_validations": [],
        "unresolved_changes": [],
        "unresolved_validations": [],
        "completion_contract_satisfied": not applicable,
        "final_workspace_status_collected": False,
        "final_diff_collected": False,
        "final_target_state_matches": False,
        "final_changed_files": [],
        "trace": [],
    }


def _invalid_contract(error: str) -> dict[str, Any]:
    contract = _base_contract(applicable=True, generation_status="invalid")
    contract["generation_error"] = error[:1000]
    contract["frozen_at"] = _utc_now()
    return contract


def _refresh_summary(contract: dict[str, Any]) -> dict[str, Any]:
    contract["unresolved_changes"] = [
        str(item.get("id"))
        for item in contract.get("required_changes", [])
        if item.get("status") != "satisfied"
    ]
    contract["unresolved_validations"] = [
        str(item.get("id"))
        for item in contract.get("required_validations", [])
        if item.get("status") != "satisfied"
    ]
    strict_complete = bool(contract.get("required_changes")) and not (
        contract["unresolved_changes"] or contract["unresolved_validations"]
    )
    if contract.get("compatibility_mode") == "legacy_phase1":
        strict_complete = False
    contract["completion_contract_satisfied"] = bool(
        strict_complete
        and contract.get("final_workspace_status_collected")
        and contract.get("final_diff_collected")
        and contract.get("final_target_state_matches")
    )
    return contract


def _request_paths(state: CodingAgentState) -> list[str]:
    values = [str(item) for item in state.get("focus_files", [])]
    values.extend(match.group(1) for match in _FILE_TOKEN_RE.finditer(str(state.get("user_input") or "")))
    return list(dict.fromkeys(value.strip("'\"`()[]{}.,;:") for value in values if value))[:MAX_CONTRACT_ITEMS]


def _source_local_references(source_path: str, text: str) -> list[tuple[str, str]]:
    suffix = PurePosixPath(source_path).suffix.casefold()
    references: list[tuple[str, str]] = []
    if suffix in {".html", ".htm"}:
        references.extend(("html_attribute", item) for item in _HTML_REFERENCE_RE.findall(text))
    if suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}:
        for item in _LOCAL_IMPORT_RE.findall(text):
            path = urlsplit(unquote(item.strip())).path
            if path.startswith(("./", "../")) and PurePosixPath(path).suffix:
                references.append(("module_import", item))
    return list(dict.fromkeys(references))


def _local_reference_path(source_path: str, reference: str, root: Path) -> str | None:
    normalized_reference = unquote(reference.strip())
    parsed = urlsplit(normalized_reference)
    if parsed.scheme or parsed.netloc or normalized_reference.startswith(
        ("//", "#", "data:")
    ):
        return None
    raw_path = parsed.path.strip()
    if not raw_path or raw_path.startswith("/"):
        return None
    combined = (root / PurePosixPath(source_path).parent / raw_path).resolve()
    if combined != root and root not in combined.parents:
        return None
    return _safe_relative_path(combined.relative_to(root).as_posix(), root)


def _safe_relative_path(path: str, root: Path) -> str | None:
    candidate = PurePosixPath(str(path).replace("\\", "/"))
    if candidate.is_absolute() or not str(candidate) or ".." in candidate.parts:
        return None
    normalized = str(candidate)
    resolved = (root / normalized).resolve()
    if resolved != root and root not in resolved.parents:
        return None
    return normalized


def _execution_root(state: CodingAgentState) -> Path:
    return Path(state.get("execution_root") or state.get("workspace_root") or ".").resolve()


def _add_change(
    changes: dict[str, RequiredChange],
    *,
    target: str,
    operation: ChangeOperation,
    description: str,
    source: dict[str, str],
) -> None:
    item_id = _stable_id("change", operation, target)
    changes.setdefault(
        item_id,
        {
            "id": item_id,
            "target": target,
            "target_kind": "path",
            "operation": operation,
            "description": description[:500],
            "source": source,
            "status": "pending",
            "satisfied_by": "",
        },
    )


def _add_validation(
    validations: dict[str, RequiredValidation],
    *,
    category: str,
    target: str,
    description: str,
) -> None:
    item_id = _stable_id("validation", category, target)
    validations.setdefault(
        item_id,
        {
            "id": item_id,
            "target": target,
            "category": category,
            "description": description[:500],
            "status": "pending",
            "tool_result_call_id": "",
        },
    )


def _validate_change_set(items: Iterable[RequiredChange]) -> None:
    changes = list(items)
    if len(changes) > MAX_CONTRACT_ITEMS:
        raise ValueError(f"completion contract exceeds {MAX_CONTRACT_ITEMS} change items")
    paths: list[PurePosixPath] = []
    for item in changes:
        if item.get("operation") not in {"create", "update", "delete"}:
            raise ValueError("completion contract contains an invalid operation")
        if item.get("target_kind") == "symbol":
            symbol = str(item.get("target") or "")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{1,199}", symbol):
                raise ValueError("completion contract contains an invalid symbol target")
            continue
        path = PurePosixPath(str(item.get("target") or ""))
        if not str(path) or path.is_absolute() or ".." in path.parts:
            raise ValueError("completion contract contains an unsafe target path")
        for existing in paths:
            if existing == path or existing in path.parents or path in existing.parents:
                raise ValueError("completion contract contains duplicate or parent-child target paths")
        paths.append(path)


def _target_state_matches(root: Path, target: str, operation: str) -> bool:
    path = root / target
    return not path.exists() if operation == "delete" else path.is_file()


def _validation_passed(result: dict[str, Any]) -> bool:
    output = result.get("result")
    return bool(result.get("ok")) and isinstance(output, dict) and output.get("exit_code") == 0


def _result_command(state: CodingAgentState, result: dict[str, Any]) -> str:
    call_id = str(result.get("call_id") or "")
    for call in reversed(state.get("tool_calls", [])):
        if call.call_id == call_id:
            return str(call.arguments.get("command") or "")
    output = result.get("result")
    return str(output.get("command") or "") if isinstance(output, dict) else ""


def _matching_validations(contract: dict[str, Any], command: str) -> list[dict[str, Any]]:
    lowered = command.casefold()
    try:
        argv = [part.casefold() for part in shlex.split(command)]
    except ValueError:
        argv = lowered.split()
    diff_only = "git" in argv and "diff" in argv and "--check" in argv
    matches: list[dict[str, Any]] = []
    changes = {str(item.get("target")) for item in contract.get("required_changes", [])}
    for requirement in contract.get("required_validations", []):
        category = requirement.get("category")
        target = str(requirement.get("target") or "")
        if category == "post_change" and command.strip() and not diff_only:
            matches.append(requirement)
        elif category == "javascript_syntax":
            if "node" in argv and "--check" in argv and target.casefold() in argv:
                matches.append(requirement)
        elif category == "local_asset_references":
            local_targets = [path for path in changes if PurePosixPath(path).suffix.casefold() in {".css", ".js", ".mjs", ".cjs"}]
            if local_targets and all(path.casefold() in lowered for path in local_targets) and any(
                marker in lowered for marker in ("exists", "is_file", "test -f", "path(")
            ):
                matches.append(requirement)
    return matches


def _stable_id(kind: str, operation: str, target: str) -> str:
    digest = hashlib.sha256(f"{kind}:{operation}:{target}".encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{digest}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "RequiredChange",
    "RequiredValidation",
    "advance_completion_contract",
    "collect_target_resolution_evidence",
    "completion_contract_prompt",
    "completion_contract_state",
    "completion_contract_summary",
    "define_completion_contract",
    "ensure_completion_contract",
    "extend_completion_contract",
    "extend_completion_contract_from_results",
    "legacy_phase1_contract",
    "resolve_change_targets",
]
