"""
app/models/article.py
──────────────────────
Pydantic models for articles flowing through the pipeline.
These are in-memory pipeline models (not ORM models).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class Article(BaseModel):
    """
    Normalised article schema — all collected articles are converted to this.
    This is the standard format passed between pipeline nodes.
    """
    article_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    url: str
    source_id: Optional[int] = None
    source_name: str
    source_tier: int = 2
    published_at: Optional[datetime] = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content: Optional[str] = None
    language: str = "en"
    content_hash: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("url")
    @classmethod
    def strip_tracking_params(cls, v: str) -> str:
        """Remove common tracking query parameters from URLs."""
        from urllib.parse import urlparse, urlencode, parse_qsl
        parsed = urlparse(v)
        TRACKING_PARAMS = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term",
            "utm_content", "fbclid", "gclid", "ref", "source",
        }
        clean_params = [
            (k, val) for k, val in parse_qsl(parsed.query)
            if k.lower() not in TRACKING_PARAMS
        ]
        clean = parsed._replace(query=urlencode(clean_params))
        return clean.geturl()

    @property
    def is_english(self) -> bool:
        """Check if article is written in English."""
        import re
        if re.search(r"[\u0600-\u06FF]", self.title or ""):
            return False
        if re.search(r"[a-zA-Z]", self.title or ""):
            return True
        return self.language == "en"

    @model_validator(mode="after")
    def compute_hash(self) -> "Article":
        """SHA-256 of normalised title + content for exact deduplication."""
        if self.content_hash is None:
            raw = f"{self.title.strip().lower()}|{(self.content or '').strip().lower()}"
            self.content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return self


class ArticleClassification(BaseModel):
    """
    LLM classification result for a single article.
    Internal use only — NEVER included in CEO email output.
    """
    article_id: str
    category: str = "Other"
    subcategory: Optional[str] = None
    entities: list[str] = Field(default_factory=list)
    is_relevant: bool = False
    importance_score: int = Field(default=0, ge=0, le=100)
    relevance_score: int = Field(default=0, ge=0, le=100)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    competitor_match: Optional[str] = None  # Matched competitor name if category=Competitor


class ClassifiedArticle(BaseModel):
    """Article + its classification, combined for downstream nodes."""
    article: Article
    classification: ArticleClassification


class ProcessedArticle(BaseModel):
    """Article after preprocessing (HTML cleaned, encoding normalised, etc.)."""
    article: Article
    clean_title: str
    clean_content: str
    word_count: int = 0
    language_detected: Optional[str] = None
