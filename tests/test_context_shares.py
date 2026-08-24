from __future__ import annotations

import unittest

from ai_agent_platform.services.context_budget import (
    ContextShares,
    divide_context_budget,
)


class DivideContextBudgetTests(unittest.TestCase):
    def test_named_shares_add_back_up_to_the_allowance(self) -> None:
        shares = divide_context_budget(
            10_000,
            system_tokens=600,
            tool_schema_tokens=400,
            evidence_ratio=0.25,
            history_ratio=0.15,
        )

        self.assertEqual(shares.total_tokens, 10_000)
        self.assertEqual(
            shares.system_tokens
            + shares.tool_schema_tokens
            + shares.evidence_tokens
            + shares.history_tokens
            + shares.transcript_tokens,
            10_000,
        )
        self.assertEqual(shares.message_tokens, 9_600)

    def test_transcript_receives_the_remainder_after_fixed_overhead(self) -> None:
        shares = divide_context_budget(
            10_000,
            system_tokens=600,
            tool_schema_tokens=400,
            evidence_ratio=0.25,
            history_ratio=0.15,
        )

        self.assertEqual(shares.evidence_tokens, 2_250)
        self.assertEqual(shares.history_tokens, 1_350)
        self.assertEqual(shares.transcript_tokens, 5_400)

    def test_shares_scale_with_the_window(self) -> None:
        large = divide_context_budget(
            100_000,
            evidence_ratio=0.25,
            history_ratio=0.15,
        )
        small = divide_context_budget(
            10_000,
            evidence_ratio=0.25,
            history_ratio=0.15,
        )

        self.assertEqual(large.evidence_tokens, small.evidence_tokens * 10)
        self.assertEqual(large.history_tokens, small.history_tokens * 10)

    def test_overhead_larger_than_the_window_leaves_no_transcript(self) -> None:
        shares = divide_context_budget(
            500,
            system_tokens=300,
            tool_schema_tokens=800,
            evidence_ratio=0.25,
            history_ratio=0.15,
        )

        self.assertEqual(shares.system_tokens, 300)
        self.assertEqual(shares.tool_schema_tokens, 200)
        self.assertEqual(shares.evidence_tokens, 0)
        self.assertEqual(shares.history_tokens, 0)
        self.assertEqual(shares.transcript_tokens, 0)
        self.assertFalse(shares.fits)

    def test_a_positive_transcript_share_reports_as_fitting(self) -> None:
        shares = divide_context_budget(
            10_000,
            system_tokens=600,
            tool_schema_tokens=400,
            evidence_ratio=0.25,
            history_ratio=0.15,
        )

        self.assertTrue(shares.fits)
        self.assertIsInstance(shares, ContextShares)

    def test_negative_allowance_is_clamped_rather_than_raising(self) -> None:
        shares = divide_context_budget(
            -1,
            system_tokens=10,
            tool_schema_tokens=10,
            evidence_ratio=0.25,
            history_ratio=0.15,
        )

        self.assertEqual(shares.total_tokens, 0)
        self.assertEqual(shares.transcript_tokens, 0)

    def test_ratios_that_leave_no_transcript_room_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            divide_context_budget(
                10_000,
                evidence_ratio=0.6,
                history_ratio=0.4,
            )

    def test_ratios_outside_the_unit_interval_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            divide_context_budget(
                10_000,
                evidence_ratio=-0.1,
                history_ratio=0.1,
            )
        with self.assertRaises(ValueError):
            divide_context_budget(
                10_000,
                evidence_ratio=0.1,
                history_ratio=1.0,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
