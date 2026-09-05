"""Deterministic model fixture for explicitly isolated, fake-provider evaluations.

This fixture sees only the prompt, available tool schemas and actual tool results.
It never loads case expectations, reference trajectories or grading thresholds.
Its scores measure runtime/evaluator plumbing, not real-model coding quality.
"""
from __future__ import annotations

import json
import re
from uuid import uuid4

from ai_agent_platform.integrations.llm import LLMToolDecision, LLMUsage
from ai_agent_platform.integrations.tools import ToolCall


# A deliberately small simulated model window exercises real compaction.
CONTEXT_WINDOW_TOKENS = 16000

def decide(messages, tools, *, model, on_delta=None):
    names = {tool.name for tool in tools}
    users = [m for m in messages if m.get('role') == 'user']
    question = str(users[0].get('content') or '') if users else ''
    summary = question if users and users[0].get('cogent_compact_boundary') else ''
    if summary:
        original = re.search(r'\[user\]: ([^\n]+)', summary)
        question = original.group(1) if original else question
    results = [m['content'] for m in messages if m.get('role') == 'tool'
               and isinstance(m.get('content'), dict)]
    reads = [r['result'] for r in results if r.get('name') == 'ReadFile' and r.get('ok')
             and isinstance(r.get('result'), dict)]
    calls, answer = [], ''

    def call(name, **arguments):
        if name in names:
            calls.append(ToolCall(name, arguments, f'eval_{uuid4().hex[:12]}', 'offline_fixture'))

    if not tools:
        # Keep the source-bearing lines of a genuine compaction request.
        answer = '<summary>' + '\n'.join(line for line in question.splitlines()
                                             if 'filler line' not in line)[:12000] + '</summary>'
    elif not results and not summary:
        identifiers = re.findall(r'\b[A-Za-z]+_[A-Za-z_]+\b|\b[A-Z][A-Za-z]+Service\b', question)
        if (identifiers or 'resume' in question) and '支持' not in question:
            call('Grep', pattern=identifiers[0] if identifiers else 'resume')
        else:
            call('Glob', pattern='*')
    elif any(marker in question for marker in ('帮我实现', '修改', '补测试')):
        if not reads:
            listing = next((r.get('result', {}).get('files', []) for r in results if r.get('name') == 'Glob'), [])
            target = next((p for p in listing if 'orders.py' in p or 'agent_runs.py' in p), 'README.md')
            call('ReadFile', file_path=target)
        else:
            target = reads[-1]
            call('WriteFile', file_path=target['path'], content=target['content'] + '\n# Proposed fixture change\n')
    else:
        candidates = []
        for result in results:
            body = result.get('result') or {}
            if result.get('name') == 'Grep':
                candidates.extend(match.get('path', '') for match in body.get('matches', []) if isinstance(match, dict))
            if result.get('name') == 'Glob':
                files = body.get('files', [])
                if 'service' in question or '服务' in question:
                    candidates.extend(p for p in files if ('/services/' in p or '/api/' in p) and ('逐一' in question or '每个' in question or any(t in p for t in ('orders', 'billing', 'inventory', 'routes'))))
                elif 'resume' in question:
                    candidates.extend(p for p in files if p.endswith('.py'))
                else:
                    candidates.extend(p for p in files if p == 'README.md')
        for read in reads:
            candidates.extend(re.findall(r'\bdocs/[A-Za-z0-9_./-]+\.md\b', read.get('content', '')))
        read_paths = {r.get('path') for r in reads}
        if summary:
            read_paths.update(re.findall(r'\"path\": \"([^\"]+)\"', summary))
        failed = any(not r.get('ok') for r in results)
        # A failed first read falls back to another discovered source before retrying.
        attempted_paths = {str(m.get('file_path')) for msg in messages for c in msg.get('tool_calls', [])
                           if c.get('name') == 'ReadFile' for m in [c.get('arguments', {})]}
        unread = [p for p in dict.fromkeys(candidates) if p and p not in read_paths and p not in attempted_paths]
        if failed and not unread and not reads:
            if not any(r.get('name') == 'Glob' for r in results):
                call('Glob', pattern='*')
            else:
                unread = ['README.md'] if 'README.md' not in attempted_paths else []
        for path in unread[:2]:
            call('ReadFile', file_path=path)
        if not calls:
            answer = '\n\n'.join(
                f"{r['path']}:{r.get('start_line', 1)}\n" + '\n'.join(
                    line for line in r.get('content', '').splitlines()[:12] if 'filler line' not in line)
                for r in reads
            ) or '未在本次已读取的仓库证据中找到相关定义。'
    if answer and on_delta:
        on_delta(answer)
    input_tokens = max(1, len(json.dumps(messages, ensure_ascii=False)) // 4)
    output_tokens = max(1, (len(answer) + len(json.dumps([c.arguments for c in calls]))) // 4)
    return LLMToolDecision(text=answer, tool_calls=calls, provider='fake', model=model,
                           usage=LLMUsage(input_tokens, output_tokens),
                           stop_reason='tool_use' if calls else 'end_turn')
