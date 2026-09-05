from __future__ import annotations
import os
import tempfile
from pathlib import Path
import pytest
from ai_agent_platform.cogent.context.manager import AGGREGATE_CHAR_LIMIT, KEEP_MAX_TOKENS, KEEP_RECENT_TOKENS, MIN_KEEP_MESSAGES, PERSISTED_TAG, CompactCircuitBreaker, _align_keep_start_to_tool_pair, _compute_keep_start_index, apply_tool_result_budget, auto_compact, build_compact_messages, cleanup_tool_results, compute_compact_threshold, ensure_session_dir, extract_summary, is_spill_readback, make_persisted_preview, persist_tool_result
from ai_agent_platform.cogent.conversation import _CHARS_PER_TOKEN, ConversationManager, Message, ToolResultBlock, ToolUseBlock, estimate_tokens

class TestPersistToolResult:

    def test_writes_file(self, tmp_path: Path) -> None:
        fp = persist_tool_result('toolu_001', 'hello world', tmp_path)
        assert fp.exists()
        assert fp.read_text() == 'hello world'

    def test_idempotent(self, tmp_path: Path) -> None:
        persist_tool_result('toolu_002', 'first', tmp_path)
        persist_tool_result('toolu_002', 'second', tmp_path)
        fp = tmp_path / 'toolu_002.txt'
        assert fp.read_text() == 'first'

class TestMakePersistedPreview:

    def test_contains_tag_and_path(self, tmp_path: Path) -> None:
        content = 'x' * 10000
        preview = make_persisted_preview(content, tmp_path / 'test.txt')
        assert preview.startswith(PERSISTED_TAG)
        assert 'test.txt' in preview
        assert '</persisted-output>' in preview

    def test_preview_truncated(self, tmp_path: Path) -> None:
        content = 'a' * 5000
        preview = make_persisted_preview(content, tmp_path / 'test.txt')
        lines = preview.split('\n')
        preview_line = [l for l in lines if l.startswith('aaa')]
        assert len(preview_line) == 1
        assert len(preview_line[0]) == 2000

class TestApplyToolResultBudget:

    def _batch(self, *sizes: int) -> list[ToolResultBlock]:
        return [ToolResultBlock(tool_use_id=f't{i + 1}', content='x' * n) for i, n in enumerate(sizes)]

    def test_under_limit_untouched(self, tmp_path: Path) -> None:
        batch = self._batch(40000, 40000)
        apply_tool_result_budget(batch, tmp_path)
        assert batch[0].content == 'x' * 40000
        assert batch[1].content == 'x' * 40000

    def test_aggregate_spills_largest_first(self, tmp_path: Path) -> None:
        batch = self._batch(45000, 45000, 45001, 45000, 45000)
        apply_tool_result_budget(batch, tmp_path)
        total = sum((len(tr.content) for tr in batch))
        assert total <= AGGREGATE_CHAR_LIMIT
        replaced = [tr for tr in batch if tr.content.startswith(PERSISTED_TAG)]
        assert len(replaced) == 1
        assert batch[2].content.startswith(PERSISTED_TAG)
        assert (tmp_path / 't3.txt').read_text() == 'x' * 45001

    def test_exempt_skipped(self, tmp_path: Path) -> None:
        batch = self._batch(45000, 45000, 45001, 45000, 45000)
        apply_tool_result_budget(batch, tmp_path, {'t3'})
        assert not batch[2].content.startswith(PERSISTED_TAG)
        total = sum((len(tr.content) for tr in batch))
        assert total <= AGGREGATE_CHAR_LIMIT

    def test_all_exempt_accepts_overage(self, tmp_path: Path) -> None:
        batch = self._batch(105000, 105000)
        apply_tool_result_budget(batch, tmp_path, {'t1', 't2'})
        assert batch[0].content == 'x' * 105000
        assert batch[1].content == 'x' * 105000

    def test_deterministic_output(self, tmp_path: Path) -> None:
        batch1 = self._batch(45000, 45000, 45001, 45000, 45000)
        batch2 = self._batch(45000, 45000, 45001, 45000, 45000)
        apply_tool_result_budget(batch1, tmp_path)
        apply_tool_result_budget(batch2, tmp_path)
        for a, b in zip(batch1, batch2):
            assert a.content == b.content

    def test_idempotent_on_processed_batch(self, tmp_path: Path) -> None:
        batch = self._batch(45000, 45000, 45001, 45000, 45000)
        apply_tool_result_budget(batch, tmp_path)
        snapshot = [tr.content for tr in batch]
        apply_tool_result_budget(batch, tmp_path)
        assert [tr.content for tr in batch] == snapshot

class TestIsSpillReadback:

    def test_readfile_inside_spill_dir(self, tmp_path: Path) -> None:
        inside = str(tmp_path / 'toolu_abc.txt')
        assert is_spill_readback('ReadFile', {'file_path': inside}, tmp_path)

    def test_readfile_outside(self, tmp_path: Path) -> None:
        assert not is_spill_readback('ReadFile', {'file_path': str(tmp_path.parent / 'main.py')}, tmp_path)

    def test_other_tool(self, tmp_path: Path) -> None:
        inside = str(tmp_path / 'toolu_abc.txt')
        assert not is_spill_readback('Bash', {'file_path': inside}, tmp_path)

    def test_missing_path(self, tmp_path: Path) -> None:
        assert not is_spill_readback('ReadFile', {}, tmp_path)

class TestComputeCompactThreshold:

    def test_auto_threshold(self) -> None:
        assert compute_compact_threshold(200000) == 167000

    def test_manual_threshold(self) -> None:
        assert compute_compact_threshold(200000, manual=True) == 177000

    def test_smaller_window(self) -> None:
        assert compute_compact_threshold(128000) == 95000

class TestExtractSummary:

    def test_extracts_between_tags(self) -> None:
        output = '<analysis>blah</analysis>\n<summary>\nthe summary\n</summary>'
        assert extract_summary(output) == 'the summary'

    def test_no_tags_returns_full(self) -> None:
        output = 'no tags here'
        assert extract_summary(output) == output

    def test_only_summary_tag(self) -> None:
        output = '<summary>just this</summary>'
        assert extract_summary(output) == 'just this'

class TestCompactCircuitBreaker:

    def test_starts_closed(self) -> None:
        breaker = CompactCircuitBreaker()
        assert not breaker.is_open()

    def test_opens_after_max_failures(self) -> None:
        breaker = CompactCircuitBreaker(max_failures=3)
        breaker.record_failure()
        breaker.record_failure()
        assert not breaker.is_open()
        breaker.record_failure()
        assert breaker.is_open()

    def test_success_resets(self) -> None:
        breaker = CompactCircuitBreaker(max_failures=3)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        assert not breaker.is_open()
        breaker.record_failure()
        assert not breaker.is_open()

class TestBuildCompactMessages:

    def test_basic_structure(self) -> None:
        msgs = build_compact_messages('the summary')
        assert len(msgs) == 1
        assert msgs[0].role == 'user'
        assert 'the summary' in msgs[0].content
        assert '早期对话的摘要' in msgs[0].content

    def test_has_keep_tail(self) -> None:
        msgs = build_compact_messages('the summary', has_keep_tail=True)
        assert '近期消息已原样保留' in msgs[0].content

    def test_transcript_path(self) -> None:
        msgs = build_compact_messages('the summary', transcript_path='/tmp/session.jsonl')
        assert '/tmp/session.jsonl' in msgs[0].content
        assert 'ReadFile' in msgs[0].content

class TestSessionDir:

    def test_ensure_creates_dir(self, tmp_path: Path) -> None:
        session_dir = ensure_session_dir(str(tmp_path))
        assert session_dir.exists()
        assert session_dir.is_dir()

    def test_cleanup(self, tmp_path: Path) -> None:
        session_dir = ensure_session_dir(str(tmp_path))
        (session_dir / 'test.txt').write_text('data')
        assert len(list(session_dir.iterdir())) == 1
        cleanup_tool_results(session_dir)
        assert session_dir.exists()
        assert len(list(session_dir.iterdir())) == 0

class TestUsageAnchor:

    def test_cold_start_falls_back_to_char_estimate(self) -> None:
        conv = ConversationManager()
        conv.add_user_message('x' * 350)
        assert conv.baseline_tokens == 0
        assert conv.current_tokens() == estimate_tokens(conv.history) == 100

    def test_anchor_aggregates_all_usage_components(self) -> None:
        conv = ConversationManager()
        conv.add_user_message('hi')
        conv.record_usage_anchor(input_tokens=1000, output_tokens=200, cache_read=5000, cache_creation=300, provider='anthropic')
        assert conv.baseline_tokens == 1000 + 5000 + 300 + 200
        assert conv.anchor_count == len(conv.history)
        assert conv.last_input_tokens == conv.baseline_tokens

    def test_current_tokens_is_baseline_plus_increment(self) -> None:
        conv = ConversationManager()
        conv.add_user_message('first turn')
        conv.record_usage_anchor(input_tokens=8000, output_tokens=100)
        baseline = conv.baseline_tokens
        assert conv.current_tokens() == baseline
        conv.add_tool_results_message([ToolResultBlock(tool_use_id='t1', content='y' * 700)])
        assert conv.current_tokens() == baseline + 200
        increment = estimate_tokens(conv.history[conv.anchor_count:])
        assert increment == 200

    def test_anchor_beats_char_estimate_after_cache_hit(self) -> None:
        conv = ConversationManager()
        conv.add_user_message('z' * 35000)
        conv.record_usage_anchor(input_tokens=200, output_tokens=50, cache_read=9000, provider='anthropic')
        assert conv.current_tokens() == 9250
        assert conv.current_tokens() < estimate_tokens(conv.history)

    def test_replace_history_resets_anchor(self) -> None:
        conv = ConversationManager()
        conv.add_user_message('old turn')
        conv.record_usage_anchor(input_tokens=9000, output_tokens=100)
        assert conv.baseline_tokens > 0
        conv.replace_history([Message(role='user', content='summary ' + 's' * 70)])
        assert conv.baseline_tokens == 0
        assert conv.anchor_count == 0
        assert conv.last_input_tokens == 0
        assert conv.current_tokens() == estimate_tokens(conv.history)

class TestEstimateTokens:

    def test_empty(self) -> None:
        assert estimate_tokens([]) == 0

    def test_counts_text_thinking_tools_and_results(self) -> None:
        from ai_agent_platform.cogent.conversation import ThinkingBlock
        msgs = [Message(role='user', content='a' * 35), Message(role='assistant', content='b' * 35, thinking_blocks=[ThinkingBlock(thinking='c' * 35, signature='sig')], tool_uses=[ToolUseBlock('id', 'Tool', {'k': 'v'})]), Message(role='user', content='', tool_results=[ToolResultBlock(tool_use_id='id', content='d' * 35)])]
        est = estimate_tokens(msgs)
        assert est >= int(140 / _CHARS_PER_TOKEN)

class TestStreamUsageCacheFields:

    def test_stream_end_carries_cache_fields(self) -> None:
        from ai_agent_platform.cogent.tools.base import StreamEnd
        end = StreamEnd(stop_reason='end_turn', input_tokens=1, output_tokens=2, cache_read=3, cache_creation=4)
        assert end.cache_read == 3 and end.cache_creation == 4

    def test_collector_propagates_cache_fields_into_response(self) -> None:
        import asyncio
        from ai_agent_platform.cogent.streaming import StreamCollector
        from ai_agent_platform.cogent.tools.base import StreamEnd

        async def _stream():
            yield StreamEnd(stop_reason='end_turn', input_tokens=1000, output_tokens=200, cache_read=5000, cache_creation=300)

        async def _run():
            collector = StreamCollector()
            async for _ in collector.consume(_stream()):
                pass
            return collector.response
        resp = asyncio.run(_run())
        assert resp.cache_read == 5000
        assert resp.cache_creation == 300
        conv = ConversationManager()
        conv.record_usage_anchor(resp.input_tokens, resp.output_tokens, resp.cache_read, resp.cache_creation, provider='anthropic')
        assert conv.baseline_tokens == 1000 + 5000 + 300 + 200

def _user(text_tokens: int) -> Message:
    return Message(role='user', content='u' * int(text_tokens * _CHARS_PER_TOKEN))

def _assistant(text_tokens: int) -> Message:
    return Message(role='assistant', content='a' * int(text_tokens * _CHARS_PER_TOKEN))

class TestComputeKeepStartIndex:

    def test_empty_history(self) -> None:
        assert _compute_keep_start_index([]) == 0

    def test_stops_at_token_floor(self) -> None:
        msgs = [_user(4000) for _ in range(10)]
        keep_start = _compute_keep_start_index(msgs)
        kept = msgs[keep_start:]
        assert len(kept) == 3
        assert keep_start == 7
        assert estimate_tokens(kept) >= KEEP_RECENT_TOKENS

    def test_message_floor_when_tail_is_tiny(self) -> None:
        msgs = [_user(50) for _ in range(20)]
        keep_start = _compute_keep_start_index(msgs)
        assert len(msgs[keep_start:]) == MIN_KEEP_MESSAGES
        assert keep_start == 20 - MIN_KEEP_MESSAGES

    def test_max_cap_stops_swallowing_history(self) -> None:
        big = _user(KEEP_MAX_TOKENS // 1000 * 1000 + 5000)
        msgs = [_user(4000) for _ in range(6)] + [big]
        keep_start = _compute_keep_start_index(msgs)
        assert keep_start == len(msgs) - 1
        assert estimate_tokens(msgs[keep_start:]) > KEEP_MAX_TOKENS

    def test_short_history_keeps_everything(self) -> None:
        msgs = [_user(50) for _ in range(3)]
        assert _compute_keep_start_index(msgs) == 0

class TestAlignKeepStartToToolPair:

    def test_orphan_tool_result_pulled_back_to_tool_use(self) -> None:
        msgs = [_user(10), _assistant(10), Message(role='assistant', content='call', tool_uses=[ToolUseBlock('t1', 'ReadFile', {})]), Message(role='user', content='', tool_results=[ToolResultBlock('t1', 'data')])]
        assert _align_keep_start_to_tool_pair(msgs, 3) == 2

    def test_non_tool_boundary_untouched(self) -> None:
        msgs = [_user(10), _assistant(10), _user(10)]
        assert _align_keep_start_to_tool_pair(msgs, 2) == 2

    def test_pairing_preserved_via_compute(self) -> None:
        msgs = [_user(4000) for _ in range(6)]
        msgs[6:6] = []
        msgs = [_user(4000), _user(4000), _user(4000), _user(4000), Message(role='assistant', content='call', tool_uses=[ToolUseBlock('tx', 'Grep', {})]), Message(role='user', content='', tool_results=[ToolResultBlock('tx', 'y' * (4000 * 3))]), _user(4000)]
        keep_start = _compute_keep_start_index(msgs)
        kept = msgs[keep_start:]
        kept_result_ids = {tr.tool_use_id for m in kept for tr in m.tool_results}
        kept_use_ids = {tu.tool_use_id for m in kept for tu in m.tool_uses}
        assert kept_result_ids <= kept_use_ids

class _SummaryClient:

    def __init__(self, summary_body: str='PREFIX SUMMARY') -> None:
        self.summary_body = summary_body
        self.summarized_history: list[Message] | None = None

    async def stream(self, conversation, system='', tools=None):
        from ai_agent_platform.cogent.tools.base import StreamEnd, TextDelta
        self.summarized_history = list(conversation.history)
        yield TextDelta(text=f'<summary>{self.summary_body}</summary>')
        yield StreamEnd(stop_reason='end_turn', input_tokens=10, output_tokens=10)

def _make_long_conversation(n_tail: int=6, tail_tokens: int=4000) -> ConversationManager:
    conv = ConversationManager()
    for i in range(8):
        conv.history.append(_user(3000))
        conv.history.append(_assistant(3000))
    for i in range(n_tail):
        conv.history.append(Message(role='user', content=f'RECENT_{i}_' + 'z' * int(tail_tokens * _CHARS_PER_TOKEN)))
    return conv

@pytest.mark.anyio
class TestAutoCompactKeepRecent:

    async def test_recent_messages_kept_verbatim(self, tmp_path: Path) -> None:
        conv = _make_long_conversation()
        keep_start = _compute_keep_start_index(conv.history)
        kept_before = list(conv.history[keep_start:])
        assert kept_before, 'fixture should keep a non-empty tail'
        client = _SummaryClient()
        conv.record_usage_anchor(input_tokens=200000)
        result = await auto_compact(conv, client, context_window=200000, session_dir=tmp_path)
        from ai_agent_platform.cogent.context.manager import CompactEvent
        assert isinstance(result, CompactEvent)
        joined = '\n'.join((m.content for m in conv.history))
        assert 'PREFIX SUMMARY' in joined
        for m in kept_before:
            assert m in conv.history

    async def test_summary_only_covers_prefix(self, tmp_path: Path) -> None:
        conv = _make_long_conversation()
        keep_start = _compute_keep_start_index(conv.history)
        kept_contents = {m.content for m in conv.history[keep_start:]}
        client = _SummaryClient()
        conv.record_usage_anchor(input_tokens=200000)
        await auto_compact(conv, client, context_window=200000, session_dir=tmp_path)
        assert client.summarized_history is not None
        summarized_contents = {m.content for m in client.summarized_history}
        assert not kept_contents & summarized_contents

    async def test_tool_pair_not_split(self, tmp_path: Path) -> None:
        conv = ConversationManager()
        for i in range(8):
            conv.history.append(_user(3000))
            conv.history.append(_assistant(3000))
        conv.history.append(Message(role='assistant', content='calling', tool_uses=[ToolUseBlock('tk', 'Grep', {})]))
        conv.history.append(Message(role='user', content='', tool_results=[ToolResultBlock('tk', 'RESULT_DATA')]))
        conv.record_usage_anchor(input_tokens=200000)
        client = _SummaryClient()
        await auto_compact(conv, client, context_window=200000, session_dir=tmp_path)
        result_ids = {tr.tool_use_id for m in conv.history for tr in m.tool_results}
        use_ids = {tu.tool_use_id for m in conv.history for tu in m.tool_uses}
        assert result_ids <= use_ids

    async def test_anchor_reset_after_compact(self, tmp_path: Path) -> None:
        conv = _make_long_conversation()
        conv.record_usage_anchor(input_tokens=200000)
        assert conv.baseline_tokens > 0 and conv.anchor_count > 0
        client = _SummaryClient()
        await auto_compact(conv, client, context_window=200000, session_dir=tmp_path)
        assert conv.baseline_tokens == 0
        assert conv.anchor_count == 0
        assert conv.last_input_tokens == 0

    async def test_too_few_messages_degrades_to_no_compaction(self, tmp_path: Path) -> None:
        conv = ConversationManager()
        for i in range(3):
            conv.history.append(Message(role='user', content=f'ONLY_{i}_' + 'z' * 100))
        before = list(conv.history)
        client = _SummaryClient()
        result = await auto_compact(conv, client, context_window=200000, session_dir=tmp_path, manual=True)
        assert result is None
        assert conv.history == before
        assert client.summarized_history is None

    async def test_event_carries_boundary_summary_and_keep(self, tmp_path: Path) -> None:
        conv = _make_long_conversation()
        keep_start = _compute_keep_start_index(conv.history)
        kept_before = list(conv.history[keep_start:])
        client = _SummaryClient()
        conv.record_usage_anchor(input_tokens=200000)
        result = await auto_compact(conv, client, context_window=200000, session_dir=tmp_path)
        from ai_agent_platform.cogent.context.manager import CompactEvent
        assert isinstance(result, CompactEvent)
        assert result.boundary is not None
        assert result.boundary.summary == 'PREFIX SUMMARY'
        assert result.boundary.keep == kept_before
