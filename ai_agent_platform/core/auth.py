"""Identity boundary shared by HTTP routers."""

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
    if settings.auth_mode == "single_user":
        return settings.single_user_id.strip()
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


def is_loopback_request(request: Request) -> bool:
    """Return whether the HTTP peer itself is a loopback address."""
    if request.client is None:
        return False
    normalized = request.client.host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def require_local_capability(
    request: Request,
    settings: Settings,
    *,
    detail: str,
) -> str:
    """Authorize a capability owned by the trusted self-hosted operator.

    Single-user self-hosting has one fixed owner and is published to host loopback by
    the Compose contract. Unauthenticated development must arrive directly from
    loopback. Trusted-header identity does not grant host administration.
    """
    if settings.auth_mode == "single_user":
        return request_user_id(request, settings)
    if settings.auth_mode == "disabled":
        if is_loopback_request(request):
            return request_user_id(request, settings)
        raise HTTPException(status_code=403, detail=detail)

    request_user_id(request, settings)
    raise HTTPException(status_code=403, detail=detail)


def validate_bind_host(*, host: str, auth_mode: str) -> None:
    """Fail closed when unauthenticated development would listen off-machine.

    ``single_user`` may bind inside a container because its Compose port is fixed to
    host loopback. It is not a public-network authentication mode.
    """
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


__all__ = [
    "is_loopback_request",
    "request_user_id",
    "require_local_capability",
    "validate_bind_host",
]
