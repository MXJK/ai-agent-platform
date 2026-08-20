from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.rag import (
    GeminiEmbeddingProvider,
    OpenAIEmbeddingProvider,
)


class _Response:
    def __init__(self, body: dict) -> None:
        self.status_code = 200
        self._body = body

    def json(self) -> dict:
        return self._body


class _RecordingHttpClient:
    requests: list[tuple[str, dict[str, str], dict]] = []

    def __init__(self, **kwargs) -> None:
        del kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        del args

    def post(self, url: str, *, headers: dict[str, str], json: dict) -> _Response:
        self.requests.append((url, headers, json))
        if url.endswith(":countTokens"):
            return _Response({"totalTokens": 4})
        if url.endswith(":embedContent"):
            return _Response({"embedding": {"values": [0.1, 0.2]}})
        return _Response(
            {
                "data": [{"embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 4},
            }
        )


class ProviderCredentialResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        _RecordingHttpClient.requests = []

    def test_openai_embedding_resolves_registry_credential_at_call_time(self) -> None:
        credentials: dict[str, str] = {}
        provider = OpenAIEmbeddingProvider(
            Settings(embedding_provider="openai", embedding_model="embedding-test"),
            credential_resolver=credentials.get,
        )
        credentials["openai"] = "openai-registry-secret"

        with patch(
            "ai_agent_platform.integrations.rag.service.httpx.Client",
            _RecordingHttpClient,
        ):
            embeddings = provider.embed_texts(["hello"])

        self.assertEqual(embeddings, [[0.1, 0.2]])
        self.assertEqual(
            _RecordingHttpClient.requests[0][1]["Authorization"],
            "Bearer openai-registry-secret",
        )

    def test_gemini_embedding_resolves_registry_credential_at_call_time(self) -> None:
        credentials = {"google": "google-registry-secret"}
        provider = GeminiEmbeddingProvider(
            Settings(embedding_provider="gemini", embedding_model="embedding-test"),
            credential_resolver=credentials.get,
        )

        with patch(
            "ai_agent_platform.integrations.rag.service.httpx.Client",
            _RecordingHttpClient,
        ):
            embeddings = provider.embed_texts(["hello"])

        self.assertEqual(embeddings, [[0.1, 0.2]])
        self.assertEqual(len(_RecordingHttpClient.requests), 2)
        self.assertTrue(
            all(
                headers["x-goog-api-key"] == "google-registry-secret"
                for _, headers, _ in _RecordingHttpClient.requests
            )
        )


if __name__ == "__main__":
    unittest.main()
