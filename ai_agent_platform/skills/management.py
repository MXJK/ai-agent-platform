"""Local-admin management for user-global declarative Skills."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import tempfile
from threading import RLock
from typing import Any

from .discovery import SkillDocumentError, parse_skill_document
from .models import SkillSource
from .service import SkillService


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_MAX_FILE_BYTES = 64 * 1024


class SkillRegistryError(RuntimeError):
    pass


class SkillRegistryNotFoundError(SkillRegistryError):
    pass


class SkillRegistryService:
    """Persist editable Skills below one user-global directory."""

    def __init__(self, *, user_root: str | Path, skill_service: SkillService) -> None:
        self._user_root = Path(user_root).expanduser()
        self._skill_service = skill_service
        self._lock = RLock()

    @property
    def user_root(self) -> Path:
        return self._user_root

    def registry_view(self) -> dict[str, Any]:
        with self._lock:
            catalog = self._skill_service.discover(enabled=True)
            items: dict[str, dict[str, Any]] = {}
            for skill in catalog.skills:
                items[skill.name] = _skill_view(
                    skill,
                    enabled=True,
                    editable=skill.source is SkillSource.USER,
                    content=(
                        self._read_managed_content(skill.name)
                        if skill.source is SkillSource.USER
                        else None
                    ),
                )
            if self._user_root.is_dir() and not self._user_root.is_symlink():
                for directory in sorted(self._user_root.iterdir(), key=lambda p: p.name):
                    if not directory.is_dir() or directory.is_symlink():
                        continue
                    marker = directory / ".disabled"
                    document = directory / "SKILL.md"
                    if not marker.exists() or not document.is_file() or document.is_symlink():
                        continue
                    try:
                        content = document.read_text(encoding="utf-8")
                        skill = parse_skill_document(
                            content,
                            source=SkillSource.USER,
                            path=f"{directory.name}/SKILL.md",
                            max_context_budget_chars=16_000,
                        )
                    except (OSError, UnicodeError, SkillDocumentError):
                        continue
                    items[skill.name] = _skill_view(
                        skill,
                        enabled=False,
                        editable=True,
                        content=content,
                    )
            return {
                "root": str(self._user_root),
                "writable": True,
                "skills": [items[name] for name in sorted(items)],
                "diagnostics": [
                    {
                        "severity": item.severity,
                        "code": item.code,
                        "source": item.source.value,
                        "path": item.path,
                        "message": item.message,
                    }
                    for item in catalog.diagnostics
                ],
            }

    def upsert(self, name: str, *, content: str, enabled: bool) -> dict[str, Any]:
        normalized = _validate_name(name)
        raw = content.encode("utf-8")
        if len(raw) > _MAX_FILE_BYTES:
            raise ValueError(f"SKILL.md exceeds the {_MAX_FILE_BYTES}-byte limit")
        parsed = parse_skill_document(
            content,
            raw=raw,
            source=SkillSource.USER,
            path=f"{normalized}/SKILL.md",
            max_context_budget_chars=16_000,
        )
        if parsed.name != normalized:
            raise ValueError("route name must match SKILL.md frontmatter name")
        with self._lock:
            directory = self._managed_directory(normalized)
            if directory.is_symlink():
                raise SkillRegistryError("Skill directory must not be a symbolic link")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            _atomic_write(directory / "SKILL.md", content)
            self._set_marker(directory / ".disabled", present=not enabled)
            return _skill_view(
                parsed,
                enabled=enabled,
                editable=True,
                content=content,
            )

    def set_enabled(self, name: str, *, enabled: bool) -> dict[str, Any]:
        normalized = _validate_name(name)
        with self._lock:
            directory = self._managed_directory(normalized)
            document = directory / "SKILL.md"
            if not document.is_file() or document.is_symlink():
                raise SkillRegistryNotFoundError("Skill not found")
            content = document.read_text(encoding="utf-8")
            skill = parse_skill_document(
                content,
                source=SkillSource.USER,
                path=f"{normalized}/SKILL.md",
                max_context_budget_chars=16_000,
            )
            self._set_marker(directory / ".disabled", present=not enabled)
            return _skill_view(
                skill,
                enabled=enabled,
                editable=True,
                content=content,
            )

    def delete(self, name: str) -> None:
        normalized = _validate_name(name)
        with self._lock:
            directory = self._managed_directory(normalized)
            if not directory.is_dir() or directory.is_symlink():
                raise SkillRegistryNotFoundError("Skill not found")
            shutil.rmtree(directory)

    def _managed_directory(self, name: str) -> Path:
        return self._user_root / name

    def _read_managed_content(self, name: str) -> str | None:
        path = self._managed_directory(name) / "SKILL.md"
        if not path.is_file() or path.is_symlink():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

    @staticmethod
    def _set_marker(path: Path, *, present: bool) -> None:
        if present:
            _atomic_write(path, "disabled\n")
        elif path.exists():
            path.unlink()


def _validate_name(name: str) -> str:
    normalized = name.strip().casefold()
    if not _NAME_RE.fullmatch(normalized):
        raise ValueError(f"Skill name must match {_NAME_RE.pattern}")
    return normalized


def _atomic_write(path: Path, content: str) -> None:
    if path.is_symlink():
        raise SkillRegistryError("Skill file must not be a symbolic link")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            if not content.endswith("\n"):
                stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _skill_view(skill: Any, *, enabled: bool, editable: bool, content: str | None) -> dict[str, Any]:
    command = skill.command
    return {
        "name": skill.name,
        "qualified_name": skill.qualified_name,
        "description": skill.description,
        "source": skill.source.value,
        "path": skill.path,
        "enabled": enabled,
        "editable": editable,
        "required_tools": list(skill.required_tools),
        "command": (
            {
                "name": command.name,
                "description": command.description,
                "usage": command.usage,
                "aliases": list(command.aliases),
            }
            if command is not None
            else None
        ),
        "content": content,
    }
