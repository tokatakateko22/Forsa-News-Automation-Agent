"""
app/models/event.py
────────────────────
Pydantic models for deduplicated news events.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from app.models.article import Article, ArticleClassification


class EventSummary(BaseModel):
    """
    LLM-generated factual summary for a single news event.
    This is the CEO-facing content — no scores, no analysis.
    """
    event_id: str
    summary_text: str
    category: str
    subcategory: Optional[str] = None
    canonical_title: str
    canonical_url: str
    source_name: str
    published_at: Optional[datetime] = None
    competitor_name: Optional[str] = None  # Set if category=Competitor


class NewsEvent(BaseModel):
    """
    A deduplicated news event, potentially covering multiple source articles.
    The CEO receives one digest item per NewsEvent.
    """
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    canonical_title: str
    event_date: Optional[datetime] = None
    category: str = "Other"
    subcategory: Optional[str] = None
    canonical_url: str
    canonical_source_name: str
    canonical_source_tier: int = 2

    # All articles covering this event (at least one)
    articles: list[Article] = Field(default_factory=list)
    # Classification from the canonical (primary) article
    classification: Optional[ArticleClassification] = None

    # Verification
    verification_status: str = "UNVERIFIED"  # VERIFIED | UNVERIFIED | OFFICIAL
    verification_source: Optional[str] = None

    # Internal scoring — NEVER sent to CEO
    importance_score: int = 0
    relevance_score: int = 0
    should_send: bool = False

    # Set after summarization
    summary: Optional[EventSummary] = None
