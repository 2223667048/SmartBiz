"""Fake HTTP transport verifies OpenAI-compatible Embeddings API contract."""
import asyncio
import httpx
import pytest
from app import embeddings


def test_embedding_provider_request_and_order(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_MODE", "openai")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "test-embeddings")
    monkeypatch.setenv("RAG_EMBEDDING_API_KEY", "fake-secret")
    monkeypatch.setenv("RAG_EMBEDDING_BASE_URL", "https://example.invalid/v1")
    seen = []

    def handler(req):
        seen.append(req)
        assert req.url.path == "/v1/embeddings"
        assert req.headers["authorization"] == "Bearer fake-secret"
        body = __import__("json").loads(req.content)
        assert body == {"model": "test-embeddings", "input": ["第一段", "第二段"]}
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [0.0, 4.0]},
            {"index": 0, "embedding": [3.0, 0.0]},
        ]})

    original_async = httpx.AsyncClient
    monkeypatch.setattr(embeddings.httpx, "AsyncClient", lambda **kwargs: original_async(transport=httpx.MockTransport(handler)))
    vectors = asyncio.run(embeddings.embed_many(["第一段", "第二段"]))
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert len(seen) == 1


def test_embedding_provider_rejects_incomplete_response(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_MODE", "openai")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "test-embeddings")
    monkeypatch.setenv("RAG_EMBEDDING_API_KEY", "fake-secret")
    monkeypatch.setenv("RAG_EMBEDDING_BASE_URL", "https://example.invalid/v1")
    original_async = httpx.AsyncClient
    monkeypatch.setattr(embeddings.httpx, "AsyncClient", lambda **kwargs: original_async(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"data": []}))
    ))
    with pytest.raises(embeddings.EmbeddingError):
        asyncio.run(embeddings.embed_many(["一句话"]))
