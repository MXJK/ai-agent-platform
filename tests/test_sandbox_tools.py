from pathlib import Path
import shlex
from tempfile import TemporaryDirectory
import sys
import unittest

from ai_agent_platform.agents.coding_agent import create_coding_tool_registry
from ai_agent_platform.integrations.tools import ToolCall, ToolExecutionContext


class SandboxToolTests(unittest.TestCase):
    def test_sandbox_tool_specs_mark_writes_and_commands_for_approval(self) -> None:
        with TemporaryDirectory() as temp_dir:
            registry = create_coding_tool_registry(root_path=Path(temp_dir))
            specs = {spec.name: spec for spec in registry.list_specs()}

        self.assertEqual(specs["sandbox.write_file"].permission_level, "write_safe")
        self.assertTrue(specs["sandbox.write_file"].requires_approval)
        self.assertEqual(specs["sandbox.apply_patch"].permission_level, "write_safe")
        self.assertTrue(specs["sandbox.apply_patch"].requires_approval)
        self.assertEqual(specs["sandbox.run_command"].permission_level, "write_safe")
        self.assertTrue(specs["sandbox.run_command"].requires_approval)
        self.assertEqual(specs["sandbox.git_diff"].permission_level, "read_only")
        self.assertFalse(specs["sandbox.git_diff"].requires_approval)

    def test_sandbox_write_command_and_diff_stay_inside_workspace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "app.py"
            source_file.write_text("print('old')\n", encoding="utf-8")
            registry = create_coding_tool_registry(root_path=root)
            context = ToolExecutionContext(
                conversation_id="sess_1",
                repository_id="repo_main",
                run_id="run_1",
            )

            write_result = registry.execute(
                ToolCall(
                    name="sandbox.write_file",
                    arguments={
                        "path": "app.py",
                        "content": "print('new')\n",
                    },
                ),
                context=context,
            )
            self.assertTrue(write_result.ok)
            self.assertEqual(source_file.read_text(encoding="utf-8"), "print('old')\n")
            self.assertIn("agent-sandbox-run-1", write_result.result["workspace"])

            command_result = registry.execute(
                ToolCall(
                    name="sandbox.run_command",
                    arguments={
                        "command": f"{shlex.quote(sys.executable)} app.py",
                    },
                ),
                context=context,
            )
            self.assertTrue(command_result.ok)
            self.assertEqual(command_result.result["exit_code"], 0)
            self.assertEqual(command_result.result["stdout"], "new\n")

            diff_result = registry.execute(
                ToolCall(name="sandbox.git_diff", arguments={}),
                context=context,
            )
            self.assertTrue(diff_result.ok)
            self.assertEqual(diff_result.permission_level, "read_only")
            self.assertIn("-print('old')", diff_result.result["diff"])
            self.assertIn("+print('new')", diff_result.result["diff"])
            self.assertEqual(diff_result.result["changed_files"], ["app.py"])

    def test_sandbox_blocks_path_escape_and_denied_command(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("print('safe')\n", encoding="utf-8")
            registry = create_coding_tool_registry(root_path=root)
            context = ToolExecutionContext(
                conversation_id="sess_1",
                repository_id="repo_main",
                run_id="run_2",
            )

            escape_result = registry.execute(
                ToolCall(
                    name="sandbox.write_file",
                    arguments={
                        "path": "../escape.py",
                        "content": "bad",
                    },
                ),
                context=context,
            )
            self.assertFalse(escape_result.ok)
            self.assertIn("escapes sandbox", escape_result.error)

            command_result = registry.execute(
                ToolCall(
                    name="sandbox.run_command",
                    arguments={"command": "rm app.py"},
                ),
                context=context,
            )
            self.assertFalse(command_result.ok)
            self.assertIn("not allowed", command_result.error)

    def test_sandbox_apply_patch_updates_workspace_only(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "app.py"
            source_file.write_text("value = 'old'\n", encoding="utf-8")
            registry = create_coding_tool_registry(root_path=root)
            context = ToolExecutionContext(
                conversation_id="sess_1",
                repository_id="repo_main",
                run_id="run_3",
            )
            patch = (
                "--- a/app.py\n"
                "+++ b/app.py\n"
                "@@ -1 +1 @@\n"
                "-value = 'old'\n"
                "+value = 'new'\n"
            )

            patch_result = registry.execute(
                ToolCall(
                    name="sandbox.apply_patch",
                    arguments={"patch": patch},
                ),
                context=context,
            )
            self.assertTrue(patch_result.ok)
            self.assertEqual(source_file.read_text(encoding="utf-8"), "value = 'old'\n")

            diff_result = registry.execute(
                ToolCall(name="sandbox.git_diff", arguments={}),
                context=context,
            )
            self.assertIn("+value = 'new'", diff_result.result["diff"])


if __name__ == "__main__":
    unittest.main()
