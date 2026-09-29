from __future__ import annotations

import re


MAX_SLUG_LENGTH = 64
_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_slug(name: str) -> str | None:
    if not name:
        return "name cannot be empty"
    if len(name) > MAX_SLUG_LENGTH:
        return f"name too long (max {MAX_SLUG_LENGTH} characters)"
    for segment in name.split("/"):
        if not segment:
            return "name contains an empty segment"
        if segment in {".", ".."}:
            return "name must not contain '.' or '..' as a segment"
        if _SEGMENT.fullmatch(segment) is None:
            return (
                f"invalid segment: {segment!r} "
                "(allowed: letters, digits, '.', '-', '_')"
            )
    return None


def flatten_slug(name: str) -> str:
    return name.replace("/", "+")
