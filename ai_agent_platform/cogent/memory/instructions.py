from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat


MAX_INCLUDE_DEPTH = 5


class InstructionReadError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedInstruction:
    path: str
    scope: str
    text: str
    content_hash: str
    dependencies: tuple[str, ...] = ()


def parse_include(line: str) -> str:
    trimmed = line.strip()
    if not trimmed.startswith("@") or trimmed.startswith("@@"):
        return ""
    value = trimmed[1:]
    if not value or any(character.isspace() for character in value):
        return ""
    if value.startswith(("./", "../", "~/", "/")):
        return value
    return ""


def load_layered_instructions(
    *,
    workspace_root: str,
    focus_files: list[str],
    work_dir: str | None = None,
    user_root: Path | None = None,
) -> list[LoadedInstruction]:
    root = Path(workspace_root).resolve(strict=True)
    working = Path(work_dir).resolve(strict=True) if work_dir else root
    if working != root and root not in working.parents:
        raise InstructionReadError('instruction working directory is outside the Workspace')
    home_root = (user_root or Path.home() / ".cogent").resolve()
    targets = {root, working}
    for relative in focus_files:
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            continue
        targets.add(candidate if candidate.is_dir() else candidate.parent)

    candidates: list[tuple[Path, Path, str]] = [
        (home_root, home_root / "COGENT.md", "user instruction"),
        (home_root, home_root / "AGENTS.md", "user instruction"),
    ]
    seen_directories: set[Path] = set()
    for target in sorted(targets, key=lambda item: item.as_posix()):
        for directory in _path_chain(root, target):
            if directory in seen_directories:
                continue
            seen_directories.add(directory)
            reason = (
                "workspace instruction"
                if directory == root
                else f"applies under {directory.relative_to(root).as_posix()}"
            )
            candidates.extend(
                (
                    (root, directory / "COGENT.md", reason),
                    (root, directory / "AGENTS.md", reason),
                    (root, directory / ".cogent" / "COGENT.md", reason),
                )
            )
    candidates.append((root, working / "COGENT.local.md", "local workspace override"))

    loaded: list[LoadedInstruction] = []
    seen_files: set[str] = set()
    for allowed_root, path, reason in candidates:
        key = str(path.absolute())
        if key in seen_files:
            continue
        raw = _read_bounded_file(path, allowed_root)
        if raw is None:
            continue
        seen_files.add(key)
        text, dependencies = process_includes(
            raw.decode("utf-8"),
            source_path=path,
            allowed_root=allowed_root,
            seen={str(path.resolve())},
        )
        digest = hashlib.sha256(
            raw + b"\0" + "\n".join(dependencies).encode("utf-8")
        ).hexdigest()
        try:
            label = path.resolve().relative_to(root).as_posix()
        except ValueError:
            label = str(path.resolve())
        loaded.append(
            LoadedInstruction(
                path=label,
                scope=reason,
                text=text,
                content_hash=digest,
                dependencies=tuple(dependencies),
            )
        )
    return loaded


def process_includes(
    content: str,
    *,
    source_path: Path,
    allowed_root: Path,
    depth: int = 0,
    seen: set[str] | None = None,
) -> tuple[str, list[str]]:
    if depth >= MAX_INCLUDE_DEPTH:
        return content, []
    seen = seen if seen is not None else set()
    result: list[str] = []
    dependencies: list[str] = []
    in_code = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            result.append(line)
            continue
        include = "" if in_code else parse_include(stripped)
        if not include:
            result.append(line)
            continue
        candidate = _resolve_include(include, source_path.parent)
        try:
            resolved = candidate.resolve(strict=True)
            authorized_root = allowed_root.resolve(strict=True)
        except (OSError, RuntimeError):
            result.append(f"<!-- @ skipped: file not found ({include}) -->")
            continue
        if resolved != authorized_root and authorized_root not in resolved.parents:
            result.append(f"<!-- @ skipped: outside authorized instruction root ({include}) -->")
            continue
        identity = str(resolved)
        if identity in seen:
            result.append(f"<!-- @ skipped: include cycle ({include}) -->")
            continue
        raw = _read_bounded_file(resolved, authorized_root)
        if raw is None:
            result.append(f"<!-- @ skipped: unsafe or unreadable ({include}) -->")
            continue
        seen.add(identity)
        expanded, nested = process_includes(
            raw.decode("utf-8"),
            source_path=resolved,
            allowed_root=authorized_root,
            depth=depth + 1,
            seen=seen,
        )
        dependencies.append(f"{identity}:{hashlib.sha256(raw).hexdigest()}")
        dependencies.extend(nested)
        result.append(f"<!-- included from {include} -->")
        result.append(expanded)
    return "\n".join(result), dependencies


def _resolve_include(value: str, base_dir: Path) -> Path:
    if value.startswith("~/"):
        return Path.home() / value[2:]
    if os.path.isabs(value):
        return Path(value)
    return base_dir / value


def _read_bounded_file(path: Path, allowed_root: Path) -> bytes | None:
    try:
        root = allowed_root.resolve(strict=True)
        resolved_parent = path.parent.resolve(strict=True)
        if resolved_parent != root and root not in resolved_parent.parents:
            return None
        before = path.lstat()
        if path.is_symlink():
            raise InstructionReadError(f"unsafe instruction file: {path}")
        if not stat.S_ISREG(before.st_mode):
            raise InstructionReadError(f"instruction must be a regular file: {path}")
        raw = path.read_bytes()
        after = path.lstat()
        if not _same_stable_file(before, after):
            raise InstructionReadError(f"instruction changed while reading: {path}")
        raw.decode("utf-8")
        return raw
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        return None
    except OSError as exc:
        raise InstructionReadError(f"instruction path is unsafe: {path}") from exc


def _path_chain(root: Path, target: Path) -> list[Path]:
    if target == root:
        return [root]
    chain = [root]
    current = root
    for part in target.relative_to(root).parts:
        current = current / part
        chain.append(current)
    return chain


def _same_stable_file(*items: os.stat_result) -> bool:
    first = items[0]
    identity = (first.st_dev, first.st_ino, first.st_mode)
    metadata = (first.st_size, first.st_mtime_ns, first.st_ctime_ns)
    return all((item.st_dev, item.st_ino, item.st_mode) == identity and
               (item.st_size, item.st_mtime_ns, item.st_ctime_ns) == metadata
               for item in items[1:])


__all__ = [
    "InstructionReadError",
    "LoadedInstruction",
    "MAX_INCLUDE_DEPTH",
    "load_layered_instructions",
    "parse_include",
    "process_includes",
]
