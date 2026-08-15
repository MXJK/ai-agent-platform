import os
import hashlib
from pathlib import Path
import shlex
import subprocess
from tempfile import TemporaryDirectory
import sys
import time
import unittest
from unittest.mock import patch

from ai_agent_platform.agents.coding_agent import create_coding_tool_registry
from ai_agent_platform.integrations.sandbox import (
    BoundedProcessResult,
    SandboxRuntime,
)
from ai_agent_platform.integrations.tools import ToolCall, ToolExecutionContext


class SandboxToolTests(unittest.TestCase):
    def test_sandbox_tool_specs_mark_writes_and_commands_for_approval(self) -> None:
        with TemporaryDirectory() as temp_dir:
            registry = self._registry()
            specs = {spec.name: spec for spec in registry.list_specs()}

        self.assertEqual(specs["sandbox.write_file"].permission_level, "write_safe")
        self.assertTrue(specs["sandbox.write_file"].requires_approval)
        self.assertEqual(specs["sandbox.apply_patch"].permission_level, "write_safe")
        self.assertTrue(specs["sandbox.apply_patch"].requires_approval)
        self.assertEqual(specs["sandbox.run_command"].permission_level, "write_safe")
        self.assertTrue(specs["sandbox.run_command"].requires_approval)
        self.assertIn("Allowed executable basenames", specs["sandbox.run_command"].description)
        self.assertIn("repo.list_files", specs["sandbox.run_command"].description)
        self.assertNotIn(" ls,", specs["sandbox.run_command"].description)
        self.assertEqual(specs["sandbox.git_diff"].permission_level, "read_only")
        self.assertFalse(specs["sandbox.git_diff"].requires_approval)

    def test_sandbox_write_command_and_diff_stay_inside_workspace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "app.py"
            source_file.write_text("print('old')\n", encoding="utf-8")
            registry = self._registry()
            context = ToolExecutionContext(
                conversation_id="sess_1",
                workspace_id="workspace_main",
                workspace_root=str(root),
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
            self.assertIn(
                "agent-sandbox-sess-1-workspace-main-run-1",
                write_result.result["workspace"],
            )

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

    def test_context_export_keeps_full_patch_and_baseline_before_cleanup(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "app.py"
            source_file.write_text("print('old')\n", encoding="utf-8")
            registry = self._registry()
            context = _context(root, run_id="run_export")
            registry.execute(
                ToolCall(
                    name="sandbox.write_file",
                    arguments={
                        "path": "app.py",
                        "content": "print('new')\n",
                    },
                ),
                context=context,
            )

            exported = registry.export_context("sandbox", context)

            self.assertEqual(exported["source_root"], str(root.resolve()))
            self.assertEqual(exported["changed_files"], ["app.py"])
            self.assertIn("+print('new')", exported["patch"])
            self.assertEqual(
                exported["baseline_file_hashes"]["app.py"],
                hashlib.sha256(b"print('old')\n").hexdigest(),
            )
            self.assertEqual(exported["binary_files"], [])
            self.assertEqual(registry.cleanup_context(context), [])

    def test_exported_patch_applies_files_without_terminal_newlines(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "app.py"
            source_file.write_bytes(b"value = 'old'")
            registry = self._registry()
            context = _context(root, run_id="run_no_terminal_newline")
            registry.execute(
                ToolCall(
                    name="sandbox.write_file",
                    arguments={"path": "app.py", "content": "value = 'new'"},
                ),
                context=context,
            )
            exported = registry.export_context("sandbox", context)

            result = subprocess.run(
                ["git", "apply", "-"],
                cwd=root,
                input=exported["patch"],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(source_file.read_bytes(), b"value = 'new'")
            self.assertEqual(
                exported["patch"].count("\\ No newline at end of file"),
                2,
            )
            self.assertEqual(registry.cleanup_context(context), [])

    def test_sandbox_blocks_path_escape_and_denied_command(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("print('safe')\n", encoding="utf-8")
            registry = self._registry()
            context = ToolExecutionContext(
                conversation_id="sess_1",
                workspace_id="workspace_main",
                workspace_root=str(root),
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
            self.assertIn("allowlist", command_result.error)

    def test_sandbox_apply_patch_updates_workspace_only(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "app.py"
            source_file.write_text("value = 'old'\n", encoding="utf-8")
            registry = self._registry()
            context = ToolExecutionContext(
                conversation_id="sess_1",
                workspace_id="workspace_main",
                workspace_root=str(root),
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

    def test_large_command_output_is_capped_without_losing_exit_code(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            registry = self._registry()
            context = ToolExecutionContext(
                conversation_id="sess_1",
                workspace_id="workspace_main",
                workspace_root=str(root),
                run_id="run_large_output",
            )
            output_script = "print('x' * 25000)"
            command = (
                f"{shlex.quote(sys.executable)} -c {shlex.quote(output_script)}"
            )

            result = registry.execute(
                ToolCall(
                    name="sandbox.run_command",
                    arguments={"command": command},
                ),
                context=context,
            )

            self.assertTrue(result.ok)
            self.assertTrue(result.output_truncated)
            self.assertEqual(result.result["exit_code"], 0)
            self.assertNotIn("stdout", result.result)
            self.assertIn("truncated_output_preview", result.result)

    def test_copy_skips_sensitive_symlink_and_special_files_with_warnings(self) -> None:
        with (
            TemporaryDirectory() as source_dir,
            TemporaryDirectory() as outside_dir,
            TemporaryDirectory() as sandbox_parent,
        ):
            root = Path(source_dir)
            outside = Path(outside_dir) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (root / ".env").write_text("SECRET=value\n", encoding="utf-8")
            (root / ".env.example").write_text("SAFE=example\n", encoding="utf-8")
            (root / "credentials.json").write_text("{}", encoding="utf-8")
            (root / "private.pem").write_text("private", encoding="utf-8")
            (root / ".ssh").mkdir()
            (root / ".ssh" / "config").write_text("Host *\n", encoding="utf-8")
            (root / "escape-link").symlink_to(outside)
            os.mkfifo(root / "named-pipe")
            registry = self._registry(
                sandbox_workspace_parent=sandbox_parent,
            )
            context = _context(root, run_id="run_copy_boundary")

            status = registry.execute(
                ToolCall(name="sandbox.workspace_status", arguments={}),
                context=context,
            )

            self.assertTrue(status.ok)
            workspace = Path(status.result["workspace"])
            self.assertFalse((workspace / ".env").exists())
            self.assertTrue((workspace / ".env.example").exists())
            self.assertFalse((workspace / "credentials.json").exists())
            self.assertFalse((workspace / "private.pem").exists())
            self.assertFalse((workspace / ".ssh").exists())
            self.assertFalse((workspace / "escape-link").exists())
            self.assertFalse((workspace / "named-pipe").exists())
            warnings = status.result["copy_warnings"]
            self.assertTrue(any("sensitive file" in item for item in warnings))
            self.assertTrue(any("symbolic link" in item for item in warnings))
            self.assertTrue(any("special file" in item for item in warnings))

            self.assertEqual(registry.cleanup_context(context), [])
            self.assertFalse(workspace.exists())

    def test_local_command_rejects_shell_wrapper_and_strips_secret_environment(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            registry = self._registry()
            context = _context(root, run_id="run_command_policy")

            shell_result = registry.execute(
                ToolCall(
                    name="sandbox.run_command",
                    arguments={"command": "sh -c 'echo unsafe'"},
                ),
                context=context,
            )
            with patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "must-not-reach-sandbox"},
            ):
                env_result = registry.execute(
                    ToolCall(
                        name="sandbox.run_command",
                        arguments={
                            "command": _python_command(
                                "import os; print(os.getenv('OPENAI_API_KEY', 'missing'))"
                            )
                        },
                    ),
                    context=context,
                )

            self.assertFalse(shell_result.ok)
            self.assertIn("shell wrappers", shell_result.error)
            self.assertTrue(env_result.ok)
            self.assertEqual(env_result.result["stdout"], "missing\n")

    def test_local_command_timeout_kills_process_group(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = self._registry(
                sandbox_command_timeout_seconds=0.2,
            )
            context = _context(root, run_id="run_timeout")
            script = (
                "import pathlib,subprocess,sys,time;"
                "subprocess.Popen([sys.executable,'-c',"
                "\"import pathlib,time;time.sleep(0.7);"
                "pathlib.Path('escaped.txt').write_text('bad')\"]);"
                "time.sleep(5)"
            )
            started = time.monotonic()

            result = registry.execute(
                ToolCall(
                    name="sandbox.run_command",
                    arguments={
                        "command": _python_command(script),
                        "timeout_seconds": 10,
                    },
                ),
                context=context,
            )
            elapsed = time.monotonic() - started
            workspace = Path(result.result["workspace"])
            time.sleep(0.8)

            self.assertTrue(result.ok)
            self.assertTrue(result.result["timed_out"])
            self.assertEqual(result.result["exit_code"], 124)
            self.assertLess(elapsed, 2)
            self.assertFalse((workspace / "escaped.txt").exists())

    def test_runtime_prunes_only_stale_sandbox_directories(self) -> None:
        with TemporaryDirectory() as sandbox_parent:
            parent = Path(sandbox_parent)
            stale = parent / "agent-sandbox-stale-example"
            fresh = parent / "agent-sandbox-fresh-example"
            unrelated = parent / "unrelated"
            for path in (stale, fresh, unrelated):
                path.mkdir()
            old = time.time() - 10
            os.utime(stale, (old, old))

            runtime = SandboxRuntime(
                workspace_parent=parent,
                workspace_ttl_seconds=1,
            )

            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(unrelated.exists())
            runtime.cleanup_all()

    def test_sandbox_parent_cannot_be_inside_source_workspace(self) -> None:
        with TemporaryDirectory() as source_dir:
            root = Path(source_dir)
            sandbox_parent = root / ".sandboxes"
            runtime = SandboxRuntime(workspace_parent=sandbox_parent)

            with self.assertRaisesRegex(ValueError, "must not be inside"):
                runtime.workspace_status(
                    context=_context(root, run_id="run_recursive_parent")
                )

    def test_docker_mode_applies_hardening_flags(self) -> None:
        with (
            TemporaryDirectory() as source_dir,
            TemporaryDirectory() as sandbox_parent,
        ):
            root = Path(source_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            registry = self._registry(
                sandbox_mode="docker",
                sandbox_workspace_parent=sandbox_parent,
            )
            context = _context(root, run_id="run_docker_flags")
            completed = BoundedProcessResult(
                returncode=0,
                stdout="ok\n",
                stderr="",
                output_truncated=False,
                timed_out=False,
            )

            with patch(
                "ai_agent_platform.integrations.sandbox._run_bounded_process",
                return_value=completed,
            ) as run_process:
                result = registry.execute(
                    ToolCall(
                        name="sandbox.run_command",
                        arguments={"command": "python app.py"},
                    ),
                    context=context,
                )

            self.assertTrue(result.ok)
            docker_command = run_process.call_args.args[0]
            for expected in (
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "128",
                "--user",
                "--tmpfs",
            ):
                self.assertIn(expected, docker_command)

    def _registry(self, **kwargs):
        registry = create_coding_tool_registry(**kwargs)
        self.addCleanup(registry.close)
        return registry


def _context(root: Path, *, run_id: str) -> ToolExecutionContext:
    return ToolExecutionContext(
        conversation_id="sess_1",
        workspace_id="workspace_main",
        workspace_root=str(root),
        run_id=run_id,
    )


def _python_command(script: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


if __name__ == "__main__":
    unittest.main()
