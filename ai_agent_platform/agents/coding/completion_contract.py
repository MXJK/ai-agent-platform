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
    evidence_paths: set[str] = set()
    for source in state.get("context_sources", []):
        if not isinstance(source, ContextSource) or source.kind != "file":
            continue
        source_path = _safe_relative_path(source.path, root)
        if source_path is None:
            continue
        evidence_paths.add(source_path)
        if PurePosixPath(source_path).suffix.casefold() not in {".html", ".htm"}:
            continue
        for raw_reference in _HTML_REFERENCE_RE.findall(source.text):
            target = _local_reference_path(source_path, raw_reference, root)
            if target is None:
                continue
            referenced_by.setdefault(source_path, []).append(target)
            if not (root / target).exists():
                _add_change(
                    changes,
                    target=target,
                    operation="create",
                    description=(
                        f"Create {target}, which is referenced by {source_path}."
                    ),
                    source={
                        "kind": "live_repository_evidence",
                        "path": source_path,
                        "detail": f"local reference {raw_reference}",
                    },
                )

    explicit_paths = _request_paths(state)
    delete_requested = bool(_DELETE_RE.search(str(state.get("user_input") or "")))
    for target in explicit_paths:
        safe = _safe_relative_path(target, root)
        if safe is None:
            continue
        # An HTML file that supplied missing local references is evidence for those
        # deliverables, not automatically a request to rewrite the HTML itself.
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

    if not changes:
        requested_symbols = {
            item.casefold() for item in extract_symbols(str(state.get("user_input") or ""))
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


def _local_reference_path(source_path: str, reference: str, root: Path) -> str | None:
    parsed = urlsplit(unquote(reference.strip()))
    if parsed.scheme or parsed.netloc or reference.startswith(("//", "#", "data:")):
        return None
    raw_path = parsed.path.strip()
    if not raw_path or raw_path.startswith("/"):
        return None
    combined = PurePosixPath(source_path).parent / PurePosixPath(raw_path)
    return _safe_relative_path(str(combined), root)


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
    "completion_contract_prompt",
    "completion_contract_state",
    "completion_contract_summary",
    "define_completion_contract",
    "ensure_completion_contract",
    "extend_completion_contract",
    "extend_completion_contract_from_results",
    "legacy_phase1_contract",
]
