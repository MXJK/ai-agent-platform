"""Deterministic, immutable, Run-scoped tool catalog and effective pool."""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping, Sequence

from ai_agent_platform.integrations.permissions import (
    PermissionDecision,
    ToolApproval,
    ToolUseContext,
)
from ai_agent_platform.integrations.tools import (
    ToolCall,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


TOOL_CATALOG_VERSION = "tool-catalog/v1"
TOOL_POOL_SNAPSHOT_VERSION = "effective-tool-pool/v1"
_TOOL_NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$"
)
_ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}

ToolSource = Literal["base", "local", "mcp"]


class ToolCatalogError(ValueError):
    """The registered tool catalog is ambiguous or malformed."""


class ToolNameConflictError(ToolCatalogError):
    """Two sources claim the same normalized model-visible tool name."""


class ToolPoolBuildError(ValueError):
    """A requested effective pool cannot be built safely."""


class ToolPoolRestoreError(RuntimeError):
    """A persisted pool cannot be restored with identical definitions."""


@dataclass(frozen=True)
class SandboxCapabilities:
    """Sandbox operations that may be exposed during one Run."""

    available: bool = True
    mode: str = "local"
    readable: bool = True
    writable: bool = True
    command_execution: bool = True
    supported_tools: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ToolCatalogEntry:
    """One registered tool plus declarative availability constraints."""

    spec: ToolSpec
    namespace: str
    source: ToolSource
    agent_types: tuple[str, ...] = ("*",)
    run_modes: tuple[str, ...] = ("*",)
    required_model_capabilities: tuple[str, ...] = ("tool_calling",)
    required_sandbox_capabilities: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def definition_hash(self) -> str:
        return _definition_hash(self.spec)


@dataclass(frozen=True)
class ToolCatalogSummaryEntry:
    """Sensitive-value-free definition summary persisted with a Run."""

    name: str
    namespace: str
    source: str
    provider: str
    permission_level: str
    requires_approval: bool
    input_schema_hash: str
    output_schema_hash: str
    definition_hash: str
    agent_types: tuple[str, ...]
    run_modes: tuple[str, ...]
    required_model_capabilities: tuple[str, ...]
    required_sandbox_capabilities: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "namespace": self.namespace,
            "source": self.source,
            "provider": self.provider,
            "permission_level": self.permission_level,
            "requires_approval": self.requires_approval,
            "input_schema_hash": self.input_schema_hash,
            "output_schema_hash": self.output_schema_hash,
            "definition_hash": self.definition_hash,
            "agent_types": list(self.agent_types),
            "run_modes": list(self.run_modes),
            "required_model_capabilities": list(
                self.required_model_capabilities
            ),
            "required_sandbox_capabilities": list(
                self.required_sandbox_capabilities
            ),
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> "ToolCatalogSummaryEntry":
        return cls(
            name=str(value.get("name") or ""),
            namespace=str(value.get("namespace") or ""),
            source=str(value.get("source") or ""),
            provider=str(value.get("provider") or ""),
            permission_level=str(value.get("permission_level") or ""),
            requires_approval=bool(value.get("requires_approval", False)),
            input_schema_hash=str(value.get("input_schema_hash") or ""),
            output_schema_hash=str(value.get("output_schema_hash") or ""),
            definition_hash=str(value.get("definition_hash") or ""),
            agent_types=_summary_string_tuple(value.get("agent_types")),
            run_modes=_summary_string_tuple(value.get("run_modes")),
            required_model_capabilities=_summary_string_tuple(
                value.get("required_model_capabilities")
            ),
            required_sandbox_capabilities=_summary_string_tuple(
                value.get("required_sandbox_capabilities")
            ),
        )


@dataclass(frozen=True)
class ToolPoolExclusion:
    name: str
    reason: str


@dataclass(frozen=True)
class MissingSkillDependency:
    skill_name: str
    missing_tools: tuple[str, ...]


class ToolCatalog:
    """Immutable, conflict-checked catalog of registered tool definitions."""

    def __init__(
        self,
        entries: Iterable[ToolCatalogEntry],
        *,
        version: str = TOOL_CATALOG_VERSION,
    ) -> None:
        ordered = sorted(
            tuple(_copy_entry(entry) for entry in entries),
            key=_entry_sort_key,
        )
        normalized_names: dict[str, ToolCatalogEntry] = {}
        for entry in ordered:
            _validate_entry(entry)
            key = _normalized_tool_name(entry.name)
            previous = normalized_names.get(key)
            if previous is not None:
                raise ToolNameConflictError(
                    "tool name conflict after namespace normalization: "
                    f"{previous.name} ({previous.source}:{previous.spec.provider}) "
                    f"vs {entry.name} ({entry.source}:{entry.spec.provider})"
                )
            normalized_names[key] = entry
        self._entries = tuple(ordered)
        self._by_name = MappingProxyType(
            {entry.name: entry for entry in ordered}
        )
        self._version = version
        self._normalized_summary = _summary_json(
            tuple(_summary_for(entry) for entry in ordered)
        )
        self._catalog_hash = _sha256(self._normalized_summary)
        self._sealed = True

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("ToolCatalog is immutable")
        object.__setattr__(self, name, value)

    @classmethod
    def from_registry(cls, registry: ToolRegistry) -> "ToolCatalog":
        return cls(
            _entry_from_spec(spec)
            for spec in registry.list_specs()
        )

    @classmethod
    def from_sources(
        cls,
        *,
        base_tools: Sequence[ToolSpec | ToolCatalogEntry] = (),
        local_tools: Sequence[ToolSpec | ToolCatalogEntry] = (),
        mcp_tools: Sequence[ToolSpec | ToolCatalogEntry] = (),
    ) -> "ToolCatalog":
        entries: list[ToolCatalogEntry] = []
        for source, values in (
            ("base", base_tools),
            ("local", local_tools),
            ("mcp", mcp_tools),
        ):
            for value in values:
                entries.append(
                    value
                    if isinstance(value, ToolCatalogEntry)
                    else _entry_from_spec(value, source=source)
                )
        return cls(entries)

    @property
    def version(self) -> str:
        return self._version

    @property
    def catalog_hash(self) -> str:
        return self._catalog_hash

    @property
    def normalized_summary(self) -> str:
        return self._normalized_summary

    @property
    def entries(self) -> tuple[ToolCatalogEntry, ...]:
        return tuple(_copy_entry(entry) for entry in self._entries)

    def get(self, name: str) -> ToolCatalogEntry | None:
        entry = self._by_name.get(name)
        return _copy_entry(entry) if entry is not None else None


class EffectiveToolPool:
    """A frozen Run tool set that delegates reliable execution to ToolRegistry."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        catalog_version: str,
        catalog_hash: str,
        catalog_summary: str,
        entries: Sequence[ToolCatalogEntry],
        exclusions: Sequence[ToolPoolExclusion] = (),
        missing_skill_dependencies: Sequence[MissingSkillDependency] = (),
        selection_provenance: Sequence[str] = (),
        diagnostics: Sequence[str] = (),
    ) -> None:
        ordered = tuple(
            sorted(
                (_copy_entry(entry) for entry in entries),
                key=_entry_sort_key,
            )
        )
        self._registry = registry
        self._catalog_version = catalog_version
        self._catalog_hash = catalog_hash
        self._catalog_summary = catalog_summary
        self._entries = ordered
        self._by_name = MappingProxyType(
            {entry.name: entry for entry in ordered}
        )
        self._summaries = tuple(_summary_for(entry) for entry in ordered)
        self._normalized_summary = _summary_json(self._summaries)
        self._pool_hash = _sha256(self._normalized_summary)
        self._exclusions = tuple(exclusions)
        self._missing_skill_dependencies = tuple(missing_skill_dependencies)
        self._selection_provenance = tuple(str(item) for item in selection_provenance)
        self._diagnostics = tuple(str(item) for item in diagnostics)
        self._sealed = True

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("EffectiveToolPool is immutable")
        object.__setattr__(self, name, value)

    @property
    def catalog_version(self) -> str:
        return self._catalog_version

    @property
    def catalog_hash(self) -> str:
        return self._catalog_hash

    @property
    def catalog_summary(self) -> str:
        return self._catalog_summary

    @property
    def pool_version(self) -> str:
        return f"{TOOL_POOL_SNAPSHOT_VERSION}:{self._pool_hash.removeprefix('sha256:')[:16]}"

    @property
    def pool_hash(self) -> str:
        return self._pool_hash

    @property
    def normalized_summary(self) -> str:
        return self._normalized_summary

    @property
    def summary_entries(self) -> tuple[ToolCatalogSummaryEntry, ...]:
        return self._summaries

    @property
    def exclusions(self) -> tuple[ToolPoolExclusion, ...]:
        return self._exclusions

    @property
    def missing_skill_dependencies(self) -> tuple[MissingSkillDependency, ...]:
        return self._missing_skill_dependencies

    @property
    def selection_provenance(self) -> tuple[str, ...]:
        return self._selection_provenance

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return self._diagnostics

    @property
    def allowed_names(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self._entries)

    def list_specs(self, context: ToolUseContext | None = None) -> list[ToolSpec]:
        self._assert_live_definitions()
        specs = [_copy_spec(entry.spec) for entry in self._entries]
        if context is None:
            return specs
        visible = {
            spec.name for spec in self._registry.list_specs(context=context)
        }
        return [spec for spec in specs if spec.name in visible]

    def get_spec(self, name: str) -> ToolSpec | None:
        entry = self._by_name.get(name)
        if entry is None:
            return None
        self._assert_live_entry(entry)
        return _copy_spec(entry.spec)

    def select(self, allowed_names: tuple[str, ...]) -> "EffectiveToolPool":
        requested = tuple(dict.fromkeys(allowed_names))
        unknown = set(requested).difference(self._by_name)
        if unknown:
            raise ToolPoolBuildError(
                "effective pool selection contains unavailable tools: "
                + ", ".join(sorted(unknown))
            )
        selected = set(requested)
        return EffectiveToolPool(
            registry=self._registry,
            catalog_version=self.catalog_version,
            catalog_hash=self.catalog_hash,
            catalog_summary=self.catalog_summary,
            entries=tuple(
                entry for entry in self._entries if entry.name in selected
            ),
            exclusions=self.exclusions,
            missing_skill_dependencies=self.missing_skill_dependencies,
            selection_provenance=self.selection_provenance,
            diagnostics=self.diagnostics,
        )

    def call(self, tool_call: ToolCall) -> Any:
        self._require_member(tool_call.name)
        return self._registry.call(tool_call)

    def execute(
        self,
        tool_call: ToolCall,
        context: ToolUseContext | None = None,
    ) -> ToolResult:
        if tool_call.name not in self._by_name:
            return self._registry.select(()).execute(tool_call, context=context)
        self._assert_live_entry(self._by_name[tool_call.name])
        return self._registry.execute(tool_call, context=context)

    def resolve_permission(
        self,
        tool_call: ToolCall,
        context: ToolUseContext,
        *,
        phase: str = "execute",
    ) -> PermissionDecision:
        if tool_call.name not in self._by_name:
            return PermissionDecision(
                effect="deny",
                matched_rule="run.effective_tool_pool",
                reason="The tool is outside the frozen effective tool pool.",
                risk_summary="Tool is unavailable for this Run.",
            )
        self._assert_live_entry(self._by_name[tool_call.name])
        return self._registry.resolve_permission(tool_call, context, phase=phase)

    def issue_approval(
        self,
        tool_call: ToolCall,
        context: ToolUseContext,
        *,
        approved_by: str,
    ) -> ToolApproval:
        self._require_member(tool_call.name)
        return self._registry.issue_approval(
            tool_call,
            context,
            approved_by=approved_by,
        )

    def export_context(self, name: str, context: ToolUseContext) -> Any:
        return self._registry.export_context(name, context)

    def cleanup_context(self, context: ToolUseContext) -> list[str]:
        return self._registry.cleanup_context(context)

    def _require_member(self, name: str) -> None:
        entry = self._by_name.get(name)
        if entry is None:
            raise ToolPoolBuildError(
                f"tool is outside the frozen effective pool: {name}"
            )
        self._assert_live_entry(entry)

    def _assert_live_definitions(self) -> None:
        for entry in self._entries:
            self._assert_live_entry(entry)

    def _assert_live_entry(self, entry: ToolCatalogEntry) -> None:
        current = self._registry.get_spec(entry.name)
        if current is None:
            raise ToolPoolRestoreError(
                f"snapshotted tool is unavailable: {entry.name}"
            )
        if _definition_hash(current) != entry.definition_hash:
            raise ToolPoolRestoreError(
                f"snapshotted tool definition changed: {entry.name}"
            )


class ToolPoolBuilder:
    """Intersect catalog tools with all Run-scoped capability boundaries."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def build(
        self,
        *,
        catalog: ToolCatalog | None = None,
        skills: Sequence[object] = (),
        enabled_skill_names: Sequence[str] | None = None,
        agent_type: str = "coding",
        run_mode: str = "default",
        model_capabilities: Mapping[str, object] | object | None = None,
        tool_use_context: ToolUseContext,
        deny_rules: Sequence[str] = (),
        sandbox_capabilities: SandboxCapabilities | None = None,
        requested_names: Sequence[str] | None = None,
        strict_skill_dependencies: bool = False,
        selection_provenance: Sequence[str] = (),
    ) -> EffectiveToolPool:
        selected_catalog = catalog or ToolCatalog.from_registry(self._registry)
        requested = (
            tuple(requested_names)
            if requested_names is not None
            else tuple(entry.name for entry in selected_catalog.entries)
        )
        if len(set(requested)) != len(requested):
            raise ToolPoolBuildError("requested tool names must be unique")
        unknown = set(requested).difference(
            entry.name for entry in selected_catalog.entries
        )
        if unknown:
            raise ToolPoolBuildError(
                "requested tool selection contains unknown tools: "
                + ", ".join(sorted(unknown))
            )
        requested_set = set(requested)
        capabilities = _capability_mapping(model_capabilities)
        sandbox = sandbox_capabilities or SandboxCapabilities()
        centrally_visible = {
            spec.name
            for spec in self._registry.list_specs(context=tool_use_context)
        }
        entries: list[ToolCatalogEntry] = []
        exclusions: list[ToolPoolExclusion] = []
        for entry in selected_catalog.entries:
            reason = _exclusion_reason(
                entry,
                requested=requested_set,
                agent_type=agent_type,
                run_mode=run_mode,
                model_capabilities=capabilities,
                context=tool_use_context,
                deny_rules=deny_rules,
                sandbox=sandbox,
                centrally_visible=centrally_visible,
            )
            if reason is not None:
                exclusions.append(ToolPoolExclusion(entry.name, reason))
                continue
            entries.append(entry)

        effective_names = {entry.name for entry in entries}
        enabled_skills = (
            None
            if enabled_skill_names is None
            else {item.strip().casefold() for item in enabled_skill_names}
        )
        missing: list[MissingSkillDependency] = []
        for skill in skills:
            applies_to = getattr(skill, "applies_to", None)
            if callable(applies_to) and not applies_to(
                agent=agent_type,
                mode=run_mode,
            ):
                continue
            skill_name = str(
                getattr(skill, "qualified_name", None)
                or getattr(skill, "name", "")
            )
            plain_name = str(getattr(skill, "name", skill_name))
            if enabled_skills is not None and not {
                skill_name.casefold(),
                plain_name.casefold(),
            }.intersection(enabled_skills):
                continue
            required = tuple(
                str(item) for item in getattr(skill, "required_tools", ())
            )
            unavailable = tuple(
                sorted(set(required).difference(effective_names))
            )
            if unavailable:
                missing.append(
                    MissingSkillDependency(
                        skill_name=skill_name,
                        missing_tools=unavailable,
                    )
                )
        if strict_skill_dependencies and missing:
            details = "; ".join(
                f"{item.skill_name}: {', '.join(item.missing_tools)}"
                for item in missing
            )
            raise ToolPoolBuildError(
                f"Skill tool dependencies are unavailable: {details}"
            )
        diagnostics = [
            f"excluded:{item.name}:{item.reason}" for item in exclusions
        ]
        diagnostics.extend(
            f"missing_skill_dependency:{item.skill_name}:"
            + ",".join(item.missing_tools)
            for item in missing
        )
        return EffectiveToolPool(
            registry=self._registry,
            catalog_version=selected_catalog.version,
            catalog_hash=selected_catalog.catalog_hash,
            catalog_summary=selected_catalog.normalized_summary,
            entries=entries,
            exclusions=exclusions,
            missing_skill_dependencies=missing,
            selection_provenance=selection_provenance,
            diagnostics=diagnostics,
        )

    def restore(self, snapshot: object) -> EffectiveToolPool:
        """Restore the exact effective set or fail explicitly on drift."""
        names = getattr(snapshot, "enabled_tools", None)
        summary_text = str(getattr(snapshot, "normalized_summary", "") or "")
        catalog_version = str(getattr(snapshot, "catalog_version", "") or "")
        catalog_hash = str(getattr(snapshot, "catalog_hash", "") or "")
        catalog_summary = str(getattr(snapshot, "catalog_summary", "") or "")
        pool_hash = str(getattr(snapshot, "pool_hash", "") or "")
        pool_version = str(getattr(snapshot, "version", "") or "")
        provenance = tuple(
            str(item)
            for item in getattr(snapshot, "selection_provenance", ())
        )
        exclusions = tuple(
            ToolPoolExclusion(str(item[0]), str(item[1]))
            for item in getattr(snapshot, "exclusions", ())
            if isinstance(item, (list, tuple)) and len(item) == 2
        )
        diagnostics = tuple(
            str(item) for item in getattr(snapshot, "diagnostics", ())
        )
        if names is None or not summary_text or not catalog_summary:
            raise ToolPoolRestoreError(
                "Run does not contain an effective tool pool snapshot"
            )
        try:
            raw_entries = json.loads(summary_text)
        except (TypeError, ValueError) as exc:
            raise ToolPoolRestoreError(
                "effective tool pool summary is invalid"
            ) from exc
        if not isinstance(raw_entries, list):
            raise ToolPoolRestoreError(
                "effective tool pool summary must be an array"
            )
        summaries = tuple(
            ToolCatalogSummaryEntry.from_mapping(item)
            for item in raw_entries
            if isinstance(item, Mapping)
        )
        canonical = _summary_json(summaries)
        if canonical != summary_text or _sha256(canonical) != pool_hash:
            raise ToolPoolRestoreError(
                "effective tool pool snapshot hash mismatch"
            )
        expected_pool_version = (
            f"{TOOL_POOL_SNAPSHOT_VERSION}:"
            f"{pool_hash.removeprefix('sha256:')[:16]}"
        )
        if pool_version != expected_pool_version:
            raise ToolPoolRestoreError(
                "effective tool pool snapshot version mismatch"
            )
        try:
            raw_catalog_summary = json.loads(catalog_summary)
            canonical_catalog_summary = _canonical_json(raw_catalog_summary)
        except (TypeError, ValueError) as exc:
            raise ToolPoolRestoreError("tool catalog summary is invalid") from exc
        if (
            canonical_catalog_summary != catalog_summary
            or _sha256(catalog_summary) != catalog_hash
        ):
            raise ToolPoolRestoreError("tool catalog snapshot hash mismatch")
        if not isinstance(raw_catalog_summary, list) or not all(
            isinstance(item, Mapping) for item in raw_catalog_summary
        ):
            raise ToolPoolRestoreError("tool catalog summary must be an array")
        catalog_summaries = tuple(
            ToolCatalogSummaryEntry.from_mapping(item)
            for item in raw_catalog_summary
        )
        catalog_by_name = {item.name: item for item in catalog_summaries}
        if len(catalog_by_name) != len(catalog_summaries) or any(
            catalog_by_name.get(item.name) != item for item in summaries
        ):
            raise ToolPoolRestoreError(
                "effective tool pool differs from its catalog snapshot"
            )
        if tuple(item.name for item in summaries) != tuple(names):
            raise ToolPoolRestoreError(
                "effective tool pool names differ from its normalized summary"
            )
        current_catalog = ToolCatalog.from_registry(self._registry)
        restored: list[ToolCatalogEntry] = []
        for summary in summaries:
            entry = current_catalog.get(summary.name)
            if entry is None:
                raise ToolPoolRestoreError(
                    f"snapshotted tool is unavailable: {summary.name}"
                )
            if _summary_for(entry) != summary:
                raise ToolPoolRestoreError(
                    f"snapshotted tool definition changed: {summary.name}"
                )
            restored.append(entry)
        if catalog_version != TOOL_CATALOG_VERSION:
            raise ToolPoolRestoreError(
                f"unsupported tool catalog version: {catalog_version}"
            )
        return EffectiveToolPool(
            registry=self._registry,
            catalog_version=catalog_version,
            catalog_hash=catalog_hash,
            catalog_summary=catalog_summary,
            entries=restored,
            exclusions=exclusions,
            selection_provenance=provenance,
            diagnostics=diagnostics,
        )


def _entry_from_spec(
    spec: ToolSpec,
    *,
    source: ToolSource | None = None,
) -> ToolCatalogEntry:
    resolved_source: ToolSource = source or (
        "mcp"
        if spec.provider.startswith("mcp:")
        else (
            "base"
            if spec.provider == "local"
            and not spec.name.startswith(("repo.", "sandbox."))
            else "local"
        )
    )
    namespace = (
        "mcp"
        if resolved_source == "mcp"
        else (
            spec.name.split(".", 1)[0]
            if "." in spec.name
            else resolved_source
        )
    )
    sandbox_requirements: tuple[str, ...] = ()
    if spec.name.startswith("sandbox."):
        if spec.name == "sandbox.run_command":
            sandbox_requirements = ("available", "command_execution")
        elif spec.permission_level == "read_only":
            sandbox_requirements = ("available", "readable")
        else:
            sandbox_requirements = ("available", "writable")
    return ToolCatalogEntry(
        spec=_copy_spec(spec),
        namespace=namespace,
        source=resolved_source,
        required_sandbox_capabilities=sandbox_requirements,
    )


def _copy_spec(spec: ToolSpec) -> ToolSpec:
    return ToolSpec(
        name=spec.name,
        description=spec.description,
        input_schema=json.loads(_canonical_json(spec.input_schema)),
        output_schema=json.loads(_canonical_json(spec.output_schema)),
        provider=spec.provider,
        permission_level=spec.permission_level,
        requires_approval=spec.requires_approval,
        accepts_context=spec.accepts_context,
        risk_summary=spec.risk_summary,
        max_output_chars=spec.max_output_chars,
        timeout_seconds=spec.timeout_seconds,
        max_retries=spec.max_retries,
        idempotent=spec.idempotent,
        permission_source=spec.permission_source,
    )


def _copy_entry(entry: ToolCatalogEntry) -> ToolCatalogEntry:
    return ToolCatalogEntry(
        spec=_copy_spec(entry.spec),
        namespace=entry.namespace,
        source=entry.source,
        agent_types=tuple(entry.agent_types),
        run_modes=tuple(entry.run_modes),
        required_model_capabilities=tuple(
            entry.required_model_capabilities
        ),
        required_sandbox_capabilities=tuple(
            entry.required_sandbox_capabilities
        ),
    )


def _validate_entry(entry: ToolCatalogEntry) -> None:
    if not _TOOL_NAME_PATTERN.fullmatch(entry.name):
        raise ToolCatalogError(
            f"invalid tool name; use dot-separated namespaces: {entry.name}"
        )
    if not _TOOL_NAME_PATTERN.fullmatch(entry.namespace):
        raise ToolCatalogError(f"invalid tool namespace: {entry.namespace}")
    if entry.source == "mcp" and (
        entry.namespace != "mcp" or not entry.name.startswith("mcp.")
    ):
        raise ToolCatalogError(
            f"MCP tool must use the mcp.<server>.<tool> namespace: {entry.name}"
        )
    if entry.source != "mcp" and entry.namespace == "mcp":
        raise ToolCatalogError(
            f"non-MCP tool cannot claim the reserved mcp namespace: {entry.name}"
        )
    for label, values in (
        ("agent_types", entry.agent_types),
        ("run_modes", entry.run_modes),
    ):
        if not values or any(not value.strip() for value in values):
            raise ToolCatalogError(f"{entry.name} has invalid {label}")


def _exclusion_reason(
    entry: ToolCatalogEntry,
    *,
    requested: set[str],
    agent_type: str,
    run_mode: str,
    model_capabilities: Mapping[str, bool],
    context: ToolUseContext,
    deny_rules: Sequence[str],
    sandbox: SandboxCapabilities,
    centrally_visible: set[str],
) -> str | None:
    name = entry.name
    if name not in requested:
        return "not_requested"
    if not _matches_scope(agent_type, entry.agent_types):
        return "agent_type"
    if not _matches_scope(run_mode, entry.run_modes):
        return "run_mode"
    if any(
        not model_capabilities.get(capability, False)
        for capability in entry.required_model_capabilities
    ):
        return "model_capability"
    if any(not bool(getattr(sandbox, item, False)) for item in entry.required_sandbox_capabilities):
        return "sandbox_capability"
    if sandbox.supported_tools is not None and name.startswith("sandbox.") and name not in sandbox.supported_tools:
        return "sandbox_tool_allowlist"
    if context.process_allowed_tools is not None and name not in context.process_allowed_tools:
        return "process_capability_boundary"
    if context.project_allowed_tools is not None and name not in context.project_allowed_tools:
        return "project_tool_selection"
    if name in context.process_denied_tools:
        return "process_deny"
    if name in context.project_denied_tools:
        return "project_deny"
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in deny_rules):
        return "run_deny_rule"
    if name not in centrally_visible:
        return "central_permission_deny"
    required_role = "viewer" if entry.spec.permission_level == "read_only" else "editor"
    if _ROLE_RANK.get(context.workspace_role, 0) < _ROLE_RANK[required_role]:
        return "workspace_role"
    return None


def _capability_mapping(value: Mapping[str, object] | object | None) -> dict[str, bool]:
    if value is None:
        return {"tool_calling": True, "structured_output": True}
    if isinstance(value, Mapping):
        return {str(key): bool(item) for key, item in value.items()}
    names = ("tool_calling", "structured_output")
    return {name: bool(getattr(value, name, False)) for name in names}


def _matches_scope(value: str, allowed: Sequence[str]) -> bool:
    normalized = value.strip().casefold()
    return "*" in allowed or normalized in {
        item.strip().casefold() for item in allowed
    }


def _summary_for(entry: ToolCatalogEntry) -> ToolCatalogSummaryEntry:
    spec = entry.spec
    return ToolCatalogSummaryEntry(
        name=spec.name,
        namespace=entry.namespace,
        source=entry.source,
        provider=spec.provider,
        permission_level=spec.permission_level,
        requires_approval=spec.requires_approval,
        input_schema_hash=_sha256(_canonical_json(spec.input_schema)),
        output_schema_hash=_sha256(_canonical_json(spec.output_schema)),
        definition_hash=entry.definition_hash,
        agent_types=entry.agent_types,
        run_modes=entry.run_modes,
        required_model_capabilities=entry.required_model_capabilities,
        required_sandbox_capabilities=(
            entry.required_sandbox_capabilities
        ),
    )


def _definition_hash(spec: ToolSpec) -> str:
    payload = {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
        "output_schema": spec.output_schema,
        "provider": spec.provider,
        "permission_level": spec.permission_level,
        "requires_approval": spec.requires_approval,
        "accepts_context": spec.accepts_context,
        "risk_summary": spec.risk_summary,
        "max_output_chars": spec.max_output_chars,
        "timeout_seconds": spec.timeout_seconds,
        "max_retries": spec.max_retries,
        "idempotent": spec.idempotent,
        "permission_source": spec.permission_source,
    }
    return _sha256(_canonical_json(payload))


def _summary_string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        return ()
    return tuple(value)


def _summary_json(entries: Sequence[ToolCatalogSummaryEntry]) -> str:
    return _canonical_json([entry.to_dict() for entry in entries])


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ToolCatalogError("tool metadata must be JSON serializable") from exc


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_tool_name(name: str) -> str:
    return ".".join(part.casefold() for part in name.split("."))


def _entry_sort_key(entry: ToolCatalogEntry) -> tuple[str, str, str, str]:
    return (
        entry.namespace.casefold(),
        _normalized_tool_name(entry.name),
        entry.source,
        entry.spec.provider.casefold(),
    )


__all__ = [
    "TOOL_CATALOG_VERSION",
    "TOOL_POOL_SNAPSHOT_VERSION",
    "EffectiveToolPool",
    "MissingSkillDependency",
    "SandboxCapabilities",
    "ToolCatalog",
    "ToolCatalogEntry",
    "ToolCatalogError",
    "ToolCatalogSummaryEntry",
    "ToolNameConflictError",
    "ToolPoolBuildError",
    "ToolPoolBuilder",
    "ToolPoolExclusion",
    "ToolPoolRestoreError",
]
