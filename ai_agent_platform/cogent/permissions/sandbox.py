from __future__ import annotations
from pathlib import Path

class PathSandbox:
    _DEFAULT_DENY_WRITE: list[str] = ['.cogent/config.yaml', '.cogent/permissions.yaml', '.cogent/permissions.local.yaml', '.cogent/skills/', '.cogent/file-history/', '.cogent/sessions/', '.cogent/memory/']

    def __init__(self, project_root: str, extra_allowed: list[str] | None=None, deny_write: list[str] | None=None) -> None:
        root = Path(project_root).resolve()
        self._allowed_roots: list[Path] = [root]
        if extra_allowed:
            for p in extra_allowed:
                self._allowed_roots.append(Path(p).resolve())
        self._deny_write: list[Path] = []
        for dp in deny_write or self._DEFAULT_DENY_WRITE:
            dp_path = Path(dp)
            if not dp_path.is_absolute():
                dp_path = root / dp_path
            self._deny_write.append(dp_path.resolve())

    @property
    def project_root(self) -> Path:
        return self._allowed_roots[0]

    def _is_deny_write(self, real_path: Path) -> bool:
        for deny_path in self._deny_write:
            if real_path == deny_path:
                return True
            try:
                real_path.relative_to(deny_path)
                return True
            except ValueError:
                continue
        return False

    def _resolve(self, path: str) -> tuple[Path | None, str]:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.project_root / p
        try:
            return (p.resolve(strict=False), '')
        except (OSError, RuntimeError, ValueError):
            return (None, f'无法解析路径: {path}')

    def check_deny_write(self, path: str) -> tuple[bool, str]:
        real_path, err = self._resolve(path)
        if real_path is None:
            return (False, err)
        if self._is_deny_write(real_path):
            return (False, f'路径 {path} 在禁写列表中')
        return (True, '')

    def check(self, path: str) -> tuple[bool, str]:
        real_path, err = self._resolve(path)
        if real_path is None:
            return (False, err)
        for root in self._allowed_roots:
            try:
                real_path.relative_to(root)
                return (True, '')
            except ValueError:
                continue
        return (False, f'路径 {path} 超出沙箱范围')
