from __future__ import annotations

import hashlib
from pathlib import Path

from ai_agent_platform.agents.coding.models import ContextSource


def load_project_instructions(
    *,
    workspace_root: str,
    focus_files: list[str],
    max_chars: int,
) -> list[ContextSource]:
    """Load scoped AGENTS instructions from root toward every focused file."""
    root = Path(workspace_root).resolve()
    target_directories = {root}
    for relative in focus_files:
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            continue
        target_directories.add(candidate if candidate.is_dir() else candidate.parent)

    selected: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    for target in sorted(target_directories, key=lambda item: item.as_posix()):
        for directory in _path_chain(root, target):
            override = directory / "AGENTS.override.md"
            regular = directory / "AGENTS.md"
            claude = directory / "CLAUDE.md"
            instruction = (
                override
                if override.is_file()
                else regular
                if regular.is_file()
                else claude
            )
            if instruction.is_file() and instruction not in seen:
                seen.add(instruction)
                selected.append((instruction, directory))

    sources: list[ContextSource] = []
    remaining = max_chars
    for instruction, scope in selected:
        if remaining <= 0:
            break
        try:
            raw = instruction.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        clipped = text[:remaining]
        relative_scope = scope.relative_to(root).as_posix()
        sources.append(
            ContextSource(
                kind="project_instruction",
                path=instruction.relative_to(root).as_posix(),
                start_line=1,
                end_line=clipped.count("\n") + 1,
                text=clipped,
                reason=(
                    "workspace instruction"
                    if relative_scope == "."
                    else f"applies under {relative_scope}"
                ),
                content_hash=hashlib.sha256(raw).hexdigest(),
                truncated=len(clipped) < len(text),
            )
        )
        remaining -= len(clipped)
    return sources


def _path_chain(root: Path, target: Path) -> list[Path]:
    if target == root:
        return [root]
    relative = target.relative_to(root)
    chain = [root]
    current = root
    for part in relative.parts:
        current = current / part
        chain.append(current)
    return chain
