from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
import re
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

import httpx

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.llm import LLMClient


SUPPORTED_TEXT_EXTENSIONS = {
    ".css",
    ".go",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".markdown",
    ".py",
    ".rs",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class ParsedDocument:
    id: str
    knowledge_base_id: str
    filename: str
    text: str
    source_uri: str | None = None


@dataclass(frozen=True)
class DocumentChunk:
    id: str
    knowledge_base_id: str
    document_id: str
    filename: str
    chunk_index: int
    text: str


@dataclass(frozen=True)
class RetrievedDocument:
    id: str
    knowledge_base_id: str
    document_id: str
    filename: str
    chunk_index: int
    text: str
    score: float
    recall_score: float | None = None
    rerank_score: float | None = None


@dataclass(frozen=True)
class IngestedDocument:
    knowledge_base_id: str
    document_id: str
    filename: str
    chunk_count: int


@dataclass(frozen=True)
class RAGAnswer:
    answer: str
    citations: list[RetrievedDocument]


class EmbeddingProvider(Protocol):
    def embed_texts(
        self,
        texts: list[str],
        *,
        task_type: str = "document",
    ) -> list[list[float]]:
        ...


class VectorStore(Protocol):
    def delete_document(self, *, document_id: str) -> None:
        ...

    def upsert_chunks(
        self,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        ...

    def search(
        self,
        *,
        knowledge_base_id: str,
        query_embedding: list[float],
        limit: int,
    ) -> list[RetrievedDocument]:
        ...


class DocumentStore(Protocol):
    def save_document(
        self,
        document: ParsedDocument,
        chunks: list[DocumentChunk],
    ) -> None:
        ...


class Reranker(Protocol):
    def rerank(
        self,
        *,
        query: str,
        candidates: list[RetrievedDocument],
        limit: int,
    ) -> list[RetrievedDocument]:
        ...


class TextDocumentParser:
    """Parses first-version text documents.

    TODO: Extend this parser boundary for more document types such as PDF,
    Word, HTML, OCR output, tables, and crawled web pages.

    Production systems usually add PDF, HTML, docx, OCR, tables, and image
    extraction here. The first version keeps this boundary small and explicit.
    """

    def parse(
        self,
        *,
        knowledge_base_id: str,
        filename: str,
        content: str,
        source_uri: str | None = None,
    ) -> ParsedDocument:
        extension = _file_extension(filename)
        if extension not in SUPPORTED_TEXT_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_TEXT_EXTENSIONS))
            raise RAGValidationError(
                f"unsupported document type: {extension or '<none>'}; "
                f"supported types: {supported}"
            )

        text = _normalize_text(content)
        if not text:
            raise RAGValidationError("document text is empty")

        return ParsedDocument(
            id=_document_id(
                knowledge_base_id=knowledge_base_id,
                filename=filename,
                source_uri=source_uri,
            ),
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            text=text,
            source_uri=source_uri,
        )


class RecursiveCharacterChunker:
    def __init__(self, *, chunk_size: int = 800, chunk_overlap: int = 120) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def split(self, document: ParsedDocument) -> list[DocumentChunk]:
        text = document.text
        chunks: list[DocumentChunk] = []
        start = 0
        text_length = len(text)

        while start < text_length:
            raw_end = min(start + self._chunk_size, text_length)
            end = self._best_breakpoint(text, start, raw_end)
            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(
                    DocumentChunk(
                        id=_chunk_id(
                            document_id=document.id,
                            chunk_index=len(chunks),
                        ),
                        knowledge_base_id=document.knowledge_base_id,
                        document_id=document.id,
                        filename=document.filename,
                        chunk_index=len(chunks),
                        text=chunk_text,
                    )
                )
            if end >= text_length:
                break
            start = max(end - self._chunk_overlap, start + 1)

        return chunks

    def _best_breakpoint(self, text: str, start: int, raw_end: int) -> int:
        if raw_end >= len(text):
            return raw_end

        min_end = start + max(int(self._chunk_size * 0.6), 1)
        candidates = [
            text.rfind("\n\n", start, raw_end),
            text.rfind("\n", start, raw_end),
            text.rfind("。", start, raw_end),
            text.rfind(".", start, raw_end),
            text.rfind(" ", start, raw_end),
        ]
        for candidate in candidates:
            if candidate >= min_end:
                return candidate + 1
        return raw_end


class HashingEmbeddingProvider:
    """Small deterministic embedding provider for local learning and tests.

    It is not a semantic model, but it gives stable vectors and lets the RAG
    pipeline run without external API keys. Swap to OpenAIEmbeddingProvider for
    real semantic retrieval quality.
    """

    def __init__(self, *, dimensions: int = 128) -> None:
        self._dimensions = dimensions

    def embed_texts(
        self,
        texts: list[str],
        *,
        task_type: str = "document",
    ) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        tokens = _tokenize(text)
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class OpenAIEmbeddingProvider:
    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.openai_api_key
        self._model = settings.embedding_model
        self._timeout_seconds = settings.llm_timeout_seconds

    def embed_texts(
        self,
        texts: list[str],
        *,
        task_type: str = "document",
    ) -> list[list[float]]:
        if not self._api_key:
            raise RAGConfigurationError("OPENAI_API_KEY is required for OpenAI embeddings")
        payload = {
            "model": self._model,
            "input": texts,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(self._timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                "https://api.openai.com/v1/embeddings",
                headers=headers,
                json=payload,
            )
        if response.status_code >= 400:
            raise RAGProviderError(
                f"embedding provider returned HTTP {response.status_code}"
            )

        body = response.json()
        data = body.get("data")
        if not isinstance(data, list):
            raise RAGProviderError("embedding provider returned malformed data")
        return [item["embedding"] for item in data]


class GeminiEmbeddingProvider:
    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.google_api_key
        self._model = settings.embedding_model
        self._timeout_seconds = settings.llm_timeout_seconds

    def embed_texts(
        self,
        texts: list[str],
        *,
        task_type: str = "document",
    ) -> list[list[float]]:
        if not self._api_key:
            raise RAGConfigurationError(
                "GOOGLE_API_KEY or GEMINI_API_KEY is required for Gemini embeddings"
            )

        gemini_task_type = _gemini_task_type(task_type)
        return [
            self._embed_one(text=text, task_type=gemini_task_type)
            for text in texts
        ]

    def _embed_one(self, *, text: str, task_type: str) -> list[float]:
        payload = {
            "content": {
                "parts": [
                    {
                        "text": text,
                    }
                ],
            },
            "taskType": task_type,
        }
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key,
        }
        model = self._model
        if not model.startswith("models/"):
            model = f"models/{model}"
        timeout = httpx.Timeout(self._timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/{model}:embedContent",
                headers=headers,
                json=payload,
            )
        if response.status_code >= 400:
            raise RAGProviderError(
                f"Gemini embedding provider returned HTTP {response.status_code}"
            )

        body = response.json()
        embedding = body.get("embedding")
        if not isinstance(embedding, dict):
            raise RAGProviderError("Gemini embedding provider returned malformed data")
        values = embedding.get("values")
        if not isinstance(values, list):
            raise RAGProviderError("Gemini embedding provider returned no values")
        return [float(value) for value in values]


class NoopReranker:
    def rerank(
        self,
        *,
        query: str,
        candidates: list[RetrievedDocument],
        limit: int,
    ) -> list[RetrievedDocument]:
        return candidates[:limit]


class SentenceTransformerCrossEncoderReranker:
    def __init__(self, *, model_name: str) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RAGConfigurationError(
                "sentence-transformers is not installed; "
                "run pip install -r requirements.txt"
            ) from exc

        self._model = CrossEncoder(model_name)

    def rerank(
        self,
        *,
        query: str,
        candidates: list[RetrievedDocument],
        limit: int,
    ) -> list[RetrievedDocument]:
        if not candidates:
            return []

        pairs = [(query, candidate.text) for candidate in candidates]
        raw_scores = self._model.predict(pairs)
        scored = [
            replace(
                candidate,
                score=float(score),
                recall_score=candidate.score,
                rerank_score=float(score),
            )
            for candidate, score in zip(candidates, raw_scores)
        ]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._rows: list[tuple[DocumentChunk, list[float]]] = []

    def delete_document(self, *, document_id: str) -> None:
        self._rows = [
            (chunk, embedding)
            for chunk, embedding in self._rows
            if chunk.document_id != document_id
        ]

    def upsert_chunks(
        self,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        existing_ids = {chunk.id for chunk in chunks}
        self._rows = [
            (chunk, embedding)
            for chunk, embedding in self._rows
            if chunk.id not in existing_ids
        ]
        self._rows.extend(zip(chunks, embeddings))

    def search(
        self,
        *,
        knowledge_base_id: str,
        query_embedding: list[float],
        limit: int,
    ) -> list[RetrievedDocument]:
        scored: list[RetrievedDocument] = []
        for chunk, embedding in self._rows:
            if chunk.knowledge_base_id != knowledge_base_id:
                continue
            score = _cosine_similarity(query_embedding, embedding)
            scored.append(
                RetrievedDocument(
                    id=chunk.id,
                    knowledge_base_id=chunk.knowledge_base_id,
                    document_id=chunk.document_id,
                    filename=chunk.filename,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    score=score,
                    recall_score=score,
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]


class ChromaVectorStore:
    def __init__(self, *, persist_directory: str, collection_name: str) -> None:
        try:
            import chromadb
        except ImportError as exc:
            raise RAGConfigurationError(
                "chromadb is not installed; run pip install -r requirements.txt"
            ) from exc

        client = chromadb.PersistentClient(path=persist_directory)
        self._collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def delete_document(self, *, document_id: str) -> None:
        self._collection.delete(where={"document_id": document_id})

    def upsert_chunks(
        self,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        if not chunks:
            return

        self._collection.upsert(
            ids=[chunk.id for chunk in chunks],
            embeddings=embeddings,
            documents=[chunk.text for chunk in chunks],
            metadatas=[
                {
                    "knowledge_base_id": chunk.knowledge_base_id,
                    "document_id": chunk.document_id,
                    "filename": chunk.filename,
                    "chunk_index": chunk.chunk_index,
                }
                for chunk in chunks
            ],
        )

    def search(
        self,
        *,
        knowledge_base_id: str,
        query_embedding: list[float],
        limit: int,
    ) -> list[RetrievedDocument]:
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            where={"knowledge_base_id": knowledge_base_id},
            include=["documents", "metadatas", "distances"],
        )

        ids = _first_result_list(result.get("ids"))
        documents = _first_result_list(result.get("documents"))
        metadatas = _first_result_list(result.get("metadatas"))
        distances = _first_result_list(result.get("distances"))

        retrieved: list[RetrievedDocument] = []
        for index, chunk_id in enumerate(ids):
            metadata = metadatas[index]
            distance = distances[index]
            score = 1.0 - float(distance)
            retrieved.append(
                RetrievedDocument(
                    id=str(chunk_id),
                    knowledge_base_id=str(metadata["knowledge_base_id"]),
                    document_id=str(metadata["document_id"]),
                    filename=str(metadata["filename"]),
                    chunk_index=int(metadata["chunk_index"]),
                    text=str(documents[index]),
                    score=score,
                    recall_score=score,
                )
            )
        return retrieved


class QdrantVectorStore:
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
            raise RAGConfigurationError(
                "qdrant-client is not installed; run pip install -r requirements.txt"
            ) from exc

        if url == ":memory:":
            self._client = QdrantClient(location=":memory:")
        else:
            self._client = QdrantClient(url=url, api_key=api_key)
        self._collection_name = collection_name
        self._vector_size: int | None = None

    def delete_document(self, *, document_id: str) -> None:
        if not self._collection_exists():
            return
        FieldCondition = _qdrant_model("FieldCondition")
        Filter = _qdrant_model("Filter")
        MatchValue = _qdrant_model("MatchValue")
        FilterSelector = _qdrant_model("FilterSelector")
        self._client.delete(
            collection_name=self._collection_name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(
                            key="document_id",
                            match=MatchValue(value=document_id),
                        )
                    ]
                )
            ),
        )

    def upsert_chunks(
        self,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        if not chunks:
            return

        self._ensure_collection(vector_size=len(embeddings[0]))
        PointStruct = _qdrant_model("PointStruct")
        points = [
            PointStruct(
                id=_qdrant_point_id(chunk.id),
                vector=embedding,
                payload={
                    "chunk_id": chunk.id,
                    "knowledge_base_id": chunk.knowledge_base_id,
                    "document_id": chunk.document_id,
                    "filename": chunk.filename,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                },
            )
            for chunk, embedding in zip(chunks, embeddings)
        ]
        self._client.upsert(collection_name=self._collection_name, points=points)

    def search(
        self,
        *,
        knowledge_base_id: str,
        query_embedding: list[float],
        limit: int,
    ) -> list[RetrievedDocument]:
        if not self._collection_exists():
            return []

        FieldCondition = _qdrant_model("FieldCondition")
        Filter = _qdrant_model("Filter")
        MatchValue = _qdrant_model("MatchValue")
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="knowledge_base_id",
                    match=MatchValue(value=knowledge_base_id),
                )
            ]
        )
        if hasattr(self._client, "search"):
            results = self._client.search(
                collection_name=self._collection_name,
                query_vector=query_embedding,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        else:
            query_response = self._client.query_points(
                collection_name=self._collection_name,
                query=query_embedding,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            results = query_response.points

        retrieved: list[RetrievedDocument] = []
        for point in results:
            payload = point.payload or {}
            retrieved.append(
                RetrievedDocument(
                    id=str(payload["chunk_id"]),
                    knowledge_base_id=str(payload["knowledge_base_id"]),
                    document_id=str(payload["document_id"]),
                    filename=str(payload["filename"]),
                    chunk_index=int(payload["chunk_index"]),
                    text=str(payload["text"]),
                    score=float(point.score),
                    recall_score=float(point.score),
                )
            )
        return retrieved

    def _ensure_collection(self, *, vector_size: int) -> None:
        if self._vector_size is not None:
            if self._vector_size != vector_size:
                raise RAGConfigurationError(
                    "Qdrant collection vector size does not match embedding size"
                )
            return

        if self._collection_exists():
            info = self._client.get_collection(collection_name=self._collection_name)
            configured_size = _qdrant_vector_size(info)
            if configured_size is not None and configured_size != vector_size:
                raise RAGConfigurationError(
                    "Qdrant collection vector size does not match embedding size"
                )
            self._vector_size = configured_size or vector_size
            return

        Distance = _qdrant_model("Distance")
        VectorParams = _qdrant_model("VectorParams")
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        self._vector_size = vector_size

    def _collection_exists(self) -> bool:
        collections = self._client.get_collections().collections
        return any(item.name == self._collection_name for item in collections)


class RAGService:
    def __init__(
        self,
        *,
        parser: TextDocumentParser,
        chunker: RecursiveCharacterChunker,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        reranker: Reranker,
        default_recall_limit: int,
        max_prompt_chars: int,
        document_store: DocumentStore | None = None,
    ) -> None:
        self._parser = parser
        self._chunker = chunker
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._reranker = reranker
        self._default_recall_limit = default_recall_limit
        self._max_prompt_chars = max_prompt_chars
        self._document_store = document_store

    def ingest_document(
        self,
        *,
        knowledge_base_id: str,
        filename: str,
        content: str,
        source_uri: str | None = None,
    ) -> IngestedDocument:
        document = self._parser.parse(
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            content=content,
            source_uri=source_uri,
        )
        chunks = self._chunker.split(document)
        if not chunks:
            raise RAGValidationError("document produced no chunks")

        embeddings = self._embedding_provider.embed_texts(
            [chunk.text for chunk in chunks],
            task_type="document",
        )
        self._vector_store.delete_document(document_id=document.id)
        self._vector_store.upsert_chunks(chunks, embeddings)
        if self._document_store is not None:
            try:
                self._document_store.save_document(document, chunks)
            except Exception:
                try:
                    self._vector_store.delete_document(document_id=document.id)
                except Exception:
                    pass
                raise
        return IngestedDocument(
            knowledge_base_id=knowledge_base_id,
            document_id=document.id,
            filename=document.filename,
            chunk_count=len(chunks),
        )

    def search(
        self,
        *,
        knowledge_base_id: str,
        query: str,
        limit: int = 5,
        recall_limit: int | None = None,
    ) -> list[RetrievedDocument]:
        query = _normalize_text(query)
        if not query:
            raise RAGValidationError("query is empty")
        query_embedding = self._embedding_provider.embed_texts(
            [query],
            task_type="query",
        )[0]
        candidate_limit = recall_limit or self._default_recall_limit
        candidate_limit = max(candidate_limit, limit)
        candidates = self._vector_store.search(
            knowledge_base_id=knowledge_base_id,
            query_embedding=query_embedding,
            limit=candidate_limit,
        )
        return self._reranker.rerank(
            query=query,
            candidates=candidates,
            limit=limit,
        )

    def answer_question(
        self,
        *,
        knowledge_base_id: str,
        question: str,
        llm_client: LLMClient,
        provider: str | None = None,
        model: str | None = None,
        limit: int = 5,
        recall_limit: int | None = None,
    ) -> RAGAnswer:
        citations = self.search(
            knowledge_base_id=knowledge_base_id,
            query=question,
            limit=limit,
            recall_limit=recall_limit,
        )
        messages = self.build_prompt_messages(question=question, citations=citations)

        answer_parts: list[str] = []
        for event in llm_client.stream_chat(messages, provider=provider, model=model):
            if event.type == "delta":
                answer_parts.append(event.text)
            elif event.type == "done":
                break

        return RAGAnswer(answer="".join(answer_parts).strip(), citations=citations)

    def build_prompt_messages(
        self,
        *,
        question: str,
        citations: list[RetrievedDocument],
    ) -> list[dict[str, str]]:
        context = self._format_context(citations)
        return [
            {
                "role": "system",
                "content": (
                    "你是一个企业知识库问答助手。只能基于参考资料回答；"
                    "如果参考资料不足，就明确说不知道。回答时保留引用编号。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户问题：\n{question}\n\n"
                    f"参考资料：\n{context or '没有检索到相关资料。'}\n\n"
                    "请给出简洁答案，并在相关句子后标注引用编号，例如 [1]。"
                ),
            },
        ]

    def _format_context(self, citations: list[RetrievedDocument]) -> str:
        parts: list[str] = []
        used_chars = 0
        for index, item in enumerate(citations, start=1):
            header = (
                f"[{index}] file={item.filename}; "
                f"document_id={item.document_id}; chunk={item.chunk_index}; "
                f"score={item.score:.3f}\n"
            )
            body = item.text.strip()
            block = f"{header}{body}"
            remaining = self._max_prompt_chars - used_chars
            if remaining <= 0:
                break
            if len(block) > remaining:
                block = block[:remaining].rstrip()
            parts.append(block)
            used_chars += len(block)
        return "\n\n".join(parts)


class RAGError(Exception):
    pass


class RAGValidationError(RAGError):
    pass


class RAGConfigurationError(RAGError):
    pass


class RAGProviderError(RAGError):
    pass


def create_rag_service(
    settings: Settings,
    *,
    document_store: DocumentStore | None = None,
) -> RAGService:
    if settings.embedding_provider == "openai":
        embedding_provider: EmbeddingProvider = OpenAIEmbeddingProvider(settings)
    elif settings.embedding_provider == "gemini":
        embedding_provider = GeminiEmbeddingProvider(settings)
    elif settings.embedding_provider == "local":
        embedding_provider = HashingEmbeddingProvider(
            dimensions=settings.local_embedding_dimensions
        )
    else:
        raise RAGConfigurationError(
            f"unsupported embedding provider: {settings.embedding_provider}"
        )

    if settings.rag_vector_store == "chroma":
        vector_store: VectorStore = ChromaVectorStore(
            persist_directory=settings.chroma_persist_directory,
            collection_name=settings.chroma_collection_name,
        )
    elif settings.rag_vector_store == "qdrant":
        vector_store = QdrantVectorStore(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            collection_name=settings.qdrant_collection_name,
        )
    elif settings.rag_vector_store == "memory":
        vector_store = InMemoryVectorStore()
    else:
        raise RAGConfigurationError(
            f"unsupported RAG vector store: {settings.rag_vector_store}"
        )

    if settings.rag_reranker_provider == "sentence_transformer":
        reranker: Reranker = SentenceTransformerCrossEncoderReranker(
            model_name=settings.sentence_transformer_reranker_model
        )
    elif settings.rag_reranker_provider == "none":
        reranker = NoopReranker()
    else:
        raise RAGConfigurationError(
            f"unsupported RAG reranker provider: {settings.rag_reranker_provider}"
        )

    return RAGService(
        parser=TextDocumentParser(),
        chunker=RecursiveCharacterChunker(
            chunk_size=settings.rag_chunk_size,
            chunk_overlap=settings.rag_chunk_overlap,
        ),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        reranker=reranker,
        default_recall_limit=settings.rag_recall_limit,
        max_prompt_chars=settings.rag_max_prompt_chars,
        document_store=document_store,
    )


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _file_extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[1].lower()


def _document_id(
    *,
    knowledge_base_id: str,
    filename: str,
    source_uri: str | None,
) -> str:
    key = f"{knowledge_base_id}:{filename}:{source_uri or ''}"
    return f"doc_{uuid5(NAMESPACE_URL, key).hex[:16]}"


def _chunk_id(*, document_id: str, chunk_index: int) -> str:
    return f"chk_{uuid5(NAMESPACE_URL, f'{document_id}:{chunk_index}').hex[:16]}"


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]", text.lower())


def _gemini_task_type(task_type: str) -> str:
    if task_type == "query":
        return "RETRIEVAL_QUERY"
    return "RETRIEVAL_DOCUMENT"


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _first_result_list(value: object) -> list:
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, list):
            return first
    return []


def _qdrant_model(name: str):
    try:
        from qdrant_client import models
    except ImportError as exc:
        raise RAGConfigurationError(
            "qdrant-client is not installed; run pip install -r requirements.txt"
        ) from exc
    return getattr(models, name)


def _qdrant_point_id(chunk_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, chunk_id))


def _qdrant_vector_size(collection_info: object) -> int | None:
    config = getattr(collection_info, "config", None)
    params = getattr(config, "params", None)
    vectors = getattr(params, "vectors", None)
    if vectors is None:
        return None
    size = getattr(vectors, "size", None)
    if size is not None:
        return int(size)
    if isinstance(vectors, dict) and vectors:
        first_vector = next(iter(vectors.values()))
        named_size = getattr(first_vector, "size", None)
        return int(named_size) if named_size is not None else None
    return None
