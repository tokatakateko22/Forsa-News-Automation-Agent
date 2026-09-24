"""
tests/unit/test_email_format.py
────────────────────────────────
Unit tests for email formatting.
Key assertions:
  - Email contains factual content (title, source, URL, date)
  - Email does NOT contain analysis, scores, recommendations, or AI labels
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.graph.nodes.format_email import (
    _event_plain_block,
    _event_html_block,
    _build_email,
    _build_no_news_email,
    CATEGORY_ORDER,
)
from app.models.event import NewsEvent, EventSummary

FORBIDDEN_PHRASES = [
    "why this matters",
    "impact on forsa",
    "forsa should",
    "recommended action",
    "strategic implication",
    "importance score",
    "relevance score",
    "confidence",
    "ai ",
    "llm",
    "we recommend",
    "you should",
    "predicted",
    "prediction",
    "investment advice",
]


def make_event(
    title: str = "FRA issues new consumer finance supervisory manual",
    category: str = "FRA",
    source: str = "Financial Regulatory Authority",
    url: str = "https://fra.gov.eg/press-release",
) -> NewsEvent:
    event = NewsEvent(
        canonical_title=title,
        category=category,
        canonical_url=url,
        canonical_source_name=source,
        canonical_source_tier=1,
        importance_score=85,  # Internal — must NOT appear in output
        relevance_score=90,   # Internal — must NOT appear in output
        should_send=True,
    )
    event.summary = EventSummary(
        event_id=event.event_id,
        summary_text="The Financial Regulatory Authority issued new supervisory rules for consumer finance companies.",
        category=category,
        canonical_title=title,
        canonical_url=url,
        source_name=source,
        published_at=datetime(2026, 9, 13, 8, 0, 0, tzinfo=timezone.utc),
    )
    return event


class TestCEOEmailContent:
    def test_contains_summary_text(self):
        event = make_event()
        block = _event_plain_block(event)
        assert "Financial Regulatory Authority issued new supervisory" in block

    def test_contains_source_name(self):
        event = make_event()
        block = _event_plain_block(event)
        assert "Financial Regulatory Authority" in block

    def test_contains_url(self):
        event = make_event()
        block = _event_plain_block(event)
        assert "https://fra.gov.eg/press-release" in block

    def test_contains_date(self):
        event = make_event()
        block = _event_plain_block(event)
        assert "2026" in block or "Sep" in block

    def test_contains_category_emoji(self):
        event = make_event(category="FRA")
        block = _event_plain_block(event)
        assert "🔴" in block


class TestCEOEmailForbiddenContent:
    """
    Strictly verify that internal data never leaks to the CEO.
    """

    def test_no_importance_score_in_plain(self):
        event = make_event()
        block = _event_plain_block(event)
        for phrase in FORBIDDEN_PHRASES:
            assert phrase.lower() not in block.lower(), \
                f"Forbidden phrase found in plain email: {phrase!r}"

    def test_no_importance_score_in_html(self):
        event = make_event()
        block = _event_html_block(event)
        # Score numbers alone can't be forbidden (they appear in dates etc.)
        # But "importance_score" or "relevance_score" as strings must not appear
        assert "importance_score" not in block
        assert "relevance_score" not in block
        assert "85" not in block.replace("2026", "").replace("100", "")  # score values

    def test_no_analysis_phrases(self):
        event = make_event()
        plain = _event_plain_block(event)
        html = _event_html_block(event)
        combined = (plain + html).lower()
        for phrase in FORBIDDEN_PHRASES:
            assert phrase not in combined, \
                f"Forbidden phrase found: {phrase!r}"


class TestCategoryOrdering:
    def test_fra_before_economy(self):
        assert CATEGORY_ORDER.index("FRA") < CATEGORY_ORDER.index("Economy")

    def test_fra_before_fintech(self):
        assert CATEGORY_ORDER.index("FRA") < CATEGORY_ORDER.index("FinTech")

    def test_critical_categories_first(self):
        critical = ["FRA", "Consumer Finance"]
        non_critical = ["Economy", "Other"]
        for c in critical:
            for nc in non_critical:
                assert CATEGORY_ORDER.index(c) < CATEGORY_ORDER.index(nc)


class TestNoNewsEmail:
    def test_no_news_subject_contains_date(self):
        subject, html, plain = _build_no_news_email("13 September 2026")
        assert "13 September 2026" in subject

    def test_no_news_body_factual(self):
        _, _, plain = _build_no_news_email("13 September 2026")
        assert "No significant" in plain
        # Must NOT contain fake news or analysis
        assert "CBE" not in plain
        assert "interest rate" not in plain

    def test_full_email_structure(self):
        events = [make_event()]
        subject, html, plain = _build_email(events, "13 September 2026")
        assert "Forsa Financial Market News" in plain
        assert "13 September 2026" in plain
        assert "Financial Regulatory Authority" in plain
