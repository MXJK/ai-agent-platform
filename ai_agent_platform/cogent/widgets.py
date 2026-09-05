from __future__ import annotations
import os
from pathlib import Path
from typing import Any
from rich.markup import escape
from textual.binding import Binding
from textual.message import Message as TMessage
from textual.widgets import Static, TextArea
from .commands.completion import CompletionPopup
MAX_TRUNCATED_LINES = 20

class ChatInput(TextArea):
    BINDINGS = [Binding('enter', 'submit', 'Submit', priority=True), Binding('shift+enter', 'newline', 'Newline', priority=True), Binding('ctrl+j', 'newline', 'Newline', priority=True), Binding('tab', 'complete', 'Complete', priority=True), Binding('escape', 'dismiss_popup', 'Dismiss', priority=True), Binding('up', 'nav_up', 'Navigate up', priority=True), Binding('down', 'nav_down', 'Navigate down', priority=True)]

    class Submitted(TMessage):

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class TabComplete(TMessage):

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cursor_blink = False
        self._history: list[str] = []
        self._history_index: int = -1
        self._history_draft: str = ''
        self._history_file: Path | None = None

    def load_history(self, work_dir: str) -> None:
        root = Path(work_dir).resolve()
        target = root / '.cogent' / 'history'
        if target.is_symlink() or target.parent.is_symlink() or not target.resolve().is_relative_to(root):
            return
        self._history_file = target
        if self._history_file.exists():
            try:
                lines = self._history_file.read_text(encoding='utf-8').splitlines()
                self._history = [l for l in lines if l.strip()][-1000:]
            except Exception:
                pass

    def _persist_entry(self, text: str) -> None:
        if self._history_file is None:
            return
        try:
            if self._history_file.is_symlink() or self._history_file.parent.is_symlink():
                return
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._history_file, 'a', encoding='utf-8') as f:
                f.write(text + '\n')
        except Exception:
            pass

    def _popup(self) -> CompletionPopup | None:
        try:
            return self.app.query_one(CompletionPopup)
        except Exception:
            return None

    def action_submit(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            selected = popup.get_selected()
            popup.hide()
            if selected:
                self._history.append(selected)
                self._persist_entry(selected)
                self._history_index = -1
                self._history_draft = ''
                self.post_message(self.Submitted(selected))
                self.clear()
                return
        text = self.text.strip()
        if text:
            self._history.append(text)
            self._persist_entry(text)
            self._history_index = -1
            self._history_draft = ''
            self.post_message(self.Submitted(text))
            self.clear()

    def action_newline(self) -> None:
        self.insert('\n')

    def action_complete(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            selected = popup.get_selected()
            if selected:
                popup.hide()
                self.clear()
                self.insert(selected + ' ')
            return
        text = self.text.strip()
        if text.startswith('/'):
            self.post_message(self.TabComplete(text))
        else:
            self.insert('\t')

    def action_dismiss_popup(self) -> None:
        popup = self._popup()
        if popup is not None:
            popup.hide()

    def action_nav_up(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            popup.move_up()
            return
        if not self._history:
            return
        if self._history_index == -1:
            self._history_draft = self.text
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        else:
            return
        self.clear()
        self.insert(self._history[self._history_index])

    def action_nav_down(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            popup.move_down()
            return
        if self._history_index == -1:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.clear()
            self.insert(self._history[self._history_index])
        else:
            self._history_index = -1
            self.clear()
            self.insert(self._history_draft)

    class AtFileRequest(TMessage):

        def __init__(self, prefix: str) -> None:
            super().__init__()
            self.prefix = prefix

    class SlashMenuUpdate(TMessage):

        def __init__(self, prefix: str | None) -> None:
            super().__init__()
            self.prefix = prefix

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        text = self.text
        if text.startswith('/') and self._history_index < 0:
            prefix = text[1:]
            if ' ' not in prefix and '\n' not in prefix:
                self.post_message(self.SlashMenuUpdate(prefix))
            else:
                self.post_message(self.SlashMenuUpdate(None))
        else:
            self.post_message(self.SlashMenuUpdate(None))
        at_idx = text.rfind('@')
        if at_idx < 0:
            return
        after = text[at_idx + 1:]
        if ' ' in after or '\n' in after:
            return
        if after:
            self.post_message(self.AtFileRequest(after))

def _tool_title(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == 'ReadFile':
        path = os.path.basename(arguments.get('file_path', ''))
        return f'Read {path}' if path else 'Read'
    if tool_name == 'WriteFile':
        path = os.path.basename(arguments.get('file_path', ''))
        content = arguments.get('content', '')
        lines = content.count('\n') + 1 if content else 0
        return f'Write {path} ({lines} lines)' if path else 'Write'
    if tool_name == 'EditFile':
        path = os.path.basename(arguments.get('file_path', ''))
        return f'Edit {path}' if path else 'Edit'
    if tool_name == 'Bash':
        cmd = arguments.get('command', '')
        short = cmd[:50] + '…' if len(cmd) > 50 else cmd
        return f'Bash: {short}' if short else 'Bash'
    if tool_name == 'Glob':
        return f"Glob: {arguments.get('pattern', '')}"
    if tool_name == 'Grep':
        return f"Grep: {arguments.get('pattern', '')}"
    return tool_name

def _format_detail(tool_name: str, arguments: dict[str, Any], output: str) -> str:
    parts: list[str] = []
    if tool_name == 'Bash':
        parts.append(f"  IN   {escape(str(arguments.get('command', '')))}")
        parts.append('')
        for line in output.splitlines():
            parts.append(f'  OUT  {escape(line)}')
    elif tool_name == 'EditFile':
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            escaped = escape(line)
            if line.startswith('+ '):
                parts.append(f'  [green]{escaped}[/]')
            elif line.startswith('- '):
                parts.append(f'  [red]{escaped}[/]')
            else:
                parts.append(f'  [dim]{escaped}[/]')
        total = output.count('\n') + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f'  [dim]… ({total - MAX_TRUNCATED_LINES} more lines)[/]')
    elif tool_name in ('ReadFile', 'WriteFile'):
        parts.append(f"  {escape(str(arguments.get('file_path', '')))}")
        parts.append('')
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            parts.append(f'  {escape(line)}')
        total = output.count('\n') + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f'  … ({total - MAX_TRUNCATED_LINES} more lines)')
    else:
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            parts.append(f'  {escape(line)}')
        total = output.count('\n') + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f'  … ({total - MAX_TRUNCATED_LINES} more lines)')
    return '\n'.join(parts)

class ToolCallBlock(Static, can_focus=True):

    def __init__(self, tool_name: str, arguments: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self._arguments = arguments
        self._title = escape(_tool_title(tool_name, arguments))
        self._full_output = ''
        self._is_error = False
        self._elapsed = 0.0
        self._collapsed = True
        self._loading = True
        self._render_loading()

    def _render_loading(self) -> None:
        self.update(f'  ● {self._title} …')
        self.add_class('tool-block-loading')

    def set_result(self, output: str, is_error: bool, elapsed: float) -> None:
        self._full_output = output
        self._is_error = is_error
        self._elapsed = elapsed
        self._loading = False
        self.remove_class('tool-block-loading')
        if is_error:
            self.add_class('tool-block-error')
        if self.tool_name == 'EditFile' and (not is_error):
            self._collapsed = False
            self._render_expanded()
        else:
            self._collapsed = True
            self._render_collapsed()

    def _render_collapsed(self) -> None:
        if self._is_error:
            self.update(f'  ✗ {self._title} ({self._elapsed:.1f}s)')
        else:
            self.update(f'  ✓ {self._title} ({self._elapsed:.1f}s)')

    def _render_expanded(self) -> None:
        if self._is_error:
            header = f'  ✗ {self._title} ({self._elapsed:.1f}s)'
        else:
            header = f'  ✓ {self._title} ({self._elapsed:.1f}s)'
        detail = _format_detail(self.tool_name, self._arguments, self._full_output)
        self.update(f'{header}\n{detail}')

    def on_click(self) -> None:
        if self._loading:
            return
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._render_collapsed()
        else:
            self._render_expanded()
