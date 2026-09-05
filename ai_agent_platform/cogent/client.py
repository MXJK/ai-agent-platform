from __future__ import annotations

import asyncio
import json
from typing import Any

from ai_agent_platform.integrations.llm import LLMClient, LLMToolDecision
from ai_agent_platform.integrations.tools import ToolSpec
from .tools.base import TextDelta, ThinkingDelta, StreamEnd, ToolCallComplete


class RegistryClient:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def resolve_context_budget(self, **kwargs):
        return self.client.resolve_context_budget(**kwargs)

    def decide_tools(self, messages, tools, **kwargs):
        offline_evaluation = kwargs.pop('offline_evaluation', False)
        resolver = getattr(self.client, 'resolve_context_budget', None)
        budget = resolver() if callable(resolver) else None
        if budget is None or budget.provider != 'fake':
            return self.client.decide_tools(messages, tools, **kwargs)
        if offline_evaluation:
            from ai_agent_platform.evaluation.offline_model import decide
            return decide(messages, tools, model=budget.model, on_delta=kwargs.get('on_delta'))
        # Fake has no native tool capability in the existing registry. Keep its
        # normal text protocol and ledger instead of relaxing model routing.
        text = []
        usage = None
        route_trace = None
        provider, model = budget.provider, budget.model
        transcript = [
            {'role': item['role'] if item['role'] in {'system', 'user', 'assistant'} else 'user',
             'content': item['content'] if isinstance(item.get('content'), str) else json.dumps(item.get('content'), ensure_ascii=False)}
            for item in messages
        ]
        for event in self.client.stream_chat(transcript, provider=provider, model=model):
            if event.type == 'delta':
                text.append(event.text)
                if kwargs.get('on_delta'):
                    kwargs['on_delta'](event.text)
            elif event.type == 'usage':
                usage = event.usage
            elif event.type == 'route':
                route_trace = event.route_trace
                provider, model = event.provider, event.model
        return LLMToolDecision(text=''.join(text), tool_calls=[], provider=provider or 'fake',
                               model=model or '', usage=usage, stop_reason='end_turn', route_trace=route_trace)

    async def stream(self, conversation, system='', tools=None, **kwargs):
        from .serialization import build_registry_messages
        from ai_agent_platform.integrations.public_reasoning import displayable_reasoning_scope
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        done = object()
        messages = build_registry_messages(conversation.history)
        if system:
            messages = [{'role': 'system', 'content': system},
                        *[item for item in messages if item['role'] != 'system']]

        def publish(event):
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def produce():
            try:
                specs = [item for item in tools or [] if isinstance(item, ToolSpec)]
                with displayable_reasoning_scope(lambda provider, text: publish(ThinkingDelta(text, True))):
                    decision = self.decide_tools(
                        messages, specs,
                        **{'disable_tool_calls': not specs, **kwargs},
                        on_delta=lambda text: publish(TextDelta(text)),
                    )
                for call in decision.tool_calls:
                    publish(ToolCallComplete(call.call_id, call.name, call.arguments))
                usage = decision.usage
                publish(StreamEnd(
                    decision.stop_reason or '', usage.input_tokens if usage else 0,
                    usage.output_tokens if usage else 0, int(usage.cached_input_tokens or 0) if usage else 0,
                    int(usage.cache_write_tokens or 0) if usage else 0, usage.thoughts_tokens if usage else 0, decision,
                ))
            except BaseException as exc:
                publish(exc)
            finally:
                publish(done)

        worker = asyncio.create_task(asyncio.to_thread(produce))
        try:
            while True:
                event = await queue.get()
                if event is done:
                    break
                if isinstance(event, BaseException):
                    raise event
                yield event
        finally:
            await worker
