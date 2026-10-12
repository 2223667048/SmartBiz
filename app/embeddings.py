"""Embedding adapters. Local hash vectors are NOT semantic embeddings."""
import hashlib
import math
import os
import re

import httpx


class EmbeddingError(Exception):
    pass


def mode() -> str:
    return os.getenv("RAG_EMBEDDING_MODE", "demo").strip().lower()


def dimensions() -> int | None:
    raw = os.getenv("RAG_EMBEDDING_DIMENSIONS", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise EmbeddingError("RAG_EMBEDDING_DIMENSIONS must be an integer") from exc
    if not 1 <= value <= 8192:
        raise EmbeddingError("RAG_EMBEDDING_DIMENSIONS out of range")
    return value


def index_spec() -> str:
    kind = mode()
    if kind == "demo":
        return "demo-char2-v1-256"
    if kind == "openai":
        model = os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small").strip()
        base = os.getenv("RAG_EMBEDDING_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
        if not model or not base.startswith(("https://", "http://")):
            raise EmbeddingError("Embedding model/base URL configuration invalid")
        # Fingerprint prevents using incompatible vectors after model changes.
        dims = dimensions()
        # Preserve legacy fingerprints when dimensions is unset.
        digest = hashlib.sha256((f"{base}|{model}" + (f"|dims={dims}" if dims else "")).encode()).hexdigest()[:16]
        return f"openai:{model}:{digest}"
    raise EmbeddingError("RAG_EMBEDDING_MODE must be demo or openai")


def normalize(vector: list[float]) -> list[float]:
    if not vector or len(vector) > 8192:
        raise EmbeddingError("Invalid embedding dimensions")
    try:
        numbers = [float(x) for x in vector]
    except (ValueError, TypeError, OverflowError) as exc:
        raise EmbeddingError("Embedding is not numeric") from exc
    if not all(math.isfinite(x) for x in numbers):
        raise EmbeddingError("Embedding contains non-finite numbers")
    length = math.sqrt(sum(x * x for x in numbers))
    if not length:
        raise EmbeddingError("Embedding vector must not be zero")
    return [x / length for x in numbers]


def demo_hash_embedding(text: str) -> list[float]:
    """Deterministic 256-dimension Chinese character n-gram hashing (demo only)."""
    clean = re.sub(r"\s+", "", text.casefold())
    if not clean:
        raise EmbeddingError("Cannot embed empty text")
    values = [0.0] * 256
    grams = [clean[i:i + 2] for i in range(len(clean) - 1)] or [clean]
    # Unigrams give a small boost to short Chinese queries.
    grams += list(clean)
    for gram in grams:
        raw = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(raw, "little") % 256
        values[index] += 1.0
    return normalize(values)


async def embed_many(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if any(not isinstance(t, str) or not t.strip() for t in texts):
        raise EmbeddingError("Empty embedding input")
    if mode() == "demo":
        return [demo_hash_embedding(t) for t in texts]
    if mode() != "openai":
        raise EmbeddingError("Unsupported embedding mode")

    key = os.getenv("RAG_EMBEDDING_API_KEY", "").strip()
    base = os.getenv("RAG_EMBEDDING_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small").strip()
    dims = dimensions()
    if not key or not model or not base.startswith(("https://", "http://")):
        raise EmbeddingError("Configure RAG_EMBEDDING_API_KEY/MODEL/BASE_URL")

    result_vectors = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as client:
            for i in range(0, len(texts), 16):
                batch = texts[i:i + 16]
                payload = {"model": model, "input": batch}
                if dims is not None:
                    payload["dimensions"] = dims
                response = await client.post(
                    base + "/embeddings",
                    json=payload,
                    headers={"Authorization": f"Bearer {key}"},
                )
                response.raise_for_status()
                body = response.json()
                entries = sorted(body["data"], key=lambda obj: obj["index"])
                if len(entries) != len(batch) or [x["index"] for x in entries] != list(range(len(batch))):
                    raise EmbeddingError("Embedding provider returned inconsistent indices")
                vectors = [normalize(x["embedding"]) for x in entries]
                result_vectors.extend(vectors)
    except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError) as exc:
        raise EmbeddingError("Embedding provider call failed") from exc
    if len({len(v) for v in result_vectors}) != 1:
        raise EmbeddingError("Embedding provider returned mixed dimensions")
    return result_vectors
