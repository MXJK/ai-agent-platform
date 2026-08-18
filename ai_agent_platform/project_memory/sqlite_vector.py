"""Rebuildable small-data dense index stored in the local SQLite file."""

from __future__ import annotations

from array import array
from datetime import datetime, timezone
import math

from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.project_memory.models import ProjectMemory


class SQLiteMemoryVectorStore:
    def __init__(self, *, database: LocalStateDatabase, model: str) -> None:
        self.database = database
        self.model = model

    def upsert(self, memory: ProjectMemory, embedding: list[float]) -> None:
        if not embedding:
            raise ValueError("memory embedding must not be empty")
        payload = array("f", (float(value) for value in embedding)).tobytes()
        with self.database.transaction(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO project_memory_vectors VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    workspace_id=excluded.workspace_id,
                    workspace_revision=excluded.workspace_revision,
                    memory_version=excluded.memory_version,
                    dimensions=excluded.dimensions,
                    model=excluded.model,
                    embedding=excluded.embedding,
                    updated_at=excluded.updated_at
                """,
                (
                    memory.id, memory.workspace_id, memory.workspace_revision,
                    memory.version, len(embedding), self.model, payload,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def delete(self, memory_id: str) -> None:
        with self.database.transaction(immediate=True) as conn:
            conn.execute("DELETE FROM project_memory_vectors WHERE memory_id = ?", (memory_id,))

    def search(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        query_embedding: list[float],
        limit: int,
    ) -> list[tuple[str, float, int]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT memory_id, memory_version, dimensions, embedding "
                "FROM project_memory_vectors WHERE workspace_id = ? AND workspace_revision = ?",
                (workspace_id, workspace_revision),
            ).fetchall()
        scored = []
        for row in rows:
            dimensions = int(row["dimensions"])
            if dimensions != len(query_embedding):
                continue
            values = array("f")
            values.frombytes(bytes(row["embedding"]))
            scored.append(
                (str(row["memory_id"]), _cosine(query_embedding, values), int(row["memory_version"]))
            )
        return sorted(scored, key=lambda item: (-item[1], item[0]))[:limit]

    def list_indexed(self, *, workspace_id: str) -> dict[str, tuple[int, int]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT memory_id, workspace_revision, memory_version "
                "FROM project_memory_vectors WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall()
        return {
            str(row["memory_id"]): (int(row["workspace_revision"]), int(row["memory_version"]))
            for row in rows
        }


def _cosine(left, right) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


__all__ = ["SQLiteMemoryVectorStore"]
