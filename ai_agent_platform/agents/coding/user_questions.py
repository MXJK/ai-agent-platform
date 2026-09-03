"""Structured user-question contracts shared by checkpoints, APIs, and tools."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable


MAX_QUESTIONS = 3
MAX_OPTIONS = 20


class UserQuestionResponseError(ValueError):
    """The submitted response does not complete the pending question batch."""


def normalize_questions(
    request: dict[str, Any],
    *,
    default_id: str = "user-input",
) -> list[dict[str, Any]]:
    """Return a bounded DeepSeek-Harness-style question batch.

    Old checkpoints only carried ``question``/``context``/``candidate_paths``.
    They remain readable, but every newly presented request receives the same
    structured shape as a current request.
    """

    raw_questions = request.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raw_options = request.get("candidate_paths")
        raw_questions = [
            {
                "id": str(request.get("call_id") or default_id),
                "header": "需要你的输入",
                "question": str(
                    request.get("question")
                    or "Agent 需要你补充信息后才能继续。"
                ),
                "detail": str(request.get("context") or ""),
                "options": (
                    [
                        {
                            "label": str(path),
                            "description": "使用此工作区相对路径继续。",
                        }
                        for path in raw_options
                    ]
                    if isinstance(raw_options, list)
                    else []
                ),
                "multi_select": False,
            }
        ]

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_questions[:MAX_QUESTIONS]):
        if not isinstance(raw, dict):
            continue
        question_id = str(
            raw.get("id") or f"{default_id}-{index + 1}"
        ).strip()[:128]
        question = str(raw.get("question") or "").strip()
        if not question_id or not question or question_id in seen_ids:
            continue
        seen_ids.add(question_id)
        options: list[dict[str, str]] = []
        seen_labels: set[str] = set()
        raw_options = raw.get("options", [])
        for option in (
            raw_options[:MAX_OPTIONS] if isinstance(raw_options, list) else []
        ):
            if isinstance(option, dict):
                label = str(option.get("label") or "").strip()
                description = str(option.get("description") or "").strip()
            else:
                label = str(option or "").strip()
                description = ""
            if not label or label in seen_labels:
                continue
            seen_labels.add(label)
            options.append(
                {
                    "label": label,
                    **({"description": description} if description else {}),
                }
            )
        normalized.append(
            {
                "id": question_id,
                "question": question[:1000],
                **(
                    {"header": str(raw.get("header") or "").strip()[:80]}
                    if str(raw.get("header") or "").strip()
                    else {}
                ),
                **(
                    {
                        "detail": str(
                            raw.get("detail") or raw.get("context") or ""
                        ).strip()[:2000]
                    }
                    if str(raw.get("detail") or raw.get("context") or "").strip()
                    else {}
                ),
                **({"options": options} if options else {}),
                "multi_select": bool(
                    raw.get("multi_select", raw.get("multiSelect", False))
                ),
            }
        )
    if not normalized:
        raise UserQuestionResponseError("question batch must not be empty")
    return normalized


def structured_input_request(
    questions: Iterable[dict[str, Any]],
    *,
    call_id: str = "",
    context: str = "",
    candidate_paths: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a UI/checkpoint payload while retaining legacy display fields."""

    batch = normalize_questions({"questions": list(questions)}, default_id=call_id)
    first = batch[0]
    paths = [str(path) for path in candidate_paths if str(path)]
    return {
        "type": "input_required",
        "question_protocol": "structured-v1",
        "questions": batch,
        "question": first["question"],
        "context": context or str(first.get("detail") or ""),
        **({"call_id": call_id} if call_id else {}),
        **({"candidate_paths": paths} if paths else {}),
    }


def parse_question_response(
    response: Any,
    questions: Iterable[dict[str, Any]],
    *,
    allow_legacy_message: bool = False,
) -> dict[str, Any]:
    """Validate stable ids, choices, custom answers, and explicit skips."""

    batch = normalize_questions({"questions": list(questions)})
    payload = response if isinstance(response, dict) else {"message": response}
    raw_answers = payload.get("answers")
    if not isinstance(raw_answers, list) or not raw_answers:
        legacy = str(
            payload.get("message")
            or payload.get("feedback")
            or payload.get("answer")
            or ""
        ).strip()
        if allow_legacy_message and legacy and len(batch) == 1:
            raw_answers = [{"id": batch[0]["id"], "custom": legacy}]
        else:
            raise UserQuestionResponseError(
                "submit a structured answer or explicitly skip the question"
            )

    answers_by_id: dict[str, dict[str, Any]] = {}
    for raw in raw_answers:
        if not isinstance(raw, dict):
            raise UserQuestionResponseError("every answer must be an object")
        question_id = str(raw.get("id") or "").strip()
        if not question_id or question_id in answers_by_id:
            raise UserQuestionResponseError("answer ids must be present and unique")
        answers_by_id[question_id] = raw

    expected_ids = {str(question["id"]) for question in batch}
    if set(answers_by_id) != expected_ids:
        raise UserQuestionResponseError(
            "answers must match every pending question id exactly"
        )

    normalized: list[dict[str, Any]] = []
    for question in batch:
        question_id = str(question["id"])
        raw = answers_by_id[question_id]
        skipped = bool(raw.get("skipped", False))
        selected_raw = raw.get("selected", [])
        if not isinstance(selected_raw, list):
            raise UserQuestionResponseError("selected choices must be an array")
        selected = list(
            dict.fromkeys(
                str(item).strip() for item in selected_raw if str(item).strip()
            )
        )
        custom = str(raw.get("custom") or "").strip()
        option_labels = {
            str(option.get("label") or "")
            for option in question.get("options", [])
            if isinstance(option, dict)
        }
        unknown = [item for item in selected if item not in option_labels]
        if unknown:
            raise UserQuestionResponseError(
                f"selected choices are outside the pending options: {', '.join(unknown)}"
            )
        if not question.get("multi_select") and len(selected) > 1:
            raise UserQuestionResponseError("question accepts only one selected choice")
        if skipped and (selected or custom):
            raise UserQuestionResponseError(
                "a skipped answer cannot also contain a selection or custom text"
            )
        if not skipped and not selected and not custom:
            raise UserQuestionResponseError(
                "every question requires a selection, custom text, or explicit skip"
            )
        normalized.append(
            {
                "id": question_id,
                "selected": selected,
                **({"custom": custom} if custom else {}),
                **({"skipped": True} if skipped else {}),
            }
        )
    return {"answers": deepcopy(normalized)}


def first_answer_text(result: dict[str, Any]) -> str:
    """Produce a bounded compatibility string for old model result consumers."""

    answers = result.get("answers", [])
    if not isinstance(answers, list) or not answers:
        return ""
    answer = answers[0] if isinstance(answers[0], dict) else {}
    custom = str(answer.get("custom") or "").strip()
    if custom:
        return custom
    selected = answer.get("selected", [])
    return ", ".join(str(item) for item in selected) if isinstance(selected, list) else ""


__all__ = [
    "UserQuestionResponseError",
    "first_answer_text",
    "normalize_questions",
    "parse_question_response",
    "structured_input_request",
]
