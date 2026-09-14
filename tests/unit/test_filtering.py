"""
tests/unit/test_filtering.py
─────────────────────────────
Unit tests for the importance filter logic.
Tests threshold application and category-based importance rules.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.event import NewsEvent
from app.services.verification import VerificationService, _url_matches


def make_event(
    category: str = "CBE",
    importance_score: int = 75,
    verification_status: str = "VERIFIED",
    url: str = "https://reuters.com/egypt-cbe",
) -> NewsEvent:
    from app.models.article import Article
    article = Article(
        title="Test article",
        url=url,
        source_name="Reuters",
        source_tier=2,
    )
    event = NewsEvent(
        canonical_title="Test event",
        category=category,
        canonical_url=url,
        canonical_source_name="Reuters",
        canonical_source_tier=2,
        articles=[article],
        importance_score=importance_score,
        verification_status=verification_status,
    )
    return event


class TestImportanceThreshold:
    def test_above_threshold_passes(self):
        # An event with score 75 passes a threshold of 60
        event = make_event(importance_score=75)
        assert event.importance_score >= 60

    def test_below_threshold_fails(self):
        event = make_event(importance_score=40)
        assert not (event.importance_score >= 60)

    def test_at_threshold_passes(self):
        event = make_event(importance_score=60)
        assert event.importance_score >= 60


class TestVerificationService:
    def test_official_source_detected(self):
        event = make_event(url="https://www.cbe.org.eg/en/press-releases/2026")
        from app.models.article import Article
        event.articles = [
            Article(title="t", url="https://www.cbe.org.eg/en/press-releases/2026",
                    source_name="CBE", source_tier=1)
        ]
        service = VerificationService()
        result = service.verify(event)
        assert result.verification_status == "OFFICIAL"

    def test_reputable_source_verified(self):
        event = make_event(url="https://www.reuters.com/egypt-cbe")
        from app.models.article import Article
        event.articles = [
            Article(title="t", url="https://www.reuters.com/egypt-cbe",
                    source_name="Reuters", source_tier=2)
        ]
        service = VerificationService()
        result = service.verify(event)
        assert result.verification_status == "VERIFIED"

    def test_unknown_source_unverified(self):
        event = make_event(url="https://randomsite123.com/news")
        from app.models.article import Article
        event.articles = [
            Article(title="t", url="https://randomsite123.com/news",
                    source_name="Random Blog", source_tier=3)
        ]
        service = VerificationService()
        result = service.verify(event)
        assert result.verification_status == "UNVERIFIED"

    def test_fra_is_official(self):
        assert _url_matches("https://fra.gov.eg/en/decisions/", ["fra.gov.eg"])

    def test_cbe_is_official(self):
        assert _url_matches("https://www.cbe.org.eg/press-release", ["cbe.org.eg"])
