import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.agents.coding.tools import create_coding_tool_registry
from ai_agent_platform.integrations.permissions import (
    PermissionRequest,
    PermissionResolver,
    ToolUseContext,
    effective_approval_policy,
)
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry


class PermissionResolverMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = PermissionResolver()
        self.read = PermissionRequest(name="repo.read", permission_level="read_only")
        self.write = PermissionRequest(
            name="sandbox.write",
            permission_level="write_safe",
            requires_approval=True,
            risk_summary="Writes an isolated Sandbox file.",
        )

    def context(self, **overrides: object) -> ToolUseContext:
        values: dict[str, object] = {
            "conversation_id": "session_1",
            "workspace_id": "workspace_1",
            "workspace_root": "/tmp/workspace_1",
            "authorized_workspace_root": "/tmp/workspace_1",
            "run_id": "run_1",
            "actor_user_id": "user_1",
            "workspace_role": "editor",
            "approval_policy": "on_request",
            "process_allowed_tools": ("repo.read", "sandbox.write", "mcp.read"),
            "project_allowed_tools": ("repo.read", "sandbox.write", "mcp.read"),
        }
        values.update(overrides)
        return ToolUseContext(**values)  # type: ignore[arg-type]

    def test_permission_matrix(self) -> None:
        cases = [
            (self.read, self.context(workspace_role="viewer"), "allow"),
            (self.write, self.context(workspace_role="viewer"), "deny"),
            (self.write, self.context(workspace_role="editor"), "ask"),
            (
                self.read,
                self.context(workspace_role="viewer", approval_policy="always"),
                "ask",
            ),
            (
                self.write,
                self.context(workspace_role="editor", approval_policy="never"),
                "deny",
            ),
        ]
        for request, context, expected in cases:
            with self.subTest(
                tool=request.name,
                role=context.workspace_role,
                policy=context.approval_policy,
            ):
                decision = self.resolver.resolve(request, context, phase="plan")
                self.assertEqual(decision.effect, expected)
                self.assertTrue(decision.matched_rule)
                self.assertTrue(decision.reason)
                self.assertTrue(decision.risk_summary)

    def test_auto_approve_policy_allows_operations_that_would_require_review(self) -> None:
        write_decision = self.resolver.resolve(
            self.write,
            self.context(workspace_role="editor", approval_policy="auto_approve"),
            phase="plan",
        )
        self.assertEqual(write_decision.effect, "allow")
        self.assertEqual(write_decision.matched_rule, "approval_policy.auto_approve")

        read_decision = self.resolver.resolve(
            self.read,
            self.context(workspace_role="viewer", approval_policy="auto_approve"),
            phase="plan",
        )
        self.assertEqual(read_decision.effect, "allow")

    def test_auto_approve_does_not_bypass_hard_boundaries(self) -> None:
        process_denied = self.resolver.resolve(
            self.write,
            self.context(
                workspace_role="editor",
                approval_policy="auto_approve",
                process_denied_tools=("sandbox.write",),
            ),
            phase="plan",
        )
        self.assertEqual(process_denied.effect, "deny")
        self.assertEqual(process_denied.matched_rule, "process.explicit_deny")

        viewer = self.resolver.resolve(
            self.write,
            self.context(workspace_role="viewer", approval_policy="auto_approve"),
            phase="plan",
        )
        self.assertEqual(viewer.effect, "deny")
        self.assertEqual(viewer.matched_rule, "workspace.rbac.editor")

    def test_effective_approval_policy_only_relaxes_on_request(self) -> None:
        self.assertEqual(
            effective_approval_policy("on_request", "auto_approve"),
            "auto_approve",
        )
        self.assertEqual(effective_approval_policy("on_request", None), "on_request")
        self.assertEqual(effective_approval_policy("on_request", "on_request"), "on_request")
        self.assertEqual(effective_approval_policy("always", "auto_approve"), "always")
        self.assertEqual(effective_approval_policy("never", "auto_approve"), "never")

    def test_hard_deny_and_explicit_deny_precede_project_allow_and_approval(self) -> None:
        bound = self.context(
            process_denied_tools=("sandbox.write",),
        ).bind(
            call_id="call_1",
            tool_name="sandbox.write",
            arguments={"path": "app.py", "content": "safe"},
        )
        decision = self.resolver.resolve(self.write, bound, phase="plan")
        self.assertEqual(decision.effect, "deny")
        self.assertEqual(decision.matched_rule, "process.explicit_deny")

        project_denied = self.context(
            project_denied_tools=("repo.read",),
        )
        decision = self.resolver.resolve(self.read, project_denied, phase="display")
        self.assertEqual(decision.effect, "deny")
        self.assertEqual(decision.matched_rule, "project.explicit_deny")

    def test_workspace_root_and_identity_rbac_are_hard_boundaries(self) -> None:
        root_mismatch = self.context(
            workspace_root="/tmp/untrusted",
            authorized_workspace_root="/tmp/workspace_1",
        )
        decision = self.resolver.resolve(self.read, root_mismatch, phase="plan")
        self.assertEqual(decision.effect, "deny")
        self.assertEqual(decision.matched_rule, "workspace.root_boundary")

        viewer = self.context(workspace_role="viewer")
        decision = self.resolver.resolve(self.write, viewer, phase="plan")
        self.assertEqual(decision.effect, "deny")
        self.assertEqual(decision.matched_rule, "workspace.rbac.editor")

    def test_exact_approval_cannot_be_replayed_or_survive_argument_tampering(self) -> None:
        original = self.context().bind(
            call_id="call_1",
            tool_name="sandbox.write",
            arguments={"path": "app.py", "content": "one"},
        )
        grant = self.resolver.issue_approval(
            self.write,
            original,
            approved_by="user_1",
        )
        allowed = self.resolver.resolve(
            self.write,
            original.with_approvals((grant,)),
            phase="execute",
        )
        self.assertEqual(allowed.effect, "allow")

        variants = [
            original.bind(
                call_id="call_1",
                tool_name="sandbox.write",
                arguments={"path": "app.py", "content": "tampered"},
            ),
            self.context(run_id="run_2").bind(
                call_id="call_1",
                tool_name="sandbox.write",
                arguments={"path": "app.py", "content": "one"},
            ),
            self.context().bind(
                call_id="call_2",
                tool_name="sandbox.write",
                arguments={"path": "app.py", "content": "one"},
            ),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                decision = self.resolver.resolve(
                    self.write,
                    variant.with_approvals((grant,)),
                    phase="execute",
                )
                self.assertEqual(decision.effect, "ask")

        different_tool = PermissionRequest(
            name="sandbox.other",
            permission_level="write_safe",
            requires_approval=True,
        )
        context = self.context(
            process_allowed_tools=("sandbox.other",),
            project_allowed_tools=("sandbox.other",),
        ).bind(
            call_id="call_1",
            tool_name="sandbox.other",
            arguments={"path": "app.py", "content": "one"},
        )
        decision = self.resolver.resolve(
            different_tool,
            context.with_approvals((grant,)),
            phase="execute",
        )
        self.assertEqual(decision.effect, "ask")

    def test_provider_annotations_are_advisory_not_final_authorization(self) -> None:
        advisory = PermissionRequest(
            name="mcp.read",
            permission_level="read_only",
            permission_source="mcp_annotation",
            risk_summary="Remote MCP server reports a read-only operation.",
        )
        decision = self.resolver.resolve(
            advisory,
            self.context(workspace_role="viewer"),
            phase="plan",
        )
        self.assertEqual(decision.effect, "ask")
        self.assertEqual(
            decision.matched_rule,
            "provider.annotation_requires_central_approval",
        )


class ToolRegistryPermissionTests(unittest.TestCase):
    def test_always_policy_interrupts_before_read_only_repository_exploration(self) -> None:
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "README.md").write_text("demo", encoding="utf-8")
            runtime = CodingAgentRuntime(
                tool_registry=create_coding_tool_registry(
                    permission_resolver=PermissionResolver()
                ),
                approval_policy="always",
            )
            waiting = runtime.run(
                conversation_id="session_1",
                user_input="what does this project do?",
                history=[],
                workspace_id="workspace_1",
                workspace_root=temp_dir,
            )

        self.assertEqual(waiting.status, "waiting_approval")
        item = waiting.pending_approval["approval_required_tools"][0]
        self.assertEqual(item["permission_level"], "read_only")
        self.assertTrue(item["call_id"])
        self.assertEqual(len(item["arguments_hash"]), 64)

    def test_argument_change_requires_a_new_approval_before_execution(self) -> None:
        calls: list[dict[str, str]] = []
        registry = ToolRegistry(permission_resolver=PermissionResolver())
        registry.register(
            "sandbox.write",
            lambda **arguments: calls.append(arguments) or {"ok": True},
            permission_level="write_safe",
            requires_approval=True,
        )
        context = ToolUseContext(
            conversation_id="session_1",
            workspace_id="workspace_1",
            workspace_root="/tmp/workspace_1",
            authorized_workspace_root="/tmp/workspace_1",
            run_id="run_1",
            actor_user_id="editor_1",
            workspace_role="editor",
            project_allowed_tools=("sandbox.write",),
        )
        original = ToolCall(
            name="sandbox.write",
            call_id="call_1",
            arguments={"content": "original"},
        )
        grant = registry.issue_approval(
            original,
            context,
            approved_by="editor_1",
        )
        tampered = ToolCall(
            name="sandbox.write",
            call_id="call_1",
            arguments={"content": "tampered"},
        )
        result = registry.execute(
            tampered,
            context=context.with_approvals((grant,)),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "permission_approval_required")
        self.assertEqual(calls, [])

    def test_display_filter_does_not_replace_execution_time_authorization(self) -> None:
        calls: list[str] = []
        registry = ToolRegistry(permission_resolver=PermissionResolver())
        registry.register(
            "sandbox.write",
            lambda **_: calls.append("executed") or {"ok": True},
            permission_level="write_safe",
            requires_approval=True,
        )
        visible_context = ToolUseContext(
            conversation_id="session_1",
            workspace_id="workspace_1",
            workspace_root="/tmp/workspace_1",
            authorized_workspace_root="/tmp/workspace_1",
            run_id="run_1",
            actor_user_id="editor_1",
            workspace_role="editor",
            project_allowed_tools=("sandbox.write",),
        )
        self.assertEqual(
            [item.name for item in registry.list_specs(context=visible_context)],
            ["sandbox.write"],
        )

        call = ToolCall(
            name="sandbox.write",
            call_id="call_1",
            arguments={"path": "app.py"},
        )
        grant = registry.issue_approval(
            call,
            visible_context,
            approved_by="editor_1",
        )
        changed_at_execution = visible_context.with_approvals((grant,))
        changed_at_execution = ToolUseContext(
            **{
                **changed_at_execution.__dict__,
                "process_denied_tools": ("sandbox.write",),
            }
        )
        result = registry.execute(call, context=changed_at_execution)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "permission_denied")
        self.assertEqual(result.permission_decision["matched_rule"], "process.explicit_deny")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
