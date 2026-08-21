"""Bounded, non-executing discovery of declarative ``SKILL.md`` files."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from .models import (
    SkillCatalog,
    SkillDefinition,
    SkillDiagnostic,
    SkillDiscoveryLimits,
    SkillSource,
    SlashCommandMetadata,
)
from .registry import CommandRegistry


_SKILL_FIELDS = frozenset(
    {
        "name",
        "description",
        "agents",
        "modes",
        "context_budget",
        "tools",
        "command",
    }
)
_COMMAND_FIELDS = frozenset({"name", "description", "usage", "aliases"})
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_DEFAULT_CONTEXT_BUDGET = 4_000
_MAX_DESCRIPTION_CHARS = 500
_MAX_APPLICABILITY_ITEMS = 16
_MAX_TOOL_ITEMS = 32
_MAX_ALIASES = 8


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class SkillDocumentError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SkillDiscovery:
    """Discover Skills below three fixed roots without executing their content."""

    def __init__(
        self,
        *,
        bundled_root: str | Path | None = None,
        user_root: str | Path | None = None,
        project_skills_directory: str = ".agents/skills",
        limits: SkillDiscoveryLimits | None = None,
    ) -> None:
        project_path = Path(project_skills_directory)
        if project_path.is_absolute() or ".." in project_path.parts:
            raise ValueError("project_skills_directory must stay relative to workspace")
        self._bundled_root = Path(bundled_root) if bundled_root is not None else None
        self._user_root = Path(user_root) if user_root is not None else None
        self._project_skills_directory = project_path
        self._limits = limits or SkillDiscoveryLimits()

    @property
    def limits(self) -> SkillDiscoveryLimits:
        return self._limits

    def discover(self, *, project_root: str | Path | None = None) -> SkillCatalog:
        diagnostics: list[SkillDiagnostic] = []
        definitions: list[SkillDefinition] = []
        discovered_count = 0
        loaded_chars = 0
        limit_reported = False
        disabled_user_names: set[str] = set()

        roots = self._source_roots(project_root, diagnostics)
        count_exhausted = False
        for source, root, display_base in roots:
            remaining_count = self._limits.max_discovered_skills - discovered_count
            candidates, root_diagnostics = _skill_candidates(
                root,
                source,
                max_candidates=max(1, remaining_count + 1),
            )
            diagnostics.extend(root_diagnostics)
            for candidate in candidates:
                if source is SkillSource.USER and (candidate.parent / ".disabled").is_file():
                    if _NAME_RE.fullmatch(candidate.parent.name.casefold()):
                        disabled_user_names.add(candidate.parent.name.casefold())
                    continue
                discovered_count += 1
                display_path = _display_path(candidate, display_base)
                if discovered_count > self._limits.max_discovered_skills:
                    if not limit_reported:
                        diagnostics.append(
                            SkillDiagnostic(
                                severity="error",
                                code="discovery_count_exceeded",
                                source=source,
                                path=display_path,
                                message=(
                                    "Skill discovery count exceeds limit "
                                    f"{self._limits.max_discovered_skills}"
                                ),
                            )
                        )
                        limit_reported = True
                    count_exhausted = True
                    break
                try:
                    raw = _read_regular_file(
                        candidate,
                        root=root,
                        max_bytes=self._limits.max_file_bytes,
                    )
                    text = raw.decode("utf-8")
                    if "\x00" in text:
                        raise SkillDocumentError(
                            "invalid_markdown", "SKILL.md contains a NUL character"
                        )
                    if loaded_chars + len(text) > self._limits.max_total_chars:
                        raise SkillDocumentError(
                            "total_chars_exceeded",
                            "loading SKILL.md would exceed the total character limit",
                        )
                    definition = parse_skill_document(
                        text,
                        raw=raw,
                        source=source,
                        path=display_path,
                        max_context_budget_chars=(
                            self._limits.max_context_budget_chars
                        ),
                    )
                except SkillDocumentError as exc:
                    diagnostics.append(
                        SkillDiagnostic(
                            severity="error",
                            code=exc.code,
                            source=source,
                            path=display_path,
                            message=str(exc),
                        )
                    )
                    continue
                except UnicodeDecodeError:
                    diagnostics.append(
                        SkillDiagnostic(
                            severity="error",
                            code="invalid_utf8",
                            source=source,
                            path=display_path,
                            message="SKILL.md must be UTF-8 text",
                        )
                    )
                    continue
                except OSError as exc:
                    diagnostics.append(
                        SkillDiagnostic(
                            severity="error",
                            code="read_error",
                            source=source,
                            path=display_path,
                            message=f"SKILL.md could not be read: {exc}",
                        )
                    )
                    continue
                loaded_chars += len(text)
                definitions.append(definition)
            if count_exhausted:
                break

        if disabled_user_names:
            definitions = [
                item
                for item in definitions
                if not (
                    item.source is SkillSource.BUNDLED
                    and item.name in disabled_user_names
                )
            ]
        effective, conflict_diagnostics = _resolve_skill_conflicts(definitions)
        diagnostics.extend(conflict_diagnostics)
        command_registry, command_diagnostics = CommandRegistry.from_skills(effective)
        diagnostics.extend(command_diagnostics)
        diagnostics.sort(key=_diagnostic_sort_key)
        return SkillCatalog(
            skills=tuple(sorted(effective, key=_skill_sort_key)),
            commands=command_registry.commands,
            diagnostics=tuple(diagnostics),
            discovered_count=discovered_count,
            loaded_chars=loaded_chars,
        )

    def _source_roots(
        self,
        project_root: str | Path | None,
        diagnostics: list[SkillDiagnostic],
    ) -> list[tuple[SkillSource, Path, Path]]:
        roots: list[tuple[SkillSource, Path, Path]] = []
        if project_root is not None:
            project = Path(project_root)
            try:
                resolved_project = project.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                diagnostics.append(
                    SkillDiagnostic(
                        severity="error",
                        code="invalid_project_root",
                        source=SkillSource.PROJECT,
                        path=str(project),
                        message=f"project root cannot be resolved: {exc}",
                    )
                )
            else:
                skill_root = project / self._project_skills_directory
                if _has_symlink_component(skill_root, project):
                    diagnostics.append(
                        SkillDiagnostic(
                            severity="error",
                            code="path_symlink",
                            source=SkillSource.PROJECT,
                            path=self._project_skills_directory.as_posix(),
                            message="project Skill root must not contain symlinks",
                        )
                    )
                elif skill_root.exists() or skill_root.is_symlink():
                    try:
                        resolved_skill_root = skill_root.resolve(strict=True)
                    except (OSError, RuntimeError) as exc:
                        diagnostics.append(
                            SkillDiagnostic(
                                severity="error",
                                code="invalid_root",
                                source=SkillSource.PROJECT,
                                path=self._project_skills_directory.as_posix(),
                                message=f"project Skill root cannot be resolved: {exc}",
                            )
                        )
                    else:
                        if not _is_within(resolved_skill_root, resolved_project):
                            diagnostics.append(
                                SkillDiagnostic(
                                    severity="error",
                                    code="path_escape",
                                    source=SkillSource.PROJECT,
                                    path=self._project_skills_directory.as_posix(),
                                    message="project Skill root escapes workspace",
                                )
                            )
                        else:
                            roots.append(
                                (
                                    SkillSource.PROJECT,
                                    resolved_skill_root,
                                    resolved_project,
                                )
                            )
        for source, configured in (
            (SkillSource.USER, self._user_root),
            (SkillSource.BUNDLED, self._bundled_root),
        ):
            if configured is None or not (
                configured.exists() or configured.is_symlink()
            ):
                continue
            if configured.is_symlink():
                diagnostics.append(
                    SkillDiagnostic(
                        severity="error",
                        code="path_symlink",
                        source=source,
                        path=str(configured),
                        message="Skill source root must not be a symlink",
                    )
                )
                continue
            try:
                resolved = configured.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                diagnostics.append(
                    SkillDiagnostic(
                        severity="error",
                        code="invalid_root",
                        source=source,
                        path=str(configured),
                        message=f"Skill source root cannot be resolved: {exc}",
                    )
                )
                continue
            roots.append((source, resolved, resolved))
        return roots


def _skill_candidates(
    root: Path,
    source: SkillSource,
    *,
    max_candidates: int,
) -> tuple[list[Path], list[SkillDiagnostic]]:
    diagnostics: list[SkillDiagnostic] = []
    if not root.is_dir():
        return [], [
            SkillDiagnostic(
                severity="error",
                code="invalid_root",
                source=source,
                path=str(root),
                message="Skill source root must be a directory",
            )
        ]
    candidates: list[Path] = []
    try:
        for current, directory_names, file_names in os.walk(root, followlinks=False):
            directory_names.sort()
            file_names.sort()
            current_path = Path(current)
            kept_directories: list[str] = []
            for name in directory_names:
                directory = current_path / name
                if directory.is_symlink():
                    diagnostics.append(
                        SkillDiagnostic(
                            severity="error",
                            code="path_symlink",
                            source=source,
                            path=_display_path(directory, root),
                            message="symlink directories are not searched for Skills",
                        )
                    )
                else:
                    kept_directories.append(name)
            directory_names[:] = kept_directories
            for name in file_names:
                if name == "SKILL.md":
                    candidates.append(current_path / name)
                    if len(candidates) >= max_candidates:
                        return candidates, diagnostics
    except OSError as exc:
        diagnostics.append(
            SkillDiagnostic(
                severity="error",
                code="discovery_io_error",
                source=source,
                path=str(root),
                message=f"Skill source could not be enumerated: {exc}",
            )
        )
    candidates.sort(key=lambda item: item.relative_to(root).as_posix())
    return candidates, diagnostics


def _read_regular_file(path: Path, *, root: Path, max_bytes: int) -> bytes:
    if path.is_symlink() or _has_symlink_component(path, root):
        raise SkillDocumentError("path_symlink", "SKILL.md must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SkillDocumentError("read_error", f"SKILL.md cannot be resolved: {exc}") from exc
    if not _is_within(resolved, root):
        raise SkillDocumentError("path_escape", "SKILL.md escapes its source root")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SkillDocumentError("read_error", f"SKILL.md cannot be opened: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SkillDocumentError(
                "not_regular_file",
                "SKILL.md must be a regular file",
            )
        if info.st_size > max_bytes:
            raise SkillDocumentError(
                "file_too_large",
                f"SKILL.md exceeds the {max_bytes}-byte file limit",
            )
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > max_bytes:
            raise SkillDocumentError(
                "file_too_large",
                f"SKILL.md exceeds the {max_bytes}-byte file limit",
            )
        return raw
    finally:
        os.close(descriptor)


def parse_skill_document(
    text: str,
    *,
    raw: bytes | None = None,
    source: SkillSource,
    path: str,
    max_context_budget_chars: int,
) -> SkillDefinition:
    resolved_raw = text.encode("utf-8") if raw is None else raw
    metadata, instructions = _frontmatter(text)
    unknown_fields = sorted(set(metadata).difference(_SKILL_FIELDS))
    if unknown_fields:
        raise SkillDocumentError(
            "unknown_metadata",
            "unsupported Skill metadata fields: " + ", ".join(unknown_fields),
        )
    name = _name(metadata.get("name"), label="Skill name")
    description = _description(metadata.get("description"), label="Skill description")
    agents = _string_list(
        metadata.get("agents", ["*"]),
        label="agents",
        max_items=_MAX_APPLICABILITY_ITEMS,
        name_values=True,
        allow_wildcard=True,
    )
    modes = _string_list(
        metadata.get("modes", ["*"]),
        label="modes",
        max_items=_MAX_APPLICABILITY_ITEMS,
        name_values=True,
        allow_wildcard=True,
    )
    context_budget = metadata.get("context_budget", _DEFAULT_CONTEXT_BUDGET)
    if isinstance(context_budget, bool) or not isinstance(context_budget, int):
        raise SkillDocumentError(
            "invalid_context_budget",
            "context_budget must be an integer",
        )
    if not 1 <= context_budget <= max_context_budget_chars:
        raise SkillDocumentError(
            "invalid_context_budget",
            f"context_budget must be between 1 and {max_context_budget_chars}",
        )
    required_tools = _string_list(
        metadata.get("tools", []),
        label="tools",
        max_items=_MAX_TOOL_ITEMS,
        tool_values=True,
    )
    command = _command(metadata.get("command"), skill_description=description)
    if command is None:
        command = SlashCommandMetadata(
            name=name,
            description=description,
        )
    return SkillDefinition(
        name=name,
        description=description,
        agents=agents,
        modes=modes,
        instructions=instructions,
        context_budget_chars=context_budget,
        required_tools=required_tools,
        command=command,
        source=source,
        path=path,
        content_hash=hashlib.sha256(resolved_raw).hexdigest(),
        project_context_untrusted=source is SkillSource.PROJECT,
    )


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise SkillDocumentError(
            "invalid_markdown", "SKILL.md must start with YAML frontmatter"
        )
    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        ),
        None,
    )
    if closing_index is None:
        raise SkillDocumentError(
            "invalid_markdown", "SKILL.md YAML frontmatter is not closed"
        )
    raw_metadata = "".join(lines[1:closing_index])
    try:
        loaded = yaml.load(raw_metadata, Loader=_UniqueKeySafeLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise SkillDocumentError(
            "invalid_frontmatter",
            f"invalid YAML frontmatter: {exc}",
        ) from exc
    if not isinstance(loaded, Mapping):
        raise SkillDocumentError(
            "invalid_frontmatter",
            "YAML frontmatter must be an object",
        )
    if not all(isinstance(key, str) for key in loaded):
        raise SkillDocumentError(
            "invalid_frontmatter",
            "Skill metadata keys must be strings",
        )
    instructions = "".join(lines[closing_index + 1 :]).strip()
    if not instructions:
        raise SkillDocumentError(
            "invalid_markdown",
            "SKILL.md instruction body is empty",
        )
    return dict(loaded), instructions


def _command(value: Any, *, skill_description: str) -> SlashCommandMetadata | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SkillDocumentError("invalid_command", "command must be an object")
    if not all(isinstance(key, str) for key in value):
        raise SkillDocumentError("invalid_command", "command keys must be strings")
    unknown_fields = sorted(set(value).difference(_COMMAND_FIELDS))
    if unknown_fields:
        raise SkillDocumentError(
            "unknown_command_metadata",
            "unsupported command fields: " + ", ".join(unknown_fields),
        )
    name = _name(value.get("name"), label="command name")
    description_value = value.get("description", skill_description)
    description = _description(description_value, label="command description")
    usage_value = value.get("usage")
    usage = None
    if usage_value is not None:
        if not isinstance(usage_value, str) or not usage_value.strip():
            raise SkillDocumentError(
                "invalid_command",
                "command usage must be non-empty text",
            )
        usage = usage_value.strip()
        if len(usage) > 200:
            raise SkillDocumentError(
                "invalid_command",
                "command usage exceeds 200 characters",
            )
    aliases = _string_list(
        value.get("aliases", []),
        label="command aliases",
        max_items=_MAX_ALIASES,
        name_values=True,
    )
    if name in aliases:
        raise SkillDocumentError(
            "invalid_command",
            "command aliases must not repeat its name",
        )
    return SlashCommandMetadata(
        name=name,
        description=description,
        usage=usage,
        aliases=aliases,
    )


def _name(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise SkillDocumentError("invalid_name", f"{label} must be text")
    normalized = value.strip().casefold()
    if not _NAME_RE.fullmatch(normalized):
        raise SkillDocumentError(
            "invalid_name",
            f"{label} must match {_NAME_RE.pattern}",
        )
    return normalized


def _description(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkillDocumentError(
            "invalid_description",
            f"{label} must be non-empty text",
        )
    normalized = " ".join(value.split())
    if len(normalized) > _MAX_DESCRIPTION_CHARS:
        raise SkillDocumentError(
            "invalid_description",
            f"{label} exceeds {_MAX_DESCRIPTION_CHARS} characters",
        )
    return normalized


def _string_list(
    value: Any,
    *,
    label: str,
    max_items: int,
    name_values: bool = False,
    tool_values: bool = False,
    allow_wildcard: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SkillDocumentError("invalid_metadata", f"{label} must be an array")
    if len(value) > max_items:
        raise SkillDocumentError(
            "invalid_metadata", f"{label} exceeds the {max_items}-item limit"
        )
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SkillDocumentError(
                "invalid_metadata", f"{label} values must be non-empty text"
            )
        candidate = item.strip().casefold() if not tool_values else item.strip()
        if allow_wildcard and candidate == "*":
            pass
        elif name_values and not _NAME_RE.fullmatch(candidate):
            raise SkillDocumentError(
                "invalid_metadata", f"{label} values must match {_NAME_RE.pattern}"
            )
        elif tool_values and not _TOOL_RE.fullmatch(candidate):
            raise SkillDocumentError(
                "invalid_metadata", f"{label} contains an invalid tool name"
            )
        if candidate not in normalized:
            normalized.append(candidate)
    return tuple(sorted(normalized))


def _resolve_skill_conflicts(
    definitions: Sequence[SkillDefinition],
) -> tuple[list[SkillDefinition], list[SkillDiagnostic]]:
    diagnostics: list[SkillDiagnostic] = []
    source_winners: list[SkillDefinition] = []
    for source in SkillSource:
        source_items = [item for item in definitions if item.source is source]
        for name in sorted({item.name for item in source_items}):
            group = sorted(
                (item for item in source_items if item.name == name),
                key=lambda item: item.path,
            )
            winner = group[0]
            source_winners.append(winner)
            for loser in group[1:]:
                diagnostics.append(
                    SkillDiagnostic(
                        severity="error",
                        code="duplicate_skill_name",
                        source=loser.source,
                        path=loser.path,
                        related_path=winner.path,
                        message=(
                            f"duplicate Skill name {name!r} in {source.value}; "
                            f"{winner.path} wins by path order"
                        ),
                    )
                )

    effective: list[SkillDefinition] = []
    for name in sorted({item.name for item in source_winners}):
        group = sorted(
            (item for item in source_winners if item.name == name),
            key=lambda item: (-item.source.priority, item.path),
        )
        winner = group[0]
        effective.append(winner)
        for loser in group[1:]:
            diagnostics.append(
                SkillDiagnostic(
                    severity="warning",
                    code="skill_overridden",
                    source=loser.source,
                    path=loser.path,
                    related_path=winner.path,
                    message=(
                        f"{loser.qualified_name} is overridden by "
                        f"{winner.qualified_name}"
                    ),
                )
            )
    return effective, diagnostics


def _has_symlink_component(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    if current.is_symlink():
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _is_within(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def _display_path(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def _skill_sort_key(skill: SkillDefinition) -> tuple[str, str, str]:
    return (skill.name, skill.qualified_name, skill.path)


def _diagnostic_sort_key(
    diagnostic: SkillDiagnostic,
) -> tuple[int, str, str, str, str]:
    return (
        -diagnostic.source.priority,
        diagnostic.path,
        diagnostic.code,
        diagnostic.severity,
        diagnostic.message,
    )
