from __future__ import annotations

from typing import Any


class ToolMessagePairingError(ValueError):
    code = "tool_message_pairing_invalid"


def tool_call_ids(calls: list[dict[str, Any]]) -> set[str]:
    ids = [call.get("call_id") for call in calls]
    if any(not isinstance(value, str) or not value.strip() for value in ids):
        raise ToolMessagePairingError("Tool calls require nonempty call IDs")
    if len(ids) != len(set(ids)):
        raise ToolMessagePairingError("A tool batch contains duplicate call IDs")
    return set(ids)


def ordered_tool_messages(
    messages: list[dict[str, Any]], *, restore_delayed_results: bool = False,
) -> list[dict[str, Any]]:
    """Check complete batches; recovery may move intervening user messages.

    Only recorded results can satisfy a call. Never invent results or cross an
    assistant/system boundary while repairing a persisted transcript.
    """
    ordered: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.get("role") == "tool":
            raise ToolMessagePairingError("Tool result has no pending assistant call")
        calls = message.get("tool_calls") or []
        ordered.append(message)
        index += 1
        if not calls:
            continue
        if message.get("role") != "assistant":
            raise ToolMessagePairingError("Only assistant messages may issue tool calls")
        pending = tool_call_ids(calls)
        delayed_users = []
        while pending and index < len(messages):
            result = messages[index]
            if result.get("role") == "tool":
                call_id = result.get("call_id")
                if call_id not in pending:
                    raise ToolMessagePairingError("Tool result ID is duplicate or does not match its batch")
                pending.remove(call_id)
                ordered.append(result)
            elif (restore_delayed_results and result.get("role") == "user"
                  and not result.get("tool_calls")):
                delayed_users.append(result)
            else:
                break
            index += 1
        if pending:
            raise ToolMessagePairingError("Assistant tool calls must be followed by every tool result before another message")
        ordered.extend(delayed_users)
    return ordered
