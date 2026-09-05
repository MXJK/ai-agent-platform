from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from ai_agent_platform.agents.coding.models import ContextSource


class InstructionSecurityError(ValueError):
    """A Workspace instruction path could not be read without crossing trust bounds."""


def load_project_instructions(
    *,
    workspace_root: str,
    focus_files: list[str],
    max_chars: int,
) -> list[ContextSource]:
    """Load scoped instructions using descriptor-anchored, no-symlink reads."""
    root = Path(workspace_root).resolve(strict=True)
    target_directories = {root}
    for relative in focus_files:
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            continue
        target_directories.add(candidate if candidate.is_dir() else candidate.parent)

    selected: list[tuple[str, str, bytes, str]] = []
    seen: set[str] = set()
    for target in sorted(target_directories, key=lambda item: item.as_posix()):
        for directory in _path_chain(root, target):
            for names in (("AGENTS.override.md", "AGENTS.md", "CLAUDE.md"), ("COGENT.md",)):
                loaded = _read_preferred_instruction(root, directory, names=names)
                if loaded is None:
                    continue
                name, raw, text = loaded
                relative_path = (directory.relative_to(root) / name).as_posix()
                if relative_path in seen:
                    continue
                seen.add(relative_path)
                if text is not None:
                    selected.append((relative_path, directory.as_posix(), raw, text))

    sources: list[ContextSource] = []
    remaining = max_chars
    for relative_path, directory_value, raw, text in selected:
        if remaining <= 0:
            break
        clipped = text[:remaining]
        scope = Path(directory_value)
        relative_scope = scope.relative_to(root).as_posix()
        sources.append(
            ContextSource(
                kind="project_instruction",
                path=relative_path,
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


def _read_preferred_instruction(
    root: Path,
    directory: Path,
    *,
    names: tuple[str, ...] = ("AGENTS.override.md", "AGENTS.md", "CLAUDE.md"),
) -> tuple[str, bytes, str | None] | None:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    opened: list[int] = []
    directory_checks: list[tuple[int, str, int, os.stat_result]] = []
    try:
        current_fd = os.open(root, directory_flags | nofollow | cloexec)
        opened.append(current_fd)
        root_before = os.fstat(current_fd)
        for part in directory.relative_to(root).parts:
            parent_fd = current_fd
            try:
                current_fd = os.open(
                    part,
                    directory_flags | nofollow | cloexec,
                    dir_fd=current_fd,
                )
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise InstructionSecurityError(
                    "Workspace instruction directory is unsafe"
                ) from exc
            opened.append(current_fd)
            directory_checks.append(
                (parent_fd, part, current_fd, os.fstat(current_fd))
            )

        for name in names:
            try:
                file_fd = os.open(
                    name,
                    os.O_RDONLY
                    | nofollow
                    | cloexec
                    | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=current_fd,
                )
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise InstructionSecurityError(
                    f"Workspace instruction file is unsafe: {name}"
                ) from exc
            try:
                before = os.fstat(file_fd)
                if not stat.S_ISREG(before.st_mode):
                    raise InstructionSecurityError(
                        f"Workspace instruction must be a regular file: {name}"
                    )
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(file_fd, 65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                after = os.fstat(file_fd)
                try:
                    current = os.stat(
                        name,
                        dir_fd=current_fd,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise InstructionSecurityError(
                        f"Workspace instruction changed while being read: {name}"
                    ) from exc
                if not _same_stable_file(before, after, current):
                    raise InstructionSecurityError(
                        f"Workspace instruction changed while being read: {name}"
                    )
                _verify_directory_chain(
                    root,
                    opened[0],
                    root_before,
                    directory_checks,
                )
                raw = b"".join(chunks)
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
                return name, raw, text
            finally:
                os.close(file_fd)
        return None
    except OSError as exc:
        raise InstructionSecurityError("Workspace instruction root is unsafe") from exc
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _same_stable_file(*items: os.stat_result) -> bool:
    first = items[0]
    identity = (first.st_dev, first.st_ino, first.st_mode)
    metadata = (first.st_size, first.st_mtime_ns, first.st_ctime_ns)
    return all(
        (item.st_dev, item.st_ino, item.st_mode) == identity
        and (item.st_size, item.st_mtime_ns, item.st_ctime_ns) == metadata
        for item in items[1:]
    )


def _verify_directory_chain(
    root: Path,
    root_fd: int,
    root_before: os.stat_result,
    checks: list[tuple[int, str, int, os.stat_result]],
) -> None:
    try:
        root_current = os.stat(root, follow_symlinks=False)
        if not _same_stable_file(root_before, os.fstat(root_fd), root_current):
            raise InstructionSecurityError(
                "Workspace instruction directory changed while being read"
            )
        for parent_fd, name, child_fd, before in checks:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not _same_stable_file(before, os.fstat(child_fd), current):
                raise InstructionSecurityError(
                    "Workspace instruction directory changed while being read"
                )
    except OSError as exc:
        raise InstructionSecurityError(
            "Workspace instruction directory changed while being read"
        ) from exc


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


__all__ = ["InstructionSecurityError", "load_project_instructions"]
