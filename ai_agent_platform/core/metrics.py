"""Small in-process metrics registry for local deployments and tests."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass
class _Timing:
    count: int = 0
    total_ms: int = 0
    max_ms: int = 0


class MetricsRegistry:
    """Thread-safe counters and duration summaries without external dependencies."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, int] = {}
        self._timings: dict[str, _Timing] = {}

    def increment(self, name: str, amount: int = 1) -> None:
        if amount < 0:
            raise ValueError("metric counter increments must be non-negative")
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def observe_ms(self, name: str, duration_ms: int) -> None:
        value = max(0, int(duration_ms))
        with self._lock:
            timing = self._timings.setdefault(name, _Timing())
            timing.count += 1
            timing.total_ms += value
            timing.max_ms = max(timing.max_ms, value)

    def set_gauge(self, name: str, value: int) -> None:
        with self._lock:
            self._gauges[name] = max(0, int(value))

    def snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            counters = dict(sorted(self._counters.items()))
            gauges = dict(sorted(self._gauges.items()))
            timings = {
                name: {
                    "count": timing.count,
                    "total_ms": timing.total_ms,
                    "max_ms": timing.max_ms,
                    "average_ms": (
                        round(timing.total_ms / timing.count, 2)
                        if timing.count
                        else 0.0
                    ),
                }
                for name, timing in sorted(self._timings.items())
            }
        return {"counters": counters, "gauges": gauges, "timings": timings}
