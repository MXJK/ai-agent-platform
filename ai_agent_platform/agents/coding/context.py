from __future__ import annotations

from pathlib import Path

from ai_agent_platform.agents.coding.models import ContextSource
from ai_agent_platform.cogent.memory.instructions import (
    InstructionReadError,
    load_layered_instructions,
)


class InstructionSecurityError(InstructionReadError):
    """A Workspace instruction path could not be read without crossing trust bounds."""


def load_project_instructions(
    *,
    workspace_root: str,
    focus_files: list[str],
    max_chars: int,
    work_dir: str | None = None,
) -> list[ContextSource]:
    """Load scoped instructions using descriptor-anchored, no-symlink reads."""
    try:
        selected = load_layered_instructions(
            workspace_root=workspace_root,
            focus_files=focus_files,
            work_dir=work_dir,
        )
    except InstructionReadError as exc:
        raise InstructionSecurityError(str(exc)) from exc

    allocated: list[tuple[object, str, bool]] = []
    remaining = max(0, max_chars)
    for item in reversed(selected):
        if remaining <= 0:
            allocated.append((item, "", True))
            continue
        clipped = item.text[:remaining]
        allocated.append((item, clipped, len(clipped) < len(item.text)))
        remaining -= len(clipped)

    sources: list[ContextSource] = []
    for item, clipped, truncated in reversed(allocated):
        sources.append(
            ContextSource(
                kind="project_instruction",
                path=item.path,
                start_line=1,
                end_line=clipped.count("\n") + 1,
                text=clipped,
                reason=item.scope,
                content_hash=item.content_hash,
                truncated=truncated,
                dependencies=item.dependencies,
            )
        )
    return sources


__all__ = ["InstructionSecurityError", "load_project_instructions"]
