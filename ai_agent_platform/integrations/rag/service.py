from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import math
import re
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

import httpx

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.llm import LLMClient
from ai_agent_platform.integrations.rag.errors import (
    RAGConfigurationError,
    RAGError,
    RAGProviderError,
    RAGValidationError,
)
from ai_agent_platform.integrations.rag.models import (
    DocumentChunk,
    DocumentStore,
    EmbeddingProvider,
    IngestedDocument,
    ParsedDocument,
    RAGAnswer,
    Reranker,
    RetrievedDocument,
    VectorStore,
)


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
CODE_TEXT_EXTENSIONS = {
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".py",
    ".rs",
    ".ts",
    ".tsx",
}


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

        if extension in CODE_TEXT_EXTENSIONS:
            text = _normalize_code_text(content)
        else:
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
        if _file_extension(document.filename) in CODE_TEXT_EXTENSIONS:
            code_chunks = self._split_code_document(document)
            if code_chunks:
                return code_chunks
            return self._split_text_with_line_ranges(document)
        return self._split_text(document)

    def _split_text(self, document: ParsedDocument) -> list[DocumentChunk]:
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
                        start_line=_line_number_for_offset(text, start),
                        end_line=_line_number_for_offset(text, end),
                    )
                )
            if end >= text_length:
                break
            start = max(end - self._chunk_overlap, start + 1)

        return chunks

    def _split_code_document(self, document: ParsedDocument) -> list[DocumentChunk]:
        lines = document.text.splitlines()
        starts = _code_symbol_starts(lines, document.filename)
        chunks: list[DocumentChunk] = []

        if starts and starts[0][0] > 1:
            preamble_text = "\n".join(lines[: starts[0][0] - 1]).strip()
            if preamble_text:
                chunks.extend(
                    self._chunks_from_line_block(
                        document=document,
                        lines=lines[: starts[0][0] - 1],
                        start_line=1,
                        symbols=[],
                        start_index=len(chunks),
                    )
                )

        for index, (start_line, symbols) in enumerate(starts):
            next_start = starts[index + 1][0] if index + 1 < len(starts) else len(lines) + 1
            block_lines = lines[start_line - 1 : next_start - 1]
            chunks.extend(
                self._chunks_from_line_block(
                    document=document,
                    lines=block_lines,
                    start_line=start_line,
                    symbols=symbols,
                    start_index=len(chunks),
                )
            )
        return chunks

    def _split_text_with_line_ranges(self, document: ParsedDocument) -> list[DocumentChunk]:
        lines = document.text.splitlines()
        return self._chunks_from_line_block(
            document=document,
            lines=lines,
            start_line=1,
            symbols=[],
            start_index=0,
        )

    def _chunks_from_line_block(
        self,
        *,
        document: ParsedDocument,
        lines: list[str],
        start_line: int,
        symbols: list[str],
        start_index: int,
    ) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        current_lines: list[str] = []
        current_start_line = start_line
        current_chars = 0

        for offset, line in enumerate(lines):
            line_length = len(line) + 1
            if current_lines and current_chars + line_length > self._chunk_size:
                chunks.append(
                    self._build_line_chunk(
                        document=document,
                        chunk_index=start_index + len(chunks),
                        lines=current_lines,
                        start_line=current_start_line,
                        symbols=symbols,
                    )
                )
                overlap_lines = _line_overlap(current_lines, self._chunk_overlap)
                current_start_line = start_line + offset - len(overlap_lines)
                current_lines = overlap_lines
                current_chars = sum(len(item) + 1 for item in current_lines)
            current_lines.append(line)
            current_chars += line_length

        if current_lines and "\n".join(current_lines).strip():
            chunks.append(
                self._build_line_chunk(
                    document=document,
                    chunk_index=start_index + len(chunks),
                    lines=current_lines,
                    start_line=current_start_line,
                    symbols=symbols,
                )
            )
        return chunks

    def _build_line_chunk(
        self,
        *,
        document: ParsedDocument,
        chunk_index: int,
        lines: list[str],
        start_line: int,
        symbols: list[str],
    ) -> DocumentChunk:
        chunk_text = "\n".join(lines).strip()
        return DocumentChunk(
            id=_chunk_id(
                document_id=document.id,
                chunk_index=chunk_index,
            ),
            knowledge_base_id=document.knowledge_base_id,
            document_id=document.id,
            filename=document.filename,
            chunk_index=chunk_index,
            text=chunk_text,
            start_line=start_line,
            end_line=start_line + len(lines) - 1,
            symbols=symbols,
        )

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
                recall_score=(
                    candidate.recall_score
                    if candidate.recall_score is not None
                    else candidate.score
                ),
                rerank_score=float(score),
            )
            for candidate, score in zip(candidates, raw_scores)
        ]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._rows: list[tuple[DocumentChunk, list[float]]] = []
        self._lock = Lock()

    def delete_document(self, *, document_id: str) -> None:
        with self._lock:
            self._rows = [
                (chunk, embedding)
                for chunk, embedding in self._rows
                if chunk.document_id != document_id
            ]

    def delete_knowledge_base(self, *, knowledge_base_id: str) -> None:
        with self._lock:
            self._rows = [
                (chunk, embedding)
                for chunk, embedding in self._rows
                if chunk.knowledge_base_id != knowledge_base_id
            ]

    def upsert_chunks(
        self,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        with self._lock:
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
        with self._lock:
            rows = list(self._rows)
        for chunk, embedding in rows:
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
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    symbols=chunk.symbols,
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

    def delete_knowledge_base(self, *, knowledge_base_id: str) -> None:
        self._collection.delete(where={"knowledge_base_id": knowledge_base_id})

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
            metadatas=[_chroma_chunk_metadata(chunk) for chunk in chunks],
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
                    start_line=_optional_int(metadata.get("start_line")),
                    end_line=_optional_int(metadata.get("end_line")),
                    symbols=_metadata_symbols(metadata.get("symbols")),
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

    def delete_knowledge_base(self, *, knowledge_base_id: str) -> None:
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
                            key="knowledge_base_id",
                            match=MatchValue(value=knowledge_base_id),
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
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "symbols": chunk.symbols,
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
                    start_line=_optional_int(payload.get("start_line")),
                    end_line=_optional_int(payload.get("end_line")),
                    symbols=_metadata_symbols(payload.get("symbols")),
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
        lexical_weight: float = 0.35,
        document_store: DocumentStore | None = None,
    ) -> None:
        if not 0.0 <= lexical_weight <= 1.0:
            raise ValueError("lexical_weight must be between 0 and 1")
        self._parser = parser
        self._chunker = chunker
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._reranker = reranker
        self._default_recall_limit = default_recall_limit
        self._max_prompt_chars = max_prompt_chars
        self._lexical_weight = lexical_weight
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

    def delete_knowledge_base(self, *, knowledge_base_id: str) -> None:
        self._vector_store.delete_knowledge_base(
            knowledge_base_id=knowledge_base_id
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
        candidates = _hybrid_rank_candidates(
            query=query,
            candidates=candidates,
            lexical_weight=self._lexical_weight,
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
        thinking_level: str | None = None,
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
        for event in llm_client.stream_chat(
            messages,
            provider=provider,
            model=model,
            thinking_level=thinking_level,
        ):
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
                f"{_line_range_label(item)}"
                f"{_symbols_label(item)}"
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


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_code_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{4,}", "\n\n\n", normalized)
    return normalized.strip()


def _file_extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[1].lower()


def _line_number_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, max(offset, 0)) + 1


def _code_symbol_starts(
    lines: list[str],
    filename: str,
) -> list[tuple[int, list[str]]]:
    if _file_extension(filename) == ".py":
        python_starts = _python_symbol_starts(lines)
        if python_starts:
            return python_starts

    starts: list[tuple[int, list[str]]] = []
    for index, line in enumerate(lines, start=1):
        symbol = _code_symbol_name(line, filename)
        if symbol is not None:
            starts.append((index, [symbol]))
    return starts


def _python_symbol_starts(lines: list[str]) -> list[tuple[int, list[str]]]:
    """Return top-level Python blocks with class-qualified child symbols.

    AST boundaries keep a class and its methods in one semantic block. Invalid
    or incomplete Python falls back to the language-neutral regex scanner.
    """

    try:
        tree = ast.parse("\n".join(lines))
    except SyntaxError:
        return []

    starts: list[tuple[int, list[str]]] = []
    symbol_nodes = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    function_nodes = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if not isinstance(node, symbol_nodes):
            continue
        decorator_lines = [item.lineno for item in node.decorator_list]
        start_line = min([node.lineno, *decorator_lines])
        symbols = [node.name]
        if isinstance(node, ast.ClassDef):
            symbols.extend(
                f"{node.name}.{child.name}"
                for child in node.body
                if isinstance(child, function_nodes)
            )
        starts.append((start_line, symbols))
    return starts


def _code_symbol_name(line: str, filename: str) -> str | None:
    extension = _file_extension(filename)
    patterns = _code_symbol_patterns(extension)
    for pattern in patterns:
        match = re.match(pattern, line)
        if match:
            return str(match.group("name"))
    return None


def _code_symbol_patterns(extension: str) -> list[str]:
    if extension == ".py":
        return [
            r"^\s*(?:async\s+def|def)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
            r"^\s*class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b",
        ]
    if extension in {".js", ".jsx", ".ts", ".tsx"}:
        return [
            r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(",
            r"^\s*(?:export\s+)?class\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b",
            r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>",
        ]
    if extension == ".go":
        return [r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("]
    if extension == ".rs":
        return [
            r"^\s*(?:pub\s+)?fn\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
            r"^\s*(?:pub\s+)?(?:struct|enum|trait|impl)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b",
        ]
    if extension == ".java":
        return [
            r"^\s*(?:public|private|protected)?\s*(?:class|interface|enum)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b",
            r"^\s*(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\]]+\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
        ]
    return []


def _line_overlap(lines: list[str], max_chars: int) -> list[str]:
    if max_chars <= 0:
        return []
    overlap: list[str] = []
    used_chars = 0
    for line in reversed(lines):
        line_chars = len(line) + 1
        if overlap and used_chars + line_chars > max_chars:
            break
        overlap.append(line)
        used_chars += line_chars
    overlap.reverse()
    return overlap


def _chroma_chunk_metadata(chunk: DocumentChunk) -> dict[str, str | int]:
    metadata: dict[str, str | int] = {
        "knowledge_base_id": chunk.knowledge_base_id,
        "document_id": chunk.document_id,
        "filename": chunk.filename,
        "chunk_index": chunk.chunk_index,
    }
    if chunk.start_line is not None:
        metadata["start_line"] = chunk.start_line
    if chunk.end_line is not None:
        metadata["end_line"] = chunk.end_line
    if chunk.symbols:
        metadata["symbols"] = ",".join(chunk.symbols)
    return metadata


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _metadata_symbols(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        return [item for item in value.split(",") if item]
    return []


def _line_range_label(item: RetrievedDocument) -> str:
    if item.start_line is None or item.end_line is None:
        return ""
    return f"lines={item.start_line}-{item.end_line}; "


def _symbols_label(item: RetrievedDocument) -> str:
    if not item.symbols:
        return ""
    return "symbols=" + ",".join(item.symbols[:3]) + "; "


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


def _hybrid_rank_candidates(
    *,
    query: str,
    candidates: list[RetrievedDocument],
    lexical_weight: float,
) -> list[RetrievedDocument]:
    """Blend vector recall with exact code, symbol, and path matches."""

    query_terms = _search_terms(query)
    ranked: list[RetrievedDocument] = []
    for candidate in candidates:
        recall_score = (
            candidate.recall_score
            if candidate.recall_score is not None
            else candidate.score
        )
        vector_score = max(0.0, min(1.0, (recall_score + 1.0) / 2.0))
        lexical_score = _lexical_relevance(query_terms, candidate)
        hybrid_score = (
            (1.0 - lexical_weight) * vector_score
            + lexical_weight * lexical_score
        )
        ranked.append(
            replace(
                candidate,
                score=hybrid_score,
                recall_score=recall_score,
                lexical_score=lexical_score,
                hybrid_score=hybrid_score,
            )
        )
    ranked.sort(
        key=lambda item: (
            item.score,
            item.recall_score if item.recall_score is not None else -1.0,
        ),
        reverse=True,
    )
    return ranked


def _lexical_relevance(
    query_terms: set[str],
    candidate: RetrievedDocument,
) -> float:
    if not query_terms:
        return 0.0

    text_terms = _search_terms(candidate.text)
    symbol_terms = _search_terms(" ".join(candidate.symbols))
    filename_terms = _search_terms(candidate.filename)
    text_coverage = len(query_terms & text_terms) / len(query_terms)
    symbol_coverage = _metadata_coverage(query_terms, symbol_terms)
    filename_coverage = _metadata_coverage(query_terms, filename_terms)
    return min(
        1.0,
        0.55 * text_coverage
        + 0.35 * symbol_coverage
        + 0.10 * filename_coverage,
    )


def _metadata_coverage(query_terms: set[str], metadata_terms: set[str]) -> float:
    meaningful_terms = {term for term in metadata_terms if len(term) > 1}
    if not meaningful_terms:
        return 0.0
    return len(query_terms & meaningful_terms) / len(meaningful_terms)


def _search_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw_term in _tokenize(text):
        if not raw_term:
            continue
        terms.add(raw_term)
        for snake_part in raw_term.split("_"):
            if snake_part:
                terms.add(snake_part)
        for camel_part in re.findall(
            r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+",
            raw_term,
        ):
            terms.add(camel_part.lower())
    return terms


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
