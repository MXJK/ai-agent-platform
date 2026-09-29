from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
from threading import RLock
import time
from typing import Any, Callable
from uuid import uuid4


@dataclass
class MailboxMessage:
    from_agent: str
    text: str
    timestamp: str = ""
    read: bool = False
    type: str = "text"
    request_id: str = ""
    approve: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "from": self.from_agent,
            "text": self.text,
            "timestamp": self.timestamp,
            "read": self.read,
        }
        if self.type != "text":
            value["type"] = self.type
        if self.request_id:
            value["requestId"] = self.request_id
        if self.approve is not None:
            value["approve"] = self.approve
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MailboxMessage":
        return cls(
            from_agent=str(value.get("from") or ""),
            text=str(value.get("text") or ""),
            timestamp=str(value.get("timestamp") or ""),
            read=bool(value.get("read", False)),
            type=str(value.get("type") or "text"),
            request_id=str(value.get("requestId") or ""),
            approve=value.get("approve") if isinstance(value.get("approve"), bool) else None,
        )


class Mailbox:
    def __init__(self, base_dir: str | Path, *, lock_timeout: float = 5.0) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_timeout = lock_timeout
        self._lock = RLock()

    def write(self, recipient: str, message: MailboxMessage) -> None:
        def append(messages: list[MailboxMessage]) -> list[MailboxMessage]:
            message.read = False
            message.timestamp = message.timestamp or datetime.now(timezone.utc).isoformat()
            messages.append(message)
            return messages

        self._mutate(recipient, append)

    def read(self, recipient: str) -> list[MailboxMessage]:
        return [message for message in self._read(recipient) if not message.read]

    def consume(self, recipient: str) -> list[MailboxMessage]:
        # Ordinary Agent turns poll collaboration notifications. Keep an empty
        # mailbox read-only so runs do not require write access merely to prove
        # that no notification exists.
        if not self.read(recipient):
            return []
        unread: list[MailboxMessage] = []

        def mark(messages: list[MailboxMessage]) -> list[MailboxMessage]:
            for message in messages:
                if not message.read:
                    unread.append(message)
                    message.read = True
            return messages

        self._mutate(recipient, mark)
        return unread

    def broadcast(
        self,
        recipients: list[str],
        message: MailboxMessage,
        *,
        exclude: str = "",
    ) -> int:
        count = 0
        for recipient in recipients:
            if recipient == exclude:
                continue
            self.write(recipient, MailboxMessage.from_dict(message.to_dict()))
            count += 1
        return count

    def cleanup(self) -> None:
        for path in self.base_dir.glob("*.json*"):
            if path.is_file() and not path.is_symlink():
                path.unlink(missing_ok=True)

    def _path(self, recipient: str) -> Path:
        safe = "".join(character for character in recipient if character.isalnum() or character in "-_")
        if not safe or safe != recipient:
            raise ValueError("mailbox recipient has invalid characters")
        return self.base_dir / f"{safe}.json"

    def _read(self, recipient: str) -> list[MailboxMessage]:
        path = self._path(recipient)
        if not path.exists():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"mailbox is unreadable: {recipient}") from exc
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise RuntimeError(f"mailbox is malformed: {recipient}")
        return [MailboxMessage.from_dict(item) for item in value]

    def _mutate(
        self,
        recipient: str,
        operation: Callable[[list[MailboxMessage]], list[MailboxMessage]],
    ) -> None:
        path = self._path(recipient)
        lock_path = path.with_suffix(".json.lock")
        with self._lock:
            deadline = time.monotonic() + self.lock_timeout
            backoff = 0.005
            while True:
                try:
                    descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(descriptor)
                    break
                except FileExistsError:
                    try:
                        if time.time() - lock_path.stat().st_mtime > 10:
                            lock_path.unlink(missing_ok=True)
                            continue
                    except OSError:
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"mailbox lock timeout: {recipient}")
                    time.sleep(backoff + random.uniform(0, backoff))
                    backoff = min(backoff * 2, 0.08)
            try:
                messages = operation(self._read(recipient))
                _atomic_json(path, [message.to_dict() for message in messages])
            finally:
                lock_path.unlink(missing_ok=True)


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def create_message(
    from_agent: str,
    text: str,
    *,
    message_type: str = "text",
    request_id: str = "",
    approve: bool | None = None,
) -> MailboxMessage:
    return MailboxMessage(
        from_agent=from_agent,
        text=text,
        timestamp=datetime.now(timezone.utc).isoformat(),
        type=message_type,
        request_id=request_id,
        approve=approve,
    )
