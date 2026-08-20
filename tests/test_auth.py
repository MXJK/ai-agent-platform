from starlette.requests import Request

from ai_agent_platform.core import (
    Settings,
    request_user_id,
    require_local_capability,
    validate_bind_host,
)


def _request(*, host: str = "172.20.0.1", headers: dict[str, str] | None = None):
    encoded_headers = [
        (name.lower().encode("ascii"), value.encode("utf-8"))
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": encoded_headers,
            "client": (host, 50000),
        }
    )


def test_single_user_identity_ignores_all_caller_claims() -> None:
    settings = Settings(auth_mode="single_user", single_user_id="owner")
    request = _request(
        headers={
            "X-User-ID": "attacker",
            "X-Authenticated-User": "attacker",
            "X-Gateway-Auth": "forged",
        }
    )

    assert request_user_id(
        request,
        settings,
        claimed_user_id="body-attacker",
    ) == "owner"


def test_single_user_owner_has_host_administration_capability() -> None:
    settings = Settings(auth_mode="single_user", single_user_id="owner")

    assert require_local_capability(
        _request(),
        settings,
        detail="not available",
    ) == "owner"


def test_single_user_container_may_bind_all_interfaces() -> None:
    validate_bind_host(host="0.0.0.0", auth_mode="single_user")
