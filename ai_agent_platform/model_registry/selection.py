"""Request-scoped model preference propagated through Chat, RAG, and Agent work."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from .models import SelectionMode, SelectionPolicy


@dataclass(frozen=True)
class ModelSelection:
    mode: SelectionMode = "auto"
    routing_policy: SelectionPolicy = "smart"
    preferred_model_id: str | None = None
    preferred_provider: str | None = None
    preferred_model: str | None = None
    thinking_level: str | None = None
    fallback_enabled: bool = True


_CURRENT_MODEL_SELECTION: ContextVar[ModelSelection | None] = ContextVar(
    "current_model_selection",
    default=None,
)


def current_model_selection() -> ModelSelection | None:
    return _CURRENT_MODEL_SELECTION.get()


@contextmanager
def model_selection_scope(selection: ModelSelection | None) -> Iterator[None]:
    if selection is None:
        yield
        return
    token = _CURRENT_MODEL_SELECTION.set(selection)
    try:
        yield
    finally:
        _CURRENT_MODEL_SELECTION.reset(token)
