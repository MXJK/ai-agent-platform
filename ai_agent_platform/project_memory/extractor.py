"""Structured project-memory extraction with conservative deterministic fallback."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
from typing import Any, Protocol

from ai_agent_platform.agents.coding.planner import json_object_from_llm
from ai_agent_platform.integrations import LLMClient
from ai_agent_platform.project_memory.models import MEMORY_KINDS


@dataclass(frozen=True)
class MemoryCandidate:
    kind: str
    title: str
    content: str
    canonical_key: str
    confidence: float
    importance: int
    authority: str


@dataclass(frozen=True)
class ExtractionResult:
    candidates: list[MemoryCandidate]
    input_tokens: int = 0
    output_tokens: int = 0


class MemoryExtractor(Protocol):
    def extract(
        self,
        *,
        user_message: str,
        assistant_message: str,
        source_type: str,
        verified: bool,
    ) -> ExtractionResult:
        ...


class LLMMemoryExtractor:
    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    def extract(
        self,
        *,
        user_message: str,
        assistant_message: str,
        source_type: str,
        verified: bool,
    ) -> ExtractionResult:
        deterministic = RuleBasedMemoryExtractor().extract(
            user_message=user_message,
            assistant_message=assistant_message,
            source_type=source_type,
            verified=verified,
        )
        if any(
            item.authority == "explicit_user"
            for item in deterministic.candidates
        ):
            return deterministic
        prompt = extraction_prompt(
            user_message=user_message,
            assistant_message=assistant_message,
            source_type=source_type,
            verified=verified,
        )
        try:
            response = self._llm_client.complete(prompt)
            body = json_object_from_llm(response.text)
            raw_candidates = body.get("memories", [])
            if not isinstance(raw_candidates, list):
                raise ValueError("memory extraction returned invalid candidates")
            candidates = []
            for item in raw_candidates[:8]:
                candidate = _candidate_from_payload(item)
                if candidate is not None:
                    candidates.append(
                        _ground_candidate_authority(
                            candidate,
                            user_message=user_message,
                            source_type=source_type,
                            verified=verified,
                        )
                    )
            usage = response.usage
            return ExtractionResult(
                candidates=candidates,
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
            )
        except Exception:
            return deterministic


class RuleBasedMemoryExtractor:
    """Only emits explicit user memories and verified Agent outcomes."""

    def extract(
        self,
        *,
        user_message: str,
        assistant_message: str,
        source_type: str,
        verified: bool,
    ) -> ExtractionResult:
        normalized = " ".join(user_message.split())
        candidates: list[MemoryCandidate] = []
        explicit = re.search(
            r"(?:请)?(?:记住|remember(?:\s+that)?)[:：,\s]*(.+)",
            normalized,
            flags=re.IGNORECASE,
        )
        if explicit:
            content = explicit.group(1).strip("。.! ")
            if content:
                candidates.append(
                    MemoryCandidate(
                        kind=_infer_kind(content),
                        title=_title(content),
                        content=content,
                        canonical_key="",
                        confidence=1.0,
                        importance=4,
                        authority="explicit_user",
                    )
                )
        elif source_type == "agent_run" and verified and assistant_message.strip():
            summary = " ".join(assistant_message.split())[:1200]
            candidates.append(
                MemoryCandidate(
                    kind="task_outcome",
                    title=_title(normalized or "Agent task outcome"),
                    content=summary,
                    canonical_key="",
                    confidence=0.86,
                    importance=3,
                    authority="verified_agent",
                )
            )
        return ExtractionResult(candidates=candidates)


def extraction_prompt(
    *,
    user_message: str,
    assistant_message: str,
    source_type: str,
    verified: bool,
) -> str:
    payload = {
        "source_type": source_type,
        "verified_agent_result": verified,
        "user_message": user_message[:8000],
        "assistant_message": assistant_message[:12000],
    }
    return (
        "Extract durable workspace project memories. Return only JSON "
        '{"memories":[{"kind":string,"title":string,"content":string,'
        '"canonical_key":string,"confidence":number,"importance":integer,'
        '"authority":string}]}. Allowed kinds: '
        + ", ".join(sorted(MEMORY_KINDS))
        + ". Authority must be explicit_user, user_statement, verified_agent, "
        "or assistant_inference. Do not retain questions, temporary plans, raw "
        "source dumps, credentials, API keys, environment-variable values, or "
        "claims unsupported by the input. Assistant-only inferences must use "
        "authority=assistant_inference and confidence <= 0.7. Use an empty list "
        "when nothing is durable.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _candidate_from_payload(value: Any) -> MemoryCandidate | None:
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "")
    title = " ".join(str(value.get("title") or "").split())[:160]
    content = " ".join(str(value.get("content") or "").split())[:2000]
    authority = str(value.get("authority") or "assistant_inference")
    if kind not in MEMORY_KINDS or not title or not content:
        return None
    if authority not in {
        "explicit_user",
        "user_statement",
        "verified_agent",
        "assistant_inference",
    }:
        authority = "assistant_inference"
    try:
        confidence = max(0.0, min(1.0, float(value.get("confidence", 0.0))))
        importance = max(1, min(5, int(value.get("importance", 3))))
    except (TypeError, ValueError):
        return None
    if authority == "assistant_inference":
        confidence = min(confidence, 0.7)
    return MemoryCandidate(
        kind=kind,
        title=title,
        content=content,
        canonical_key=str(value.get("canonical_key") or "")[:200],
        confidence=confidence,
        importance=importance,
        authority=authority,
    )


def _ground_candidate_authority(
    candidate: MemoryCandidate,
    *,
    user_message: str,
    source_type: str,
    verified: bool,
) -> MemoryCandidate:
    authority = candidate.authority
    if authority == "explicit_user":
        authority = "assistant_inference"
    elif authority == "user_statement" and not _supported_by_user_text(
        candidate, user_message
    ):
        authority = "assistant_inference"
    elif authority == "verified_agent" and not (
        source_type == "agent_run" and verified
    ):
        authority = "assistant_inference"
    if authority == candidate.authority:
        return candidate
    return replace(
        candidate,
        authority=authority,
        confidence=min(candidate.confidence, 0.7),
    )


def _supported_by_user_text(
    candidate: MemoryCandidate, user_message: str
) -> bool:
    user_tokens = set(
        re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]", user_message.casefold())
    )
    candidate_tokens = set(
        re.findall(
            r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]",
            f"{candidate.title} {candidate.content}".casefold(),
        )
    )
    if not user_tokens or not candidate_tokens:
        return False
    overlap = len(user_tokens & candidate_tokens)
    return overlap / min(len(candidate_tokens), 12) >= 0.30


def _infer_kind(content: str) -> str:
    lowered = content.casefold()
    if any(word in lowered for word in ("必须", "禁止", "不能", "constraint")):
        return "constraint"
    if any(word in lowered for word in ("决定", "选择", "decision")):
        return "decision"
    if any(word in lowered for word in ("规范", "约定", "convention")):
        return "convention"
    return "architecture_fact"


def _title(content: str) -> str:
    compact = " ".join(content.split())
    return compact[:80] or "Project memory"


__all__ = [
    "ExtractionResult",
    "LLMMemoryExtractor",
    "MemoryCandidate",
    "MemoryExtractor",
    "RuleBasedMemoryExtractor",
]
