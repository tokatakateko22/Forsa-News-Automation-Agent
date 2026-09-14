"""
app/services/embeddings.py
───────────────────────────
Embedding generation for semantic deduplication.
Uses Gemini embedding model. Results are cached to avoid duplicate API calls.
"""
from __future__ import annotations

import structlog
import google.generativeai as genai

from app.config import settings

log = structlog.get_logger(__name__)

# Ensure Gemini is configured
genai.configure(api_key=settings.google_api_key)

_EMBEDDING_MODEL = "models/text-embedding-004"
_TASK_TYPE = "SEMANTIC_SIMILARITY"

# In-process cache: article_id -> embedding vector
_cache: dict[str, list[float]] = {}


async def get_embedding(text: str, article_id: str) -> list[float]:
    """
    Get embedding for a text string. Result is cached by article_id.
    """
    if article_id in _cache:
        return _cache[article_id]

    try:
        result = genai.embed_content(
            model=_EMBEDDING_MODEL,
            content=text[:2000],  # Truncate to avoid token limits
            task_type=_TASK_TYPE,
        )
        embedding: list[float] = result["embedding"]
        _cache[article_id] = embedding
        return embedding
    except Exception as exc:
        log.warning("embeddings.failed", article_id=article_id, error=str(exc))
        return []


async def get_embeddings_batch(
    articles: list[tuple[str, str]]  # [(article_id, text), ...]
) -> dict[str, list[float]]:
    """
    Get embeddings for multiple articles.
    Returns {article_id: embedding}.
    """
    results: dict[str, list[float]] = {}
    for article_id, text in articles:
        emb = await get_embedding(text, article_id)
        if emb:
            results[article_id] = emb
    return results
