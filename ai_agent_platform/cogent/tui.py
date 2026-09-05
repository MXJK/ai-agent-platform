from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, AsyncIterator

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Collapsible, Footer, Header, Static

from ai_agent_platform.domain import AgentEvent, QueryCommand, QueryLifecycle
from .commands.completion import CompletionPopup
from .widgets import ChatInput, ToolCallBlock

if TYPE_CHECKING:
    from ai_agent_platform.cli import CliApplication


class CogentApp(App):
    TITLE = "Cogent"
    BINDINGS = [
        Binding("ctrl+c", "cancel_run", "Cancel Run", priority=True),
        Binding("ctrl+p", "pause_run", "Pause", priority=True),
        Binding("ctrl+q", "close", "Exit", priority=True),
    ]
    CSS = """
    Screen { layout: vertical; }
    #conversation { height: 1fr; padding: 0 2; }
    .user-message { margin-top: 1; color: $text-muted; }
    .answer, ToolCallBlock { height: auto; margin-bottom: 1; }
    #activity { height: auto; padding: 0 2; color: $text-muted; }
    #approval { height: auto; max-height: 16; padding: 1 2; }
    #approval-actions { height: auto; }
    #pending { height: auto; max-height: 10; overflow-y: auto; }
    #composer { height: 5; margin: 0 2; }
    CompletionPopup { margin: 0 2; }
    .tool-block-error { color: $error; }
    """

    def __init__(self, application: CliApplication, **kwargs):
        super().__init__(**kwargs)
        self.application = application
        self.sdk = application.sdk
        self.active_run_id: str | None = None
        self.run_status = ""
        self.busy = False
        self.seen_events: set[tuple[str, int]] = set()
        self.tools: dict[tuple[str, str], ToolCallBlock] = {}
        self.answers: dict[str, Static] = {}
        self.answer_text: dict[str, str] = {}
        self.thinking: dict[str, Collapsible] = {}
        self.thinking_text: dict[str, str] = {}
        self.pending: dict = {}
        self.capabilities: dict = {}

    @property
    def actor(self):
        return self.application.user_id if self.application.runtime.settings.auth_mode != "disabled" else None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield VerticalScroll(id="conversation")
        yield Static("Ready · /help lists shared commands", id="activity", markup=False)
        with Vertical(id="approval"):
            yield Static(id="pending", markup=False)
            with Horizontal(id="approval-actions"):
                yield Button("Approve once", id="approve", variant="warning")
                yield Button("Reject", id="reject")
                yield Button("Continue", id="continue")
        yield CompletionPopup()
        yield ChatInput(id="composer")
        yield Footer()

    async def on_mount(self) -> None:
        self.application._prepare_context()
        self.sub_title = self.application.workspace_root
        self.query_one("#approval").display = False
        self.query_one(ChatInput).load_history(self.application.workspace_root)
        self.query_one(ChatInput).focus()
        await self.refresh_capabilities()

    async def refresh_capabilities(self):
        try:
            self.capabilities = await asyncio.to_thread(
                self.sdk.query_service.composer_capabilities,
                conversation_id=self.application.session_id,
                workspace_id=self.application.workspace_id,
                actor_user_id=self.actor,
            )
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
        await self.query_one("#conversation").mount(Static(text, classes="user-message", markup=False))
        try:
            events = self.sdk.query(self.application._query_params(text, mode="tui"))
        except (ValueError, RuntimeError, PermissionError) as exc:
            self.show_activity(str(exc))
            return
        self.busy = True
        self.consume(events)

    @work(group="query-stream", exclusive=True)
    async def consume(self, events: AsyncIterator[AgentEvent]):
        try:
            async for event in events:
                await self.render_event(event)
            if self.active_run_id:
                result = self.sdk.result(self.active_run_id, actor_user_id=self.actor)
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
        feed = self.query_one("#conversation", VerticalScroll)
        if event.type == "answer_delta":
            text = str(output.get("text") or "")
            if event.run_id not in self.answers:
                node = Static("", classes="answer", markup=False)
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
            events = self.sdk.resume(self.active_run_id, approved=approved, message=message, actor_user_id=self.actor)
        except (ValueError, RuntimeError, PermissionError) as exc:
            self.show_activity(str(exc))
            return
        self.busy = True
        self.query_one("#approval").display = False
        self.consume(events)

    async def action_cancel_run(self):
        if self.active_run_id and self.run_status not in QueryLifecycle.TERMINAL_STATUSES:
            try:
                result = self.sdk.control(self.active_run_id, QueryCommand.CANCEL, actor_user_id=self.actor)
                self.run_status = result.status
                await self.show_pending()
                self.show_activity(f"Cancellation requested · {self.active_run_id}")
            except (ValueError, RuntimeError, PermissionError) as exc:
                self.show_activity(str(exc))

    def action_pause_run(self):
        if self.active_run_id and self.run_status == "running":
            try:
                self.sdk.control(self.active_run_id, QueryCommand.PAUSE, actor_user_id=self.actor)
                self.show_activity("Pause requested; waiting for a safe boundary")
            except (ValueError, RuntimeError, PermissionError) as exc:
                self.show_activity(str(exc))

    async def action_close(self):
        await self.action_cancel_run()
        self.exit()

    def complete(self, prefix: str | None):
        popup = self.query_one(CompletionPopup)
        if prefix is None:
            popup.hide()
            return
        items = [*self.capabilities.get("commands", []), *self.capabilities.get("skill_commands", []), *self.capabilities.get("mcp_tools", [])]
        pairs = [(f"/{item['name']}  {item.get('description', '')}", f"/{item['name']}") for item in items if item['name'].startswith(prefix)]
        if "exit".startswith(prefix):
            pairs.append(("/exit  Exit Cogent CLI", "/exit"))
        popup.show_pairs(pairs[:8]) if pairs else popup.hide()

    def on_chat_input_slash_menu_update(self, event: ChatInput.SlashMenuUpdate):
        self.complete(event.prefix)

    def on_chat_input_tab_complete(self, event: ChatInput.TabComplete):
        self.complete(event.text.removeprefix("/"))

    def on_completion_popup_selected(self, event: CompletionPopup.Selected):
        self.query_one(ChatInput).load_text(event.value + " ")
        self.query_one(ChatInput).focus()
