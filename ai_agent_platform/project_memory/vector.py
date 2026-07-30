"""Rebuildable dense index for project memories."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from ai_agent_platform.integrations.rag import EmbeddingProvider
from ai_agent_platform.project_memory.models import ProjectMemory


class MemoryVectorStore(Protocol):
    def upsert(self, memory: ProjectMemory, embedding: list[float]) -> None:
        ...

    def delete(self, memory_id: str) -> None:
        ...

    def search(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        query_embedding: list[float],
        limit: int,
    ) -> list[tuple[str, float, int]]:
        ...

    def list_indexed(self, *, workspace_id: str) -> dict[str, tuple[int, int]]:
        ...


@dataclass(frozen=True)
class _VectorRow:
    memory_id: str
    workspace_id: str
    workspace_revision: int
    version: int
    embedding: list[float]


class InMemoryMemoryVectorStore:
    def __init__(self) -> None:
        self._rows: dict[str, _VectorRow] = {}
        self._lock = Lock()

    def upsert(self, memory: ProjectMemory, embedding: list[float]) -> None:
        with self._lock:
            self._rows[memory.id] = _VectorRow(
                memory_id=memory.id,
                workspace_id=memory.workspace_id,
                workspace_revision=memory.workspace_revision,
                version=memory.version,
                embedding=list(embedding),
            )

    def delete(self, memory_id: str) -> None:
        with self._lock:
            self._rows.pop(memory_id, None)

    def search(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        query_embedding: list[float],
        limit: int,
    ) -> list[tuple[str, float, int]]:
        with self._lock:
            rows = list(self._rows.values())
        scored = [
            (
                row.memory_id,
                _cosine(query_embedding, row.embedding),
                row.version,
            )
            for row in rows
            if row.workspace_id == workspace_id
            and row.workspace_revision == workspace_revision
        ]
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:limit]

    def list_indexed(self, *, workspace_id: str) -> dict[str, tuple[int, int]]:
        with self._lock:
            return {
                row.memory_id: (row.workspace_revision, row.version)
                for row in self._rows.values()
                if row.workspace_id == workspace_id
            }


class QdrantMemoryVectorStore:
    """Qdrant collection containing only IDs, scope metadata, and vectors."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None,
        collection_name: str,
    ) -> None:
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise RuntimeError("qdrant-client is required for project memory") from exc
        self._client = (
            QdrantClient(location=":memory:")
            if url == ":memory:"
            else QdrantClient(url=url, api_key=api_key)
        )
        self._collection_name = collection_name
        self._vector_size: int | None = None

    def upsert(self, memory: ProjectMemory, embedding: list[float]) -> None:
        if not embedding:
            raise ValueError("memory embedding must not be empty")
        self._ensure_collection(len(embedding))
        PointStruct = _qdrant_model("PointStruct")
        self._client.upsert(
            collection_name=self._collection_name,
            points=[
                PointStruct(
                    id=_point_id(memory.id),
                    vector=embedding,
                    payload={
                        "memory_id": memory.id,
                        "workspace_id": memory.workspace_id,
                        "workspace_revision": memory.workspace_revision,
                        "version": memory.version,
                    },
                )
            ],
        )

    def delete(self, memory_id: str) -> None:
        if not self._collection_exists():
            return
        self._client.delete(
            collection_name=self._collection_name,
            points_selector=[_point_id(memory_id)],
        )

    def search(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        query_embedding: list[float],
        limit: int,
    ) -> list[tuple[str, float, int]]:
        if not self._collection_exists():
            return []
        FieldCondition = _qdrant_model("FieldCondition")
        Filter = _qdrant_model("Filter")
        MatchValue = _qdrant_model("MatchValue")
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="workspace_id",
                    match=MatchValue(value=workspace_id),
                ),
                FieldCondition(
                    key="workspace_revision",
                    match=MatchValue(value=workspace_revision),
                ),
            ]
        )
        if hasattr(self._client, "search"):
            points = self._client.search(
                collection_name=self._collection_name,
                query_vector=query_embedding,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        else:
            points = self._client.query_points(
                collection_name=self._collection_name,
                query=query_embedding,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            ).points
        return [
            (
                str((point.payload or {})["memory_id"]),
                float(point.score),
                int((point.payload or {}).get("version", 0)),
            )
            for point in points
            if (point.payload or {}).get("memory_id")
        ]

    def list_indexed(self, *, workspace_id: str) -> dict[str, tuple[int, int]]:
        if not self._collection_exists():
            return {}
        FieldCondition = _qdrant_model("FieldCondition")
        Filter = _qdrant_model("Filter")
        MatchValue = _qdrant_model("MatchValue")
        workspace_filter = Filter(
            must=[
                FieldCondition(
                    key="workspace_id",
                    match=MatchValue(value=workspace_id),
                )
            ]
        )
        indexed: dict[str, tuple[int, int]] = {}
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection_name,
                scroll_filter=workspace_filter,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                memory_id = payload.get("memory_id")
                if memory_id:
                    indexed[str(memory_id)] = (
                        int(payload.get("workspace_revision", 0)),
                        int(payload.get("version", 0)),
                    )
            if offset is None:
                break
        return indexed

    def _ensure_collection(self, vector_size: int) -> None:
        if self._vector_size is not None:
            if self._vector_size != vector_size:
                raise ValueError("project memory vector size mismatch")
            return
        if self._collection_exists():
            info = self._client.get_collection(collection_name=self._collection_name)
            configured = _vector_size(info)
            if configured is not None and configured != vector_size:
                raise ValueError("project memory vector size mismatch")
            self._vector_size = configured or vector_size
            return
        Distance = _qdrant_model("Distance")
        VectorParams = _qdrant_model("VectorParams")
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        self._vector_size = vector_size

    def _collection_exists(self) -> bool:
        return any(
            item.name == self._collection_name
            for item in self._client.get_collections().collections
        )


def memory_text(memory: ProjectMemory) -> str:
    return f"{memory.kind}\n{memory.title}\n{memory.content}"


def embed_memory(
    embedding_provider: EmbeddingProvider, memory: ProjectMemory
) -> list[float]:
    return embedding_provider.embed_texts(
        [memory_text(memory)], task_type="document"
    )[0]


def _point_id(memory_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"project-memory:{memory_id}"))


def _qdrant_model(name: str):
    from qdrant_client import models

    return getattr(models, name)


def _vector_size(collection_info: object) -> int | None:
    config = getattr(collection_info, "config", None)
    params = getattr(config, "params", None)
    vectors = getattr(params, "vectors", None)
    size = getattr(vectors, "size", None)
    return int(size) if size is not None else None


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right))


__all__ = [
    "InMemoryMemoryVectorStore",
    "MemoryVectorStore",
    "QdrantMemoryVectorStore",
    "embed_memory",
    "memory_text",
]
