from __future__ import annotations
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from ai_agent_platform.cogent.conversation import ConversationManager, Message, ToolResultBlock, estimate_tokens
AGGREGATE_CHAR_LIMIT = 200000
PREVIEW_CHARS = 2000
SUMMARY_OUTPUT_RESERVE = 20000
AUTO_COMPACT_SAFETY_MARGIN = 13000
MANUAL_COMPACT_SAFETY_MARGIN = 3000
KEEP_RECENT_TOKENS = 10000
MIN_KEEP_MESSAGES = 5
KEEP_MAX_TOKENS = 40000
MIN_SUMMARIZE_PREFIX_TOKENS = 2000
PERSISTED_TAG = '<persisted-output>'

@dataclass
class CompactBoundary:
    summary: str
    keep: list[Message]

@dataclass
class CompactEvent:
    before_tokens: int
    boundary: CompactBoundary | None = None

def spill_dir(work_dir: str, session_id: str='') -> Path:
    sid = session_id or 'default'
    return Path(work_dir) / '.cogent' / 'sessions' / sid / 'tool-results'

def ensure_session_dir(work_dir: str, session_id: str='') -> Path:
    session_dir = spill_dir(work_dir, session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir

def cleanup_tool_results(session_dir: Path) -> None:
    if session_dir.exists():
        shutil.rmtree(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)

def persist_tool_result(tool_use_id: str, content: str, session_dir: Path) -> Path:
    file_path = session_dir / f'{tool_use_id}.txt'
    try:
        fd = os.open(str(file_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
    except FileExistsError:
        pass
    return file_path

def make_persisted_preview(content: str, file_path: Path) -> str:
    size_kb = len(content) // 1024
    preview = content[:PREVIEW_CHARS]
    more = '\n...' if len(content) > PREVIEW_CHARS else ''
    return f'{PERSISTED_TAG}\n输出太大（{size_kb}KB），完整内容已保存到：\n{file_path}\n\n预览（前 2KB）：\n{preview}{more}\n</persisted-output>'

def is_spill_readback(tool_name: str, arguments: Mapping[str, object], session_dir: Path) -> bool:
    if tool_name != 'ReadFile':
        return False
    raw = arguments.get('file_path', '')
    if not isinstance(raw, str) or not raw:
        return False
    abs_path = os.path.abspath(raw)
    return abs_path.startswith(os.path.abspath(str(session_dir)))

def apply_tool_result_budget(tool_results: list[ToolResultBlock], session_dir: Path, exempt_ids: set[str] | None=None) -> None:
    exempt = exempt_ids or set()
    total = sum((len(tr.content) for tr in tool_results))
    if total <= AGGREGATE_CHAR_LIMIT:
        return
    ranked = sorted(tool_results, key=lambda tr: len(tr.content), reverse=True)
    for tr in ranked:
        if total <= AGGREGATE_CHAR_LIMIT:
            break
        if tr.tool_use_id in exempt:
            continue
        if len(tr.content) <= PREVIEW_CHARS:
            continue
        try:
            fp = persist_tool_result(tr.tool_use_id, tr.content, session_dir)
        except OSError:
            continue
        preview = make_persisted_preview(tr.content, fp)
        total -= len(tr.content) - len(preview)
        tr.content = preview

def compute_compact_threshold(context_window: int, manual: bool=False) -> int:
    effective = context_window - SUMMARY_OUTPUT_RESERVE
    margin = MANUAL_COMPACT_SAFETY_MARGIN if manual else AUTO_COMPACT_SAFETY_MARGIN
    return effective - margin
from ai_agent_platform.cogent.prompts import COMPACTION_PROMPT
SUMMARY_PROMPT = COMPACTION_PROMPT

def extract_summary(llm_output: str) -> str:
    start = llm_output.find('<summary>')
    end = llm_output.find('</summary>')
    if start == -1 or end == -1:
        return llm_output
    return llm_output[start + len('<summary>'):end].strip()

def build_compact_messages(summary: str, attachment: str='', has_keep_tail: bool=False, transcript_path: str='') -> list[Message]:
    content = '本次会话延续自之前的对话，因上下文空间不足进行了压缩。以下是早期对话的摘要：\n\n' + summary
    if has_keep_tail:
        content += '\n\n近期消息已原样保留。'
    if transcript_path:
        content += f'\n\n如果你需要压缩前的具体细节（代码片段、报错信息等），请用 ReadFile 读取完整会话记录：{transcript_path}'
    if attachment:
        content += '\n\n---\n\n' + attachment
    return [Message(role='user', content=content)]
RECOVERY_FILE_LIMIT = 5
RECOVERY_TOKENS_PER_FILE = 5000
RECOVERY_SKILLS_BUDGET = 25000
RECOVERY_TOKENS_PER_SKILL = 5000
_RECOVERY_CHARS_PER_TOKEN = 3.5

@dataclass
class FileReadRecord:
    path: str
    content: str
    timestamp: float

@dataclass
class SkillInvocationRecord:
    name: str
    body: str
    timestamp: float

class RecoveryState:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._files: dict[str, FileReadRecord] = {}
        self._skills: dict[str, SkillInvocationRecord] = {}

    def record_file_read(self, path: str, content: str) -> None:
        if not path:
            return
        with self._lock:
            self._files[path] = FileReadRecord(path=path, content=content, timestamp=time.time())

    def record_skill_invocation(self, name: str, body: str) -> None:
        if not name:
            return
        with self._lock:
            self._skills[name] = SkillInvocationRecord(name=name, body=body, timestamp=time.time())

    def snapshot_files(self, limit: int) -> list[FileReadRecord]:
        with self._lock:
            records = list(self._files.values())
        records.sort(key=lambda r: r.timestamp, reverse=True)
        if limit > 0:
            records = records[:limit]
        return records

    def snapshot_skills(self) -> list[SkillInvocationRecord]:
        with self._lock:
            records = list(self._skills.values())
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records

def _approx_tokens(s: str) -> int:
    if not s:
        return 0
    return int(len(s) / _RECOVERY_CHARS_PER_TOKEN)

def _truncate_by_tokens(s: str, token_budget: int) -> str:
    if token_budget <= 0 or not s:
        return s
    if _approx_tokens(s) <= token_budget:
        return s
    max_chars = int(token_budget * _RECOVERY_CHARS_PER_TOKEN)
    if max_chars <= 0 or max_chars >= len(s):
        return s
    return s[:max_chars] + '\n… (内容已截断)'

def _first_line(s: str) -> str:
    for line in s.split('\n'):
        stripped = line.strip()
        if stripped:
            return stripped
    return ''

def build_recovery_attachment(state: RecoveryState | None, tool_schemas: list[Mapping[str, Any]] | None) -> str:
    sections: list[str] = []
    if state is not None:
        files = state.snapshot_files(RECOVERY_FILE_LIMIT)
        if files:
            buf = ['## 最近读过的文件\n', '以下快照是文件读取工具上次返回的内容。如需当前字节请重新读取。\n']
            for rec in files:
                content = _truncate_by_tokens(rec.content, RECOVERY_TOKENS_PER_FILE)
                ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(rec.timestamp))
                buf.append(f'### {rec.path}  (read {ts})\n')
                buf.append('```\n')
                buf.append(content)
                if not content.endswith('\n'):
                    buf.append('\n')
                buf.append('```\n')
            sections.append(''.join(buf))
        skills = state.snapshot_skills()
        if skills:
            buf = ['## 已激活的技能\n', '下列技能在本会话中被调用过，其触发条件仍然适用。\n']
            used = 0
            emitted = False
            for sk in skills:
                body = _truncate_by_tokens(sk.body, RECOVERY_TOKENS_PER_SKILL)
                tokens = _approx_tokens(body) + _approx_tokens(sk.name) + 8
                if used + tokens > RECOVERY_SKILLS_BUDGET:
                    break
                used += tokens
                buf.append(f'### {sk.name}\n\n{body}\n')
                emitted = True
            if emitted:
                sections.append(''.join(buf))
    if tool_schemas:
        buf = ['## 可用工具\n', '你仍然可以调用以下工具，需要时直接发起调用即可：\n']
        for t in tool_schemas:
            name = t.get('name') if isinstance(t, Mapping) else None
            if not name:
                continue
            desc = t.get('description', '') if isinstance(t, Mapping) else ''
            desc = _first_line(desc or '')
            if desc:
                buf.append(f'- {name} — {desc}\n')
            else:
                buf.append(f'- {name}\n')
        sections.append(''.join(buf))
    if not sections:
        return ''
    sections.append('## 提示\n\n以上恢复的上下文是重建的。若需要原文代码、错误信息或用户原话，请用文件读取工具重新读取，不要根据摘要猜测细节。\n')
    return '\n'.join(sections)

def _group_messages_by_turn(messages: list[Message]) -> list[list[Message]]:
    groups: list[list[Message]] = []
    current: list[Message] = []
    for msg in messages:
        current.append(msg)
        if msg.role == 'assistant' and (not msg.tool_uses):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups

def _message_tokens(msg: Message) -> int:
    return estimate_tokens([msg])

def _compute_keep_start_index(messages: list[Message]) -> int:
    n = len(messages)
    if n == 0:
        return 0
    kept_tokens = 0
    kept_count = 0
    keep_start = n
    for i in range(n - 1, -1, -1):
        tok = _message_tokens(messages[i])
        if kept_count > 0 and kept_tokens + tok > KEEP_MAX_TOKENS:
            break
        kept_tokens += tok
        kept_count += 1
        keep_start = i
        if kept_tokens >= KEEP_RECENT_TOKENS or kept_count >= MIN_KEEP_MESSAGES:
            break
    return _align_keep_start_to_tool_pair(messages, keep_start)

def _align_keep_start_to_tool_pair(messages: list[Message], keep_start: int) -> int:
    while 0 < keep_start < len(messages):
        msg = messages[keep_start]
        if msg.role == 'user' and msg.tool_results:
            prev = messages[keep_start - 1]
            if prev.role == 'assistant' and prev.tool_uses:
                keep_start -= 1
                continue
        break
    return keep_start

def _prefix_too_small_to_compact(prefix: list[Message]) -> bool:
    if not prefix:
        return True
    return estimate_tokens(prefix) < MIN_SUMMARIZE_PREFIX_TOKENS

def _build_prefix_text(prefix: list[Message]) -> str:
    parts: list[str] = []
    for m in prefix:
        parts.append(f'[{m.role}]: {m.content}')
        for tu in m.tool_uses:
            parts.append(f'[tool_use {tu.tool_name}]: {tu.tool_use_id}')
        for tr in m.tool_results:
            content = tr.content
            if len(content) > 500:
                content = content[:500] + '...'
            parts.append(f'[tool_result]: {content}')
    return '\n'.join(parts)

async def _call_summary_with_cache_sharing(client: Any, messages: list[Message], tool_schemas: list[Mapping[str, Any]] | None) -> str | None:
    from ai_agent_platform.cogent.tools.base import TextDelta
    last_assistant = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == 'assistant':
            last_assistant = i
            break
    if last_assistant < 0:
        return None
    summary_conv = ConversationManager()
    summary_conv.history = list(messages[:last_assistant + 1])
    summary_conv.history.append(Message(role='user', content=SUMMARY_PROMPT))
    try:
        collected_text = ''
        async for event in client.stream(summary_conv, system=SUMMARY_PROMPT, tools=[]):
            if isinstance(event, TextDelta):
                collected_text += event.text
        return extract_summary(collected_text)
    except Exception as e:
        err_msg = str(e).lower()
        if 'prompt' in err_msg and 'long' in err_msg or 'too many' in err_msg:
            return None
        raise

async def _call_summary_with_ptl_retry(client: Any, prefix: list[Message], tool_schemas: list[Mapping[str, Any]] | None, max_retries: int=3) -> str | None:
    from ai_agent_platform.cogent.tools.base import TextDelta
    current_prefix = list(prefix)
    for attempt in range(max_retries):
        text = _build_prefix_text(current_prefix)
        summary_conv = ConversationManager()
        summary_conv.history = [Message(role='user', content=SUMMARY_PROMPT + '\n\n' + text)]
        try:
            collected_text = ''
            async for event in client.stream(summary_conv, system=SUMMARY_PROMPT, tools=[]):
                if isinstance(event, TextDelta):
                    collected_text += event.text
            return extract_summary(collected_text)
        except Exception as e:
            err_msg = str(e).lower()
            if 'prompt' in err_msg and 'long' in err_msg or 'too many' in err_msg:
                groups = _group_messages_by_turn(current_prefix)
                drop_count = max(1, len(groups) // 5)
                remaining = groups[drop_count:]
                current_prefix = [m for g in remaining for m in g]
                if not current_prefix:
                    return None
                continue
            raise
    return None

@dataclass
class CompactCircuitBreaker:
    max_failures: int = 3
    consecutive_failures: int = field(default=0, init=False)

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def is_open(self) -> bool:
        return self.consecutive_failures >= self.max_failures

@dataclass
class UsageAnchor:
    baseline_tokens: int = 0
    anchor_count: int = 0
    has_usage: bool = False

    @staticmethod
    def from_api_usage(input_tokens: int, output_tokens: int=0, cache_read: int=0, cache_creation: int=0, msg_count: int=0) -> UsageAnchor:
        return UsageAnchor(baseline_tokens=input_tokens + cache_read + cache_creation + output_tokens, anchor_count=msg_count, has_usage=True)

async def auto_compact(conversation: ConversationManager, client: Any, context_window: int, session_dir: Path, manual: bool=False, breaker: CompactCircuitBreaker | None=None, recovery: RecoveryState | None=None, tool_schemas: list[Mapping[str, Any]] | None=None, transcript_path: str='') -> CompactEvent | str | None:
    current = conversation.current_tokens()
    if manual:
        pass
    else:
        soft_threshold = compute_compact_threshold(context_window, manual=False)
        if current < soft_threshold:
            return None
        hard_threshold = compute_compact_threshold(context_window, manual=True)
        if current >= hard_threshold:
            pass
        elif breaker is not None and breaker.is_open():
            return '自动压缩已熔断（连续失败 3 次），请手动处理或使用 /compact'
    before_tokens = current
    effective_history = conversation.history
    keep_start = _compute_keep_start_index(effective_history)
    to_summarize = effective_history[:keep_start]
    keep_tail = effective_history[keep_start:]
    if keep_start <= 0 or _prefix_too_small_to_compact(to_summarize):
        return None
    try:
        summary = await _call_summary_with_cache_sharing(client, to_summarize, [])
    except Exception as e:
        if breaker is not None:
            breaker.record_failure()
        return f'摘要生成失败: {e}'
    if summary is None:
        try:
            summary = await _call_summary_with_ptl_retry(client, to_summarize, tool_schemas)
        except Exception as e:
            if breaker is not None:
                breaker.record_failure()
            return f'摘要生成失败: {e}'
    if summary is None:
        if breaker is not None:
            breaker.record_failure()
        return '摘要生成失败：多次重试后仍超出上下文限制'
    attachment = build_recovery_attachment(recovery, tool_schemas)
    new_messages = build_compact_messages(summary, attachment=attachment, has_keep_tail=bool(keep_tail), transcript_path=transcript_path)
    new_messages = new_messages + list(keep_tail)
    conversation.replace_history(new_messages)
    if breaker is not None:
        breaker.record_success()
    return CompactEvent(before_tokens=before_tokens, boundary=CompactBoundary(summary=summary, keep=list(keep_tail)))
