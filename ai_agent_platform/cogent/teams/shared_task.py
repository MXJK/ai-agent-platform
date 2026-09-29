from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4


VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "blocked"})


@dataclass
class SharedTask:
    id: str
    title: str
    description: str = ""
    status: str = "pending"
    assignee: str = ""
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    created_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SharedTask":
        return cls(
            **{
                key: item
                for key, item in value.items()
                if key in cls.__dataclass_fields__
            }
        )


class SharedTaskStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def initialize(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._save(1, {})

    def create(
        self,
        *,
        title: str,
        description: str = "",
        assignee: str = "",
        blocks: list[str] | None = None,
        blocked_by: list[str] | None = None,
        created_by: str = "",
    ) -> SharedTask:
        if not title.strip():
            raise ValueError("task title is required")
        with self._lock:
            next_id, tasks = self._load()
            task = SharedTask(
                id=str(next_id),
                title=title.strip(),
                description=description,
                assignee=assignee,
                blocks=list(dict.fromkeys(blocks or [])),
                blocked_by=list(dict.fromkeys(blocked_by or [])),
                created_by=created_by,
            )
            tasks[task.id] = task
            self._save(next_id + 1, tasks)
            return task

    def get(self, task_id: str) -> SharedTask | None:
        with self._lock:
            _, tasks = self._load()
            return tasks.get(task_id)

    def list(self, *, status: str | None = None, assignee: str | None = None) -> list[SharedTask]:
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"invalid task status: {status}")
        with self._lock:
            _, tasks = self._load()
        values = list(tasks.values())
        if status:
            values = [task for task in values if task.status == status]
        if assignee:
            values = [task for task in values if task.assignee == assignee]
        return values

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        assignee: str | None = None,
        description: str | None = None,
        add_blocks: list[str] | None = None,
        add_blocked_by: list[str] | None = None,
    ) -> SharedTask | None:
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"invalid task status: {status}")
        with self._lock:
            next_id, tasks = self._load()
            task = tasks.get(task_id)
            if task is None:
                return None
            if status is not None:
                task.status = status
            if assignee is not None:
                task.assignee = assignee
            if description is not None:
                task.description = description
            task.blocks = list(dict.fromkeys([*task.blocks, *(add_blocks or [])]))
            task.blocked_by = list(
                dict.fromkeys([*task.blocked_by, *(add_blocked_by or [])])
            )
            self._save(next_id, tasks)
            return task

    def _load(self) -> tuple[int, dict[str, SharedTask]]:
        if not self.path.exists():
            return 1, {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"team task store is unreadable: {self.path}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("tasks", []), list):
            raise RuntimeError(f"team task store is malformed: {self.path}")
        tasks = {
            task.id: task
            for item in value.get("tasks", [])
            if isinstance(item, dict)
            for task in [SharedTask.from_dict(item)]
        }
        return int(value.get("next_id") or 1), tasks

    def _save(self, next_id: int, tasks: dict[str, SharedTask]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "next_id": next_id,
                    "tasks": [task.to_dict() for task in tasks.values()],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
