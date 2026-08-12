from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
import time
import unittest

from ai_agent_platform.integrations.tools import (
    ToolCall,
    ToolExecutionContext,
    ToolExecutionError,
    ToolRegistry,
)


class ToolExecutionTests(unittest.TestCase):
    def test_run_scoped_view_does_not_modify_source_registry(self) -> None:
        registry = ToolRegistry()
        registry.register("tool.a", lambda: {})
        registry.register("tool.b", lambda: {})

        view = registry.select(("tool.a",))

        self.assertEqual([spec.name for spec in view.list_specs()], ["tool.a"])
        self.assertEqual(
            [spec.name for spec in registry.list_specs()],
            ["tool.a", "tool.b"],
        )
        denied = view.execute(ToolCall(name="tool.b", arguments={}))
        self.assertFalse(denied.ok)
        self.assertEqual(denied.error_code, "unknown_tool")
        self.assertTrue(registry.execute(ToolCall(name="tool.b", arguments={})).ok)

    def test_registry_selection_can_only_remove_known_tools(self) -> None:
        registry = ToolRegistry()
        registry.register("read", lambda: {})
        registry.register("write", lambda: {})

        registry.restrict_to(("read",))

        self.assertEqual([spec.name for spec in registry.list_specs()], ["read"])
        with self.assertRaisesRegex(ValueError, "unknown tool"):
            registry.call(ToolCall(name="write", arguments={}))
        with self.assertRaisesRegex(ValueError, "unknown tools"):
            registry.restrict_to(("missing",))

    def test_rejects_invalid_input_or_output_schema_at_registration(self) -> None:
        registry = ToolRegistry()

        with self.assertRaisesRegex(ValueError, "invalid input schema"):
            registry.register(
                "broken.schema",
                lambda: {},
                input_schema={"type": "not-a-json-schema-type"},
                output_schema={"type": "object"},
            )
        with self.assertRaisesRegex(ValueError, "invalid output schema"):
            registry.register(
                "broken.output_schema",
                lambda: {},
                input_schema={"type": "object"},
                output_schema={"required": "must-be-an-array"},
            )

    def test_validates_nested_input_and_rejects_additional_properties(self) -> None:
        registry = ToolRegistry()
        registry.register(
            "profile.lookup",
            lambda profile: {"name": profile["name"]},
            input_schema={
                "type": "object",
                "properties": {
                    "profile": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "minLength": 2},
                            "role": {"type": "string", "enum": ["admin", "reader"]},
                        },
                        "required": ["name", "role"],
                        "additionalProperties": False,
                    }
                },
                "required": ["profile"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        )

        result = registry.execute(
            ToolCall(
                name="profile.lookup",
                arguments={
                    "profile": {
                        "name": "A",
                        "role": "owner",
                        "unexpected": True,
                    }
                },
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "invalid_tool_arguments")
        self.assertIn("$.profile", result.error)

    def test_rejects_output_that_does_not_match_schema(self) -> None:
        registry = ToolRegistry()
        registry.register(
            "broken.output",
            lambda: {"count": "not-an-integer"},
            input_schema={
                "type": "object",
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
                "additionalProperties": False,
            },
        )

        result = registry.execute(ToolCall(name="broken.output", arguments={}))

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "invalid_tool_output")
        self.assertIn("$.count", result.error)

    def test_times_out_and_retries_only_idempotent_retryable_tools(self) -> None:
        registry = ToolRegistry()
        attempts = 0

        def flaky() -> dict[str, bool]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ToolExecutionError(
                    "temporary failure",
                    code="temporary",
                    retryable=True,
                )
            return {"ready": True}

        registry.register(
            "status.flaky",
            flaky,
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={
                "type": "object",
                "properties": {"ready": {"type": "boolean"}},
                "required": ["ready"],
                "additionalProperties": False,
            },
            max_retries=1,
            idempotent=True,
        )
        registry.register(
            "status.slow",
            lambda: (time.sleep(0.05) or {"ready": True}),
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object"},
            timeout_seconds=0.005,
        )

        recovered = registry.execute(ToolCall(name="status.flaky", arguments={}))
        timed_out = registry.execute(ToolCall(name="status.slow", arguments={}))

        self.assertTrue(recovered.ok)
        self.assertEqual(recovered.attempts, 2)
        self.assertEqual(attempts, 2)
        self.assertFalse(timed_out.ok)
        self.assertEqual(timed_out.error_code, "tool_timeout")

    def test_call_id_is_idempotent_within_run_and_detects_conflicts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            calls = 0

            def mutate(value: int) -> dict[str, int]:
                nonlocal calls
                calls += 1
                return {"value": value}

            registry = ToolRegistry()
            registry.register(
                "state.write",
                mutate,
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                permission_level="write_safe",
                idempotent=False,
            )
            context = ToolExecutionContext(
                conversation_id="sess_1",
                workspace_id="workspace_1",
                workspace_root=str(Path(temp_dir)),
                run_id="run_1",
            )

            first = registry.execute(
                ToolCall(
                    call_id="call_stable",
                    name="state.write",
                    arguments={"value": 1},
                ),
                context=context,
            )
            replay = registry.execute(
                ToolCall(
                    call_id="call_stable",
                    name="state.write",
                    arguments={"value": 1},
                ),
                context=context,
            )
            conflict = registry.execute(
                ToolCall(
                    call_id="call_stable",
                    name="state.write",
                    arguments={"value": 2},
                ),
                context=context,
            )

            self.assertTrue(first.ok)
            self.assertTrue(replay.ok)
            self.assertTrue(replay.cached)
            self.assertEqual(calls, 1)
            self.assertFalse(conflict.ok)
            self.assertEqual(conflict.error_code, "idempotency_conflict")

    def test_concurrent_replay_with_same_call_id_executes_once(self) -> None:
        with TemporaryDirectory() as temp_dir:
            calls = 0

            def mutate() -> dict[str, bool]:
                nonlocal calls
                calls += 1
                time.sleep(0.02)
                return {"done": True}

            registry = ToolRegistry()
            registry.register(
                "state.concurrent_write",
                mutate,
                input_schema={"type": "object", "additionalProperties": False},
                output_schema={
                    "type": "object",
                    "properties": {"done": {"type": "boolean"}},
                    "required": ["done"],
                    "additionalProperties": False,
                },
                permission_level="write_safe",
                idempotent=False,
            )
            context = ToolExecutionContext(
                conversation_id="sess_1",
                workspace_id="workspace_1",
                workspace_root=str(Path(temp_dir)),
                run_id="run_concurrent",
            )

            def execute():
                return registry.execute(
                    ToolCall(
                        call_id="call_once",
                        name="state.concurrent_write",
                        arguments={},
                    ),
                    context=context,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = pool.map(lambda _: execute(), range(2))

            self.assertEqual(calls, 1)
            self.assertTrue(first.ok)
            self.assertTrue(second.ok)
            self.assertTrue(first.cached or second.cached)


if __name__ == "__main__":
    unittest.main()
