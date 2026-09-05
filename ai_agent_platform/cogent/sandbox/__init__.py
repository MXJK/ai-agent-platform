from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import platform
import shlex

@dataclass(frozen=True)
class SandboxConfig:
    allow_write: list[str] = field(default_factory=list)
    deny_write: list[str] = field(default_factory=list)
    deny_read: list[str] = field(default_factory=list)
    network_enabled: bool = False

class Sandbox(ABC):

    @abstractmethod
    def wrap_argv(self, argv: list[str], config: SandboxConfig) -> list[str]:
        ...

    def wrap(self, command: str, config: SandboxConfig) -> str:
        return shlex.join(self.wrap_argv(['/bin/bash', '-c', command], config))

    @abstractmethod
    def available(self) -> bool:
        ...

def create_sandbox() -> Sandbox | None:
    system = platform.system()
    if system == 'Darwin':
        from .seatbelt import SeatbeltSandbox
        return SeatbeltSandbox()
    if system == 'Linux':
        from .bwrap import BwrapSandbox
        return BwrapSandbox()
    return None
