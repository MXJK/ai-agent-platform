from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from ai_agent_platform.integrations import (
    DirectoryPickerUnavailableError,
    SystemDirectoryPicker,
)


class SystemDirectoryPickerTests(unittest.TestCase):
    def test_macos_picker_returns_selected_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            selected = Path(temp_dir).resolve()
            calls = []

            def run(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=f"{selected}/\n",
                    stderr="",
                )

            picker = SystemDirectoryPicker(
                platform_name="darwin",
                executable_finder=lambda name: "/usr/bin/osascript",
                command_runner=run,
            )

            result = picker.pick_directory(initial_path=str(selected))

        self.assertEqual(result, str(selected))
        self.assertEqual(calls[0][0][0], "/usr/bin/osascript")
        self.assertIn('tell application "Finder"', calls[0][0][2])
        self.assertEqual(calls[0][0][-1], str(selected))
        self.assertEqual(calls[0][1]["timeout"], 300.0)

    def test_macos_picker_cancel_returns_none(self) -> None:
        picker = SystemDirectoryPicker(
            platform_name="darwin",
            executable_finder=lambda name: "/usr/bin/osascript",
            command_runner=lambda command, **kwargs: subprocess.CompletedProcess(
                command,
                0,
                stdout="__CANCELLED__\n",
                stderr="",
            ),
        )

        self.assertIsNone(picker.pick_directory())

    def test_non_macos_picker_reports_unavailable(self) -> None:
        picker = SystemDirectoryPicker(platform_name="linux")

        with self.assertRaises(DirectoryPickerUnavailableError):
            picker.pick_directory()


if __name__ == "__main__":
    unittest.main()
