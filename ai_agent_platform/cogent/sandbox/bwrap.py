from __future__ import annotations
from pathlib import Path
import shutil
from ai_agent_platform.cogent.sandbox import Sandbox, SandboxConfig

class BwrapSandbox(Sandbox):

    def wrap_argv(self, argv: list[str], config: SandboxConfig) -> list[str]:
        args = [shutil.which('bwrap') or 'bwrap', '--die-with-parent', '--new-session', '--unshare-user', '--unshare-pid', '--ro-bind', '/', '/']
        for path in config.allow_write:
            resolved = str(Path(path).resolve(strict=True))
            args.extend(['--bind', resolved, resolved])
        for path in config.deny_write:
            target = Path(path).resolve()
            if not target.exists():
                continue
            resolved = str(target)
            args.extend(['--ro-bind', resolved, resolved])
        for path in config.deny_read:
            target = Path(path).resolve()
            if not target.exists():
                continue
            if target.is_dir():
                args.extend(['--tmpfs', str(target)])
            else:
                args.extend(['--ro-bind', '/dev/null', str(target)])
        if not config.network_enabled:
            args.append('--unshare-net')
        return [*args, '--proc', '/proc', '--dev', '/dev', '--', *argv]

    def available(self) -> bool:
        return shutil.which('bwrap') is not None
