from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path
from typing import Any
import unittest

from ai_agent_platform.services.context_budget import (
    ContextBudgetPolicy,
    fit_context_to_budget,
    fit_text_to_tokens,
)
from ai_agent_platform.token_counting import estimate_text_tokens


class _NativeMessagePolicy:
    """Test adapter for the native transcript's richer dictionary shape."""

    def cost(self, item: dict[str, Any]) -> int:
        return len(str(item.get("content") or ""))

    def truncate(
        self,
        item: dict[str, Any],
        *,
        overflow_tokens: int,
        minimum_tokens: int,
    ) -> dict[str, Any]:
        content = item.get("content")
        if not isinstance(content, str):
            return item
        allowed = max(minimum_tokens, len(content) - overflow_tokens)
        if len(content) <= allowed:
            return item
        return {**item, "content": content[:allowed]}

    def is_protected(
        self,
        item: dict[str, Any],
        *,
        index: int,
        items: Sequence[dict[str, Any]],
    ) -> bool:
        del index, items
        return bool(item.get("tool_calls") or item.get("call_id"))


class ContextBudgetPrimitiveTests(unittest.TestCase):
    def test_native_policy_preserves_tool_pair_metadata_without_normalizing(self) -> None:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "old turn"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "call_id": "call_1",
                        "name": "repo.read_file",
                        "arguments": {"path": "app.py"},
                    }
                ],
            },
            {
                "role": "tool",
                "call_id": "call_1",
                "name": "repo.read_file",
                "content": "x" * 100,
                "is_error": False,
            },
        ]
        original = [dict(item) for item in messages]
        policy: ContextBudgetPolicy[dict[str, Any]] = _NativeMessagePolicy()

        reduction = fit_context_to_budget(
            messages,
            8,
            policy=policy,
            minimum_truncated_tokens=4,
        )

        self.assertEqual(reduction.dropped, 1)
        self.assertEqual(reduction.truncated, 1)
        self.assertEqual(reduction.compacted, 0)
        self.assertEqual(reduction.evicted, 0)
        self.assertEqual(
            reduction.items[0]["tool_calls"][0]["call_id"],
            "call_1",
        )
        self.assertEqual(reduction.items[1]["call_id"], "call_1")
        self.assertIs(reduction.items[1]["is_error"], False)
        self.assertEqual(reduction.items[1]["content"], "xxxxxxxx")
        self.assertEqual(messages, original)

    def test_text_fitting_preserves_head_tail_and_respects_estimator(self) -> None:
        fitted = fit_text_to_tokens(
            "head " + "x" * 1000 + " tail",
            80,
            estimate_tokens=estimate_text_tokens,
        )

        self.assertLessEqual(estimate_text_tokens(fitted), 80)
        self.assertTrue(fitted.startswith("head "))
        self.assertTrue(fitted.endswith(" tail"))
        self.assertIn("truncated to fit the context budget", fitted)

    def test_module_has_no_repository_llm_or_metrics_imports(self) -> None:
        source_path = (
            Path(__file__).parents[1]
            / "ai_agent_platform"
            / "services"
            / "context_budget.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        project_imports = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("ai_agent_platform")
        ]

        self.assertEqual(project_imports, [])


if __name__ == "__main__":
    unittest.main()
