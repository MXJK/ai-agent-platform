"""Identity boundary shared by HTTP routers.

In production the Go gateway validates OIDC tokens and signs the trusted
identity headers with a shared secret. Direct client identity headers are not
accepted in that mode.
"""

from __future__ import annotations

import hmac
import ipaddress

from fastapi import HTTPException, Request

from ai_agent_platform.core.config import Settings


def request_user_id(
    request: Request,
    settings: Settings,
    *,
    claimed_user_id: str | None = None,
) -> str:
    if settings.auth_mode == "disabled":
        return (
            claimed_user_id
            or request.headers.get("X-User-ID")
            or "demo_user"
        ).strip()

    supplied_secret = request.headers.get("X-Gateway-Auth", "")
    expected_secret = settings.gateway_trust_secret or ""
    if not supplied_secret or not hmac.compare_digest(
        supplied_secret, expected_secret
    ):
        raise HTTPException(status_code=401, detail="trusted gateway identity required")
    user_id = request.headers.get("X-Authenticated-User", "").strip()
    if not user_id or len(user_id) > 256:
        raise HTTPException(status_code=401, detail="authenticated user is missing")
    return user_id


def validate_bind_host(*, host: str, auth_mode: str) -> None:
    """Fail closed when unauthenticated local mode would listen off-machine."""
    if auth_mode != "disabled":
        return
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError as exc:
        raise ValueError(
            "AUTH_MODE=disabled requires APP_HOST to be a loopback address"
        ) from exc
    if not address.is_loopback:
        raise ValueError(
            "AUTH_MODE=disabled requires APP_HOST to be a loopback address"
        )


__all__ = ["request_user_id", "validate_bind_host"]
