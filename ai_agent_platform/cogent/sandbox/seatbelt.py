from __future__ import annotations
import json
from pathlib import Path
from ai_agent_platform.cogent.sandbox import Sandbox, SandboxConfig
_SANDBOX_EXEC = '/usr/bin/sandbox-exec'

def _path(path: str) -> str:
    return json.dumps(str(Path(path).resolve()), ensure_ascii=False)

def _build_profile(config: SandboxConfig) -> str:
    rules = ['(version 1)', '(deny default)', '(allow process-exec)', '(allow process-fork)', '(allow sysctl-read)', '(allow file-read* (subpath "/"))', '(allow file-write* (literal "/dev/null") (literal "/dev/tty"))']
    for path in config.allow_write:
        rules.append(f'(allow file-write* (subpath {_path(path)}))')
    for path in config.deny_write:
        rules.append(f'(deny file-write* (subpath {_path(path)}))')
    for path in config.deny_read:
        rules.append(f'(deny file-read* (subpath {_path(path)}))')
    rules.append('(allow network*)' if config.network_enabled else '(deny network*)')
    return '\n'.join(rules)

class SeatbeltSandbox(Sandbox):

    def wrap_argv(self, argv: list[str], config: SandboxConfig) -> list[str]:
        return [_SANDBOX_EXEC, '-p', _build_profile(config), *argv]

    def available(self) -> bool:
        return Path(_SANDBOX_EXEC).is_file()
