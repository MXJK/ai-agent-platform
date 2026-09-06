"""Deterministic helpers for creating, validating, and packaging Cogent Skills."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile

from .discovery import SkillDocumentError, parse_skill_document
from .models import SkillSource


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PLACEHOLDER_RE = re.compile(
    r"(?:\[TODO(?::|\])|\bTODO:|__SKILL_[A-Z0-9_]*PLACEHOLDER__|"
    r"replace this placeholder)",
    re.IGNORECASE,
)
_RESOURCE_NAMES = frozenset({"scripts", "references", "assets"})
_EXCLUDED_DIRECTORIES = frozenset({"__pycache__", "node_modules"})
_EXCLUDED_ROOT_DIRECTORIES = frozenset({"evals"})
_EXCLUDED_FILES = frozenset({".DS_Store"})
_MAX_PACKAGE_FILES = 256
_MAX_PACKAGE_FILE_BYTES = 5 * 1024 * 1024
_MAX_PACKAGE_BYTES = 10 * 1024 * 1024
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class SkillCreatorError(ValueError):
    """Raised when a Skill package cannot be safely created or packaged."""


@dataclass(frozen=True)
class SkillValidationIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class SkillValidationReport:
    valid: bool
    name: str | None
    files: tuple[str, ...]
    issues: tuple[SkillValidationIssue, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "name": self.name,
            "files": list(self.files),
            "issues": [
                {"code": issue.code, "path": issue.path, "message": issue.message}
                for issue in self.issues
            ],
        }


def initialize_skill(
    workspace_root: str | Path,
    *,
    name: str,
    description: str,
    instructions: str,
    resources: tuple[str, ...] = (),
) -> Path:
    """Create one complete Skill directory without overwriting existing content."""

    normalized_name = _validate_name(name)
    normalized_description = " ".join(description.split())
    if not normalized_description:
        raise SkillCreatorError("description must be non-empty")
    normalized_instructions = instructions.strip()
    if not normalized_instructions:
        raise SkillCreatorError("instructions must be non-empty")
    unknown_resources = sorted(set(resources).difference(_RESOURCE_NAMES))
    if unknown_resources:
        raise SkillCreatorError(
            "unsupported resource directories: " + ", ".join(unknown_resources)
        )

    workspace = Path(workspace_root).expanduser()
    if workspace.is_symlink() or not workspace.is_dir():
        raise SkillCreatorError("Workspace root must be an existing non-symlink directory")
    workspace_resolved = workspace.resolve(strict=True)
    relative_root = Path(".cogent/skills")
    root_path = workspace
    for part in relative_root.parts:
        root_path = root_path / part
        if root_path.is_symlink():
            raise SkillCreatorError("Skill directory must not contain symbolic links")
        root_path.mkdir(mode=0o700, exist_ok=True)
        if not root_path.is_dir():
            raise SkillCreatorError("Skill root must be a directory")
    root_resolved = root_path.resolve(strict=True)
    if root_resolved != workspace_resolved and workspace_resolved not in root_resolved.parents:
        raise SkillCreatorError("Skill directory escapes the Workspace")
    target = root_path / normalized_name
    if target.exists() or target.is_symlink():
        raise SkillCreatorError(f"Skill already exists: {target}")

    document = _skill_document(
        name=normalized_name,
        description=normalized_description,
        instructions=normalized_instructions,
    )
    staging = Path(
        tempfile.mkdtemp(prefix=f".{normalized_name}.", dir=str(root_path))
    )
    try:
        (staging / "SKILL.md").write_text(document, encoding="utf-8")
        os.chmod(staging / "SKILL.md", 0o600)
        for resource in sorted(set(resources)):
            (staging / resource).mkdir(mode=0o700)
        report = validate_skill_package(staging, expected_name=normalized_name)
        if not report.valid:
            raise SkillCreatorError(_format_issues(report.issues))
        if target.exists() or target.is_symlink():
            raise SkillCreatorError(f"Skill already exists: {target}")
        staging.rename(target)
        return target
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def validate_skill_package(
    skill_path: str | Path,
    *,
    expected_name: str | None = None,
) -> SkillValidationReport:
    """Validate a Skill entrypoint and every packaged filesystem object."""

    path = Path(skill_path).expanduser()
    issues: list[SkillValidationIssue] = []
    if path.is_symlink():
        return _invalid("path_symlink", str(path), "Skill directory must not be a symbolic link")
    if not path.is_dir():
        return _invalid("missing_directory", str(path), "Skill directory was not found")

    files = _inspect_package_tree(path, issues)
    document_path = path / "SKILL.md"
    definition = None
    if document_path.is_symlink():
        issues.append(
            SkillValidationIssue(
                "path_symlink", "SKILL.md", "SKILL.md must not be a symbolic link"
            )
        )
    elif not document_path.is_file():
        issues.append(
            SkillValidationIssue("missing_entrypoint", "SKILL.md", "SKILL.md was not found")
        )
    else:
        try:
            raw = _read_regular_bytes(document_path, max_bytes=_MAX_PACKAGE_FILE_BYTES)
            text = raw.decode("utf-8")
            definition = parse_skill_document(
                text,
                raw=raw,
                source=SkillSource.PROJECT,
                path=f"{path.name}/SKILL.md",
                max_context_budget_chars=16_000,
            )
        except UnicodeDecodeError:
            issues.append(
                SkillValidationIssue("invalid_utf8", "SKILL.md", "SKILL.md must be UTF-8 text")
            )
        except (OSError, SkillCreatorError, SkillDocumentError) as exc:
            issues.append(
                SkillValidationIssue(
                    getattr(exc, "code", "read_error"), "SKILL.md", str(exc)
                )
            )
        else:
            requested_name = expected_name or path.name
            if definition.name != requested_name:
                issues.append(
                    SkillValidationIssue(
                        "name_mismatch",
                        "SKILL.md",
                        f"frontmatter name {definition.name!r} must match directory name {requested_name!r}",
                    )
                )
            if _PLACEHOLDER_RE.search(text):
                issues.append(
                    SkillValidationIssue(
                        "unfinished_placeholder",
                        "SKILL.md",
                        "SKILL.md contains an unfinished scaffold placeholder",
                    )
                )

    issues.sort(key=lambda issue: (issue.path, issue.code, issue.message))
    return SkillValidationReport(
        valid=not issues,
        name=definition.name if definition is not None else None,
        files=tuple(files),
        issues=tuple(issues),
    )


def package_skill(
    skill_path: str | Path,
    *,
    output_directory: str | Path,
) -> Path:
    """Validate and create a deterministic .skill ZIP archive."""

    unresolved_source = Path(skill_path).expanduser()
    if unresolved_source.is_symlink():
        raise SkillCreatorError("Skill directory must not be a symbolic link")
    source = unresolved_source.resolve(strict=True)
    report = validate_skill_package(source)
    if not report.valid:
        raise SkillCreatorError(_format_issues(report.issues))

    output = Path(output_directory).expanduser()
    if output.is_symlink():
        raise SkillCreatorError("output directory must not be a symbolic link")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_resolved = output.resolve(strict=True)
    if output_resolved == source or source in output_resolved.parents:
        raise SkillCreatorError("output directory must stay outside the Skill directory")
    destination = output_resolved / f"{source.name}.skill"
    if destination.exists() or destination.is_symlink():
        raise SkillCreatorError(f"package already exists: {destination}")

    included = [relative for relative in report.files if not _should_exclude(Path(relative))]
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{source.name}.", suffix=".skill", dir=str(output_resolved)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative_name in included:
                relative = Path(relative_name)
                file_path = source / relative
                if file_path.is_symlink() or not file_path.is_file():
                    raise SkillCreatorError(f"unsafe package entry: {relative.as_posix()}")
                info = zipfile.ZipInfo(
                    filename=f"{source.name}/{relative.as_posix()}",
                    date_time=_ZIP_TIMESTAMP,
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(
                    info,
                    _read_regular_bytes(file_path, max_bytes=_MAX_PACKAGE_FILE_BYTES),
                )
        if destination.exists() or destination.is_symlink():
            raise SkillCreatorError(f"package already exists: {destination}")
        temporary.rename(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def _skill_document(*, name: str, description: str, instructions: str) -> str:
    title = " ".join(part.capitalize() for part in name.split("-"))
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "agents: [coding]\n"
        "modes: [default]\n"
        "context_budget: 4000\n"
        "tools: []\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{instructions}\n"
    )


def _inspect_package_tree(
    root: Path,
    issues: list[SkillValidationIssue],
) -> list[str]:
    files: list[str] = []
    total_bytes = 0
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in sorted(directory_names):
            directory = current_path / name
            relative = directory.relative_to(root).as_posix()
            if directory.is_symlink():
                issues.append(
                    SkillValidationIssue(
                        "path_symlink", relative, "symbolic link directories are not allowed"
                    )
                )
            else:
                kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in sorted(file_names):
            file_path = current_path / name
            relative = file_path.relative_to(root).as_posix()
            if file_path.is_symlink():
                issues.append(
                    SkillValidationIssue(
                        "path_symlink", relative, "symbolic link files are not allowed"
                    )
                )
                continue
            try:
                details = file_path.stat()
            except OSError as exc:
                issues.append(SkillValidationIssue("read_error", relative, str(exc)))
                continue
            if not stat.S_ISREG(details.st_mode):
                issues.append(
                    SkillValidationIssue(
                        "not_regular_file", relative, "package entries must be regular files"
                    )
                )
                continue
            if details.st_size > _MAX_PACKAGE_FILE_BYTES:
                issues.append(
                    SkillValidationIssue(
                        "file_too_large",
                        relative,
                        f"file exceeds {_MAX_PACKAGE_FILE_BYTES} bytes",
                    )
                )
            total_bytes += details.st_size
            files.append(relative)
    if len(files) > _MAX_PACKAGE_FILES:
        issues.append(
            SkillValidationIssue(
                "too_many_files",
                ".",
                f"package contains more than {_MAX_PACKAGE_FILES} files",
            )
        )
    if total_bytes > _MAX_PACKAGE_BYTES:
        issues.append(
            SkillValidationIssue(
                "package_too_large",
                ".",
                f"package exceeds {_MAX_PACKAGE_BYTES} bytes",
            )
        )
    return sorted(files)


def _should_exclude(relative: Path) -> bool:
    if any(part in _EXCLUDED_DIRECTORIES for part in relative.parts):
        return True
    if relative.parts and relative.parts[0] in _EXCLUDED_ROOT_DIRECTORIES:
        return True
    return relative.name in _EXCLUDED_FILES or relative.suffix == ".pyc"


def _read_regular_bytes(path: Path, *, max_bytes: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SkillCreatorError(f"cannot safely read {path}: {exc}") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise SkillCreatorError(f"package entry is not a regular file: {path}")
        if details.st_size > max_bytes:
            raise SkillCreatorError(f"package entry exceeds {max_bytes} bytes: {path}")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise SkillCreatorError(f"package entry exceeds {max_bytes} bytes: {path}")
        return data
    finally:
        os.close(descriptor)


def _validate_name(name: str) -> str:
    normalized = name.strip().casefold()
    if not _NAME_RE.fullmatch(normalized):
        raise SkillCreatorError(f"Skill name must match {_NAME_RE.pattern}")
    if normalized.startswith("-") or normalized.endswith("-") or "--" in normalized:
        raise SkillCreatorError("Skill name must not start or end with '-' or contain '--'")
    return normalized


def _invalid(code: str, path: str, message: str) -> SkillValidationReport:
    return SkillValidationReport(
        valid=False,
        name=None,
        files=(),
        issues=(SkillValidationIssue(code, path, message),),
    )


def _format_issues(issues: tuple[SkillValidationIssue, ...]) -> str:
    return "; ".join(f"{issue.path}: {issue.message}" for issue in issues)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ai_agent_platform.skills.creator",
        description="Create, validate, or package a project-local Cogent Skill.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create a complete Skill package")
    init.add_argument("name")
    init.add_argument("--workspace", default=".")
    init.add_argument("--description", required=True)
    body = init.add_mutually_exclusive_group(required=True)
    body.add_argument("--instructions")
    body.add_argument("--instructions-file")
    init.add_argument(
        "--resource",
        action="append",
        choices=sorted(_RESOURCE_NAMES),
        default=[],
    )

    validate = commands.add_parser("validate", help="validate one Skill directory")
    validate.add_argument("skill_path")

    package = commands.add_parser("package", help="create a deterministic .skill archive")
    package.add_argument("skill_path")
    package.add_argument("--output-directory", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            instructions = args.instructions
            if args.instructions_file:
                instructions = Path(args.instructions_file).read_text(encoding="utf-8")
            target = initialize_skill(
                args.workspace,
                name=args.name,
                description=args.description,
                instructions=instructions,
                resources=tuple(args.resource),
            )
            print(json.dumps({"created": str(target)}, ensure_ascii=False))
            return 0
        if args.command == "validate":
            report = validate_skill_package(args.skill_path)
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
            return 0 if report.valid else 1
        destination = package_skill(
            args.skill_path,
            output_directory=args.output_directory,
        )
        print(json.dumps({"package": str(destination)}, ensure_ascii=False))
        return 0
    except (OSError, SkillCreatorError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
