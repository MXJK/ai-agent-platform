"""Process-owning CLI and REPL adapters over ``RuntimeContainer``."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager, suppress
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shlex
import signal
import sys
from time import perf_counter
from typing import AsyncIterator, Sequence, TextIO
import warnings

from ai_agent_platform.core import ConfigError, ConfigResolver, ResolvedConfig
from ai_agent_platform.domain import (
    AgentEvent,
    QueryCommand,
    QueryLifecycle,
    QueryParams,
    QueryResult,
    QueryStateError,
)
from ai_agent_platform.runtime import RuntimeContainer, build_runtime
from ai_agent_platform.sdk import AgentSDK
from ai_agent_platform.skills import CommandRegistry, SkillInvocationError
from ai_agent_platform.agents.coding.models import AgentRunInvalidStateError


_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class CliEnvironmentError(ValueError):
    """The requested CLI working environment violates a process hard bound."""


@dataclass(frozen=True)
class CliStartupCheckpoint:
    name: str
    elapsed_ms: int


class CliStartupTimeline:
    """Entrypoint timing that starts before dependency assembly is imported."""

    def __init__(self) -> None:
        self._started_at = perf_counter()
        self._items: list[CliStartupCheckpoint] = []
        self.checkpoint("process_started")

    @property
    def items(self) -> tuple[CliStartupCheckpoint, ...]:
        return tuple(self._items)

    def checkpoint(self, name: str) -> None:
        self._items.append(
            CliStartupCheckpoint(
                name=name,
                elapsed_ms=int((perf_counter() - self._started_at) * 1000),
            )
        )


class CliInterruptController:
    """Bridge a process SIGINT into cancellation of the active Query only."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._event = asyncio.Event()
        self.active_run_id: str | None = None
        self.interrupted = False

    def begin_run(self, run_id: str) -> None:
        self.active_run_id = run_id
        self.interrupted = False
        self._event.clear()

    def finish_run(self) -> None:
        self.active_run_id = None
        self._event.clear()

    def request_interrupt(self) -> None:
        if self.active_run_id is None:
            return
        self.interrupted = True
        self._loop.call_soon_threadsafe(self._event.set)

    async def wait(self) -> None:
        await self._event.wait()


class CliApplication:
    """Testable print/REPL adapter; process concerns remain in ``main``."""

    def __init__(
        self,
        runtime: RuntimeContainer,
        *,
        workspace_root: str | Path,
        workspace_id: str,
        user_id: str = "cli-user",
        session_id: str | None = None,
        input_stream: TextIO = sys.stdin,
        output_stream: TextIO = sys.stdout,
        error_stream: TextIO = sys.stderr,
        interrupt: CliInterruptController | None = None,
    ) -> None:
        self.runtime = runtime
        self.sdk = AgentSDK(runtime)
        self.workspace_root = str(Path(workspace_root).resolve(strict=True))
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.session_id = session_id
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.error_stream = error_stream
        self.interrupt = interrupt
        self.last_run_id: str | None = None
        self._prepared = False

    async def run_print(self, message: str) -> int:
        self._prepare_context()
        result, interrupted = await self._stream(
            self.sdk.query(self._query_params(message, mode="print"))
        )
        return _exit_code(result, interrupted=interrupted)

    async def run_repl(self) -> int:
        self._prepare_context()
        self.error_stream.write(
            "Cogent REPL. Use /skills, /tools, /mcp, /permissions, "
            "/compact, /resume, or /exit.\n"
        )
        self.error_stream.flush()
        while True:
            raw = self._readline("cogent> ")
            if raw is None:
                return 0
            message = raw.strip()
            if not message:
                continue
            if message.startswith("/"):
                should_exit = await self._handle_slash_command(message)
                if should_exit:
                    return 0
                continue
            await self._stream(
                self.sdk.query(self._query_params(message, mode="repl"))
            )

    def _prepare_context(self) -> None:
        if self._prepared:
            return
        workspace_service = self.runtime.workspace_service
        session_service = self.runtime.session_service
        if workspace_service is None or session_service is None:
            raise RuntimeError(
                "RuntimeContainer does not provide workspace/session services"
            )
        existing = workspace_service.get_including_removed(self.workspace_id)
        if existing is None or existing.removed_at is not None:
            workspace_service.register(
                workspace_id=self.workspace_id,
                root_path=self.workspace_root,
            )
        elif Path(existing.root_path).resolve() != Path(self.workspace_root):
            raise CliEnvironmentError(
                f"workspace ID {self.workspace_id!r} is already bound to "
                f"{existing.root_path}"
            )
        elif not workspace_service.is_available(self.workspace_id):
            raise CliEnvironmentError(
                f"workspace {self.workspace_id!r} is not available"
            )

        memory_service = self.runtime.project_memory_service
        if memory_service is not None:
            memory_service.ensure_workspace_admin(
                workspace_id=self.workspace_id,
                actor_user_id=self.user_id,
            )

        if self.session_id is None:
            self.session_id = session_service.create_session(self.user_id).id
        else:
            session = session_service.get_session(self.session_id)
            if session.user_id != self.user_id:
                raise PermissionError("CLI session belongs to another user")
        self._prepared = True

    def _query_params(
        self,
        message: str,
        *,
        mode: str,
        skill_name: str | None = None,
        skill_arguments: Sequence[str] = (),
    ) -> QueryParams:
        assert self.session_id is not None
        return QueryParams(
            conversation_id=self.session_id,
            message=message,
            workspace_id=self.workspace_id,
            cwd=self.workspace_root,
            actor_user_id=(
                self.user_id
                if self.runtime.settings.auth_mode != "disabled"
                else None
            ),
            skill_name=skill_name,
            skill_arguments=tuple(skill_arguments),
            entrypoint="cli",
            entrypoint_metadata={"adapter": mode, "transport": "stdio"},
        )

    async def _stream(
        self,
        events: AsyncIterator[AgentEvent],
    ) -> tuple[QueryResult, bool]:
        iterator = events.__aiter__()
        first = await anext(iterator)
        self.last_run_id = first.run_id
        self._write_event(first)
        interrupt = self.interrupt
        if interrupt is not None:
            interrupt.begin_run(first.run_id)
        cancellation_sent = False
        last_status = first.status
        try:
            while True:
                next_event = asyncio.create_task(anext(iterator))
                interrupt_wait = (
                    asyncio.create_task(interrupt.wait())
                    if interrupt is not None and not cancellation_sent
                    else None
                )
                waiters = {next_event}
                if interrupt_wait is not None:
                    waiters.add(interrupt_wait)
                done, _pending = await asyncio.wait(
                    waiters,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if interrupt_wait is not None and interrupt_wait in done:
                    cancellation_sent = True
                    if last_status not in QueryLifecycle.STREAM_STOP_STATUSES:
                        self.error_stream.write(
                            f"warning: cancelling active Run {first.run_id}\n"
                        )
                        self.error_stream.flush()
                        try:
                            self.sdk.control(first.run_id, QueryCommand.CANCEL)
                        except (QueryStateError, RuntimeError, ValueError):
                            # Completion may win the race with SIGINT.
                            pass
                if interrupt_wait is not None and not interrupt_wait.done():
                    interrupt_wait.cancel()
                    with suppress(asyncio.CancelledError):
                        await interrupt_wait
                try:
                    event = await next_event
                except StopAsyncIteration:
                    break
                last_status = event.status
                self._write_event(event)
        finally:
            if interrupt is not None:
                interrupt.finish_run()
            aclose = getattr(iterator, "aclose", None)
            if callable(aclose):
                await aclose()
        return self.sdk.result(first.run_id), cancellation_sent

    async def _handle_slash_command(self, raw: str) -> bool:
        if raw.strip().casefold() == "/exit":
            return True
        try:
            await self._stream(self.sdk.query(self._query_params(raw, mode="repl")))
        except (ValueError, SkillInvocationError, AgentRunInvalidStateError, QueryStateError) as exc:
            self._write_diagnostic("error", {"message": str(exc), "code": getattr(exc, 'code', 'invalid_command')})
        return False

    def _effective_snapshot(self):
        factory = self.runtime.execution_context_factory
        if factory is None:
            raise RuntimeError("effective context factory is unavailable")
        if self.last_run_id:
            runtime = self.runtime.coding_agent_runtime
            if runtime is not None:
                record = runtime.get_run(self.last_run_id)
                if record.context_snapshot is not None:
                    return record.context_snapshot
        assert self.session_id is not None
        return factory.preview(
            conversation_id=self.session_id,
            workspace_id=self.workspace_id,
            actor_user_id=(
                self.user_id
                if self.runtime.settings.auth_mode != "disabled"
                else None
            ),
        )

    def _effective_tools(self, snapshot):
        runtime = self.runtime.coding_agent_runtime
        if self.last_run_id == snapshot.metadata.run_id and runtime is not None:
            return runtime.effective_tool_pool(self.last_run_id)
        factory = self.runtime.execution_context_factory
        if factory is None:
            raise RuntimeError("effective context factory is unavailable")
        return factory.restore_tool_access(snapshot)

    async def _resume(self, arguments: list[str]) -> None:
        run_id = self.last_run_id
        if arguments and arguments[0].casefold() not in {"approve", "deny"}:
            run_id = arguments.pop(0)
        if not run_id:
            self._write_diagnostic(
                "error",
                {"message": "/resume requires a Run ID before any Run exists"},
            )
            return
        decision = "approve"
        if arguments and arguments[0].casefold() in {"approve", "deny"}:
            decision = arguments.pop(0).casefold()
        message = " ".join(arguments)
        await self._stream(
            self.sdk.resume(
                run_id,
                approved=decision == "approve",
                message=message,
                actor_user_id=(
                    self.user_id
                    if self.runtime.settings.auth_mode != "disabled"
                    else None
                ),
            )
        )

    def _write_event(self, event: AgentEvent) -> None:
        service = self.runtime.query_service
        assert service is not None
        self.output_stream.write(service.event_encoder.encode_json(event) + "\n")
        self.output_stream.flush()

    def _write_diagnostic(self, kind: str, payload: dict[str, object]) -> None:
        self.output_stream.write(
            json.dumps(
                {"kind": kind, **payload},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        self.output_stream.flush()

    def _readline(self, prompt: str) -> str | None:
        self.output_stream.write(prompt)
        self.output_stream.flush()
        value = self.input_stream.readline()
        return value if value != "" else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cogent",
        description="Cogent Textual interface and non-interactive Query output.",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Registered workspace root (default: current directory).",
    )
    parser.add_argument(
        "--workspace-id",
        help="Workspace ID; defaults to a stable ID derived from the root.",
    )
    parser.add_argument("--session-id", help="Reuse an existing conversation.")
    parser.add_argument("--user", default="cli-user", help="Session owner ID.")
    parser.add_argument(
        "--startup-timing",
        action="store_true",
        help="Write process and runtime startup checkpoints to stderr.",
    )
    parser.add_argument("--print", dest="print_message", nargs=argparse.REMAINDER, help="Run one Query non-interactively.")
    modes = parser.add_subparsers(dest="mode", required=False)
    print_parser = modes.add_parser("print", help="Run one Query and print JSON events.")
    print_parser.add_argument("message", nargs="+", help="Query message.")
    modes.add_parser("repl", help="Start a multi-turn interactive session.")
    parser.set_defaults(mode="tui", message=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Own parsing, safe startup, warnings, exits, SIGINT, and cleanup."""

    timeline = CliStartupTimeline()
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code or 0)
    timeline.checkpoint("arguments_parsed")
    if args.print_message is not None:
        args.mode, args.message = "print", args.print_message
        if not args.message:
            parser.print_usage(sys.stderr)
            return 2

    runtime: RuntimeContainer | None = None
    caught_warnings: list[warnings.WarningMessage] = []
    try:
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("default")
            config = ConfigResolver.from_default_locations().resolve_process()
            workspace_root = validate_cli_environment(
                config,
                workspace=args.workspace,
                workspace_id=args.workspace_id,
            )
            timeline.checkpoint("environment_validated")
            _warn_for_sensitive_cli_modes(config)
            runtime = build_runtime(config, role="cli")
            runtime.checkpoint("cli_ready")
            timeline.checkpoint("runtime_ready")
            caught_warnings.extend(observed)
        _write_warnings(caught_warnings, sys.stderr)
        if args.startup_timing:
            _write_startup_timing(timeline, runtime, sys.stderr)
        return asyncio.run(
            _run_mode(
                args,
                runtime,
                workspace_root=workspace_root,
                install_sigint=True,
            )
        )
    except KeyboardInterrupt:
        sys.stderr.write("warning: interrupted during CLI startup or shutdown\n")
        return 130
    except (CliEnvironmentError, ConfigError, PermissionError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except Exception as exc:  # pragma: no cover - defensive process boundary
        sys.stderr.write(f"error: {exc}\n")
        return 1
    finally:
        if runtime is not None:
            runtime.close()


async def _run_mode(
    args: argparse.Namespace,
    runtime: RuntimeContainer,
    *,
    workspace_root: Path,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
    error_stream: TextIO = sys.stderr,
    install_sigint: bool = False,
) -> int:
    loop = asyncio.get_running_loop()
    interrupt = CliInterruptController(loop)
    application = CliApplication(
        runtime,
        workspace_root=workspace_root,
        workspace_id=(
            args.workspace_id or _default_workspace_id(workspace_root)
        ),
        user_id=args.user,
        session_id=args.session_id,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
        interrupt=interrupt,
    )
    signal_context = (
        _sigint_handler(interrupt)
        if install_sigint
        else _null_context()
    )
    with signal_context:
        if args.mode == "print":
            return await application.run_print(" ".join(args.message))
        if args.mode == "tui":
            from ai_agent_platform.cogent.tui import CogentApp
            await CogentApp(application).run_async()
            return 0
        return await application.run_repl()


def validate_cli_environment(
    config: ResolvedConfig,
    *,
    workspace: str,
    workspace_id: str | None,
) -> Path:
    """Resolve symlinks and enforce the process-owned workspace allowlist."""

    try:
        root = Path(workspace).expanduser().resolve(strict=True)
    except OSError as exc:
        raise CliEnvironmentError(
            "CLI workspace must be an existing readable directory"
        ) from exc
    if not root.is_dir():
        raise CliEnvironmentError("CLI workspace must be an existing directory")
    allowed = tuple(
        Path(item).expanduser().resolve()
        for item in config.settings.workspace_allowed_roots
    )
    if not any(root == item or item in root.parents for item in allowed):
        raise CliEnvironmentError(
            "CLI workspace is outside WORKSPACE_ALLOWED_ROOTS"
        )
    if workspace_id is not None and not _WORKSPACE_ID.fullmatch(workspace_id):
        raise CliEnvironmentError(
            "workspace ID must match ^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        )
    return root


def _warn_for_sensitive_cli_modes(config: ResolvedConfig) -> None:
    if config.settings.live_workspace_writes_enabled:
        warnings.warn(
            "live workspace writes are enabled; tool approvals still apply",
            RuntimeWarning,
            stacklevel=2,
        )
    if config.settings.task_queue_backend == "celery":
        warnings.warn(
            "CLI is using Celery; a worker with the same persistent stores must be running",
            RuntimeWarning,
            stacklevel=2,
        )


def _write_warnings(items: list[warnings.WarningMessage], stream: TextIO) -> None:
    for item in items:
        stream.write(f"warning: {item.message}\n")
    if items:
        stream.flush()


def _write_startup_timing(
    timeline: CliStartupTimeline,
    runtime: RuntimeContainer,
    stream: TextIO,
) -> None:
    payload = {
        "cli": [item.__dict__ for item in timeline.items],
        "runtime": [item.__dict__ for item in runtime.startup_timeline],
    }
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


def _default_workspace_id(root: Path) -> str:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return f"cli-{digest}"


def _exit_code(result: QueryResult, *, interrupted: bool) -> int:
    if interrupted or result.status == "cancelled":
        return 130
    if result.status == "completed":
        return 0
    if result.status in QueryLifecycle.SUSPENDED_STATUSES:
        return 3
    return 1


@contextmanager
def _sigint_handler(interrupt: CliInterruptController):
    previous = signal.getsignal(signal.SIGINT)

    def handle_sigint(_signum, _frame) -> None:
        interrupt.request_interrupt()

    signal.signal(signal.SIGINT, handle_sigint)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


@contextmanager
def _null_context():
    yield


__all__ = [
    "CliApplication",
    "CliEnvironmentError",
    "CliInterruptController",
    "CliStartupCheckpoint",
    "CliStartupTimeline",
    "build_parser",
    "main",
    "validate_cli_environment",
]


if __name__ == "__main__":
    raise SystemExit(main())
