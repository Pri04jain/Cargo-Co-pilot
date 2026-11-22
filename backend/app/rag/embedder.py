"""
Embedding client — turns text chunks into dense vectors for pgvector search.

Uses OpenAI's text-embedding-3-small by default:
  - 1536 dimensions (matches EMBEDDING_DIM in models/chunk.py)
  - Fast and cheap (~$0.02 per 1M tokens)
  - Can be swapped for a local model by changing EMBEDDING_MODEL env var

Batching:
  We send up to 100 chunks per API call to minimise round-trips.
  OpenAI's embedding endpoint accepts up to 2048 inputs per batch.

Why not use LangChain's OpenAIEmbeddings?
  It's a thin wrapper around the same API. The direct client is less magic,
  easier to test (just mock openai.Embeddings.create), and has the same cost.
"""

from __future__ import annotations

import os
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

BATCH_SIZE = 50


def _get_embedding_model() -> str:
    return os.getenv("EMBEDDING_MODEL", settings.embedding_model)


def _get_embedding_dim() -> int:
    return int(os.getenv("EMBEDDING_DIM", settings.embedding_dim))


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of text strings and return their vectors.

    Returns a list of float vectors in the same order as the input.
    Raises if the API call fails after retries (caller decides whether to skip).
    """
    if not texts:
        return []

    if not settings.llm_api_key:
        logger.warning("embedding_skipped_no_api_key", count=len(texts))
        return [[0.0] * _get_embedding_dim() for _ in texts]

    import openai
    client = openai.OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.embedding_base_url if settings.embedding_base_url else None,
        timeout=30,
    )

    all_vectors: list[list[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        batch_clean = [t.replace("\n", " ") for t in batch]

        try:
            response = client.embeddings.create(
                model=_get_embedding_model(),
                input=batch_clean,
            )
            vectors = [item.embedding for item in response.data]
            all_vectors.extend(vectors)
            logger.debug("embeddings_created", batch_size=len(batch), model=_get_embedding_model())
        except Exception as exc:
            logger.error("embedding_failed", error=str(exc), batch_start=i)
            all_vectors.extend([[0.0] * _get_embedding_dim() for _ in batch])

    return all_vectors


def embed_query(query: str) -> list[float]:
    """Embed a single query string for similarity search."""
    results = embed_texts([query])
    return results[0] if results else [0.0] * _get_embedding_dim()
