from __future__ import annotations

import secrets


TEXT = "text"
SHUTDOWN_REQUEST = "shutdown_request"
SHUTDOWN_RESPONSE = "shutdown_response"
PLAN_APPROVAL_REQUEST = "plan_approval_request"
PLAN_APPROVAL_RESPONSE = "plan_approval_response"
VALID_MESSAGE_TYPES = frozenset(
    {
        TEXT,
        SHUTDOWN_REQUEST,
        SHUTDOWN_RESPONSE,
        PLAN_APPROVAL_REQUEST,
        PLAN_APPROVAL_RESPONSE,
    }
)


def new_request_id() -> str:
    return f"req-{secrets.token_hex(8)}"
