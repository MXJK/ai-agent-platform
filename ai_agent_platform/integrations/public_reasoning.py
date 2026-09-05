from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable


_sink: ContextVar[Callable[[str, str], None] | None] = ContextVar('public_reasoning_sink', default=None)


@contextmanager
def displayable_reasoning_scope(callback):
    token = _sink.set(callback)
    try:
        yield
    finally:
        _sink.reset(token)


def publish_summary(provider: str, text: str):
    callback = _sink.get()
    if callback is not None and isinstance(text, str) and text:
        callback(provider, text)


def summary_text(provider, items):
    """Return only documented user-displayable fields; never opaque signatures."""
    chunks = []
    for item in items:
        if provider == 'openai' and item.get('type') == 'reasoning':
            chunks.extend(str(block.get('text') or '') for block in item.get('summary', [])
                          if block.get('type') == 'summary_text')
        elif provider == 'anthropic' and item.get('type') == 'thinking':
            chunks.append(str(item.get('thinking') or ''))
        elif provider == 'google':
            chunks.extend(str(part.get('text') or '') for part in item.get('parts', []) if part.get('thought') is True)
        elif provider in {'deepseek', 'glm', 'doubao', 'minimax'}:
            chunks.append(str(item.get('reasoning_content') or ''))
            if not item.get('reasoning_content'):
                chunks.extend(str(block.get('text') or '') for block in item.get('reasoning_details', [])
                              if block.get('type') == 'reasoning.text')
    return '\n'.join(filter(None, chunks))
