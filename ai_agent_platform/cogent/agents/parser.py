from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ai_agent_platform.cogent.memory.instructions import process_includes

from .models import AgentDefinition


VALID_PERMISSION_MODES = {"default", "acceptEdits", "bypassPermissions", "plan", ""}
VALID_ISOLATION_MODES = {"", "worktree"}


class AgentParseError(ValueError):
    pass


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    stripped = raw.lstrip()
    lines = stripped.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise AgentParseError("Missing YAML frontmatter (must start with ---)")
    end = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if end is None:
        raise AgentParseError("Unclosed YAML frontmatter (missing closing ---)")
    try:
        metadata = yaml.safe_load("".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise AgentParseError(f"Invalid YAML in frontmatter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise AgentParseError("Frontmatter must be a YAML mapping")
    return metadata, "".join(lines[end + 1 :]).lstrip("\n")


def validate_agent_metadata(metadata: dict[str, Any], source: str = "") -> None:
    suffix = f" in {source}" if source else ""
    for field in ("name", "description"):
        if not str(metadata.get(field) or "").strip():
            raise AgentParseError(f"Missing required field '{field}'{suffix}")
    model = str(metadata.get("model", "inherit")).strip()
    if not model:
        raise AgentParseError(f"Invalid empty model{suffix}")
    metadata["model"] = "inherit" if model.casefold() == "inherit" else model
    permission_mode = str(metadata.get("permissionMode", "default"))
    if permission_mode not in VALID_PERMISSION_MODES:
        raise AgentParseError(
            f"Invalid permissionMode '{permission_mode}'{suffix}: "
            f"must be one of {sorted(VALID_PERMISSION_MODES - {''})}"
        )
    max_turns = metadata.get("maxTurns")
    if max_turns is not None and (
        not isinstance(max_turns, int) or isinstance(max_turns, bool) or max_turns <= 0
    ):
        raise AgentParseError(
            f"Invalid maxTurns '{max_turns}'{suffix}: must be a positive integer"
        )
    isolation = str(metadata.get("isolation", ""))
    if isolation not in VALID_ISOLATION_MODES:
        raise AgentParseError(
            f"Invalid isolation '{isolation}'{suffix}: "
            f"must be one of {sorted(VALID_ISOLATION_MODES - {''})}"
        )
    if not isinstance(metadata.get("background", False), bool):
        raise AgentParseError(f"background{suffix} must be a boolean")
    for field in ("tools", "disallowedTools"):
        value = metadata.get(field, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise AgentParseError(f"{field}{suffix} must be an array of tool names")


def parse_agent_file(path: Path, *, authorized_root: Path | None = None) -> AgentDefinition:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AgentParseError(f"Cannot read agent file {path}: {exc}") from exc
    metadata, body = parse_frontmatter(raw)
    validate_agent_metadata(metadata, str(path))
    root = (authorized_root or path.parent).resolve()
    try:
        body, _ = process_includes(
            body,
            source_path=path.resolve(),
            allowed_root=root,
        )
    except (OSError, ValueError) as exc:
        raise AgentParseError(f"Cannot expand agent instructions in {path}: {exc}") from exc
    return AgentDefinition(
        agent_type=str(metadata["name"]).strip(),
        when_to_use=str(metadata["description"]).strip(),
        system_prompt=body,
        tools=[str(item) for item in metadata.get("tools", [])],
        disallowed_tools=[str(item) for item in metadata.get("disallowedTools", [])],
        model=str(metadata.get("model", "inherit")),
        max_turns=int(metadata.get("maxTurns") or 200),
        permission_mode=str(metadata.get("permissionMode", "default")) or "default",
        background=bool(metadata.get("background", False)),
        isolation=str(metadata.get("isolation", "")),
        file_path=path,
    )
