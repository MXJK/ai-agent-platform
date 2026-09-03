from __future__ import annotations

import unittest

from ai_agent_platform.agents.coding.user_questions import (
    UserQuestionResponseError,
    normalize_questions,
    parse_question_response,
    structured_input_request,
)


class UserQuestionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.questions = [
            {
                "id": "change-target-selection",
                "header": "Choose target",
                "question": "Which file should change?",
                "options": [
                    {"label": "alpha.py", "description": "First candidate"},
                    {"label": "beta.py", "description": "Second candidate"},
                ],
                "multi_select": False,
            }
        ]

    def test_structured_selection_round_trips_with_stable_id(self) -> None:
        request = structured_input_request(self.questions)

        result = parse_question_response(
            {
                "answers": [
                    {
                        "id": "change-target-selection",
                        "selected": ["alpha.py"],
                    }
                ]
            },
            request["questions"],
        )

        self.assertEqual(
            result,
            {
                "answers": [
                    {
                        "id": "change-target-selection",
                        "selected": ["alpha.py"],
                    }
                ]
            },
        )

    def test_blank_and_unknown_choices_are_rejected(self) -> None:
        for response, message in (
            ({"answers": []}, "structured answer"),
            (
                {
                    "answers": [
                        {
                            "id": "change-target-selection",
                            "selected": [],
                        }
                    ]
                },
                "requires a selection",
            ),
            (
                {
                    "answers": [
                        {
                            "id": "change-target-selection",
                            "selected": ["invented.py"],
                        }
                    ]
                },
                "outside the pending options",
            ),
        ):
            with self.subTest(response=response), self.assertRaisesRegex(
                UserQuestionResponseError, message
            ):
                parse_question_response(response, self.questions)

    def test_skip_must_be_explicit(self) -> None:
        result = parse_question_response(
            {
                "answers": [
                    {
                        "id": "change-target-selection",
                        "selected": [],
                        "skipped": True,
                    }
                ]
            },
            self.questions,
        )

        self.assertTrue(result["answers"][0]["skipped"])

    def test_legacy_checkpoint_is_normalized_but_text_needs_opt_in(self) -> None:
        questions = normalize_questions(
            {
                "call_id": "legacy-call",
                "question": "Name the symbol",
                "context": "Old checkpoint",
            }
        )

        with self.assertRaises(UserQuestionResponseError):
            parse_question_response({"message": "Service"}, questions)
        result = parse_question_response(
            {"message": "Service"},
            questions,
            allow_legacy_message=True,
        )

        self.assertEqual(result["answers"][0]["custom"], "Service")


if __name__ == "__main__":
    unittest.main()
