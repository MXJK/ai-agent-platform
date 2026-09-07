from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, AsyncIterator

from rich.text import Text as RichText
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual.widgets import Button, Collapsible, Markdown, Static

from ai_agent_platform.domain import AgentEvent, QueryCommand, QueryLifecycle
from .commands.completion import CompletionPopup
from .permissions import PermissionMode
from .widgets import ChatInput, ToolCallBlock

if TYPE_CHECKING:
    from ai_agent_platform.cli import CliApplication


_MODE_CYCLE = [
    PermissionMode.DEFAULT,
    PermissionMode.ACCEPT_EDITS,
    PermissionMode.PLAN,
    PermissionMode.BYPASS,
]

_MODE_COLORS = {
    PermissionMode.DEFAULT: "dim",
    PermissionMode.ACCEPT_EDITS: "green",
    PermissionMode.PLAN: "yellow",
    PermissionMode.BYPASS: "red",
}


class CogentApp(App):
    TITLE = "Cogent"
    INLINE_PADDING = 0
    BINDINGS = [
        Binding("ctrl+c", "cancel_run", "Cancel Run", priority=True),
        Binding("ctrl+p", "pause_run", "Pause", priority=True),
        Binding("ctrl+o", "toggle_tool_blocks", "Toggle tools", priority=True),
        Binding("shift+tab", "cycle_mode", "Cycle mode", priority=True),
        Binding("ctrl+q", "close", "Exit", priority=True),
    ]
    CSS = """
    Screen { background: #1a1a1a; }
    #title-bar { dock: top; width: 100%; height: 3; padding: 0 1; }
    #chat-area { height: 1fr; min-height: 12; padding: 0 1; }
    .message { padding: 0 2; width: 100%; height: auto; }
    .user-message { margin-top: 1; color: $text; }
    .answer { height: auto; margin-bottom: 1; color: $text; }
    .system-message { height: auto; color: $text-muted; padding: 0 2; }
    ToolCallBlock { height: auto; margin-bottom: 1; padding: 0 2; }
    .tool-block-error { color: $error; }
    #approval { height: auto; max-height: 16; padding: 1 2; border-top: solid #303030; }
    #approval-actions { height: auto; }
    #pending { height: auto; max-height: 10; overflow-y: auto; }
    #input-area { dock: bottom; height: auto; max-height: 22; padding: 0 1; border-top: solid #303030; }
    #chat-input { height: auto; min-height: 3; max-height: 10; border: none; }
    #status-bar { height: auto; min-height: 1; width: 100%; padding: 0 1; border-top: solid #303030; }
    #activity { width: 1fr; height: 1; color: $text-muted; }
    #credential-label { width: auto; height: 1; color: $text-muted; padding: 0 1; }
    #mode-label { width: auto; height: 1; color: $text-muted; padding: 0 1; }
    #model-label { width: auto; height: 1; text-align: right; color: $text-muted; }
    CompletionPopup { margin: 0 1; }
    """

    def __init__(self, application: CliApplication, **kwargs):
        super().__init__(**kwargs)
        self.application = application
        self.active_run_id: str | None = None
        self.run_status = ""
        self.busy = False
        self.seen_events: set[tuple[str, int]] = set()
        self.tools: dict[tuple[str, str], ToolCallBlock] = {}
        self.answers: dict[str, Markdown] = {}
        self.answer_text: dict[str, str] = {}
        self.thinking: dict[str, Collapsible] = {}
        self.thinking_text: dict[str, str] = {}
        self.pending: dict = {}
        self.capabilities: dict = {}

    def compose(self) -> ComposeResult:
        yield Static(self._make_banner(), id="title-bar")
        yield VerticalScroll(id="chat-area")
        with Vertical(id="approval"):
            yield Static(id="pending", markup=False)
            with Horizontal(id="approval-actions"):
                yield Button("Approve once", id="approve", variant="warning")
                yield Button("Reject", id="reject")
                yield Button("Continue", id="continue")
        with Vertical(id="input-area"):
            yield ChatInput(id="chat-input")
            with Horizontal(id="status-bar"):
                yield Static("Connecting…", id="activity", markup=False)
                yield Static("default", id="mode-label")
                yield Static("", id="credential-label", markup=False)
                yield Static("", id="model-label", markup=False)
            yield CompletionPopup()

    @staticmethod
    def _make_banner(
        model: str = "connecting",
        workspace: str = "shared server",
    ) -> RichText:
        text = RichText()
        text.append("  ◇  Cogent\n", style="bold #875fff")
        text.append("     ", style="#875fff")
        text.append(f"{model}\n", style="color(242)")
        text.append("     ", style="#875fff")
        text.append(workspace, style="color(242)")
        return text

    async def on_mount(self) -> None:
        self.register_theme(
            Theme(
                name="cogent-terminal",
                primary="#875fff",
                background="#1a1a1a",
                surface="#1a1a1a",
                panel="#1a1a1a",
                dark=True,
            )
        )
        self.theme = "cogent-terminal"
        self.query_one("#approval").display = False
        composer = self.query_one(ChatInput)
        composer.placeholder = "Send a message…"
        try:
            await self.application.prepare_context()
            self.sub_title = self.application.workspace_id
            self.query_one("#title-bar", Static).update(
                self._make_banner(
                    self.application.model_summary,
                    f"{self.application.workspace_id} · {self.application.api_url}",
                )
            )
            self.query_one("#credential-label", Static).update(
                self.application.credential_summary
            )
            self._update_runtime_labels()
            composer.load_history(str(self.application.local_cwd))
            composer.focus()
            self.show_activity("Ready · /help · /models")
            await self.refresh_capabilities()
        except Exception as exc:
            composer.disabled = True
            self.show_activity(str(exc))

    async def refresh_capabilities(self):
        try:
            self.capabilities = await self.application.capabilities()
        except (ValueError, RuntimeError, PermissionError) as exc:
            self.show_activity(str(exc))

    def show_activity(self, text: str):
        self.query_one("#activity", Static).update(text)

    async def on_chat_input_submitted(self, event: ChatInput.Submitted):
        await self.submit(event.text)

    async def submit(self, text: str):
        if text.strip() == "/exit":
            await self.action_close()
            return
        if text.startswith(("/memory", "/models", "/permissions", "/session")):
            try:
                output = await self.application.execute_management_command(text)
                if output is not None:
                    await self.query_one("#chat-area", VerticalScroll).mount(
                        Static(output, classes="system-message", markup=False)
                    )
                    self.show_activity("Ready")
                    self._update_runtime_labels()
                    await self.refresh_capabilities()
                    return
            except (ValueError, RuntimeError, PermissionError) as exc:
                self.show_activity(str(exc))
                return
        if self.busy:
            self.show_activity("Run in progress · pause or cancel before submitting another request")
            self.query_one(ChatInput).load_text(text)
            return
        if self.run_status == "waiting_input" and not text.startswith("/"):
            self.resume_run(message=text)
            return
        if self.run_status in QueryLifecycle.SUSPENDED_STATUSES and not text.startswith("/"):
            self.show_activity("Review the pending request, then approve, reject, continue, or cancel")
            self.query_one(ChatInput).load_text(text)
            return
        await self.query_one("#chat-area").mount(
            Static(f"❯ {text}", classes="message user-message", markup=False)
        )
        try:
            events = self.application.query(text, mode="tui")
            self._update_permission_mode_label()
        except (ValueError, RuntimeError, PermissionError) as exc:
            self.show_activity(str(exc))
            return
        self.busy = True
        self.consume(events)

    def action_cycle_mode(self) -> None:
        current = PermissionMode(self.application.permission_mode)
        try:
            idx = _MODE_CYCLE.index(current)
        except ValueError:
            idx = 0
        next_mode = _MODE_CYCLE[(idx + 1) % len(_MODE_CYCLE)]
        message = self.application.set_permission_mode(next_mode.value)
        self._update_mode_label()
        self.show_activity(message)

    _MODE_DISPLAY = {
        PermissionMode.DEFAULT: "default",
        PermissionMode.ACCEPT_EDITS: "accept-edits",
        PermissionMode.PLAN: "plan",
        PermissionMode.BYPASS: "YOLO",
    }

    def _update_mode_label(self) -> None:
        perm = PermissionMode(self.application.permission_mode)
        display = self._MODE_DISPLAY.get(perm, perm.value)
        color = _MODE_COLORS.get(perm, "dim")
        label = self.query_one("#mode-label", Static)
        if perm == PermissionMode.DEFAULT:
            label.update(f"[{color}]{display}[/{color}]")
        else:
            label.update(f"[{color}]{display}[/{color}]  (shift+tab to cycle)")

    def _update_permission_mode_label(self) -> None:
        self._update_mode_label()

    def _update_runtime_labels(self) -> None:
        self.query_one("#model-label", Static).update(
            self.application.model_summary
        )
        self.query_one("#credential-label", Static).update(
            self.application.credential_summary
        )
        self.query_one("#title-bar", Static).update(
            self._make_banner(
                self.application.model_summary,
                f"{self.application.workspace_id} · {self.application.api_url}",
            )
        )
        self._update_permission_mode_label()

    @work(group="query-stream", exclusive=True)
    async def consume(self, events: AsyncIterator[AgentEvent]):
        try:
            async for event in events:
                await self.render_event(event)
            if self.active_run_id:
                result = await self.application.result(self.active_run_id)
                self.run_status = result.status
                self.pending = dict(result.output_dict().get("pending") or {})
                await self.show_pending()
                if result.output_dict().get("error"):
                    self.show_activity(str(result.output_dict()["error"]))
                else:
                    self.show_activity(f"{self.run_status} · {self.active_run_id}")
        except Exception as exc:
            self.show_activity(f"Request failed: {exc}")
        finally:
            self.busy = False
            self._update_permission_mode_label()
            await self.refresh_capabilities()

    async def render_event(self, event: AgentEvent):
        key = (event.run_id, event.sequence)
        if key in self.seen_events:
            return
        self.seen_events.add(key)
        self.active_run_id = event.run_id
        self.application.last_run_id = event.run_id
        self.run_status = event.status
        output = event.output_dict()
        feed = self.query_one("#chat-area", VerticalScroll)
        if event.type == "answer_delta":
            text = str(output.get("text") or "")
            if event.run_id not in self.answers:
                node = Markdown("", classes="message answer")
                self.answers[event.run_id] = node
                await feed.mount(node)
            self.answer_text[event.run_id] = self.answer_text.get(event.run_id, "") + text
            self.answers[event.run_id].update(self.answer_text[event.run_id])
        elif event.type in {"thinking_delta", "thinking_completed", "usage"}:
            text = str(output.get("text") or "") if event.type == "thinking_delta" else ""
            count = int(output.get("thoughts_tokens") or 0) if event.type == "usage" else 0
            if text or count:
                panel = self.thinking.get(event.run_id)
                if panel is None:
                    panel = Collapsible(Static("", classes="thinking-content", markup=False), title="Provider thinking summary", collapsed=True)
                    self.thinking[event.run_id] = panel
                    await feed.mount(panel)
                self.thinking_text[event.run_id] = self.thinking_text.get(event.run_id, "") + text
                panel.query_one(".thinking-content", Static).update(self.thinking_text[event.run_id] or "The provider did not return a displayable summary.")
                if count:
                    panel.title = f"Provider thinking · {count} tokens"
        elif event.type == "tool_started":
            call_key = (event.run_id, str(output.get("call_id") or ""))
            block = ToolCallBlock(str(output.get("name") or "Tool"), dict(output.get("arguments") or {}))
            self.tools[call_key] = block
            await feed.mount(block)
        elif event.type == "tool_result":
            call_key = (event.run_id, str(output.get("call_id") or ""))
            block = self.tools.get(call_key)
            if block is None:
                block = ToolCallBlock(str(output.get("name") or "Tool"), {})
                self.tools[call_key] = block
                await feed.mount(block)
            block.set_result(json.dumps(output.get("result") if output.get("ok") else output.get("error"), ensure_ascii=False, indent=2), not bool(output.get("ok")), 0.0)
        if event.type not in {"answer_delta", "thinking_delta"}:
            self.show_activity(event.summary)
        feed.scroll_end(animate=False)

    async def show_pending(self):
        visible = self.run_status in QueryLifecycle.SUSPENDED_STATUSES
        self.query_one("#approval").display = visible
        for name in ("approve", "reject"):
            self.query_one(f"#{name}").display = self.run_status == "waiting_approval"
        self.query_one("#continue").display = self.run_status == "paused"
        text = json.dumps(self.pending, ensure_ascii=False, indent=2)
        if self.run_status == "waiting_input":
            text += "\nReply in the input box to answer these questions."
        self.query_one("#pending", Static).update(text)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id in {"approve", "reject", "continue"}:
            self.resume_run(approved=event.button.id != "reject")

    def resume_run(self, *, approved=True, message=""):
        if not self.active_run_id or self.busy:
            return
        try:
            events = self.application.resume(
                self.active_run_id,
                approved=approved,
                message=message,
            )
        except (ValueError, RuntimeError, PermissionError) as exc:
            self.show_activity(str(exc))
            return
        self.busy = True
        self.query_one("#approval").display = False
        self.consume(events)

    async def action_cancel_run(self):
        if self.active_run_id and self.run_status not in QueryLifecycle.TERMINAL_STATUSES:
            try:
                result = await self.application.control(
                    self.active_run_id,
                    QueryCommand.CANCEL,
                )
                self.run_status = result.status
                await self.show_pending()
                self.show_activity(f"Cancellation requested · {self.active_run_id}")
            except (ValueError, RuntimeError, PermissionError) as exc:
                self.show_activity(str(exc))

    async def action_pause_run(self):
        if self.active_run_id and self.run_status == "running":
            try:
                await self.application.control(
                    self.active_run_id,
                    QueryCommand.PAUSE,
                )
                self.show_activity("Pause requested; waiting for a safe boundary")
            except (ValueError, RuntimeError, PermissionError) as exc:
                self.show_activity(str(exc))

    async def action_close(self):
        await self.action_cancel_run()
        self.exit()

    def action_toggle_tool_blocks(self):
        for block in self.tools.values():
            block.on_click()

    def complete(self, prefix: str | None):
        popup = self.query_one(CompletionPopup)
        if prefix is None:
            popup.hide()
            return
        local_commands = [
            ("/models list  Show server models", "/models list"),
            ("/models register <provider> <model>", "/models register"),
            ("/models use <model-id|provider/model>", "/models use"),
            ("/models auto [smart|quality|cost|latency]", "/models auto"),
            ("/permissions default", "/permissions default"),
            ("/permissions acceptEdits", "/permissions acceptEdits"),
            ("/permissions plan", "/permissions plan"),
            ("/permissions bypassPermissions", "/permissions bypassPermissions"),
        ]
        if " " in prefix:
            normalized = f"/{prefix}"
            pairs = [item for item in local_commands if item[1].startswith(normalized)]
            popup.show_pairs(pairs[:8]) if pairs else popup.hide()
            return
        items = [*self.capabilities.get("commands", []), *self.capabilities.get("skill_commands", []), *self.capabilities.get("mcp_tools", [])]
        pairs = [(f"/{item['name']}  {item.get('description', '')}", f"/{item['name']}") for item in items if item['name'].startswith(prefix)]
        if "exit".startswith(prefix):
            pairs.append(("/exit  Exit Cogent CLI", "/exit"))
        if "models".startswith(prefix):
            pairs.append(("/models  List, register, or select server models", "/models"))
        popup.show_pairs(pairs[:8]) if pairs else popup.hide()

    def on_chat_input_slash_menu_update(self, event: ChatInput.SlashMenuUpdate):
        self.complete(event.prefix)

    def on_chat_input_tab_complete(self, event: ChatInput.TabComplete):
        self.complete(event.text.removeprefix("/"))

    def on_completion_popup_selected(self, event: CompletionPopup.Selected):
        self.query_one(ChatInput).load_text(event.value + " ")
        self.query_one(ChatInput).focus()
