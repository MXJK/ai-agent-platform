from queue import Empty, Queue
from threading import Thread
from typing import Iterable, Iterator

from ai_agent_platform.integrations.llm import LLMStreamEvent


def sse_heartbeat() -> str:
    return ': heartbeat\n\n'


def stream_with_heartbeat(events: Iterable[LLMStreamEvent], *, heartbeat_seconds: float) -> Iterator[LLMStreamEvent | None]:
    queue: Queue[tuple[str, object]] = Queue()

    def produce():
        try:
            for event in events:
                queue.put(('event', event))
        except BaseException as exc:
            queue.put(('error', exc))
        finally:
            queue.put(('done', None))

    Thread(target=produce, name='llm-stream', daemon=True).start()
    while True:
        try:
            kind, value = queue.get(timeout=heartbeat_seconds)
        except Empty:
            yield None
            continue
        if kind == 'event':
            yield value
        elif kind == 'error':
            raise value
        else:
            break
