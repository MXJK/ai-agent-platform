from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Lock
from typing import Protocol


_CANCELLED_OUTPUT = "__CANCELLED__"
_MACOS_PICKER_SCRIPT = f"""
on run argv
    set initialPath to item 1 of argv
    try
        tell application "Finder"
            activate
            set selectedFolder to choose folder with prompt "选择工作区文件夹" default location (POSIX file initialPath)
        end tell
        return POSIX path of selectedFolder
    on error number -128
        return "{_CANCELLED_OUTPUT}"
    end try
end run
""".strip()


class DirectoryPicker(Protocol):
    def pick_directory(self, *, initial_path: str | None = None) -> str | None:
        ...


class DirectoryPickerUnavailableError(RuntimeError):
    pass


class DirectoryPickerBusyError(RuntimeError):
    pass


class DirectoryPickerError(RuntimeError):
    pass


class SystemDirectoryPicker:
    """Opens an operating-system folder dialog on the local API host."""

    def __init__(
        self,
        *,
        platform_name: str | None = None,
        executable_finder: Callable[[str], str | None] = shutil.which,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: float = 300.0,
    ) -> None:
        self._platform_name = platform_name or sys.platform
        self._executable_finder = executable_finder
        self._command_runner = command_runner
        self._timeout_seconds = timeout_seconds
        self._lock = Lock()

    def pick_directory(self, *, initial_path: str | None = None) -> str | None:
        if not self._lock.acquire(blocking=False):
            raise DirectoryPickerBusyError("a directory picker is already open")
        try:
            if self._platform_name != "darwin":
                raise DirectoryPickerUnavailableError(
                    "native directory picker is not available on this platform"
                )
            executable = self._executable_finder("osascript")
            if executable is None:
                raise DirectoryPickerUnavailableError(
                    "macOS directory picker is unavailable"
                )
            initial = _existing_initial_directory(initial_path)
            try:
                result = self._command_runner(
                    [
                        executable,
                        "-e",
                        _MACOS_PICKER_SCRIPT,
                        str(initial),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise DirectoryPickerError("directory picker timed out") from exc
            except OSError as exc:
                raise DirectoryPickerUnavailableError(
                    "macOS directory picker could not be started"
                ) from exc

            if result.returncode != 0:
                raise DirectoryPickerError("directory picker failed")
            selected = result.stdout.strip()
            if not selected or selected == _CANCELLED_OUTPUT:
                return None
            return str(Path(selected).expanduser().resolve())
        finally:
            self._lock.release()


def _existing_initial_directory(initial_path: str | None) -> Path:
    initial = (
        Path(initial_path).expanduser().resolve()
        if initial_path
        else Path.home().resolve()
    )
    if initial.exists() and initial.is_dir():
        return initial
    return Path.home().resolve()
