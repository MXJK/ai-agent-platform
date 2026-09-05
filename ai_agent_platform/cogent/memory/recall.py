from __future__ import annotations
import time

def memory_age_days(mtime_ms: int) -> int:
    d = (int(time.time() * 1000) - mtime_ms) // 86400000
    return max(d, 0)

def memory_age(mtime_ms: int) -> str:
    d = memory_age_days(mtime_ms)
    if d == 0:
        return 'today'
    if d == 1:
        return 'yesterday'
    return f'{d} days ago'

def memory_freshness_text(mtime_ms: int) -> str:
    d = memory_age_days(mtime_ms)
    if d <= 1:
        return ''
    return f'This memory is {d} days old. Memories are point-in-time observations, not live state — claims about code behavior or file:line citations may be outdated. Verify against current code before asserting as fact.'
