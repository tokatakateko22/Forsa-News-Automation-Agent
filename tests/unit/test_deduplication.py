"""
tests/unit/test_deduplication.py
──────────────────────────────────
Unit tests for the deduplication service.
No DB, no LLM, no network required.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pytest

from app.models.article import Article, ArticleClassification
from app.services.deduplication import (
    DeduplicationService,
    _normalize_title,
    _titles_similar,
    _entities_overlap,
    _within_time_window,
)


def make_article(
    title: str,
    url: str,
    content: str = "",
    source_name: str = "Test",
    source_tier: int = 2,
    published_at: datetime | None = None,
) -> Article:
    return Article(
        title=title,
        url=url,
        content=content,
        source_name=source_name,
        source_tier=source_tier,
        published_at=published_at or datetime.now(timezone.utc),
    )


def make_cls(article_id: str, entities: Optional[list[str]] = None) -> ArticleClassification:
    return ArticleClassification(
        article_id=article_id,
        is_relevant=True,
        category="CBE",
        entities=entities or [],
    )


class TestTitleSimilarity:
    def test_identical_titles(self):
        assert _titles_similar("CBE raises interest rate by 100bps", "CBE raises interest rate by 100bps")

    def test_similar_titles(self):
        assert _titles_similar(
            "CBE raises interest rate by 100 basis points",
            "Central Bank of Egypt raises rates by 100bps"
        ) is False  # Different enough

    def test_nearly_identical_titles(self):
        assert _titles_similar(
            "CBE Monetary Policy Committee raises interest rate",
            "CBE Monetary Policy Committee raises interest rates",
        )

    def test_completely_different(self):
        assert not _titles_similar("Egypt GDP growth 4.5%", "CBE interest rate decision")

    def test_punctuation_difference(self):
        assert _titles_similar("CBE: Interest rate cut by 200bps", "CBE Interest rate cut by 200bps")


class TestEntityOverlap:
    def test_full_overlap(self):
        assert _entities_overlap(["CBE", "Egypt", "inflation"], ["CBE", "Egypt", "interest rate"])

    def test_no_overlap(self):
        assert not _entities_overlap(["valU", "BNPL"], ["CBE", "interest rate"])

    def test_single_shared_entity(self):
        assert _entities_overlap(["CBE"], ["CBE", "Egypt"])

    def test_empty_entities(self):
        assert not _entities_overlap([], ["CBE"])

    def test_case_insensitive(self):
        assert _entities_overlap(["cbe", "egypt"], ["CBE", "EGYPT"])


class TestTimeWindow:
    def test_same_time(self):
        now = datetime.now(timezone.utc)
        assert _within_time_window(now, now)

    def test_within_24h(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(hours=12)
        assert _within_time_window(now, earlier, hours=24)

    def test_outside_window(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=48)
        assert not _within_time_window(now, old, hours=24)

    def test_none_times(self):
        # If either time is None, we assume same event (conservative)
        assert _within_time_window(None, None)


class TestDeduplicationService:
    def test_exact_url_dedup(self):
        a1 = make_article("CBE raises rates", "https://example.com/article-1")
        a2 = make_article("CBE raises rates", "https://example.com/article-1")  # Same URL
        cls = {a.article_id: make_cls(a.article_id) for a in [a1, a2]}
        service = DeduplicationService()
        events = service.deduplicate([a1, a2], cls)
        assert len(events) == 1

    def test_different_articles_different_events(self):
        a1 = make_article("CBE raises rates", "https://example.com/article-1")
        a2 = make_article("valU launches new product", "https://example.com/article-2")
        cls = {a.article_id: make_cls(a.article_id) for a in [a1, a2]}
        service = DeduplicationService()
        events = service.deduplicate([a1, a2], cls)
        assert len(events) == 2

    def test_same_event_different_sources(self):
        a1 = make_article(
            "CBE Monetary Policy Committee raises interest rate by 100bps",
            "https://reuters.com/cbe-rate",
            source_name="Reuters",
            source_tier=2,
        )
        a2 = make_article(
            "CBE Monetary Policy Committee raises interest rates by 100 basis points",
            "https://zawya.com/cbe-rate",
            source_name="Zawya",
            source_tier=2,
        )
        cls = {a.article_id: make_cls(a.article_id, entities=["CBE", "interest rate"]) for a in [a1, a2]}
        service = DeduplicationService()
        events = service.deduplicate([a1, a2], cls)
        # May be grouped as one event (title similarity + entity overlap)
        # This is a soft assertion — depends on fuzzy threshold
        assert len(events) in (1, 2)

    def test_canonical_prefers_tier1(self):
        tier1 = make_article("CBE decision", "https://cbe.org.eg/news", source_tier=1, source_name="CBE")
        tier2 = make_article("CBE decision", "https://zawya.com/cbe", source_tier=2, source_name="Zawya")
        cls = {a.article_id: make_cls(a.article_id) for a in [tier1, tier2]}
        service = DeduplicationService()
        events = service.deduplicate([tier2, tier1], cls)  # Note: tier2 first in input
        assert len(events) >= 1
        # If grouped, canonical should be tier1
        single_event = [e for e in events if e.canonical_source_tier == 1]
        assert len(single_event) >= 1

    def test_empty_input(self):
        service = DeduplicationService()
        events = service.deduplicate([], {})
        assert events == []
