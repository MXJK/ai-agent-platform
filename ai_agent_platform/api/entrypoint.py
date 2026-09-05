"""Process entrypoint for the FastAPI/uvicorn adapter."""

from __future__ import annotations

import argparse
import os
from typing import Sequence

import uvicorn

from ai_agent_platform.core import ConfigResolver, validate_bind_host
from ai_agent_platform.main import LazyASGIApp, app, create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cogent-api",
        description="Run the Cogent HTTP adapter with uvicorn.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("APP_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("APP_PORT", "8000")),
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=os.getenv("APP_RELOAD", "0") == "1",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    resolved = ConfigResolver.from_default_locations().resolve_process()
    validate_bind_host(host=args.host, auth_mode=resolved.settings.auth_mode)
    if args.reload:
        uvicorn.run(
            "ai_agent_platform.api.entrypoint:app",
            host=args.host,
            port=args.port,
            reload=True,
        )
    else:
        application = create_app(resolved)
        try:
            uvicorn.run(
                application,
                host=args.host,
                port=args.port,
            )
        finally:
            application.state.runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["LazyASGIApp", "app", "build_parser", "create_app", "main"]
