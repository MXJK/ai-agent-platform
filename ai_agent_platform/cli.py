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
from ai_agent_platform.services import AgentEventEncoder


_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_PERMISSION_MODES = (
    "default",
    "acceptEdits",
    "plan",
    "bypassPermissions",
)
_PERMISSION_MODE_LABELS = {
    "default": "default · reads run automatically; writes and commands ask",
    "acceptEdits": "acceptEdits · reads and file edits run automatically; commands ask",
    "plan": "plan · plan-first read-only discipline; unexpected writes and commands ask",
    "bypassPermissions": "bypassPermissions · permitted operations skip ordinary confirmation",
}


def _validate_permission_mode(value: str) -> str:
    if value not in _PERMISSION_MODES:
        raise ValueError(
            "permission mode must be one of: " + ", ".join(_PERMISSION_MODES)
        )
    return value


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
        permission_mode: str = "default",
    ) -> None:
        self.runtime = runtime
        self.sdk = AgentSDK(runtime)
        self.local_cwd = Path(workspace_root).resolve(strict=True)
        self.workspace_root = str(self.local_cwd)
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.session_id = session_id
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.error_stream = error_stream
        self.interrupt = interrupt
        self.permission_mode = _validate_permission_mode(permission_mode)
        self.last_run_id: str | None = None
        self._prepared = False

    async def prepare_context(self) -> None:
        self._prepare_context()

    async def capabilities(self) -> dict:
        self._prepare_context()
        return await asyncio.to_thread(
            self.sdk.query_service.composer_capabilities,
            conversation_id=self.session_id,
            workspace_id=self.workspace_id,
            actor_user_id=(
                self.user_id
                if self.runtime.settings.auth_mode != "disabled"
                else None
            ),
        )

    def query(self, text: str, *, mode: str = "tui") -> AsyncIterator[AgentEvent]:
        target = self._permission_command_target(text)
        events = self.sdk.query(self._query_params(text, mode=mode))
        return self._commit_permission_mode_after(events, target)

    def resume(self, run_id: str, *, approved: bool = True, message: str = ""):
        return self.sdk.resume(
            run_id,
            approved=approved,
            message=message,
            actor_user_id=(
                self.user_id
                if self.runtime.settings.auth_mode != "disabled"
                else None
            ),
        )

    async def result(self, run_id: str) -> QueryResult:
        return self.sdk.result(
            run_id,
            actor_user_id=(
                self.user_id
                if self.runtime.settings.auth_mode != "disabled"
                else None
            ),
        )

    async def control(
        self,
        run_id: str,
        command: QueryCommand | str,
        *,
        message: str = "",
    ) -> QueryResult:
        return self.sdk.control(
            run_id,
            command,
            message=message,
            actor_user_id=(
                self.user_id
                if self.runtime.settings.auth_mode != "disabled"
                else None
            ),
        )

    @property
    def model_summary(self) -> str:
        return "embedded runtime"

    @property
    def credential_summary(self) -> str:
        return "runtime credentials"

    @property
    def api_url(self) -> str:
        return "embedded"

    def registry_text(self) -> str:
        return "Model registry is provided by the embedded test runtime."

    @property
    def permission_mode_summary(self) -> str:
        return _PERMISSION_MODE_LABELS[self.permission_mode]

    def set_permission_mode(self, value: str) -> str:
        self.permission_mode = _validate_permission_mode(value)
        return f"Cogent permission mode: {self.permission_mode_summary}. Hard denies remain active."

    def cycle_permission_mode(self) -> str:
        index = _PERMISSION_MODES.index(self.permission_mode)
        return self.set_permission_mode(
            _PERMISSION_MODES[(index + 1) % len(_PERMISSION_MODES)]
        )

    def _permission_command_target(self, raw: str) -> str | None:
        parts = shlex.split(raw)
        if not parts or parts[0] != "/permissions":
            return None
        if len(parts) == 2:
            return _validate_permission_mode(parts[1])
        if len(parts) > 2:
            raise ValueError(
                "Usage: /permissions [default|acceptEdits|plan|bypassPermissions]"
            )
        return None

    async def _commit_permission_mode_after(
        self,
        events: AsyncIterator[AgentEvent],
        target: str | None,
    ) -> AsyncIterator[AgentEvent]:
        completed = False
        async for event in events:
            completed = completed or (
                event.type == "command_completed" and event.status == "completed"
            )
            yield event
        if completed and target is not None:
            self.set_permission_mode(target)

    async def run_print(self, message: str) -> int:
        self._prepare_context()
        managed = await self.execute_management_command(message)
        if managed is not None:
            self.output_stream.write(managed + "\n")
            self.output_stream.flush()
            return 0
        result, interrupted = await self._stream(
            self.sdk.query(self._query_params(message, mode="print"))
        )
        return _exit_code(result, interrupted=interrupted)

    async def run_repl(self) -> int:
        self._prepare_context()
        self.error_stream.write(
            "Cogent REPL. Use /help, /memory, /session, /resume, or /exit.\n"
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

        access_service = self.runtime.workspace_access_service
        if access_service is not None:
            access_service.ensure_workspace_admin(
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
            permission_mode=self.permission_mode,
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
        managed = await self.execute_management_command(raw)
        if managed is not None:
            self.output_stream.write(managed + "\n")
            self.output_stream.flush()
            return False
        try:
            await self._stream(self.sdk.query(self._query_params(raw, mode="repl")))
        except (ValueError, SkillInvocationError, AgentRunInvalidStateError, QueryStateError) as exc:
            self._write_diagnostic("error", {"message": str(exc), "code": getattr(exc, 'code', 'invalid_command')})
        return False

    async def execute_management_command(self, raw: str) -> str | None:
        parts = shlex.split(raw)
        if not parts or parts[0] not in {"/memory", "/session"}:
            return None
        self._prepare_context()
        if parts[0] == "/session":
            return self._manage_session(parts[1:])
        return self._manage_memory(raw, parts[1:])

    def _manage_session(self, arguments: list[str]) -> str:
        sessions = self.runtime.session_service
        assert sessions is not None
        action = arguments[0] if arguments else "list"
        if action == "list":
            rows, _ = sessions.list_sessions_page(user_id=self.user_id, limit=100)
            return "\n".join(
                f"{'*' if item.id == self.session_id else ' '} {item.id} · {item.title} · {item.message_count} messages"
                for item in rows
            ) or "当前没有会话。"
        if action == "new":
            created = sessions.create_session(self.user_id)
            created = sessions.update_session(
                session_id=created.id, actor_user_id=self.user_id,
                workspace_id=self.workspace_id, composer_mode="agent")
            self.session_id = created.id
            self.last_run_id = None
            return f"已切换到新会话 {created.id}。"
        if action == "resume" and len(arguments) == 2:
            selected = sessions.get_session(arguments[1])
            if selected.user_id != self.user_id:
                raise PermissionError("conversation access denied")
            if selected.archived_at is not None:
                raise ValueError("archived conversation cannot be resumed")
            if selected.workspace_id not in {None, self.workspace_id}:
                raise ValueError("conversation belongs to another Workspace")
            if selected.workspace_id is None or selected.composer_mode != "agent":
                selected = sessions.update_session(
                    session_id=selected.id, actor_user_id=self.user_id,
                    workspace_id=self.workspace_id, composer_mode="agent")
            self.session_id = selected.id
            self.last_run_id = None
            return f"已选择会话 {selected.id}；不会自动恢复或批准暂停的 Run。"
        if action == "delete" and len(arguments) == 2:
            target = arguments[1]
            if target == self.session_id:
                raise ValueError("cannot delete the active conversation")
            selected = sessions.get_session(target)
            if selected.user_id != self.user_id:
                raise PermissionError("conversation access denied")
            runtime = self.runtime.coding_agent_runtime
            latest = runtime.get_latest_run(target) if runtime is not None else None
            if latest is not None and latest.status not in QueryLifecycle.TERMINAL_STATUSES:
                raise ValueError("conversation has an active or suspended Run")
            if not sessions.delete_session(target):
                raise ValueError("conversation was not found")
            return f"已删除会话 {target}。"
        return "用法：/session list | /session new | /session resume <id> | /session delete <id>"

    def _manage_memory(self, raw: str, arguments: list[str]) -> str:
        service = self.runtime.file_memory_service
        if service is None:
            raise RuntimeError("Cogent file memory is unavailable")
        scope = arguments[1] if len(arguments) > 1 else None
        if scope is not None and scope not in {"user", "project"}:
            raise ValueError("memory scope must be user or project")
        role = "admin"
        if self.runtime.settings.auth_mode != "disabled":
            role = self.runtime.workspace_access_service.role_for(
                workspace_id=self.workspace_id, actor_user_id=self.user_id) or "viewer"
        action = arguments[0] if arguments else "list"
        if action == "list":
            rows = service.list_files(workspace_root=self.workspace_root,
                actor_user_id=self.user_id, role=role, scope=scope)
            return "\n".join(f"{item['id']} — {item['description']}" for item in rows) or "当前没有 Cogent 文件记忆。"
        if action == "edit":
            left, marker, body = raw.partition("::")
            prefix = shlex.split(left)
            if not marker or len(prefix) != 6:
                return ('用法：/memory edit <user|project> <name> '
                        '<user|feedback|project|reference> "description" :: body')
            _, _, edit_scope, name, kind, description = prefix
            existing = next((item for item in service.list_files(
                workspace_root=self.workspace_root, actor_user_id=self.user_id,
                role=role, scope=edit_scope) if item['name'] == name), None)
            expected = (hashlib.sha256(existing['text'].encode()).hexdigest()
                        if existing else None)
            saved = service.upsert_file(workspace_root=self.workspace_root,
                actor_user_id=self.user_id, role=role, scope=edit_scope,
                name=name, kind=kind, description=description,
                body=body.strip(), expected_hash=expected,
                must_not_exist=existing is None)
            return f"已保存 {saved['id']}。"
        if action == "clear" and scope:
            rows = service.list_files(workspace_root=self.workspace_root,
                actor_user_id=self.user_id, role=role, scope=scope)
            if len(arguments) < 3 or arguments[2] != "confirm":
                return (f"将清空 {scope} 范围的 {len(rows)} 个主题：\n" +
                        "\n".join(item['id'] for item in rows) +
                        f"\n确认执行：/memory clear {scope} confirm")
            count = service.clear(workspace_root=self.workspace_root,
                actor_user_id=self.user_id, role=role, scope=scope)
            return f"已清空 {scope} 范围，共删除 {count} 个主题。"
        if action == "consolidate":
            runtime = self.runtime.coding_agent_runtime
            record = runtime.get_latest_run(self.session_id) if runtime else None
            if record is None:
                raise ValueError("当前会话还没有可用于治理的 Run")
            service.schedule_consolidate(record, force=True, scope=scope)
            return "已安排文件记忆治理任务。"
        return "用法：/memory list [user|project] | /memory edit ... | /memory clear <scope> [confirm] | /memory consolidate"

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
        "--api-url",
        help=(
            "Cogent server URL (default: COGENT_API_URL or "
            "http://127.0.0.1:8000/api/v1)."
        ),
    )
    parser.add_argument(
        "--workspace-id",
        help=(
            "Workspace already registered in the web UI; auto-selects a "
            "default or unique match when omitted."
        ),
    )
    parser.add_argument("--session-id", help="Reuse an existing conversation.")
    parser.add_argument("--user", default="cli-user", help="Session owner ID.")
    parser.add_argument(
        "--permission-mode",
        choices=_PERMISSION_MODES,
        default="default",
        help="Initial permission mode; change it interactively with /permissions or Shift+Tab.",
    )
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
    """Run the public CLI as a client of the shared Cogent service."""

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

    try:
        application = RemoteCliApplication(
            api_url=args.api_url,
            workspace_id=args.workspace_id,
            session_id=args.session_id,
            user_id=args.user,
            cwd=Path.cwd(),
            permission_mode=args.permission_mode,
        )
        timeline.checkpoint("client_ready")
        if args.startup_timing:
            sys.stderr.write(
                json.dumps(
                    {"cli": [item.__dict__ for item in timeline.items]},
                    ensure_ascii=False,
                )
                + "\n"
            )
        return asyncio.run(_run_remote_mode(args, application, install_sigint=True))
    except KeyboardInterrupt:
        sys.stderr.write("warning: interrupted during CLI startup or shutdown\n")
        return 130
    except (CliEnvironmentError, ConfigError, PermissionError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except Exception as exc:  # pragma: no cover - defensive process boundary
        sys.stderr.write(f"error: {exc}\n")
        return 1


class RemoteCliApplication:
    """Full-screen terminal adapter backed exclusively by Cogent HTTP/SSE."""

    def __init__(
        self,
        *,
        api_url: str | None,
        workspace_id: str | None,
        session_id: str | None,
        user_id: str,
        cwd: Path,
        input_stream: TextIO = sys.stdin,
        output_stream: TextIO = sys.stdout,
        error_stream: TextIO = sys.stderr,
        permission_mode: str = "default",
    ) -> None:
        from ai_agent_platform.cogent.http_client import CogentHTTPClient

        self.client = CogentHTTPClient(api_url, user_id=user_id)
        self.requested_workspace_id = workspace_id
        self.session_id = session_id
        self.user_id = user_id
        self.local_cwd = cwd.resolve()
        self.workspace_id = workspace_id or ""
        self.workspace_root = str(self.local_cwd)
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.error_stream = error_stream
        self.last_run_id: str | None = None
        self.interrupt: CliInterruptController | None = None
        self._prepared = False
        self._event_encoder = AgentEventEncoder()
        self.permission_mode = _validate_permission_mode(permission_mode)

    async def prepare_context(self) -> None:
        if self._prepared:
            return
        context = await self.client.prepare(
            cwd=self.local_cwd,
            workspace_id=self.requested_workspace_id,
            session_id=self.session_id,
        )
        self.session_id = context.session_id
        self.workspace_id = context.workspace_id
        self.workspace_root = context.workspace_root
        self._prepared = True

    async def capabilities(self) -> dict:
        await self.prepare_context()
        return await self.client.composer_capabilities()

    def _query_params(self, message: str, *, mode: str) -> QueryParams:
        if not self._prepared or self.session_id is None:
            raise RuntimeError("Cogent server context is not ready")
        return QueryParams(
            conversation_id=self.session_id,
            message=message,
            workspace_id=self.workspace_id,
            cwd=self.workspace_root,
            actor_user_id=self.user_id,
            permission_mode=self.permission_mode,
            entrypoint="cli",
            entrypoint_metadata={"adapter": mode, "transport": "http+sse"},
        )

    def query(self, text: str, *, mode: str = "tui") -> AsyncIterator[AgentEvent]:
        target = self._permission_command_target(text)
        events = self.client.query(self._query_params(text, mode=mode))
        return self._commit_permission_mode_after(events, target)

    def resume(self, run_id: str, *, approved: bool = True, message: str = ""):
        return self.client.resume(run_id, approved=approved, message=message)

    async def result(self, run_id: str) -> QueryResult:
        return await self.client.result(run_id)

    async def control(
        self,
        run_id: str,
        command: QueryCommand | str,
        *,
        message: str = "",
    ) -> QueryResult:
        return await self.client.control(run_id, command, message=message)

    @property
    def model_summary(self) -> str:
        return self.client.context.model_summary if self.client.context else "connecting"

    @property
    def credential_summary(self) -> str:
        return (
            self.client.context.credential_summary
            if self.client.context
            else "credentials pending"
        )

    @property
    def api_url(self) -> str:
        return self.client.api_url

    def registry_text(self) -> str:
        return self.client.registry_text()

    @property
    def permission_mode_summary(self) -> str:
        return _PERMISSION_MODE_LABELS[self.permission_mode]

    def set_permission_mode(self, value: str) -> str:
        self.permission_mode = _validate_permission_mode(value)
        return f"Cogent permission mode: {self.permission_mode_summary}. Hard denies remain active."

    def cycle_permission_mode(self) -> str:
        index = _PERMISSION_MODES.index(self.permission_mode)
        return self.set_permission_mode(
            _PERMISSION_MODES[(index + 1) % len(_PERMISSION_MODES)]
        )

    def _permission_command_target(self, raw: str) -> str | None:
        parts = shlex.split(raw)
        if not parts or parts[0] != "/permissions":
            return None
        if len(parts) == 2:
            return _validate_permission_mode(parts[1])
        if len(parts) > 2:
            raise ValueError(
                "Usage: /permissions [default|acceptEdits|plan|bypassPermissions]"
            )
        return None

    async def _commit_permission_mode_after(
        self,
        events: AsyncIterator[AgentEvent],
        target: str | None,
    ) -> AsyncIterator[AgentEvent]:
        completed = False
        async for event in events:
            completed = completed or (
                event.type == "command_completed" and event.status == "completed"
            )
            yield event
        if completed and target is not None:
            self.set_permission_mode(target)

    async def execute_management_command(self, raw: str) -> str | None:
        parts = shlex.split(raw)
        if not parts or parts[0] not in {"/memory", "/session"}:
            return None
        await self.prepare_context()
        action = parts[1] if len(parts) > 1 else "list"
        if parts[0] == "/session":
            if action == "list":
                rows = await self.client.list_sessions()
                return "\n".join(
                    f"{'*' if item['id'] == self.session_id else ' '} {item['id']} · {item['title']} · {item['message_count']} messages"
                    for item in rows
                ) or "当前没有会话。"
            if action == "new":
                selected = await self.client.select_session()
                self.session_id = str(selected["id"])
                self.last_run_id = None
                return f"已切换到新会话 {self.session_id}。"
            if action == "resume" and len(parts) == 3:
                selected = await self.client.select_session(parts[2])
                self.session_id = str(selected["id"])
                self.last_run_id = None
                return f"已选择会话 {self.session_id}；不会自动恢复或批准暂停的 Run。"
            if action == "delete" and len(parts) == 3:
                if parts[2] == self.session_id:
                    raise ValueError("cannot delete the active conversation")
                await self.client.delete_session(parts[2])
                return f"已删除会话 {parts[2]}。"
            return "用法：/session list | /session new | /session resume <id> | /session delete <id>"

        scope = parts[2] if len(parts) > 2 else None
        if scope is not None and scope not in {"user", "project"}:
            raise ValueError("memory scope must be user or project")
        if action == "list":
            rows = await self.client.memory_files(scope)
            return "\n".join(f"{item['id']} — {item['description']}" for item in rows) or "当前没有 Cogent 文件记忆。"
        if action == "edit":
            left, marker, body = raw.partition("::")
            prefix = shlex.split(left)
            if not marker or len(prefix) != 6:
                return ('用法：/memory edit <user|project> <name> '
                        '<user|feedback|project|reference> "description" :: body')
            _, _, edit_scope, name, kind, description = prefix
            existing = next((item for item in await self.client.memory_files(edit_scope)
                             if item['name'] == name), None)
            payload = {
                "workspace_id": self.workspace_id, "scope": edit_scope,
                "name": name, "type": kind, "description": description,
                "body": body.strip(),
            }
            if existing:
                payload["expected_hash"] = existing["sha256"]
            saved = await self.client.write_memory(payload, create=existing is None)
            return f"已保存 {saved['id']}。"
        if action == "clear" and scope:
            rows = await self.client.memory_files(scope)
            if len(parts) < 4 or parts[3] != "confirm":
                return (f"将清空 {scope} 范围的 {len(rows)} 个主题：\n" +
                        "\n".join(item['id'] for item in rows) +
                        f"\n确认执行：/memory clear {scope} confirm")
            for item in rows:
                await self.client.delete_memory({
                    "workspace_id": self.workspace_id, "scope": scope,
                    "name": item["name"], "expected_hash": item["sha256"],
                })
            return f"已清空 {scope} 范围，共删除 {len(rows)} 个主题。"
        if action == "consolidate":
            await self.client.maintain_memory("consolidate", scope=scope)
            return "已安排文件记忆治理任务。"
        return "用法：/memory list [user|project] | /memory edit ... | /memory clear <scope> [confirm] | /memory consolidate"

    async def run_print(self, message: str) -> int:
        await self.prepare_context()
        managed = await self.execute_management_command(message)
        if managed is not None:
            self.output_stream.write(managed + "\n")
            self.output_stream.flush()
            return 0
        result, interrupted = await self._stream(self.query(message, mode="print"))
        return _exit_code(result, interrupted=interrupted)

    async def run_repl(self) -> int:
        await self.prepare_context()
        self.error_stream.write(
            f"Cogent REPL · {self.workspace_id} · {self.model_summary}. "
            "Use /help or /exit.\n"
        )
        self.error_stream.flush()
        while True:
            self.output_stream.write("cogent> ")
            self.output_stream.flush()
            raw = self.input_stream.readline()
            if raw == "":
                return 0
            message = raw.strip()
            if not message:
                continue
            if message.casefold() == "/exit":
                return 0
            managed = await self.execute_management_command(message)
            if managed is not None:
                self.output_stream.write(managed + "\n")
                self.output_stream.flush()
                continue
            await self._stream(self.query(message, mode="repl"))

    async def _stream(
        self,
        events: AsyncIterator[AgentEvent],
    ) -> tuple[QueryResult, bool]:
        iterator = events.__aiter__()
        first = await anext(iterator)
        self.last_run_id = first.run_id
        self.output_stream.write(self._event_encoder.encode_json(first) + "\n")
        self.output_stream.flush()
        interrupt = self.interrupt
        if interrupt is not None:
            interrupt.begin_run(first.run_id)
        interrupted = False
        try:
            while True:
                next_event = asyncio.create_task(anext(iterator))
                interrupt_wait = (
                    asyncio.create_task(interrupt.wait())
                    if interrupt is not None and not interrupted
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
                    interrupted = True
                    await self.control(first.run_id, QueryCommand.CANCEL)
                if interrupt_wait is not None and not interrupt_wait.done():
                    interrupt_wait.cancel()
                    with suppress(asyncio.CancelledError):
                        await interrupt_wait
                try:
                    event = await next_event
                except StopAsyncIteration:
                    break
                self.output_stream.write(
                    self._event_encoder.encode_json(event) + "\n"
                )
                self.output_stream.flush()
        finally:
            if interrupt is not None:
                interrupt.finish_run()
            aclose = getattr(iterator, "aclose", None)
            if callable(aclose):
                await aclose()
        return await self.result(first.run_id), interrupted

    async def close(self) -> None:
        await self.client.close()


async def _run_remote_mode(
    args: argparse.Namespace,
    application: RemoteCliApplication,
    *,
    install_sigint: bool = False,
) -> int:
    from ai_agent_platform.cogent.http_client import CogentAPIError

    interrupt = CliInterruptController(asyncio.get_running_loop())
    application.interrupt = interrupt
    signal_context = _sigint_handler(interrupt) if install_sigint else _null_context()
    try:
        with signal_context:
            if args.mode == "print":
                return await application.run_print(" ".join(args.message))
            if args.mode == "tui":
                from ai_agent_platform.cogent.tui import CogentApp

                await CogentApp(application).run_async()
                return 0
            return await application.run_repl()
    except CogentAPIError as exc:
        application.error_stream.write(f"error: {exc}\n")
        return 2
    finally:
        await application.close()


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
        permission_mode=getattr(args, "permission_mode", "default"),
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
