from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ToolUseBlock:
    tool_use_id: str
    tool_name: str
    arguments: dict[str, Any]

@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    content_blocks: list[dict[str, Any]] | None = None

@dataclass
class ThinkingBlock:
    thinking: str
    signature: str
    provider: str = ''
    native: dict[str, Any] | None = None

@dataclass
class Message:
    role: str
    content: str
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    tool_results: list[ToolResultBlock] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)
    provider: str = ''
    metadata: dict[str, Any] = field(default_factory=dict)
    persisted: bool = field(default=False, compare=False, repr=False)
_CHARS_PER_TOKEN = 3.5

def _message_chars(m: Message) -> int:
    n = len(m.content)
    for tb in m.thinking_blocks:
        n += len(tb.thinking)
    for tu in m.tool_uses:
        n += len(tu.tool_name) + len(json.dumps(tu.arguments, ensure_ascii=False))
    for tr in m.tool_results:
        n += len(tr.content)
    return n

def estimate_tokens(messages: list[Message]) -> int:
    total = sum((_message_chars(m) for m in messages))
    return int(total / _CHARS_PER_TOKEN)

@dataclass
class ConversationManager:
    history: list[Message] = field(default_factory=list)
    env_injected: bool = field(default=False, init=False)
    ltm_injected: bool = field(default=False, init=False)
    last_input_tokens: int = field(default=0, init=False)
    baseline_tokens: int = field(default=0, init=False)
    anchor_count: int = field(default=0, init=False)

    def record_usage_anchor(self, input_tokens: int, output_tokens: int=0, cache_read: int=0, cache_creation: int=0, *, provider: str='') -> None:
        cache_prefix = cache_read + cache_creation if provider == 'anthropic' else 0
        self.baseline_tokens = input_tokens + cache_prefix + output_tokens
        self.anchor_count = len(self.history)
        self.last_input_tokens = self.baseline_tokens

    def current_tokens(self) -> int:
        if self.baseline_tokens <= 0:
            return estimate_tokens(self.history)
        tail = self.history[self.anchor_count:]
        return self.baseline_tokens + estimate_tokens(tail)

    def add_user_message(self, content: str) -> None:
        self.history.append(Message(role='user', content=content))

    def add_assistant_message(self, content: str, tool_uses: list[ToolUseBlock] | None=None, thinking_blocks: list[ThinkingBlock] | None=None) -> None:
        self.history.append(Message(role='assistant', content=content, tool_uses=tool_uses or [], thinking_blocks=thinking_blocks or []))

    def add_system_reminder(self, content: str) -> None:
        self.history.append(Message(role='user', content=f'<system-reminder>\n{content}\n</system-reminder>'))

    def has_reminder_containing(self, marker: str) -> bool:
        return any((msg.role == 'user' and marker in (msg.content or '') for msg in self.history))

    def add_tool_results_message(self, tool_results: list[ToolResultBlock]) -> None:
        self.history.append(Message(role='user', content='', tool_results=tool_results))

    def inject_environment(self, context: str) -> None:
        if not self.env_injected:
            self.history.insert(0, Message(role='user', content=context))
            self.env_injected = True

    def inject_long_term_memory(self, instructions: str, memories: str, skills: str='') -> None:
        if self.ltm_injected:
            return
        sections: list[str] = []
        if instructions:
            sections.append('# cogentInstructions\nCodebase and user instructions are shown below. Be sure to adhere to these instructions. IMPORTANT: These instructions OVERRIDE any default behavior and you MUST follow them exactly as written.\n\n' + instructions)
        if memories:
            sections.append('# autoMemory\n' + memories)
        if skills:
            sections.append('# availableSkills\n' + skills)
        if not sections:
            return
        from datetime import date
        sections.append(f"# currentDate\nToday's date is {date.today().isoformat()}.")
        body = '\n\n'.join(sections)
        wrapped = "<system-reminder>\nAs you answer the user's questions, you can use the following context:\n" + body + '\n\n      IMPORTANT: this context may or may not be relevant to your tasks. You should not respond to this context unless it is highly relevant to your task.\n</system-reminder>'
        pos = 1 if self.env_injected else 0
        self.history.insert(pos, Message(role='user', content=wrapped))
        self.ltm_injected = True

    def replace_history(self, new_messages: list[Message]) -> None:
        self.history = new_messages
        self.env_injected = False
        self.ltm_injected = False
        self.baseline_tokens = 0
        self.anchor_count = 0
        self.last_input_tokens = 0

    def get_messages(self) -> list[Message]:
        return list(self.history)
