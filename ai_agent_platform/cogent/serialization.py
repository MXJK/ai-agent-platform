from __future__ import annotations
import json
from typing import Any
from ai_agent_platform.cogent.conversation import Message


def build_registry_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Serialize canonical turns into the platform's provider-routing contract.

    Native items retain their owner so adapters can replay signed blocks only
    to that provider. Tool results stay paired by ID across provider switches.
    """
    from copy import deepcopy
    result = []
    for message in messages:
        item = deepcopy(message.metadata)
        if message.tool_results:
            for block in message.tool_results:
                content = item.get('content', block.content)
                result.append({**item, 'role': 'tool', 'call_id': block.tool_use_id,
                               'content': content, 'is_error': block.is_error})
            continue
        item.update(role=message.role, content=message.content)
        if message.provider:
            item['provider'] = message.provider
        if message.tool_uses:
            originals = {call.get('call_id'): call for call in item.get('tool_calls', [])}
            item['tool_calls'] = [
                {**originals.get(block.tool_use_id, {}), 'call_id': block.tool_use_id,
                 'name': block.tool_name, 'arguments': deepcopy(block.arguments)}
                for block in message.tool_uses
            ]
        if message.thinking_blocks and 'provider_items' not in item:
            item['provider_items'] = [deepcopy(block.native) for block in message.thinking_blocks
                                      if block.native and block.provider == message.provider]
        result.append(item)
    return result

def build_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for m in messages:
        if m.tool_uses or m.thinking_blocks:
            content: list[dict[str, Any]] = []
            for tb in m.thinking_blocks:
                if tb.provider == 'anthropic':
                    content.append(tb.native or {'type': 'thinking', 'thinking': tb.thinking, 'signature': tb.signature})
            if m.content:
                content.append({'type': 'text', 'text': m.content})
            for tu in m.tool_uses:
                content.append({'type': 'tool_use', 'id': tu.tool_use_id, 'name': tu.tool_name, 'input': tu.arguments})
            if not content:
                content.append({'type': 'text', 'text': ''})
            result.append({'role': 'assistant', 'content': content})
        elif m.tool_results:
            content = []
            for tr in m.tool_results:
                body: Any = tr.content_blocks if tr.content_blocks else tr.content
                content.append({'type': 'tool_result', 'tool_use_id': tr.tool_use_id, 'content': body, 'is_error': tr.is_error})
            result.append({'role': 'user', 'content': content})
        elif m.role == 'user' and result and (result[-1]['role'] == 'user') and isinstance(result[-1]['content'], str):
            result[-1]['content'] = result[-1]['content'] + '\n' + m.content
        else:
            result.append({'role': m.role, 'content': m.content})
    return result

def build_openai_input(messages: list[Message]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for m in messages:
        if m.tool_uses:
            for tb in m.thinking_blocks or []:
                if tb.provider == 'openai' and tb.native and tb.native.get('type') == 'reasoning':
                    result.append(dict(tb.native))
            if m.content:
                result.append({'role': 'assistant', 'content': m.content})
            for tu in m.tool_uses:
                result.append({'type': 'function_call', 'name': tu.tool_name, 'call_id': tu.tool_use_id, 'arguments': json.dumps(tu.arguments)})
        elif m.tool_results:
            for tr in m.tool_results:
                result.append({'type': 'function_call_output', 'call_id': tr.tool_use_id, 'output': tr.content})
        else:
            for tb in m.thinking_blocks or []:
                if tb.provider == 'openai' and tb.native and tb.native.get('type') == 'reasoning':
                    result.append(dict(tb.native))
            result.append({'role': m.role, 'content': m.content})
    return result

def build_chat_completion_messages(messages: list[Message], *, provider: str='openai-compat') -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for m in messages:
        reasoning = ''.join(tb.thinking for tb in m.thinking_blocks if tb.provider == provider)
        if m.tool_uses:
            tool_calls = []
            for tu in m.tool_uses:
                tool_calls.append({'id': tu.tool_use_id, 'type': 'function', 'function': {'name': tu.tool_name, 'arguments': json.dumps(tu.arguments)}})
            msg: dict[str, Any] = {'role': 'assistant', 'content': m.content or None, 'tool_calls': tool_calls}
            if reasoning:
                msg['reasoning_content'] = reasoning
            result.append(msg)
        elif m.tool_results:
            for tr in m.tool_results:
                result.append({'role': 'tool', 'tool_call_id': tr.tool_use_id, 'content': tr.content})
        else:
            msg = {'role': m.role, 'content': m.content}
            if reasoning:
                msg['reasoning_content'] = reasoning
            result.append(msg)
    return result

def build_messages(messages: list[Message], protocol: str='anthropic') -> list[dict[str, Any]]:
    if protocol == 'openai':
        return build_openai_input(messages)
    if protocol == 'openai-compat':
        return build_chat_completion_messages(messages)
    return build_anthropic_messages(messages)
